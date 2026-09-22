"""
Comprehensive unit and integration tests for Phase 3 Studio-Grade Architecture:
- Hierarchical Storage & PathManager (MAX_PATH budgeting, slugification, safe_unlink, two-tier fallback resolution)
- Subtitle Style Template Registry & Bidirectional Hex/ASS Color Converters
- Universal Soft Deletion (preserving files vs disk purging across queue, dead-letter, and channels)
- Pre-flight YouTube Channel Auto-Discovery & Registration
- Non-destructive 1-Click Caption Re-rendering Endpoint
- Runtime Settings Hot-Reload & HTMX Interactivity
"""

import json
import uuid
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from starlette.testclient import TestClient
from sqlalchemy.orm import Session

from shorts_engine.config.settings import settings
from shorts_engine.services.storage.path_manager import slugify, PathManager
from shorts_engine.services.captioning.templates import (
    CaptionStyleTemplate,
    BUILTIN_TEMPLATES,
    hex_to_ass_color,
    ass_to_hex_color,
)
from shorts_engine.dashboard.app import create_dashboard_app
from shorts_engine.dashboard.auth import create_session_token, generate_csrf_token, SESSION_COOKIE_NAME
from shorts_engine.db.session import SessionLocal
from shorts_engine.db.models import (
    User,
    UserRole,
    Channel,
    SourceVideo,
    SourceVideoStatus,
    ClipCandidate,
    Clip,
    ClipStatus,
)


@pytest.fixture
def auth_client():
    db = SessionLocal()
    db.rollback()
    app = create_dashboard_app()
    client = TestClient(app)

    user = db.query(User).filter_by(username="test_operator_phase3").first()
    if not user:
        user = User(
            username="test_operator_phase3",
            password_hash="dummy_hash",
            role=UserRole.OPERATOR,
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    token = create_session_token(user.id)
    client.cookies.set(SESSION_COOKIE_NAME, token)
    yield client, db, user

    # Teardown
    try:
        db.rollback()
        db.query(User).filter_by(username="test_operator_phase3").delete()
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 1. PathManager & Hierarchical Storage Tests
# ---------------------------------------------------------------------------

def test_path_manager_slugify_and_max_path_budgeting():
    raw_title = 'How To Build AI Robots: The Ultimate 2026 Guide? <Part 1> "Must Watch" | 100%'
    slug = slugify(raw_title, max_length=35)
    
    assert len(slug) <= 35
    for bad_char in [':', '*', '?', '"', '<', '>', '|', '/', '\\']:
        assert bad_char not in slug
    assert slug.startswith("how_to_build_ai_robots")


def test_path_manager_hierarchical_directories(tmp_path):
    with patch.object(settings, "STORAGE_DIR", tmp_path), patch.object(settings, "OUTPUT_DIR", tmp_path / "outputs"), patch.object(settings, "TEMP_DIR", tmp_path / "temp"):
        dl_dir = PathManager.get_download_dir("Tech Channel", "UC123", "My Great Video", "vid123")
        assert dl_dir.exists()
        assert "downloads" in str(dl_dir)
        assert "tech_channel" in str(dl_dir)

        out_dir = PathManager.get_output_dir("Tech Channel", "UC123", "My Great Video", "vid123")
        assert out_dir.exists()
        assert "outputs" in str(out_dir)

        temp_dir = PathManager.get_temp_dir("vid123")
        assert temp_dir.exists()
        assert "temp" in str(temp_dir)


def test_path_manager_two_tier_download_fallback(tmp_path):
    with patch.object(settings, "STORAGE_DIR", tmp_path):
        flat_dir = tmp_path / "downloads"
        flat_dir.mkdir(parents=True, exist_ok=True)
        legacy_file = flat_dir / "vid_legacy.mp4"
        legacy_file.write_text("dummy video")

        mock_video_legacy = MagicMock(downloaded_path=None, youtube_video_id="vid_legacy")
        resolved = PathManager.resolve_download_path(mock_video_legacy)
        assert resolved == legacy_file
        assert resolved.exists()

        hier_dir = PathManager.get_download_dir("New Channel", "UC999", "New Video", "vid_new", base_dir=flat_dir)
        hier_file = hier_dir / "new_video_vid_new.mp4"
        hier_file.write_text("hierarchical video")

        mock_video_hier = MagicMock(downloaded_path=None, youtube_video_id="vid_new")
        resolved_hier = PathManager.resolve_download_path(mock_video_hier)
        assert resolved_hier == hier_file
        assert resolved_hier.exists()


def test_path_manager_safe_unlink(tmp_path):
    test_file = tmp_path / "to_delete.txt"
    test_file.write_text("delete me")
    assert test_file.exists()

    success = PathManager.safe_unlink(test_file)
    assert success is True
    assert not test_file.exists()
    assert PathManager.safe_unlink(test_file) is True


# ---------------------------------------------------------------------------
# 2. Subtitle Style Template Registry & Color Conversion Tests
# ---------------------------------------------------------------------------

def test_hex_to_ass_color_conversion():
    assert hex_to_ass_color("#FFFFFF") == "&H00FFFFFF"
    assert hex_to_ass_color("#ffffff") == "&H00FFFFFF"
    assert hex_to_ass_color("#FFFF00") == "&H0000FFFF"
    assert hex_to_ass_color("#00FFFF") == "&H00FFFF00"
    assert hex_to_ass_color("#80FF0000") == "&H800000FF"


def test_ass_to_hex_color_conversion():
    assert ass_to_hex_color("&H00FFFFFF") == "#ffffff"
    assert ass_to_hex_color("&H0000FFFF") == "#ffff00"
    assert ass_to_hex_color("&H00FFFF00") == "#00ffff"


def test_builtin_caption_templates_catalog():
    expected_presets = ["hormozi", "mrbeast", "neon", "minimalist", "high_contrast_box"]
    for preset_id in expected_presets:
        assert preset_id in BUILTIN_TEMPLATES
        tmpl = BUILTIN_TEMPLATES[preset_id]
        overrides = tmpl.to_ass_overrides()
        assert "font_name" in overrides
        assert "font_size" in overrides
        assert "primary_color" in overrides
        assert "highlight_color" in overrides

        ui_dict = tmpl.to_ui_dict()
        assert ui_dict["primary_hex"].startswith("#")
        assert ui_dict["highlight_hex"].startswith("#")


# ---------------------------------------------------------------------------
# 3. Universal Soft Deletion Tests
# ---------------------------------------------------------------------------

def test_soft_delete_clip_preserves_disk_file(auth_client, tmp_path):
    client, db, user = auth_client
    uid = uuid.uuid4().hex[:8]

    clip_file = tmp_path / f"clip_preserve_{uid}.mp4"
    clip_file.write_text("rendered clip contents")

    ch = Channel(name=f"Channel Pres {uid}", youtube_channel_id=f"UC_PRES_{uid}")
    db.add(ch)
    db.commit()

    sv = SourceVideo(youtube_video_id=f"yt_pr_{uid}", channel_id=ch.id, title="Test Pres Video", status=SourceVideoStatus.DONE)
    db.add(sv)
    db.commit()

    cand = ClipCandidate(source_video_id=sv.id, start_ms=0, end_ms=15000, score=85, reason="Test preserve")
    db.add(cand)
    db.commit()

    clip = Clip(clip_candidate_id=cand.id, rendered_path=str(clip_file), status=ClipStatus.FAILED)
    db.add(clip)
    db.commit()
    clip_id = clip.id

    csrf_token = generate_csrf_token(client.cookies.get(SESSION_COOKIE_NAME))

    # Soft Delete: delete_files = False
    resp = client.post(
        f"/dead-letter/clip/{clip_id}/delete",
        data={"delete_files": "false", "csrf_token": csrf_token},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200

    deleted_clip = db.query(Clip).filter_by(id=clip_id).first()
    assert deleted_clip is None
    assert clip_file.exists()

    # Cleanup
    try:
        db.delete(sv)
        db.delete(ch)
        db.commit()
    except Exception:
        db.rollback()
    clip_file.unlink(missing_ok=True)


def test_hard_delete_clip_purges_disk_file(auth_client, tmp_path):
    client, db, user = auth_client
    uid = uuid.uuid4().hex[:8]

    clip_file = tmp_path / f"clip_purge_{uid}.mp4"
    clip_file.write_text("rendered clip contents to purge")

    ch = Channel(name=f"Channel Purge {uid}", youtube_channel_id=f"UC_PUR_{uid}")
    db.add(ch)
    db.commit()

    sv = SourceVideo(youtube_video_id=f"yt_pu_{uid}", channel_id=ch.id, title="Test Purge Video", status=SourceVideoStatus.DONE)
    db.add(sv)
    db.commit()

    cand = ClipCandidate(source_video_id=sv.id, start_ms=0, end_ms=15000, score=85, reason="Test purge")
    db.add(cand)
    db.commit()

    clip = Clip(clip_candidate_id=cand.id, rendered_path=str(clip_file), status=ClipStatus.FAILED)
    db.add(clip)
    db.commit()
    clip_id = clip.id

    csrf_token = generate_csrf_token(client.cookies.get(SESSION_COOKIE_NAME))

    # Hard Delete: delete_files = True
    resp = client.post(
        f"/dead-letter/clip/{clip_id}/delete",
        data={"delete_files": "true", "csrf_token": csrf_token},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200

    deleted_clip = db.query(Clip).filter_by(id=clip_id).first()
    assert deleted_clip is None
    assert not clip_file.exists()

    # Cleanup
    try:
        db.delete(sv)
        db.delete(ch)
        db.commit()
    except Exception:
        db.rollback()


# ---------------------------------------------------------------------------
# 4. Channel Discovery & Interactive Registration Tests
# ---------------------------------------------------------------------------

def test_preflight_channel_discovery_registration(auth_client):
    client, db, user = auth_client
    csrf_token = generate_csrf_token(client.cookies.get(SESSION_COOKIE_NAME))
    uid = uuid.uuid4().hex[:8]
    fresh_yt_id = f"yt_{uid}".ljust(11, "0")[:11]

    channel_name = f"Discovered Channel {uid}"
    channel_handle = f"@Discovered_{uid}"

    mock_oembed_data = {
        "title": "Awesome New Tech Video",
        "author_name": channel_name,
        "author_url": f"https://www.youtube.com/{channel_handle}",
    }

    with patch("shorts_engine.dashboard.routes.status.httpx.Client") as mock_client_cls:
        mock_instance = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_instance
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = mock_oembed_data
        mock_instance.get.return_value = mock_resp

        resp = client.get(f"/api/video-status/{fresh_yt_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_valid"] is True
        assert data["channel"]["detected_name"] == channel_name
        assert data["channel"]["detected_id"] == channel_handle
        assert data["channel"]["exists"] is False

        # Ingest with register_channel = true
        with patch("shorts_engine.dashboard.routes.status.threading.Thread"):
            ingest_resp = client.post(
                "/process-video",
                data={
                    "video_input": fresh_yt_id,
                    "force": "false",
                    "register_channel": "true",
                    "detected_channel_name": channel_name,
                    "detected_channel_id": channel_handle,
                    "csrf_token": csrf_token,
                },
                headers={"HX-Request": "true"},
            )
            assert ingest_resp.status_code == 200

            # Verify channel registered in DB
            created_channel = db.query(Channel).filter_by(youtube_channel_id=channel_handle).first()
            assert created_channel is not None
            assert created_channel.name == channel_name

            # Cleanup
            try:
                db.delete(created_channel)
                db.commit()
            except Exception:
                db.rollback()


# ---------------------------------------------------------------------------
# 5. Non-Destructive Subtitle Re-rendering Tests
# ---------------------------------------------------------------------------

def test_rerender_clip_style_hot_swap(auth_client, tmp_path):
    client, db, user = auth_client
    uid = uuid.uuid4().hex[:8]

    src_file = tmp_path / f"src_{uid}.mp4"
    src_file.write_text("source video dummy")

    ch = Channel(name=f"Rerender Channel {uid}", youtube_channel_id=f"UC_RER_{uid}")
    db.add(ch)
    db.commit()

    sv = SourceVideo(
        youtube_video_id=f"yt_re_{uid}",
        channel_id=ch.id,
        title="Rerender Source",
        downloaded_path=str(src_file),
        status=SourceVideoStatus.DONE,
    )
    db.add(sv)
    db.commit()

    cand = ClipCandidate(
        source_video_id=sv.id,
        start_ms=0,
        end_ms=10000,
        score=90,
        reason="Test rerender reason",
        hook_text="Hook",
        payoff_text="Payoff",
    )
    db.add(cand)
    db.commit()

    original_clip_file = tmp_path / f"original_clip_{uid}.mp4"
    original_clip_file.write_text("original video")

    clip = Clip(clip_candidate_id=cand.id, rendered_path=str(original_clip_file), status=ClipStatus.APPROVED)
    db.add(clip)
    db.commit()

    csrf_token = generate_csrf_token(client.cookies.get(SESSION_COOKIE_NAME))

    with patch("shorts_engine.services.rendering.compositor.VideoCompositor.render_short") as mock_render, \
         patch("shorts_engine.services.reframing.cropper.VideoReframer.compute_crop_plan") as mock_crop, \
         patch("shorts_engine.services.captioning.ass_generator.ASSSubtitleGenerator.generate_ass"):
        mock_render.return_value = original_clip_file
        mock_crop.return_value = []

        resp = client.post(
            f"/queue/{clip.id}/re-render-style",
            data={"template_id": "mrbeast", "csrf_token": csrf_token},
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "re_rendered"
        assert data["style"] == "MrBeast Impact"

    # Cleanup
    try:
        db.delete(clip)
        db.delete(cand)
        db.delete(sv)
        db.delete(ch)
        db.commit()
    except Exception:
        db.rollback()
    original_clip_file.unlink(missing_ok=True)
    src_file.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 6. Settings Hot-Reload & HTMX Interactivity Tests
# ---------------------------------------------------------------------------

def test_settings_update_and_hot_reload(auth_client):
    client, db, user = auth_client
    csrf_token = generate_csrf_token(client.cookies.get(SESSION_COOKIE_NAME))

    post_data = {
        "csrf_token": csrf_token,
        "target_duration_min_sec": 15,
        "target_duration_max_sec": 55,
        "min_segments_per_video": 2,
        "max_segments_per_video": 6,
        "caption_margin_vertical": 400,
        "max_retry_count": 4,
        "quota_warning_threshold": 0.85,
        "llm_model_name": "gemini-3.8-flash",
        "gemini_pacing_sec": 3.5,
        "llm_provider": "auto",
        "video_scale_algorithm": "lanczos",
        "video_encoding_crf": 18,
        "video_encoding_preset": "slow",
        "video_target_fps": 30,
        "audio_bitrate": "320k",
        "audio_sample_rate": 48000,
        "default_caption_preset": "mrbeast",
        "whisper_beam_size": 5,
        "whisper_temperature": 0.0,
        "face_tracking_confidence": 0.65,
        "llm_temperature": 0.25,
        "default_privacy_status": "unlisted",
    }

    resp = client.post(
        "/settings",
        data=post_data,
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    assert "Settings saved" in resp.text
    assert "showToast" in resp.headers.get("HX-Trigger", "")
