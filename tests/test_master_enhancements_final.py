"""
Master Architectural Enhancements Final Verification Test Suite:
Covers Enhancements 10, 6, 4, and 7 to verify 100% architectural completion.

1. Enhancement 10:
   - Pre-flight disk space assertion (< 5.0 GB circuit guard).
   - Temp directory housekeeping purging *.wav, *.ass, *.tmp.
   - HousekeepingDaemon thread lifecycle and periodic sweep.

2. Enhancement 6:
   - FONTS_DIR definition and automatic directory creation.
   - get_ass_filter_string Windows path escaping and fontsdir attribute.
   - VideoCompositor._build_filter_complex fontsdir inclusion.

3. Enhancement 4:
   - get_preview_proxy_path deterministic naming.
   - is_preview_proxy_needed container (.mkv) and codec (HEVC/ProRes/VP9/AV1) detection.
   - generate_preview_proxy fast 720p H.264 creation and caching.
   - stream_clip_video proxy-aware serving for HTML5 compatibility.

4. Enhancement 7:
   - VideoCompositor.inject_cover_frame 0.033s frame 0 overlay filter generation.
   - VideoCompositor.render_clip alias and cover_frame_sec parameter support.
   - POST /api/clips/{clip_id}/cover-frame endpoint validation, injection, and DB metadata persistence.
"""

import os
import json
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from shorts_engine.config.settings import settings
from shorts_engine.db.session import Base
from shorts_engine.db.models import (
    Clip,
    ClipStatus,
    ClipCandidate,
    SourceVideo,
    Channel,
    User,
    UserRole,
)
from shorts_engine.orchestration.runner import assert_sufficient_disk_space
from shorts_engine.services.ingestion.downloader import purge_stale_temp_files
from shorts_engine.services.storage.housekeeping import HousekeepingDaemon
from shorts_engine.services.storage.path_manager import PathManager, get_preview_proxy_path
from shorts_engine.services.ingestion.local_loader import (
    is_preview_proxy_needed,
    generate_preview_proxy,
)
from shorts_engine.services.rendering.compositor import (
    VideoCompositor,
    get_ass_filter_string,
)
from shorts_engine.dashboard.app import create_dashboard_app
from shorts_engine.dashboard.routes import queue as queue_route
from shorts_engine.dashboard.auth import require_operator, require_viewer, verify_csrf


# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------

@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def client(db_session):
    app = create_dashboard_app()
    app.dependency_overrides[queue_route.get_db] = lambda: db_session
    fake_user = User(username="test_op", role=UserRole.OPERATOR, is_active=True)
    app.dependency_overrides[require_operator] = lambda: fake_user
    app.dependency_overrides[require_viewer] = lambda: fake_user
    app.dependency_overrides[verify_csrf] = lambda: True
    return TestClient(app)


# -----------------------------------------------------------------------------
# Enhancement 10: Low Disk Space Circuit Guard & Temp Housekeeping
# -----------------------------------------------------------------------------

def test_assert_sufficient_disk_space_pass(tmp_path):
    mock_usage = MagicMock(free=10 * (1024**3))  # 10 GB free
    with patch("shutil.disk_usage", return_value=mock_usage):
        result = assert_sufficient_disk_space(tmp_path, min_free_gb=5.0)
        assert result >= 5.0


def test_assert_sufficient_disk_space_fail(tmp_path):
    mock_usage = MagicMock(free=2 * (1024**3))  # 2 GB free (< 5 GB)
    with patch("shutil.disk_usage", return_value=mock_usage):
        with pytest.raises(RuntimeError, match="Low Disk Space"):
            assert_sufficient_disk_space(tmp_path, min_free_gb=5.0)


def test_assert_sufficient_disk_space_custom_threshold(tmp_path):
    mock_usage = MagicMock(free=8 * (1024**3))  # 8 GB free
    with patch("shutil.disk_usage", return_value=mock_usage):
        with pytest.raises(RuntimeError, match="Low Disk Space"):
            assert_sufficient_disk_space(tmp_path, min_free_gb=10.0)


def test_purge_stale_temp_files_purges_wav(tmp_path):
    # Create stale temp files
    wav_file = tmp_path / "audio_temp.wav"
    ass_file = tmp_path / "subtitle_temp.ass"
    tmp_file = tmp_path / "cache.tmp"
    mp4_file = tmp_path / "important_video.mp4"

    wav_file.write_bytes(b"dummy wav data")
    ass_file.write_text("dummy ass data")
    tmp_file.write_bytes(b"dummy tmp data")
    mp4_file.write_bytes(b"dummy mp4 data")

    # Backdate mtimes by 48 hours
    old_time = time.time() - (48 * 3600)
    for f in (wav_file, ass_file, tmp_file, mp4_file):
        os.utime(f, (old_time, old_time))

    purged = purge_stale_temp_files(max_age_hours=24.0, temp_dir=tmp_path)

    assert not wav_file.exists(), "*.wav must be purged by temp housekeeping"
    assert not ass_file.exists(), "*.ass must be purged"
    assert not tmp_file.exists(), "*.tmp must be purged"
    assert mp4_file.exists(), "*.mp4 should not be deleted by temp housekeeping"
    assert purged >= 3


def test_housekeeping_daemon_cycle():
    daemon = HousekeepingDaemon(interval_seconds=10)
    assert not daemon._running

    with patch("shorts_engine.services.ingestion.downloader.IngestionService.purge_stale_temp_files", return_value=4):
        with patch("shorts_engine.services.ingestion.downloader.IngestionService.purge_stale_fragments", return_value=2):
            res = daemon.run_cycle()
            assert res["stale_temp_purged"] == 4
            assert res["stale_fragments_purged"] == 2


@pytest.mark.anyio
async def test_housekeeping_daemon_async_lifecycle():
    daemon = HousekeepingDaemon(interval_seconds=1)
    assert not daemon._running

    await daemon.start()
    assert daemon._running
    assert daemon._task is not None

    await daemon.stop()
    assert not daemon._running


# -----------------------------------------------------------------------------
# Enhancement 6: Color Emoji Subtitles & Fonts Directory
# -----------------------------------------------------------------------------

def test_fonts_dir_defined_in_settings():
    assert hasattr(settings, "FONTS_DIR")
    assert isinstance(settings.FONTS_DIR, Path)
    assert settings.FONTS_DIR.name == "fonts"
    assert settings.FONTS_DIR.parent.name == "assets"
    assert settings.FONTS_DIR.exists()


def test_get_ass_filter_string_formatting():
    ass_path = Path("D:/Projects/YT AUTOMATION/storage/temp/sub.ass")
    fonts_dir = Path(settings.FONTS_DIR)

    filter_str = get_ass_filter_string(ass_path, fonts_dir)
    assert filter_str.startswith("ass='")
    assert ":fontsdir='" in filter_str
    # Backslashes must be normalized to forward slashes and colons escaped
    from shorts_engine.services.rendering.compositor import escape_ffmpeg_filter_path
    assert escape_ffmpeg_filter_path(fonts_dir) in filter_str
    assert "D\\:/Projects/YT AUTOMATION/storage/temp/sub.ass" in filter_str


def test_compositor_build_filter_complex_incorporates_fontsdir():
    compositor = VideoCompositor()
    ass_path = Path("D:/test/sub.ass")

    fc = compositor._build_filter_complex(
        base_filter="crop=1080:1920:0:0",
        has_subs=True,
        subtitle_ass_path=ass_path,
    )

    assert "ass=" in fc
    assert ":fontsdir=" in fc


# -----------------------------------------------------------------------------
# Enhancement 4: Instant Browser Playback Proxy
# -----------------------------------------------------------------------------

def test_get_preview_proxy_path_deterministic():
    src1 = Path("D:/videos/sample.mp4")
    proxy1 = get_preview_proxy_path(src1)
    assert proxy1 == Path("D:/videos/sample_proxy_preview.mp4")

    src2 = Path("D:/videos/movie.mkv")
    proxy2 = PathManager.get_preview_proxy_path(src2)
    assert proxy2 == Path("D:/videos/movie_proxy_preview.mp4")


def test_is_preview_proxy_needed():
    # Containers
    assert is_preview_proxy_needed("video.mkv") is True
    assert is_preview_proxy_needed(Path("video.MKV")) is True

    # Codecs
    for codec in ["hevc", "h265", "prores", "vp9", "av1"]:
        assert is_preview_proxy_needed("video.mp4", codec=codec) is True

    # Standard browser-safe formats
    assert is_preview_proxy_needed("video.mp4", codec="h264") is False
    assert is_preview_proxy_needed("video.mp4", codec="avc1") is False


def test_generate_preview_proxy_cached_and_creation(tmp_path):
    src = tmp_path / "test_source.mkv"
    src.write_bytes(b"dummy video data")

    proxy_target = tmp_path / "test_source_proxy_preview.mp4"

    # When proxy already exists, return immediately without running command
    proxy_target.write_bytes(b"existing proxy")
    res = generate_preview_proxy(src, force=False)
    assert res == proxy_target

    # When proxy does not exist, run watchdog
    proxy_target.unlink()
    mock_run_result = MagicMock(returncode=0)

    def fake_run(cmd, *args, **kwargs):
        # Create destination temp proxy to simulate ffmpeg
        temp_proxy = proxy_target.parent / f"{proxy_target.stem}_tmp.mp4"
        temp_proxy.write_bytes(b"generated proxy data")
        return mock_run_result

    with patch("shorts_engine.services.execution.watchdog.SubprocessWatchdog.run", side_effect=fake_run):
        created = generate_preview_proxy(src, force=True)
        assert created == proxy_target
        assert proxy_target.exists()


def test_stream_clip_video_serves_proxy_for_mkv(db_session, client, tmp_path):
    # Setup channel, video, candidate, clip
    channel = Channel(id="c1", name="Chan", youtube_channel_id="UC1")
    db_session.add(channel)
    sv = SourceVideo(id="sv1", channel_id="c1", youtube_video_id="yt1", title="Vid")
    db_session.add(sv)
    cand = ClipCandidate(id="cand1", source_video_id="sv1", start_ms=0, end_ms=10000, score=9.0, reason="Viral segment hook")
    db_session.add(cand)

    clip_file = tmp_path / "clip.mkv"
    clip_file.write_bytes(b"dummy mkv")

    # Create proxy
    proxy_file = tmp_path / "clip_proxy_preview.mp4"
    proxy_file.write_bytes(b"dummy proxy mp4")

    clip = Clip(id="clip1", clip_candidate_id="cand1", rendered_path=str(clip_file), status=ClipStatus.WAITING_APPROVAL)
    db_session.add(clip)
    db_session.commit()

    resp = client.get(f"/api/video/{clip.id}")
    assert resp.status_code == 200
    assert resp.headers.get("content-type") == "video/mp4"
    # Content should be from the proxy
    assert resp.content == b"dummy proxy mp4"


# -----------------------------------------------------------------------------
# Enhancement 7: YouTube Short Cover Frame Selector
# -----------------------------------------------------------------------------

def test_compositor_inject_cover_frame_command(tmp_path):
    compositor = VideoCompositor()
    video_path = tmp_path / "short.mp4"
    video_path.write_bytes(b"video content")
    cover_image = tmp_path / "cover.jpg"
    cover_image.write_bytes(b"cover image")
    output_path = tmp_path / "short_injected.mp4"

    captured_cmds = []

    def fake_run(cmd, *args, **kwargs):
        captured_cmds.append(cmd)
        # Create temp_out so inject_cover_frame doesn't fail
        temp_out = output_path.parent / f"{output_path.stem}_tmp_cov.mp4"
        temp_out.write_bytes(b"injected video")
        return MagicMock(returncode=0)

    with patch("shorts_engine.services.execution.watchdog.SubprocessWatchdog.run", side_effect=fake_run):
        out = compositor.inject_cover_frame(
            video_path=video_path,
            cover_image_path=cover_image,
            output_path=output_path,
        )
        assert out == output_path
        assert output_path.exists()
        assert len(captured_cmds) == 1
        cmd_str = " ".join(captured_cmds[0])
        # Verify 0.033s frame 0 overlay injection
        assert "overlay=0:0:enable='between(t,0,0.033)'" in cmd_str
        assert "scale=1080:1920" in cmd_str


def test_compositor_render_clip_alias():
    compositor = VideoCompositor()
    assert hasattr(compositor, "render_clip")
    with patch.object(compositor, "render_short", return_value=Path("out.mp4")) as mock_rs:
        compositor.render_clip("arg1", cover_frame_sec=3.5)
        mock_rs.assert_called_once_with("arg1", cover_frame_sec=3.5)


def test_post_cover_frame_endpoint(db_session, client, tmp_path):
    # Setup DB records
    channel = Channel(id="c2", name="Chan2", youtube_channel_id="UC2")
    db_session.add(channel)
    sv = SourceVideo(id="sv2", channel_id="c2", youtube_video_id="yt2", title="Vid2")
    db_session.add(sv)
    cand = ClipCandidate(id="cand2", source_video_id="sv2", start_ms=0, end_ms=15000, score=9.2, reason="Viral segment hook")
    db_session.add(cand)

    clip_file = tmp_path / "rendered_short.mp4"
    clip_file.write_bytes(b"initial mp4 content")

    clip = Clip(id="clip2", clip_candidate_id="cand2", rendered_path=str(clip_file), status=ClipStatus.WAITING_APPROVAL)
    db_session.add(clip)
    db_session.commit()

    # 1. Test 404 on missing clip
    res_404 = client.post("/api/clips/non_existent_clip/cover-frame", json={"timestamp_sec": 2.0})
    assert res_404.status_code == 404

    # 2. Test successful cover frame injection
    def fake_watchdog(cmd, *args, **kwargs):
        # Check if frame extraction or injection
        if "-frames:v" in cmd:
            # Output is cover image
            out_img = Path(cmd[-1])
            out_img.write_bytes(b"extracted jpg")
        return MagicMock(returncode=0)

    def fake_inject(video_path, cover_image_path, output_path=None):
        out_p = Path(output_path) if output_path else Path(video_path)
        out_p.write_bytes(b"injected mp4 content")
        return out_p

    with patch("shorts_engine.services.execution.watchdog.SubprocessWatchdog.run", side_effect=fake_watchdog):
        with patch.object(VideoCompositor, "inject_cover_frame", side_effect=fake_inject):
            resp = client.post(
                f"/api/clips/{clip.id}/cover-frame",
                json={"timestamp_sec": 4.25},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "success"
            assert data["clip_id"] == clip.id
            assert data["timestamp_sec"] == 4.25
            assert "cover_frame_path" in data

            # Verify DB persistence of cover_frame_sec and path in quality_check_result
            db_session.refresh(clip)
            assert clip.quality_check_result is not None
            qr = json.loads(clip.quality_check_result)
            assert qr["cover_frame_sec"] == 4.25
            assert "cover_frame_path" in qr
            assert qr["cover_frame_path"].endswith(f"{clip.id}_cover.jpg")


def test_purge_stale_fragments_static(tmp_path):
    from shorts_engine.services.ingestion.downloader import purge_stale_fragments, IngestionService

    # Create stale download fragments
    part_file = tmp_path / "video.mp4.part"
    temp_file = tmp_path / "video.mp4.temp"
    ytdl_file = tmp_path / "video.mp4.ytdl"
    finished_file = tmp_path / "video.mp4"

    part_file.write_bytes(b"partial data")
    temp_file.write_bytes(b"temp data")
    ytdl_file.write_bytes(b"ytdl data")
    finished_file.write_bytes(b"finished video")

    # Backdate mtimes by 5 hours (> 2h threshold)
    old_time = time.time() - (5 * 3600)
    for f in (part_file, temp_file, ytdl_file, finished_file):
        os.utime(f, (old_time, old_time))

    # Test static invocation via module-level alias without instantiation
    purged = purge_stale_fragments(max_age_hours=2.0, output_dir=tmp_path)
    assert purged == 3
    assert not part_file.exists()
    assert not temp_file.exists()
    assert not ytdl_file.exists()
    assert finished_file.exists(), "Completed mp4 files must never be purged"

    # Also test via IngestionService.purge_stale_fragments directly
    assert hasattr(IngestionService, "purge_stale_fragments")


def test_post_cover_frame_clamps_to_duration(db_session, client, tmp_path):
    # Setup DB records for a 10-second candidate
    channel = Channel(id="c3", name="Chan3", youtube_channel_id="UC3")
    db_session.add(channel)
    sv = SourceVideo(id="sv3", channel_id="c3", youtube_video_id="yt3", title="Vid3")
    db_session.add(sv)
    cand = ClipCandidate(id="cand3", source_video_id="sv3", start_ms=0, end_ms=10000, score=8.5, reason="Hook")
    db_session.add(cand)

    clip_file = tmp_path / "short3.mp4"
    clip_file.write_bytes(b"short content")
    clip = Clip(id="clip3", clip_candidate_id="cand3", rendered_path=str(clip_file), status=ClipStatus.WAITING_APPROVAL)
    db_session.add(clip)
    db_session.commit()

    captured_ss = []

    def fake_watchdog(cmd, *args, **kwargs):
        if "-frames:v" in cmd:
            idx = cmd.index("-ss")
            captured_ss.append(float(cmd[idx + 1]))
            out_img = Path(cmd[-1])
            out_img.write_bytes(b"extracted jpg")
        return MagicMock(returncode=0)

    def fake_inject(video_path, cover_image_path, output_path=None):
        out_p = Path(output_path) if output_path else Path(video_path)
        out_p.write_bytes(b"injected short")
        return out_p

    with patch("shorts_engine.services.execution.watchdog.SubprocessWatchdog.run", side_effect=fake_watchdog):
        with patch.object(VideoCompositor, "inject_cover_frame", side_effect=fake_inject):
            # Request 99.0s on a 10.0s clip
            resp = client.post(
                f"/api/clips/{clip.id}/cover-frame",
                json={"timestamp_sec": 99.0},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "success"
            # Clamped within max duration - 0.05
            assert data["timestamp_sec"] == pytest.approx(9.95, rel=1e-2)
            assert len(captured_ss) == 1
            assert captured_ss[0] == pytest.approx(9.95, rel=1e-2)


def test_preview_proxy_uses_atomic_replace(tmp_path):
    from shorts_engine.utils import fs as fs_utils

    src = tmp_path / "source.mkv"
    src.write_bytes(b"dummy mkv data")
    target = tmp_path / "source_proxy_preview.mp4"

    def fake_run(cmd, *args, **kwargs):
        # Verify the corrected filter syntax
        cmd_str = " ".join(cmd)
        assert "'min(1280,iw)':'min(720,ih)'" in cmd_str
        temp_proxy = target.parent / f"{target.stem}_tmp.mp4"
        temp_proxy.write_bytes(b"proxy data")
        return MagicMock(returncode=0)

    with patch("shorts_engine.services.execution.watchdog.SubprocessWatchdog.run", side_effect=fake_run):
        with patch.object(fs_utils, "atomic_replace", wraps=fs_utils.atomic_replace) as mock_atomic:
            res = generate_preview_proxy(src, output_proxy_path=target, force=True)
            assert res == target
            assert target.exists()
            assert mock_atomic.called

