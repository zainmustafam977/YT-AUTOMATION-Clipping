"""
Tests for Component 3: B-Roll Stock Footage Integration (BrollClient & VideoCompositor Overlay)
"""

import json
import pytest
from unittest.mock import patch, MagicMock

from shorts_engine.services.broll.client import BrollClient
from shorts_engine.services.rendering.compositor import VideoCompositor
from shorts_engine.services.reframing.cropper import CropPlan


@pytest.fixture
def temp_cache_dir(tmp_path):
    cache_d = tmp_path / "broll_cache"
    cache_d.mkdir(parents=True, exist_ok=True)
    return cache_d


def test_broll_client_cache_hit(temp_cache_dir):
    """Verifies that an existing cached B-roll file is returned without network calls."""
    query = "cyberpunk city"
    cached_file = temp_cache_dir / "cyberpunk_city.mp4"
    cached_file.write_bytes(b"dummy mp4 video bytes" * 100)

    client = BrollClient(cache_dir=temp_cache_dir)
    result = client.fetch_broll_clip(query)

    assert result == cached_file
    assert result.exists()


def test_broll_client_no_api_key(temp_cache_dir, monkeypatch):
    """Verifies that fetch_broll_clip returns None gracefully when no Pexels API key is configured."""
    from shorts_engine.config.settings import settings
    monkeypatch.setattr(settings, "PEXELS_API_KEY", None)

    client = BrollClient(cache_dir=temp_cache_dir)
    result = client.fetch_broll_clip("neon lights")
    assert result is None


def test_broll_client_mock_download(temp_cache_dir, monkeypatch):
    """Verifies mock network call and download parsing for Pexels Video API."""
    from shorts_engine.config.settings import settings
    monkeypatch.setattr(settings, "PEXELS_API_KEY", "mock_key_12345")

    client = BrollClient(cache_dir=temp_cache_dir)

    mock_search_payload = {
        "videos": [
            {
                "id": 999,
                "duration": 5,
                "video_files": [
                    {
                        "id": 1,
                        "quality": "hd",
                        "width": 1080,
                        "height": 1920,
                        "link": "https://example.com/broll/sample_1080.mp4",
                    }
                ],
            }
        ]
    }

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.side_effect = [
        json.dumps(mock_search_payload).encode("utf-8"),
        b"fake mp4 video content data stream " * 50,
        b"",
    ]
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        res = client.fetch_broll_clip("success celebration")
        assert res is not None
        assert res.exists()
        assert res.name == "success_celebration.mp4"
        assert res.stat().st_size > 100


def test_video_compositor_broll_filter_complex(tmp_path, monkeypatch):
    """Verifies that VideoCompositor properly constructs multi-input filter_complex when broll_video_path is passed."""
    out_dir = tmp_path / "renders"
    compositor = VideoCompositor(output_dir=out_dir)

    source_video = tmp_path / "source.mp4"
    source_video.write_bytes(b"mock source video data")

    broll_video = tmp_path / "broll.mp4"
    broll_video.write_bytes(b"mock broll video data")

    crop_plan = CropPlan(
        crop_w=720,
        crop_h=1280,
        crop_x=100,
        crop_y=0,
    )

    captured_cmds = []

    def mock_subprocess_run(cmd, *args, **kwargs):
        captured_cmds.append(cmd)
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = ""
        mock_proc.stderr = ""
        return mock_proc

    # Mock validate_render to return False initially so render_short executes
    monkeypatch.setattr(compositor, "validate_render", lambda p: MagicMock(is_valid=False))
    monkeypatch.setattr("subprocess.run", mock_subprocess_run)

    res = compositor.render_short(
        source_video_path=source_video,
        start_ms=1000,
        end_ms=6000,
        crop_plan=crop_plan,
        subtitle_ass_path=None,
        output_filename="test_broll_short.mp4",
        broll_video_path=broll_video,
        broll_start_sec=1.5,
        broll_duration_sec=2.0,
    )

    # Filter out encoder probing commands like ['ffmpeg', '-encoders']
    render_cmds = [c for c in captured_cmds if "-i" in c]
    assert len(render_cmds) > 0
    cmd = render_cmds[0]

    # Verify multi-input structure and filter_complex
    assert "-filter_complex" in cmd
    fc_idx = cmd.index("-filter_complex")
    fc_str = cmd[fc_idx + 1]

    # Verify broll input is included
    assert str(broll_video) in cmd
    # Verify overlay filter syntax
    assert "overlay=0:0:enable='between(t,1.5,3.5)'" in fc_str
    assert "[broll_scaled]" in fc_str
    assert "-map" in cmd
    assert "[v_out]" in cmd
    assert "[a_out]" in cmd
