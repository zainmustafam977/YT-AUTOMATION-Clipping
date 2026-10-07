"""
Phase 3 Verification Test Suite:
Studio Captioning, Watermarking & Visual Compositor.

Covers:
- CaptionPreset ORM model and ASS color conversion utilities.
- Caption template resolution, aliases ('cyberpunk' -> 'neon', 'high_contrast' -> 'high_contrast_box'),
  and DB preset CRUD operations.
- ASS subtitle generation with Segoe UI Emoji font fallback, active-word scale-pop animation,
  and background box styles.
- Dual-engine watermark matrix with 9-point grid anchor coordinate calculation.
- VideoCompositor single-pass rendering with PNG watermark, text handle, dynamic hook banner,
  footer CTA overlay, and cache invalidation.
- Dashboard REST endpoints (/api/caption-presets and /queue/{clip_id}/re-render-style).
"""

import json
import subprocess
import pytest
from pathlib import Path
from PIL import Image

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from shorts_engine.db.session import Base
from shorts_engine.db.models import (
    CaptionPreset,
    Channel,
    SourceVideo,
    Transcript,
    ClipCandidate,
    Clip,
    User,
    UserRole,
    SourceVideoStatus,
    ClipStatus,
)
from shorts_engine.services.captioning.templates import (
    hex_to_ass_color,
    ass_to_hex_color,
    get_caption_template,
    list_caption_templates,
    parse_custom_template,
    save_preset_to_db,
    delete_preset_from_db,
)
from shorts_engine.services.captioning.ass_generator import ASSSubtitleGenerator
from shorts_engine.services.transcription.formatter import WordSegment
from shorts_engine.services.rendering.compositor import (
    VideoCompositor,
    compute_watermark_position,
)
from shorts_engine.services.reframing.cropper import CropPlan
from shorts_engine.dashboard.app import create_dashboard_app
from shorts_engine.dashboard.routes import queue as queue_route
from shorts_engine.dashboard.auth import require_operator, require_viewer, verify_csrf


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
def client(db_session):
    app = create_dashboard_app()
    app.dependency_overrides[queue_route.get_db] = lambda: db_session
    fake_user = User(username="test_studio_op", role=UserRole.OPERATOR, is_active=True)
    app.dependency_overrides[require_operator] = lambda: fake_user
    app.dependency_overrides[require_viewer] = lambda: fake_user
    app.dependency_overrides[verify_csrf] = lambda: True
    return TestClient(app)


# =========================================================================
# 1. CaptionPreset ORM Model & Color Conversions
# =========================================================================

def test_caption_preset_model_and_conversions():
    preset = CaptionPreset(
        name="Test Cyberpunk",
        description="Vibrant neon styling",
        font_family="Impact",
        font_size=62,
        primary_color="&H00FFFFFF",
        highlight_color="&H0000FF7F",
        outline_color="&H00800080",
        outline_width=4.5,
        shadow_depth=3.0,
        shadow_color="&H80000000",
        background_box=True,
        box_color="&H80000000",
        margin_vertical=400,
        max_words_per_line=3,
        case_transform="uppercase",
        scale_pop=True,
        active_scale=120,
        is_builtin=False,
    )

    d = preset.to_dict()
    assert d["name"] == "Test Cyberpunk"
    assert d["font_family"] == "Impact"
    assert d["font_size"] == 62
    assert d["background_box"] is True
    assert d["scale_pop"] is True
    assert d["active_scale"] == 120
    assert d["primary_hex"] == "#ffffff"

    overrides = preset.to_ass_overrides()
    assert overrides["font_name"] == "Impact"
    assert overrides["font_size"] == 62
    assert overrides["primary_color"] == "&H00FFFFFF"
    assert overrides["background_box"] is True
    assert overrides["scale_pop"] is True
    assert overrides["active_scale"] == 120


def test_hex_and_ass_color_conversion_edge_cases():
    # 6-char hex
    assert hex_to_ass_color("#ffffff") == "&H00FFFFFF"
    assert hex_to_ass_color("#ff0000") == "&H000000FF"  # Red in ASS is BBGGRR -> 0000FF
    assert hex_to_ass_color("#00ff00") == "&H0000FF00"  # Green
    assert hex_to_ass_color("#0000ff") == "&H00FF0000"  # Blue

    # 3-char hex shorthand
    assert hex_to_ass_color("#fff") == "&H00FFFFFF"
    assert hex_to_ass_color("#f00") == "&H000000FF"

    # Already ASS format
    assert hex_to_ass_color("&H0000FFFF") == "&H0000FFFF"

    # ass_to_hex_color
    assert ass_to_hex_color("&H00FFFFFF") == "#ffffff"
    assert ass_to_hex_color("&H000000FF") == "#ff0000"


# =========================================================================
# 2. Template Engine & Preset CRUD
# =========================================================================

def test_get_caption_template_resolution_and_aliases():
    # Default fallback
    tmpl_default = get_caption_template(None)
    assert tmpl_default.template_id == "hormozi"

    # Builtin IDs
    assert get_caption_template("hormozi").template_id == "hormozi"
    assert get_caption_template("mrbeast").template_id == "mrbeast"
    assert get_caption_template("neon").template_id == "neon"
    assert get_caption_template("minimalist").template_id == "minimalist"
    assert get_caption_template("high_contrast_box").template_id == "high_contrast_box"

    # Aliases
    assert get_caption_template("cyberpunk").template_id == "neon"
    assert get_caption_template("high_contrast").template_id == "high_contrast_box"


def test_caption_preset_db_lifecycle(db_session):
    # 1. Save new preset
    payload = {
        "name": "Custom Gold Studio",
        "description": "Luxurious gold highlight with drop shadow",
        "font_family": "Trebuchet MS",
        "font_size": 52,
        "primary_hex": "#F5F5F5",
        "highlight_hex": "#FFD700",
        "outline_hex": "#000000",
        "outline_width": 3.5,
        "shadow_depth": 2.0,
        "background_box": False,
        "margin_vertical": 380,
        "max_words_per_line": 4,
        "case_transform": "uppercase",
        "scale_pop": True,
        "active_scale": 118,
    }
    saved = save_preset_to_db(db_session, payload)
    assert saved.id is not None
    assert saved.name == "Custom Gold Studio"

    # 2. List templates (includes built-in + custom)
    all_tmpls = list_caption_templates(db=db_session)
    preset_names = [t["name"] for t in all_tmpls]
    assert "Alex Hormozi Viral" in preset_names
    assert "Custom Gold Studio" in preset_names

    # 3. Lookup by name or ID in get_caption_template
    resolved = get_caption_template("Custom Gold Studio", db=db_session)
    assert resolved.template_id == saved.id
    assert resolved.font_name == "Trebuchet MS"

    resolved_by_id = get_caption_template(saved.id, db=db_session)
    assert resolved_by_id.template_id == saved.id

    # 4. Update preset
    update_payload = {
        "id": saved.id,
        "name": "Custom Gold Studio",
        "font_size": 58,
    }
    updated = save_preset_to_db(db_session, update_payload)
    assert updated.font_size == 58

    # 5. Delete preset
    deleted = delete_preset_from_db(db_session, saved.id)
    assert deleted is True

    # 6. Builtin cannot be deleted
    assert delete_preset_from_db(db_session, "non_existent") is False

    # 7. Saving without name raises ValueError
    with pytest.raises(ValueError, match="Caption preset name is required"):
        save_preset_to_db(db_session, {"name": "   "})


def test_parse_custom_template():
    custom = parse_custom_template({
        "font_name": "Montserrat",
        "font_size": 50,
        "primary_hex": "#ffffff",
        "highlight_hex": "#00ffff",
        "background_box": True,
        "scale_pop": False,
    })
    assert custom.font_name == "Montserrat"
    assert custom.font_size == 50
    assert custom.background_box is True
    assert custom.scale_pop is False


# =========================================================================
# 3. ASS Subtitle Generation (Emojis, Scale-Pop, Background Box)
# =========================================================================

def test_ass_generator_emoji_font_fallback(tmp_path):
    words = [
        WordSegment("WAIT", 1.0, 1.4),
        WordSegment("FOR", 1.5, 1.8),
        WordSegment("IT! 😱 🔥", 1.9, 2.5),
    ]

    generator = ASSSubtitleGenerator(font_name="Arial Black")
    out_file = tmp_path / "emoji_subs.ass"
    generator.generate_ass(
        words,
        segment_start_ms=1000,
        segment_end_ms=2500,
        output_path=out_file,
    )

    assert out_file.exists()
    content = out_file.read_text(encoding="utf-8")

    # Segoe UI Emoji tag must wrap emoji characters and revert to Arial Black
    assert "{\\fnSegoe UI Emoji}😱{\\fnArial Black}" in content
    assert "{\\fnSegoe UI Emoji}🔥{\\fnArial Black}" in content
    # Normal text should not be wrapped in Segoe UI Emoji
    assert "WAIT" in content


def test_ass_generator_scale_pop_animation(tmp_path):
    words = [
        WordSegment("INCREDIBLE", 0.0, 0.5),
        WordSegment("RESULTS", 0.6, 1.0),
    ]

    gen = ASSSubtitleGenerator(scale_pop=True, active_scale=125)
    out_file = tmp_path / "scale_pop.ass"
    gen.generate_ass(
        words,
        segment_start_ms=0,
        segment_end_ms=1000,
        output_path=out_file,
    )

    content = out_file.read_text(encoding="utf-8")
    # Active scale tags present
    assert "\\fscx125\\fscy125" in content
    assert "\\fscx100\\fscy100" in content

    # Test scale_pop=False
    out_file_nopop = tmp_path / "no_pop.ass"
    gen.generate_ass(
        words,
        segment_start_ms=0,
        segment_end_ms=1000,
        output_path=out_file_nopop,
        style_overrides={"scale_pop": False},
    )
    content_nopop = out_file_nopop.read_text(encoding="utf-8")
    assert "\\fscx" not in content_nopop


def test_ass_generator_background_box(tmp_path):
    words = [WordSegment("TESTING", 0.0, 0.5)]
    gen = ASSSubtitleGenerator()
    out_box = tmp_path / "box.ass"
    gen.generate_ass(
        words,
        segment_start_ms=0,
        segment_end_ms=1000,
        output_path=out_box,
        style_overrides={"background_box": True, "box_color": "&H90000000"},
    )
    content = out_box.read_text(encoding="utf-8")
    # BorderStyle 3 indicates opaque backing box in ASS V4+ Styles definition
    assert ",3," in content
    assert "&H90000000" in content


def test_ass_generator_case_transforms(tmp_path):
    words = [WordSegment("mIxEd CaSe", 0.0, 0.5)]
    gen = ASSSubtitleGenerator()

    out_upper = tmp_path / "upper.ass"
    gen.generate_ass(words, 0, 1000, out_upper, style_overrides={"case_transform": "uppercase", "scale_pop": False})
    assert "MIXED CASE" in out_upper.read_text(encoding="utf-8")

    out_lower = tmp_path / "lower.ass"
    gen.generate_ass(words, 0, 1000, out_lower, style_overrides={"case_transform": "lowercase", "scale_pop": False})
    assert "mixed case" in out_lower.read_text(encoding="utf-8")

    out_cap = tmp_path / "cap.ass"
    gen.generate_ass(words, 0, 1000, out_cap, style_overrides={"case_transform": "capitalize", "scale_pop": False})
    assert "Mixed case" in out_cap.read_text(encoding="utf-8")


# =========================================================================
# 4. Watermark 9-Point Grid Anchor Coordinates
# =========================================================================

def test_compute_watermark_position_all_anchors():
    # Overlay coordinates (using W, H, w, h)
    assert compute_watermark_position("top_left", 30, 40, is_drawtext=False) == ("30", "40")
    assert compute_watermark_position("tl", 30, 40, is_drawtext=False) == ("30", "40")
    assert compute_watermark_position("top_center", 30, 40, is_drawtext=False) == ("(W-w)/2", "40")
    assert compute_watermark_position("top_right", 30, 40, is_drawtext=False) == ("W-w-30", "40")
    assert compute_watermark_position("center_left", 30, 40, is_drawtext=False) == ("30", "(H-h)/2")
    assert compute_watermark_position("center", 30, 40, is_drawtext=False) == ("(W-w)/2", "(H-h)/2")
    assert compute_watermark_position("middle", 30, 40, is_drawtext=False) == ("(W-w)/2", "(H-h)/2")
    assert compute_watermark_position("center_right", 30, 40, is_drawtext=False) == ("W-w-30", "(H-h)/2")
    assert compute_watermark_position("bottom_left", 30, 40, is_drawtext=False) == ("30", "H-h-40")
    assert compute_watermark_position("bottom_center", 30, 40, is_drawtext=False) == ("(W-w)/2", "H-h-40")
    assert compute_watermark_position("bottom_right", 30, 40, is_drawtext=False) == ("W-w-30", "H-h-40")

    # Drawtext coordinates (using w, h, text_w, text_h)
    assert compute_watermark_position("top_left", 20, 25, is_drawtext=True) == ("20", "25")
    assert compute_watermark_position("top_center", 20, 25, is_drawtext=True) == ("(w-text_w)/2", "25")
    assert compute_watermark_position("top_right", 20, 25, is_drawtext=True) == ("w-text_w-20", "25")
    assert compute_watermark_position("center_left", 20, 25, is_drawtext=True) == ("20", "(h-text_h)/2")
    assert compute_watermark_position("center", 20, 25, is_drawtext=True) == ("(w-text_w)/2", "(h-text_h)/2")
    assert compute_watermark_position("bottom_right", 20, 25, is_drawtext=True) == ("w-text_w-20", "h-text_h-25")


# =========================================================================
# 5. VideoCompositor & Multi-Input Single-Pass Rendering
# =========================================================================

def _generate_synthetic_video(target_path: Path, duration_sec: float = 2.0):
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000",
        "-t", f"{duration_sec:.2f}",
        "-c:v", "libx264", "-c:a", "aac",
        str(target_path)
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def test_render_short_watermark_hook_and_cta_full_pipeline(tmp_path):
    # 1. Create synthetic video
    source_video = tmp_path / "source.mp4"
    _generate_synthetic_video(source_video, duration_sec=2.5)

    # 2. Create small PNG watermark
    wm_png = tmp_path / "watermark.png"
    img = Image.new("RGBA", (120, 120), (255, 0, 0, 180))
    img.save(wm_png)

    # 3. Create ASS subtitles with emoji
    ass_path = tmp_path / "subs.ass"
    words = [
        WordSegment("VIRAL", 0.1, 0.6),
        WordSegment("MOMENT 🚀", 0.7, 1.8),
    ]
    gen = ASSSubtitleGenerator()
    gen.generate_ass(words, segment_start_ms=0, segment_end_ms=2500, output_path=ass_path)

    # 4. Render Short with dual watermark + hook banner + footer CTA
    compositor = VideoCompositor(output_dir=tmp_path / "rendered")
    crop_plan = CropPlan(crop_w=607, crop_h=1080, crop_x=656, crop_y=0)

    rendered = compositor.render_short(
        source_video_path=source_video,
        start_ms=0,
        end_ms=2500,
        crop_plan=crop_plan,
        subtitle_ass_path=ass_path,
        output_filename="composed_short.mp4",
        normalize_audio=True,
        watermark_image_path=wm_png,
        watermark_text="@clippingbot",
        watermark_anchor="bottom_right",
        watermark_fade="fade_in_out",
        hook_overlay_text="WAIT FOR IT 😱",
        hook_duration_sec=1.5,
        cta_overlay_text="SUBSCRIBE NOW!",
        cta_duration_sec=1.0,
        force_re_render=True,
    )

    assert rendered.exists()
    assert rendered.stat().st_size > 1000

    val = compositor.validate_render(rendered)
    assert val.is_valid is True
    assert val.width == 1080
    assert val.height == 1920
    assert val.has_audio is True
    assert 2.0 <= val.duration_sec <= 3.0


def test_render_short_force_re_render_cache_invalidation(tmp_path):
    source_video = tmp_path / "source2.mp4"
    _generate_synthetic_video(source_video, duration_sec=1.5)

    compositor = VideoCompositor(output_dir=tmp_path / "out_cache")
    crop_plan = CropPlan(crop_w=607, crop_h=1080, crop_x=656, crop_y=0)
    out_name = "cached_test.mp4"

    # First render
    first_path = compositor.render_short(
        source_video_path=source_video,
        start_ms=0,
        end_ms=1500,
        crop_plan=crop_plan,
        output_filename=out_name,
        force_re_render=False,
    )
    first_mtime = first_path.stat().st_mtime_ns

    # Second render without force_re_render -> returns existing cached file
    cached_path = compositor.render_short(
        source_video_path=source_video,
        start_ms=0,
        end_ms=1500,
        crop_plan=crop_plan,
        output_filename=out_name,
        force_re_render=False,
    )
    assert cached_path.stat().st_mtime_ns == first_mtime

    # Third render with force_re_render=True -> regenerates file
    force_path = compositor.render_short(
        source_video_path=source_video,
        start_ms=0,
        end_ms=1500,
        crop_plan=crop_plan,
        output_filename=out_name,
        force_re_render=True,
    )
    assert force_path.exists()


# =========================================================================
# 6. Dashboard REST API Endpoints
# =========================================================================

def test_caption_presets_api_endpoints(client, db_session):
    # 1. GET presets
    resp = client.get("/api/caption-presets")
    assert resp.status_code == 200
    presets = resp.json()
    assert any(p["template_id"] == "hormozi" for p in presets)
    assert any(p["template_id"] == "neon" for p in presets)

    # 2. POST create preset
    create_payload = {
        "name": "API Test Preset",
        "description": "Created via API",
        "font_family": "Arial Black",
        "font_size": 54,
        "primary_hex": "#00FF00",
        "highlight_hex": "#FF00FF",
        "outline_width": 4.0,
        "background_box": True,
        "scale_pop": True,
        "active_scale": 120,
    }
    resp_create = client.post("/api/caption-presets", json=create_payload)
    assert resp_create.status_code == 200
    created_data = resp_create.json()["preset"]
    preset_id = created_data["id"]
    assert created_data["name"] == "API Test Preset"
    assert created_data["background_box"] is True

    # 3. GET verify created preset appears
    resp_list = client.get("/api/caption-presets")
    assert any(p["name"] == "API Test Preset" for p in resp_list.json())

    # 4. DELETE preset
    resp_del = client.delete(f"/api/caption-presets/{preset_id}")
    assert resp_del.status_code == 200
    assert resp_del.json()["status"] == "deleted"

    # 5. DELETE non-existent / builtin returns 404
    resp_del_fail = client.delete("/api/caption-presets/hormozi")
    assert resp_del_fail.status_code == 404


def test_re_render_clip_style_endpoint_success_and_cache_invalidation(client, db_session, tmp_path):
    # Setup database entities
    channel = Channel(id="ch_p3", youtube_channel_id="UC_p3", name="P3 Channel")
    db_session.add(channel)

    source_path = tmp_path / "raw_src.mp4"
    _generate_synthetic_video(source_path, duration_sec=2.0)

    video = SourceVideo(
        id="vid_p3",
        channel_id=channel.id,
        youtube_video_id="yt_p3",
        title="P3 Source Video",
        status=SourceVideoStatus.DONE,
        downloaded_path=str(source_path),
    )
    db_session.add(video)

    words_data = [
        {"word": "HELLO", "start": 0.1, "end": 0.5},
        {"word": "WORLD", "start": 0.6, "end": 1.2},
    ]

    transcript = Transcript(
        id="trans_p3",
        source_video_id=video.id,
        word_level_json=json.dumps(words_data),
        language="en",
    )
    db_session.add(transcript)

    candidate = ClipCandidate(
        id="cand_p3",
        source_video_id=video.id,
        start_ms=0,
        end_ms=2000,
        score=85.0,
        reason="Viral segment",
        hook_text="Hello World Hook",
    )
    db_session.add(candidate)

    clip = Clip(
        id="clip_p3",
        clip_candidate_id=candidate.id,
        status=ClipStatus.WAITING_APPROVAL,
        caption_style="hormozi",
        rendered_path=str(tmp_path / "old_clip.mp4"),
    )
    db_session.add(clip)
    db_session.commit()

    # Pre-create old clip on disk
    old_clip_file = tmp_path / "old_clip.mp4"
    old_clip_file.write_bytes(b"old_rendered_content")

    # Call re-render endpoint
    resp = client.post(
        f"/queue/{clip.id}/re-render-style",
        data={
            "template_id": "neon",
            "force_re_render": "true",
            "hook_overlay_text": "HOOK OVERLAY 😱",
            "cta_overlay_text": "CTA FOOTER",
            "watermark_text": "@testbrand",
            "watermark_anchor": "top_right",
        },
        headers={"Accept": "application/json"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "re_rendered"
    assert data["style"] == "Cyberpunk Neon"
    assert "render_time_sec" in data
    assert Path(data["rendered_path"]).exists()
    assert Path(data["rendered_path"]).stat().st_size > 1000

    # Verify clip in DB has been updated
    db_session.refresh(clip)
    assert "neon" in clip.caption_style or "Cyberpunk" in clip.caption_style


# =========================================================================
# 7. Robustness, Edge Cases & Verification Tests
# =========================================================================

def test_queue_html_script_bracket_balance_and_syntax():
    """Verifies that all script tags in queue.html have balanced braces and valid syntax."""
    import re
    template_path = Path("shorts_engine/dashboard/templates/queue.html")
    assert template_path.exists()
    content = template_path.read_text(encoding="utf-8")

    scripts = re.findall(r"<script>(.*?)</script>", content, re.DOTALL)
    assert len(scripts) >= 2
    for idx, s in enumerate(scripts):
        opens = s.count("{")
        closes = s.count("}")
        assert opens == closes, f"Script tag #{idx} in queue.html has unbalanced braces: {opens} vs {closes}"

    assert "btn-open-style" in content
    assert "data-caption-style" in content
    assert "openStyleModal" in content
    assert "captionStyleModal" in content


def test_queue_page_renders_with_caption_templates(client, db_session):
    """Verifies that /queue loads successfully and renders caption_templates in the DOM."""
    # Seed a custom preset
    save_preset_to_db(db_session, {
        "name": "Custom Emerald Pro",
        "font_family": "Segoe UI",
        "primary_hex": "#00FF7F",
        "highlight_hex": "#FFFF00",
    })

    resp = client.get("/queue")
    assert resp.status_code == 200
    html = resp.text
    assert "Alex Hormozi Viral" in html
    assert "Custom Emerald Pro" in html
    assert "btn-open-style" in html


def test_ass_generator_flags_variation_selectors_and_zwj(tmp_path):
    """Verifies that country flags, emoji variation selectors, and symbols are not split or corrupted."""
    words = [
        WordSegment("WARNING", 0.0, 0.4),
        WordSegment("⚠️", 0.5, 0.9),       # Variation selector 16 (\u26A0\uFE0F)
        WordSegment("LOVE", 1.0, 1.4),
        WordSegment("❤️", 1.5, 1.9),       # Variation selector 16 (\u2764\uFE0F)
        WordSegment("USA", 2.0, 2.4),
        WordSegment("🇺🇸", 2.5, 2.9),       # Flag pair (\U0001F1FA\U0001F1F8)
    ]
    gen = ASSSubtitleGenerator(font_name="Arial Black")
    out_file = tmp_path / "advanced_emojis.ass"
    gen.generate_ass(words, 0, 3000, out_file)
    content = out_file.read_text(encoding="utf-8")

    # Full emoji sequences must be enclosed inside emoji font tags
    assert "{\\fnSegoe UI Emoji}⚠️{\\fnArial Black}" in content
    assert "{\\fnSegoe UI Emoji}❤️{\\fnArial Black}" in content
    assert "{\\fnSegoe UI Emoji}🇺🇸{\\fnArial Black}" in content


def test_ass_generator_sanitizes_transcript_braces(tmp_path):
    """Verifies that transcript words with curly braces are sanitized and not swallowed as ASS tags."""
    words = [
        WordSegment("{laughter}", 0.0, 0.5),
        WordSegment("{applause}", 0.6, 1.0),
    ]
    gen = ASSSubtitleGenerator()
    out_file = tmp_path / "sanitized_braces.ass"
    gen.generate_ass(words, 0, 1000, out_file)
    content = out_file.read_text(encoding="utf-8")

    # Braces sanitized to parens
    assert "(laughter)" in content
    assert "(applause)" in content
    assert "{laughter}" not in content


def test_ass_generator_gap_bridging_prevents_flicker(tmp_path):
    """Verifies that minor pauses (<=400ms) between words in the same line are bridged to prevent black flicker."""
    words = [
        WordSegment("Word1", 0.0, 0.3),
        WordSegment("Word2", 0.5, 0.8),  # 200ms gap between Word1 and Word2
    ]
    gen = ASSSubtitleGenerator()
    out_file = tmp_path / "gap_bridging.ass"
    gen.generate_ass(words, 0, 1000, out_file)
    content = out_file.read_text(encoding="utf-8")

    # Dialogue line 1 should extend to 0.50 (start of Word2) instead of ending abruptly at 0.30
    assert "Dialogue: 0,0:00:00.00,0:00:00.50" in content


def test_color_conversion_lowercase_and_preset_model_template_id():
    """Verifies lowercase &h parsing, CaptionPreset template_id, and reserved preset name rejection."""
    assert ass_to_hex_color("&h000000ff") == "#ff0000"
    assert ass_to_hex_color("&h0000ffff") == "#ffff00"
    assert ass_to_hex_color("&H00FFFFFF") == "#ffffff"
    assert ass_to_hex_color("") == "#ffffff"

    preset = CaptionPreset(name="Unique Name", font_family="Arial")
    d = preset.to_dict()
    assert "template_id" in d
    assert d["template_id"] == preset.id


def test_save_preset_rejects_reserved_names(db_session):
    """Verifies save_preset_to_db raises ValueError when attempting to overwrite built-in templates."""
    with pytest.raises(ValueError, match="reserved built-in preset identifier"):
        save_preset_to_db(db_session, {"name": "hormozi"})
    with pytest.raises(ValueError, match="reserved built-in preset identifier"):
        save_preset_to_db(db_session, {"name": "cyberpunk"})


def test_compute_watermark_position_directional_aliases():
    """Verifies directional aliases 'top', 'bottom', 'left', 'right' and integer margin handling."""
    assert compute_watermark_position("top", 30, 40) == ("(W-w)/2", "40")
    assert compute_watermark_position("bottom", 30, 40) == ("(W-w)/2", "H-h-40")
    assert compute_watermark_position("left", 30, 40) == ("30", "(H-h)/2")
    assert compute_watermark_position("right", 30, 40) == ("W-w-30", "(H-h)/2")


def test_re_render_clip_style_with_parameter_overrides(client, db_session, tmp_path):
    """Verifies re-rendering applies explicit parameter overrides on top of a selected preset."""
    channel = Channel(id="ch_p3_ov", youtube_channel_id="UC_p3_ov", name="P3 Overrides Channel")
    db_session.add(channel)

    source_path = tmp_path / "raw_src_ov.mp4"
    _generate_synthetic_video(source_path, duration_sec=2.0)

    video = SourceVideo(
        id="vid_p3_ov",
        channel_id=channel.id,
        youtube_video_id="yt_p3_ov",
        title="P3 Source Overrides",
        status=SourceVideoStatus.DONE,
        downloaded_path=str(source_path),
    )
    db_session.add(video)

    words_data = [
        {"word": "TESTING", "start": 0.1, "end": 0.5},
        {"word": "OVERRIDES", "start": 0.6, "end": 1.2},
    ]

    transcript = Transcript(
        id="trans_p3_ov",
        source_video_id=video.id,
        word_level_json=json.dumps(words_data),
        language="en",
    )
    db_session.add(transcript)

    candidate = ClipCandidate(
        id="cand_p3_ov",
        source_video_id=video.id,
        start_ms=0,
        end_ms=2000,
        score=90.0,
        reason="Viral segment",
        hook_text="Hook Text",
    )
    db_session.add(candidate)

    clip = Clip(
        id="clip_p3_ov",
        clip_candidate_id=candidate.id,
        status=ClipStatus.WAITING_APPROVAL,
        caption_style="hormozi",
        rendered_path=str(tmp_path / "old_clip_ov.mp4"),
    )
    db_session.add(clip)
    db_session.commit()

    # Post re-render selecting preset 'hormozi' with explicit margin_vertical=370 override
    resp = client.post(
        f"/queue/{clip.id}/re-render-style",
        data={
            "template_id": "hormozi",
            "force_re_render": "true",
            "margin_vertical": 370,
            "words_per_line": 2,
            "scale_pop": "false",
        },
        headers={"Accept": "application/json"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "re_rendered"

    # Verify generated ASS has overridden margin
    from shorts_engine.services.storage.path_manager import PathManager
    ass_path = PathManager.get_subtitle_path(video.youtube_video_id, candidate.start_ms, candidate.end_ms)
    assert ass_path.exists()
    content = ass_path.read_text(encoding="utf-8")
    assert ",370," in content  # margin_vertical=370 applied in Style and Dialogue
    assert "\\fscx" not in content  # scale_pop=False applied

