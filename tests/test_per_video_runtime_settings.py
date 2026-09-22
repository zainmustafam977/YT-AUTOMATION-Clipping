"""
Tests for Per-Video Runtime Settings & Engine Tuning.
Verifies seamless fallback to global defaults when omitted,
correct propagation of per-video overrides when specified,
and database persistence on SourceVideo.
"""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shorts_engine.config.settings import settings
from shorts_engine.db.session import Base, ensure_schema_upgrades
from shorts_engine.db.models import Channel, SourceVideo, SourceVideoStatus
from shorts_engine.orchestration.runner import PipelineRunner
from shorts_engine.services.clip_selection.selector import ClipSelectionService, SelectedClip
from shorts_engine.services.transcription.formatter import FormattedTranscript, WordSegment
from shorts_engine.services.reframing.cropper import CropPlan
from shorts_engine.services.rendering.compositor import VideoCompositor


@pytest.fixture
def test_db():
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=test_engine)
    ensure_schema_upgrades(test_engine)
    Session = sessionmaker(bind=test_engine)
    db = Session()
    yield db
    db.close()


def test_source_video_runtime_overrides_property(test_db):
    """Verifies that SourceVideo.runtime_overrides getter and setter serialize to JSON."""
    ch = Channel(youtube_channel_id="ch_tune_test", name="Tuning Test Channel")
    test_db.add(ch)
    test_db.commit()

    # 1. Default empty
    v1 = SourceVideo(channel_id=ch.id, youtube_video_id="v_default_123", title="Video 1")
    test_db.add(v1)
    test_db.commit()
    assert v1.runtime_overrides == {}
    assert v1.runtime_overrides_json is None

    # 2. Set overrides
    overrides = {
        "llm_provider": "groq",
        "target_duration_min_sec": 20,
        "target_duration_max_sec": 45,
        "video_encoding_crf": 16,
        "enable_sfx": False,
    }
    v1.runtime_overrides = overrides
    test_db.commit()
    test_db.refresh(v1)

    assert v1.runtime_overrides_json is not None
    loaded = json.loads(v1.runtime_overrides_json)
    assert loaded["llm_provider"] == "groq"
    assert loaded["video_encoding_crf"] == 16
    assert loaded["enable_sfx"] is False
    assert v1.runtime_overrides == overrides

    # 3. Clear overrides
    v1.runtime_overrides = None
    test_db.commit()
    test_db.refresh(v1)
    assert v1.runtime_overrides == {}
    assert v1.runtime_overrides_json is None


def test_select_clips_respects_overrides():
    """Verifies that ClipSelectionService.select_clips respects runtime overrides and defaults otherwise."""
    words = [
        WordSegment(word="Hello", start=0.0, end=1.0),
        WordSegment(word="world", start=1.0, end=2.0),
        WordSegment(word="this", start=2.0, end=3.0),
        WordSegment(word="is", start=3.0, end=4.0),
        WordSegment(word="a", start=4.0, end=5.0),
        WordSegment(word="test", start=5.0, end=25.0),
    ]
    transcript = FormattedTranscript(words=words, full_text="Hello world this is a test", language="en")

    selector = ClipSelectionService()

    # Mock router.generate_structured to inspect prompt and temperature
    mock_resp = MagicMock()
    mock_resp.candidates = []
    with patch.object(selector.router, "generate_structured", return_value=(mock_resp, {"error": "test"})) as mock_gen:
        with patch.object(selector, "_heuristic_select", return_value=[]) as mock_heur:
            # 1. Without overrides -> uses settings defaults
            selector.select_clips(transcript)
            assert mock_gen.call_count == 1
            call_kwargs = mock_gen.call_args[1]
            assert "min_count" not in call_kwargs # prompt was formatted
            assert mock_heur.call_args[0][1] == settings.MIN_SEGMENTS_PER_VIDEO
            assert mock_heur.call_args[0][2] == settings.MAX_SEGMENTS_PER_VIDEO

            # 2. With overrides -> uses provided values
            selector.select_clips(
                transcript,
                min_clips=1,
                max_clips=2,
                target_duration_min=10,
                target_duration_max=30,
                temperature_override=0.8,
            )
            assert mock_heur.call_args[0][1] == 1
            assert mock_heur.call_args[0][2] == 2


def test_compositor_render_short_accepts_overrides(tmp_path):
    """Verifies that VideoCompositor.render_short accepts and applies parameter overrides."""
    compositor = VideoCompositor(output_dir=tmp_path)
    crop_plan = CropPlan(crop_w=1080, crop_h=1920, crop_x=0, crop_y=0)

    # Test scale_algo override in crop plan
    filter_default = crop_plan.to_ffmpeg_filter()
    assert f"flags={settings.VIDEO_SCALE_ALGORITHM}" in filter_default

    filter_override = crop_plan.to_ffmpeg_filter(scale_algo="bicubic")
    assert "flags=bicubic" in filter_override

    # Test subprocess call with overrides
    with patch("subprocess.run") as mock_run:
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_run.return_value = mock_proc

        dummy_src = tmp_path / "dummy.mp4"
        dummy_src.touch()

        compositor.render_short(
            source_video_path=dummy_src,
            start_ms=0,
            end_ms=10000,
            crop_plan=crop_plan,
            subtitle_ass_path=None,
            output_filename="test_out.mp4",
            crf=15,
            preset="veryslow",
            scale_algo="bicubic",
            audio_bitrate="192k",
            target_fps=60,
        )

        assert mock_run.called
        cmd = mock_run.call_args[0][0]
        # Check that target fps was set
        assert "-r" in cmd
        fps_idx = cmd.index("-r")
        assert cmd[fps_idx + 1] == "60"

        # Check that audio bitrate was set
        assert "-b:a" in cmd
        br_idx = cmd.index("-b:a")
        assert cmd[br_idx + 1] == "192k"


def test_pipeline_runner_persists_and_applies_overrides(test_db, tmp_path):
    """Verifies that PipelineRunner.run_pipeline_for_video saves overrides to DB and applies them."""
    runner = PipelineRunner(test_db)
    ch = runner.get_or_create_channel("test_channel", "Test Channel")

    overrides = {
        "llm_provider": "groq",
        "target_duration_min_sec": 18,
        "target_duration_max_sec": 50,
        "min_segments_per_video": 2,
        "max_segments_per_video": 4,
        "video_encoding_crf": 17,
        "enable_sfx": False,
    }

    vid_id = "test_vid_tune"
    with patch.object(runner.ingestion, "extract_video_id", return_value=vid_id):
        with patch.object(runner.ingestion, "download_video") as mock_dl:
            mock_dl_res = MagicMock()
            mock_dl_res.title = "Test Tuning Video"
            mock_dl_res.duration_sec = 600
            mock_dl_res.video_path = tmp_path / "dummy.mp4"
            mock_dl_res.audio_path = tmp_path / "dummy.wav"
            mock_dl_res.metadata = {}
            mock_dl.return_value = mock_dl_res

            # Ensure downloaded files exist
            mock_dl_res.video_path.touch()
            mock_dl_res.audio_path.touch()

            with patch.object(runner.transcription, "transcribe") as mock_tr:
                words = [
                    WordSegment(word="Hello", start=0.0, end=1.0),
                    WordSegment(word="world", start=1.0, end=30.0),
                ]
                mock_tr.return_value = FormattedTranscript(words=words, full_text="Hello world", language="en")

                with patch.object(runner.clip_selector, "select_clips", return_value=[]) as mock_sel:
                    runner.run_pipeline_for_video(
                        vid_id,
                        channel=ch,
                        dry_run=True,
                        runtime_overrides=overrides,
                    )

                    # Verify video record in DB has overrides saved
                    v_db = test_db.query(SourceVideo).filter_by(youtube_video_id=vid_id).first()
                    assert v_db is not None
                    assert v_db.runtime_overrides == overrides

                    # Verify select_clips was called with overridden bounds
                    assert mock_sel.called
                    call_kwargs = mock_sel.call_args[1]
                    assert call_kwargs["min_clips"] == 2
                    assert call_kwargs["max_clips"] == 4
                    assert call_kwargs["target_duration_min"] == 18
                    assert call_kwargs["target_duration_max"] == 50
                    assert call_kwargs["provider_override"] == "groq"
