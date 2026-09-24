import os
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from shorts_engine.services.quality_gate.recovery import ClipRecoveryService, TrimmerProtocol
from shorts_engine.db.models import Clip, ClipCandidate, SourceVideo
from shorts_engine.services.execution.watchdog import ExecutionResult


def test_recovery_service_decoupled_from_rendering():
    """Verify that recovery.py does not import VideoCompositor from rendering.compositor."""
    import shorts_engine.services.quality_gate.recovery as rec_mod

    # Verify VideoCompositor is not in recovery module namespace
    assert "VideoCompositor" not in rec_mod.__dict__


def test_caption_safe_rerender_delegates_to_trimmer_protocol(tmp_path):
    """Verify that caption_safe_rerender calls trimmer_service.trim_clip without TypeError."""
    mock_trimmer = MagicMock()
    mock_trimmer.trim_clip.return_value = {"success": True, "rendered_path": str(tmp_path / "new.mp4")}

    recovery = ClipRecoveryService(trimmer_service=mock_trimmer)

    existing_rendered = tmp_path / "old.mp4"
    existing_rendered.write_text("dummy")

    clip = MagicMock(spec=Clip)
    clip.id = "test-clip-123"
    clip.rendered_path = str(existing_rendered)
    clip.caption_style = "hormozi"
    clip.candidate = MagicMock(spec=ClipCandidate)
    clip.candidate.start_ms = 1000
    clip.candidate.end_ms = 15000
    clip.candidate.source_video = MagicMock(spec=SourceVideo)

    db = MagicMock()

    with patch.object(recovery, "re_evaluate_clip", return_value=(MagicMock(), {})):
        res = recovery.apply_auto_fix(clip, "caption_safe_rerender", db)

    assert res["status"] == "success"
    mock_trimmer.trim_clip.assert_called_once_with(
        clip_id="test-clip-123",
        start_sec=1.0,
        end_sec=15.0,
        snap_words=False,
        caption_style_preset="hormozi",
    )


def test_trimmer_unlinks_old_render_on_path_change(tmp_path):
    """Verify that ClipTrimmerService unlinks old rendered file if the new rendered path differs."""
    from shorts_engine.services.rendering.trimmer import ClipTrimmerService

    old_file = tmp_path / "old_clip.mp4"
    old_file.write_text("old video content")

    new_file = tmp_path / "new_clip.mp4"
    new_file.write_text("new video content")

    db = MagicMock()
    clip = MagicMock(spec=Clip)
    clip.id = "clip-123"
    clip.rendered_path = str(old_file)
    clip.caption_style = None

    cand = MagicMock(spec=ClipCandidate)
    cand.start_ms = 0
    cand.end_ms = 20000
    cand.hook_text = "Amazing test"
    cand.variations = None
    clip.candidate = cand

    source_vid = MagicMock(spec=SourceVideo)
    source_vid.downloaded_path = str(tmp_path / "source.mp4")
    source_vid.youtube_video_id = "src123"
    source_vid.title = "Source Title"
    source_vid.channel = None
    source_vid.transcript = None
    cand.source_video = source_vid

    db.query.return_value.filter_by.return_value.first.return_value = clip

    trimmer = ClipTrimmerService(db)

    # Mock PathManager and rendering steps
    with patch("shorts_engine.services.rendering.trimmer.PathManager.resolve_download_path", return_value=tmp_path / "source.mp4"), \
         patch("shorts_engine.services.rendering.trimmer.PathManager.get_subtitle_path", return_value=tmp_path / "sub.ass"), \
         patch("shorts_engine.services.rendering.trimmer.PathManager.get_clip_output_path", return_value=tmp_path / "output.mp4"), \
         patch.object(Path, "exists", return_value=True), \
         patch.object(trimmer.ass_generator, "generate_ass"), \
         patch.object(trimmer.reframer, "compute_crop_plan", return_value=MagicMock()), \
         patch.object(trimmer.compositor, "render_short", return_value=new_file), \
         patch.object(trimmer.compositor, "validate_render", return_value=MagicMock(is_valid=True)), \
         patch("shorts_engine.services.rendering.trimmer.TechnicalVerifier.verify", return_value=(True, {})):

        res = trimmer.trim_clip("clip-123", start_sec=0.0, end_sec=20.0, snap_words=False)

    assert res["success"] is True
    # The old file must have been unlinked
    assert not old_file.exists()
    assert new_file.exists()
