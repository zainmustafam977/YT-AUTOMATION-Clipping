"""
Phase 3 Enterprise Enhancements Test Suite:
1. Automated A/B Title & Hook Variations (CTR Optimizer)
2. AI Auto-Reframer with Multi-Speaker Tracking & Clustering
3. Opening Visual Hook Overlay (0-3s retention booster) & Re-rendering
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from shorts_engine.db.session import Base
from shorts_engine.db.models import Clip, ClipStatus, ClipCandidate, SourceVideo, SourceVideoStatus, Channel, User, UserRole
from shorts_engine.dashboard.app import create_dashboard_app
from shorts_engine.dashboard.routes import queue as queue_route
from shorts_engine.dashboard.auth import require_operator, require_viewer, verify_csrf
from shorts_engine.services.clip_selection.prompts import TitleHookVariation, CandidateSegmentSchema
from shorts_engine.services.reframing.cropper import VideoReframer, CropPlan
from shorts_engine.services.rendering.compositor import VideoCompositor, get_default_fontfile
from shorts_engine.dashboard.routes.queue import generate_deterministic_variations


@pytest.fixture
def test_setup():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    def override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app = create_dashboard_app()
    app.dependency_overrides[queue_route.get_db] = override_get_db

    fake_user = User(username="test_op", role=UserRole.OPERATOR, is_active=True)
    app.dependency_overrides[require_operator] = lambda: fake_user
    app.dependency_overrides[require_viewer] = lambda: fake_user
    app.dependency_overrides[verify_csrf] = lambda: True

    client = TestClient(app)
    db = TestSession()

    return {"client": client, "db": db}


# ==========================================
# 1. Automated A/B Title & Hook Variations
# ==========================================

def test_title_hook_variation_schema():
    var = TitleHookVariation(
        title="The Secret to Viral Shorts",
        angle="Curiosity Gap",
        hook_overlay="Wait For It..."
    )
    assert var.title == "The Secret to Viral Shorts"
    assert var.angle == "Curiosity Gap"
    assert var.hook_overlay == "Wait For It..."

    cand = CandidateSegmentSchema(
        start_sec=1.0,
        end_sec=15.0,
        hook_text="Did you know this?",
        payoff_text="That's why it works.",
        score=0.85,
        reason="Punchy opening",
        suggested_title="Title",
        suggested_description="Description #shorts",
        suggested_tags=["#shorts"],
        variations=[var]
    )
    assert len(cand.variations) == 1
    assert cand.variations[0].angle == "Curiosity Gap"


def test_generate_deterministic_variations():
    vars_list = generate_deterministic_variations("You won't believe how this algorithm works", "Algorithm Secrets")
    assert len(vars_list) == 3
    angles = [v["angle"] for v in vars_list]
    assert "Curiosity Gap" in angles
    assert "Contrarian" in angles
    assert "High Stakes" in angles
    assert all("title" in v and "hook_overlay" in v for v in vars_list)


def test_select_variation_endpoint(test_setup):
    client = test_setup["client"]
    db = test_setup["db"]

    ch = Channel(id="ch_p3_var", youtube_channel_id="UC_p3_var", name="P3 Var Channel")
    db.add(ch)
    sv = SourceVideo(id="sv_p3_var", youtube_video_id="yt_p3_var", channel_id=ch.id, title="Test Source Video", status=SourceVideoStatus.DONE)
    db.add(sv)

    cand = ClipCandidate(id="cand_p3_var", source_video_id=sv.id, start_ms=0, end_ms=10000, hook_text="Hook", payoff_text="Payoff", score=0.88, reason="Strong hook")
    db.add(cand)
    clip = Clip(id="clip_p3_var", clip_candidate_id=cand.id, status=ClipStatus.WAITING_APPROVAL, generated_title="Initial Title")
    db.add(clip)
    db.commit()


    resp = client.post(
        f"/queue/{clip.id}/select-variation",
        data={
            "title": "Why Everyone Is Wrong About Algorithms",
            "angle": "Contrarian",
            "hook_overlay": "Stop Doing This!",
        },
        headers={"HX-Request": "true"}
    )
    assert resp.status_code == 200

    db.refresh(clip)
    assert clip.generated_title == "Why Everyone Is Wrong About Algorithms"
    qc = json.loads(clip.quality_check_result)
    assert "selected_variation" in qc
    assert qc["selected_variation"]["selected_angle"] == "Contrarian"
    assert qc["selected_variation"]["selected_hook_overlay"] == "Stop Doing This!"


# ==========================================
# 2. Multi-Speaker Detection & Reframing
# ==========================================

def test_crop_plan_multi_speaker_attributes():
    plan = CropPlan(
        crop_w=608,
        crop_h=1080,
        crop_x=120,
        crop_y=0,
        mode="multi_speaker_group",
        is_multi_speaker=True,
        speaker_count=2,
    )
    assert plan.is_multi_speaker is True
    assert plan.speaker_count == 2
    assert plan.mode == "multi_speaker_group"


def test_reframer_multi_speaker_clustering(tmp_path):
    reframer = VideoReframer()

    # Mock cv2.VideoCapture to return valid 1920x1080 frames
    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = True
    mock_cap.get.side_effect = lambda prop: {3: 1920, 4: 1080, 5: 30.0}.get(prop, 0)
    mock_cap.read.return_value = (True, MagicMock())

    # Two speakers separated by > 18% of 1920 (1920 * 0.18 = 345px): 400 and 1100
    face_a = MagicMock(center_x=400.0)
    face_b = MagicMock(center_x=1100.0)

    with patch("cv2.VideoCapture", return_value=mock_cap), \
         patch.object(reframer.detector, "detect_faces", return_value=[face_a, face_b]), \
         patch.object(reframer.speaker_selector, "select_speaker", return_value=face_a):
        
        plan = reframer.compute_crop_plan(
            video_path=tmp_path / "dummy.mp4",
            start_ms=0,
            end_ms=5000,
        )

        assert plan.is_multi_speaker is True
        assert plan.speaker_count >= 2
        assert plan.mode in ("multi_speaker_group", "multi_speaker_tracking")


# ==========================================
# 3. Visual Hook Overlay & VideoCompositor
# ==========================================

def test_default_fontfile_locator():
    font = get_default_fontfile()
    # On Windows or Linux, if system font exists, it returns an escaped path
    if Path("C:/Windows/Fonts/arialbd.ttf").exists() or Path("C:/Windows/Fonts/arial.ttf").exists():
        assert font is not None
        assert "arial" in font.lower()


def test_compositor_hook_overlay_filter(tmp_path):
    compositor = VideoCompositor(output_dir=tmp_path)

    plan = CropPlan(
        crop_w=608,
        crop_h=1080,
        crop_x=656,
        crop_y=0,
    )

    dummy_source = tmp_path / "source.mp4"
    dummy_source.write_bytes(b"dummy")

    with patch("subprocess.run") as mock_run, \
         patch.object(compositor, "validate_render", return_value=MagicMock(is_valid=False)):
        
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        out = compositor.render_short(
            source_video_path=dummy_source,
            start_ms=0,
            end_ms=5000,
            crop_plan=plan,
            subtitle_ass_path=None,
            output_filename="test_hook.mp4",
            hook_overlay_text="Don't Miss This Viral Moment!",
        )

        # Verify that subprocess was called with drawtext in the video filter
        assert mock_run.called
        cmd_args = mock_run.call_args[0][0]
        assert "-vf" in cmd_args
        vf_idx = cmd_args.index("-vf")
        vf_str = cmd_args[vf_idx + 1]
        assert "drawtext=" in vf_str
        assert "between(t,0,3)" in vf_str
        assert "expansion=none" in vf_str
