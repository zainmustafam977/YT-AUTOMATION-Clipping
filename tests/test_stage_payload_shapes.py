import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from shorts_engine.orchestration.stage_registry import caption_stage, render_stage, StageContext
from shorts_engine.services.transcription.formatter import WordSegment
from shorts_engine.services.reframing.cropper import CropPlan


def test_caption_stage_supports_both_wordsegment_and_dict_shapes(tmp_path):
    sub_path = tmp_path / "subtitles.ass"

    # Shape 1: Live WordSegment objects (monolith path)
    live_words = [
        WordSegment(word="Hello", start=0.0, end=1.0),
        WordSegment(word="World", start=1.0, end=2.0),
    ]
    ctx_live = StageContext(
        db_session=MagicMock(),
        video_id="test_vid_1",
        clip_id="clip_1",
        artifacts={"transcribe": {"transcript": {"words": live_words}}},
    )

    with patch("shorts_engine.services.captioning.ass_generator.ASSSubtitleGenerator.generate_ass") as mock_gen, \
         patch("shorts_engine.services.storage.path_manager.PathManager.get_subtitle_path", return_value=sub_path):
        res = caption_stage(ctx_live)
        assert res["clip_id"] == "clip_1"
        assert mock_gen.called
        called_words = mock_gen.call_args[1]["words"]
        assert all(isinstance(w, WordSegment) for w in called_words)
        assert called_words[0].word == "Hello"

    # Shape 2: Dictionaries (queue / deserialized from DB path)
    dict_words = [
        {"word": "Hello", "start": 0.0, "end": 1.0, "probability": 0.95},
        {"word": "Queue", "start": 1.0, "end": 2.0, "probability": 0.99},
    ]
    ctx_dict = StageContext(
        db_session=MagicMock(),
        video_id="test_vid_2",
        clip_id="clip_2",
        artifacts={"transcribe": {"transcript": {"words": dict_words}}},
    )

    with patch("shorts_engine.services.captioning.ass_generator.ASSSubtitleGenerator.generate_ass") as mock_gen, \
         patch("shorts_engine.services.storage.path_manager.PathManager.get_subtitle_path", return_value=sub_path):
        res = caption_stage(ctx_dict)
        assert res["clip_id"] == "clip_2"
        assert mock_gen.called
        called_words = mock_gen.call_args[1]["words"]
        assert all(isinstance(w, WordSegment) for w in called_words)
        assert called_words[1].word == "Queue"
        assert called_words[1].end == 2.0


def test_render_stage_supports_both_cropplan_and_dict_shapes(tmp_path):
    dummy_video = tmp_path / "dummy.mp4"
    dummy_video.write_bytes(b"dummy")

    # Shape 1: Live CropPlan instance (monolith path)
    live_plan = CropPlan(crop_w=405, crop_h=720, crop_x=100, crop_y=0)
    ctx_live = StageContext(
        db_session=MagicMock(),
        video_id="test_vid_1",
        clip_id="clip_1",
        artifacts={
            "reframe": {"crop_plan": live_plan},
            "ingest": {"video_path": str(dummy_video)},
        },
    )

    with patch("shorts_engine.services.rendering.compositor.VideoCompositor.render_short", return_value={"output_path": "out.mp4"}) as mock_render, \
         patch("shorts_engine.services.storage.path_manager.PathManager.get_subtitle_path", return_value=tmp_path / "sub.ass"):
        res = render_stage(ctx_live)
        assert mock_render.called
        plan_used = mock_render.call_args[1]["crop_plan"]
        assert isinstance(plan_used, CropPlan)
        assert plan_used.crop_w == 405

    # Shape 2: Dictionary (queue / disk path)
    dict_plan = {
        "crop_w": 405,
        "crop_h": 720,
        "crop_x": 200,
        "crop_y": 0,
        "mode": "tracking",
        "is_multi_speaker": False,
        "speaker_count": 1,
        "layout_mode": "single",
        "split_crops": [],
        "divider_width": 4,
    }
    ctx_dict = StageContext(
        db_session=MagicMock(),
        video_id="test_vid_2",
        clip_id="clip_2",
        artifacts={
            "reframe": {"crop_plan": dict_plan},
            "ingest": {"video_path": str(dummy_video)},
        },
    )

    with patch("shorts_engine.services.rendering.compositor.VideoCompositor.render_short", return_value={"output_path": "out.mp4"}) as mock_render, \
         patch("shorts_engine.services.storage.path_manager.PathManager.get_subtitle_path", return_value=tmp_path / "sub.ass"):
        res = render_stage(ctx_dict)
        assert mock_render.called
        plan_used = mock_render.call_args[1]["crop_plan"]
        assert isinstance(plan_used, CropPlan)
        assert plan_used.crop_x == 200
