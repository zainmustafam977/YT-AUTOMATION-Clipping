"""
Comprehensive Test Suite for Package 3: Pipeline AI & Media Enhancements.
Tests:
1. Smart Speaker Reframing & Multi-Speaker Stacked / Split-Screen Layouts.
2. Dynamic Animated Captions (Bouncy Kinetic Pop-In, Karaoke Sweep, and Viral Presets).
3. Autonomous A/B Variant Suite Generation (3 Diverse Thumbnails, Titles, and YouTube Promotion).
"""

import os
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from PIL import Image

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shorts_engine.db.session import Base
from shorts_engine.db.models import (
    Channel,
    SourceVideo,
    ClipCandidate,
    Clip,
    ClipStatus,
    PublishLog,
)
from shorts_engine.services.reframing.cropper import CropPlan, VideoReframer
from shorts_engine.services.reframing.detector import DetectedFace
from shorts_engine.services.rendering.compositor import VideoCompositor
from shorts_engine.services.captioning.templates import (
    get_caption_template,
    CaptionStyleTemplate,
    BUILTIN_TEMPLATES,
    TEMPLATE_ALIASES,
)
from shorts_engine.services.captioning.ass_generator import ASSSubtitleGenerator
from shorts_engine.services.transcription.formatter import WordSegment
from shorts_engine.services.ab_testing.thumbnail_generator import ThumbnailVariantGenerator
from shorts_engine.services.ab_testing.coordinator import ABVariantCoordinator
from shorts_engine.services.ab_testing.bandit import BayesianVariantOptimizer, VariantEvaluationResult
from shorts_engine.services.ab_testing.winner_daemon import evaluate_and_promote_clip
from shorts_engine.services.publishing.client import YouTubePublisher


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
def sample_video(tmp_path):
    """Creates a small synthetic MP4 video for thumbnail and reframing testing."""
    import subprocess
    vid_path = tmp_path / "test_sample.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=24",
        "-t", "2.0",
        "-c:v", "libx264",
        str(vid_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return vid_path


# =========================================================================
# 1. Smart Speaker Reframing & Multi-Speaker Layouts
# =========================================================================

def test_crop_plan_single_vs_split_screen():
    single_plan = CropPlan(
        crop_w=607,
        crop_h=1080,
        crop_x=500,
        crop_y=0,
        mode="tracking",
        layout_mode="single",
    )
    assert not single_plan.is_split_screen
    filter_single = single_plan.to_ffmpeg_filter()
    assert "crop=607:1080:500:0" in filter_single
    assert "scale=1080:1920" in filter_single
    assert "vstack" not in filter_single

    split_plan = CropPlan(
        crop_w=607,
        crop_h=1080,
        crop_x=500,
        crop_y=0,
        mode="split_screen",
        layout_mode="split_screen",
        split_crops=[
            {"crop_w": 1214, "crop_h": 1080, "crop_x": 0, "crop_y": 0},
            {"crop_w": 1214, "crop_h": 1080, "crop_x": 700, "crop_y": 0},
        ],
        divider_width=4,
    )
    assert split_plan.is_split_screen
    filter_split = split_plan.to_ffmpeg_filter()
    assert "split [top_in][bot_in]" in filter_split
    assert "scale=1080:960" in filter_split
    assert "vstack" in filter_split
    assert "drawbox=y=958:h=4" in filter_split
    assert "setsar=1" in filter_split


def test_video_reframer_auto_split_screen_on_wide_speakers(tmp_path):
    reframer = VideoReframer()

    # Mock detector returning two far-apart speakers (x=200 and x=1600 on a 1920 frame)
    face_left = DetectedFace(x=200, y=200, w=150, h=150, score=0.9)
    face_right = DetectedFace(x=1600, y=200, w=150, h=150, score=0.9)

    with patch.object(reframer.detector, "detect_faces", return_value=[face_left, face_right]):
        dummy_vid = tmp_path / "dummy.mp4"
        dummy_vid.write_bytes(b"0" * 100)

        # Mock cv2.VideoCapture to simulate 1920x1080 frame
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        mock_cap.get.side_effect = lambda prop: 1920.0 if prop == 3 else (1080.0 if prop == 4 else 0.0)
        mock_cap.read.return_value = (True, MagicMock())

        with patch("cv2.VideoCapture", return_value=mock_cap):
            plan = reframer.compute_crop_plan(
                dummy_vid,
                start_ms=0,
                end_ms=5000,
                layout_preference="auto",
            )
            assert plan.is_split_screen
            assert plan.layout_mode == "split_screen"
            assert len(plan.split_crops) == 2
            # Top crop should be around left speaker (x <= 200), bottom around right speaker (x >= 800)
            assert plan.split_crops[0]["crop_x"] <= 300
            assert plan.split_crops[1]["crop_x"] >= 500


def test_compositor_handles_split_screen_filter():
    compositor = VideoCompositor(output_dir=Path("./storage/test_out"))
    split_plan = CropPlan(
        crop_w=607,
        crop_h=1080,
        crop_x=500,
        crop_y=0,
        mode="split_screen",
        layout_mode="split_screen",
        split_crops=[
            {"crop_w": 1214, "crop_h": 1080, "crop_x": 0, "crop_y": 0},
            {"crop_w": 1214, "crop_h": 1080, "crop_x": 700, "crop_y": 0},
        ],
    )
    filter_str, is_complex_split = compositor.build_aspect_ratio_filter(
        source_path=Path("dummy.mp4"),
        crop_plan=split_plan,
        aspect_ratio_mode="16:9",
    )
    assert is_complex_split is True
    assert "split [top_in][bot_in]" in filter_str
    assert "vstack" in filter_str


# =========================================================================
# 2. Dynamic Animated Captions & Typography Presets
# =========================================================================

def test_new_caption_presets_and_aliases():
    # Built-in presets exist
    assert "hormozi_bounce" in BUILTIN_TEMPLATES
    assert "neon_punch" in BUILTIN_TEMPLATES
    assert "karaoke_sweep" in BUILTIN_TEMPLATES
    assert "minimal_clean" in BUILTIN_TEMPLATES

    # Preset properties
    hb = BUILTIN_TEMPLATES["hormozi_bounce"]
    assert hb.animation_style == "bounce"
    assert hb.scale_pop is True
    assert hb.active_scale == 125

    ks = BUILTIN_TEMPLATES["karaoke_sweep"]
    assert ks.animation_style == "karaoke"

    # Alias resolution
    assert get_caption_template("hormozi_animated").template_id == "hormozi_bounce"
    assert get_caption_template("minimal").template_id == "minimal_clean"
    assert get_caption_template("karaoke").template_id == "karaoke_sweep"

    # Conversion includes new fields
    overrides = hb.to_ass_overrides()
    assert overrides["animation_style"] == "bounce"
    ui_dict = hb.to_ui_dict()
    assert ui_dict["animation_style"] == "bounce"


def test_ass_generator_bouncy_kinetic_animation(tmp_path):
    words = [
        WordSegment("VIRAL", 1.0, 1.4),
        WordSegment("MOMENT", 1.45, 1.8),
    ]
    gen = ASSSubtitleGenerator(
        animation_style="bounce",
        scale_pop=True,
        active_scale=125,
    )
    out_file = tmp_path / "bounce.ass"
    gen.generate_ass(words, segment_start_ms=1000, segment_end_ms=2000, output_path=out_file)

    assert out_file.exists()
    content = out_file.read_text(encoding="utf-8")
    assert "\\t(0," in content
    assert "\\fscx125\\fscy125" in content
    assert "\\fscx105\\fscy105" in content


def test_ass_generator_karaoke_sweep_animation(tmp_path):
    words = [
        WordSegment("KARAOKE", 1.0, 1.5),
        WordSegment("SWEEP", 1.55, 2.0),
    ]
    gen = ASSSubtitleGenerator(animation_style="karaoke")
    out_file = tmp_path / "karaoke.ass"
    gen.generate_ass(words, segment_start_ms=1000, segment_end_ms=2000, output_path=out_file)

    assert out_file.exists()
    content = out_file.read_text(encoding="utf-8")
    assert "\\kf" in content


def test_ass_generator_secondary_highlight_alternation(tmp_path):
    words = [
        WordSegment("WORD1", 1.0, 1.3),
        WordSegment("WORD2", 1.35, 1.6),
    ]
    gen = ASSSubtitleGenerator(
        primary_color="&H00FFFFFF",
        highlight_color="&H0000FFFF",           # Yellow
        secondary_highlight_color="&H0000FF7F", # Green
    )
    out_file = tmp_path / "alt_colors.ass"
    gen.generate_ass(words, segment_start_ms=1000, segment_end_ms=2000, output_path=out_file)

    content = out_file.read_text(encoding="utf-8")
    assert "&H0000FFFF&" in content  # Yellow for active word 0
    assert "&H0000FF7F&" in content  # Green for active word 1


# =========================================================================
# 3. Autonomous A/B Variant Generation & Analytics Integration
# =========================================================================

def test_thumbnail_variant_generator_creates_3_diverse_covers(sample_video, tmp_path):
    gen = ThumbnailVariantGenerator()
    out_dir = tmp_path / "thumbs"
    variants = gen.generate_3_variants(
        video_path=sample_video,
        output_dir=out_dir,
        hook_text="HOW TO GO VIRAL FAST",
        duration_sec=2.0,
    )
    assert len(variants) == 3
    assert variants[0]["variant_id"] == "var_a"
    assert variants[1]["variant_id"] == "var_b"
    assert variants[2]["variant_id"] == "var_c"

    for v in variants:
        p = Path(v["thumbnail_path"])
        assert p.exists()
        assert p.stat().st_size > 0
        with Image.open(p) as img:
            assert img.size == (1080, 1920)


def test_ab_variant_coordinator_pairs_titles_and_thumbnails(sample_video, db_session, tmp_path):
    # Setup test clip
    ch = Channel(name="Test Channel", youtube_channel_id="UC123")
    db_session.add(ch)
    db_session.flush()

    sv = SourceVideo(channel_id=ch.id, youtube_video_id="vid_123", title="Master Video")
    db_session.add(sv)
    db_session.flush()

    cand = ClipCandidate(source_video_id=sv.id, start_ms=0, end_ms=2000, score=9.0, reason="Top hook", hook_text="INSANE LIFE HACK")
    db_session.add(cand)
    db_session.flush()

    clip = Clip(clip_candidate_id=cand.id, status=ClipStatus.RENDERING)
    db_session.add(clip)
    db_session.commit()

    coord = ABVariantCoordinator()
    out_dir = tmp_path / "ab_suite"

    variants = coord.generate_clip_variants(
        clip=clip,
        video_path=sample_video,
        output_dir=out_dir,
        duration_sec=2.0,
        db_session=db_session,
    )

    assert len(variants) == 3
    assert variants[0]["is_active"] is True
    assert variants[1]["is_active"] is False
    assert variants[2]["is_active"] is False

    # Check persistence in clip.variations
    db_session.refresh(clip)
    assert len(clip.variations) == 3
    assert clip.generated_title == variants[0]["title"]
    assert Path(variants[0]["thumbnail_path"]).exists()
    assert Path(variants[1]["thumbnail_path"]).exists()
    assert Path(variants[2]["thumbnail_path"]).exists()


def test_youtube_publisher_update_video_thumbnail_dry_run(tmp_path):
    pub = YouTubePublisher(db_session=MagicMock())
    dummy_thumb = tmp_path / "thumb.jpg"
    dummy_thumb.write_bytes(b"dummy")

    res = pub.update_video_thumbnail(
        youtube_video_id="yt_test_123",
        thumbnail_path=str(dummy_thumb),
        dry_run=True,
    )
    assert res["success"] is True
    assert res["dry_run"] is True
    assert res["video_id"] == "yt_test_123"


def test_winner_daemon_promotes_both_title_and_thumbnail(db_session, tmp_path):
    ch = Channel(name="Test Channel", youtube_channel_id="UC456")
    db_session.add(ch)
    db_session.flush()

    sv = SourceVideo(channel_id=ch.id, youtube_video_id="src_456", title="Test Source")
    db_session.add(sv)
    db_session.flush()

    cand = ClipCandidate(source_video_id=sv.id, start_ms=0, end_ms=2000, score=8.5, reason="Hook", hook_text="Hook")
    db_session.add(cand)
    db_session.flush()

    thumb_a = tmp_path / "thumb_a.jpg"
    thumb_b = tmp_path / "thumb_b.jpg"
    thumb_a.write_bytes(b"thumb_a")
    thumb_b.write_bytes(b"thumb_b")

    clip = Clip(
        clip_candidate_id=cand.id,
        status=ClipStatus.PUBLISHED,
        generated_title="Baseline Title",
    )
    clip.variations = [
        {"variant_id": "var_a", "title": "Baseline Title", "thumbnail_path": str(thumb_a), "clicks": 60, "impressions": 1200, "is_active": True},
        {"variant_id": "var_b", "title": "Winning High-CTR Title", "thumbnail_path": str(thumb_b), "clicks": 540, "impressions": 1200, "is_active": False},
    ]
    clip.analytics = {"baseline_velocity": 10.0, "current_velocity": 45.0}
    db_session.add(clip)
    db_session.flush()

    from datetime import datetime, timedelta
    pub_log = PublishLog(
        clip_id=clip.id,
        youtube_video_id="yt_win_789",
        status="PUBLISHED",
        title="Baseline Title",
        description="Test Description",
        published_at=datetime.utcnow() - timedelta(days=4),
    )
    db_session.add(pub_log)
    db_session.commit()

    mock_pub = MagicMock(spec=YouTubePublisher)
    mock_pub.update_video_title.return_value = {"success": True}
    mock_pub.update_video_thumbnail.return_value = {"success": True}

    # Evaluate clip
    eval_res = evaluate_and_promote_clip(
        db=db_session,
        clip=clip,
        publisher=mock_pub,
    )

    assert eval_res is not None
    assert eval_res.action == "PROMOTE"
    assert eval_res.winning_variant_index == 1

    # Verify both title and thumbnail were updated on YouTube
    mock_pub.update_video_title.assert_called_once()
    mock_pub.update_video_thumbnail.assert_called_once()
    call_args = mock_pub.update_video_thumbnail.call_args[1]
    assert call_args["youtube_video_id"] == "yt_win_789"
    assert call_args["thumbnail_path"] == str(thumb_b)

    # Verify DB state
    db_session.refresh(clip)
    assert clip.variations[1]["is_active"] is True
    assert clip.variations[0]["is_active"] is False
    assert clip.generated_title == "Winning High-CTR Title"
    assert clip.analytics["last_promotion"]["thumbnail_path"] == str(thumb_b)
