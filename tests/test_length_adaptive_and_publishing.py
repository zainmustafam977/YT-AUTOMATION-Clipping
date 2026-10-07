"""
Tests for Length-Adaptive Dynamic Clip Estimation, AI Model Sizing,
Channel Setup & Upload Defaults, and Autonomous SEO Upload Packaging.
"""

from shorts_engine.services.clip_selection.estimator import (
    estimate_optimal_clip_count,
    select_optimal_model_for_duration,
)
from shorts_engine.services.seo.engine import SEOMetadataEngine
from shorts_engine.db.models import Clip, ClipCandidate, Channel, SourceVideo


def test_estimate_optimal_clip_count_four_minutes():
    """Verify that a 4-minute (240s) video calculates 3-4 clips with quality flexibility."""
    res = estimate_optimal_clip_count(240.0, target_duration_min=30.0, target_duration_max=60.0)
    assert res["duration_sec"] == 240.0
    assert 2 <= res["min_clips"] <= 3
    assert 3 <= res["max_clips"] <= 5
    assert 3 <= res["estimated_clips"] <= 4
    assert res["recommended_model"] == "groq"
    assert "llama-3.3-70b-versatile" in res["model_name"]
    assert "pacing" in res
    assert "explanation" in res


def test_estimate_optimal_clip_count_under_minute():
    """Verify that a short video (<45s) returns 1 clip."""
    res = estimate_optimal_clip_count(35.0)
    assert res["estimated_clips"] == 1
    assert res["min_clips"] == 1
    assert res["max_clips"] == 1
    assert res["recommended_model"] == "groq"


def test_estimate_optimal_clip_count_twenty_minutes():
    """Verify that a 20-minute video scales clip limit gracefully up to 10-15."""
    res = estimate_optimal_clip_count(1200.0)
    assert res["estimated_clips"] >= 8
    assert res["max_clips"] >= 10
    assert res["recommended_model"] in ("gemini", "groq")


def test_select_optimal_model_for_duration():
    """Verify model routing tiers based on video duration."""
    # <5 minutes -> Groq LPU
    m1, name1, tier1 = select_optimal_model_for_duration(200.0)
    assert m1 == "groq"
    assert "llama" in name1

    # 5 - 20 minutes -> Gemini 2.0 Flash
    m2, name2, tier2 = select_optimal_model_for_duration(600.0)
    assert m2 == "gemini"
    assert "flash" in name2

    # >20 minutes -> Gemini 1.5 Pro
    m3, name3, tier3 = select_optimal_model_for_duration(1500.0)
    assert m3 == "gemini"
    assert "pro" in name3


def test_channel_upload_defaults_serialization():
    """Verify Channel upload_defaults JSON serialization & fallback."""
    ch = Channel(
        id="test-ch-1",
        name="Tech Shorts",
        youtube_channel_id="UC1234567890123456789012",
        watch_enabled=True,
        human_review_required=True,
    )
    # Default when upload_defaults_json is None
    defs = ch.upload_defaults
    assert defs["privacy_status"] == "unlisted"
    assert defs["category_id"] == "22"
    assert defs["daily_upload_quota"] == 4
    assert defs["made_for_kids"] is False

    # Custom update
    ch.upload_defaults = {
        "privacy_status": "public",
        "category_id": "24",
        "made_for_kids": False,
        "default_tags": ["#Entertainment", "#Shorts"],
        "daily_upload_quota": 6,
        "preferred_publish_hour_utc": 19,
        "auto_publish": True,
    }
    assert ch.upload_defaults["privacy_status"] == "public"
    assert ch.upload_defaults["category_id"] == "24"
    assert ch.upload_defaults["daily_upload_quota"] == 6
    assert ch.upload_defaults["preferred_publish_hour_utc"] == 19
    assert ch.upload_defaults["auto_publish"] is True


def test_generate_complete_upload_package():
    """Verify SEOMetadataEngine synthesizes a complete YouTube upload package."""
    engine = SEOMetadataEngine(router=None)  # Use deterministic fallback

    cand = ClipCandidate(
        id="cand-100",
        source_video_id="vid-100",
        hook_text="This secret hack changed everything for millionaires",
        start_ms=10000,
        end_ms=55000,
        score=9.5,
        reason="High curiosity hook",
    )
    clip = Clip(
        id="clip-abc12345",
        clip_candidate_id="cand-100",
        candidate=cand,
        generated_title="Default Title #Shorts",
    )
    ch = Channel(
        id="ch-tech",
        name="Tech Channel",
        youtube_channel_id="UC0000000000000000000000",
        upload_defaults_json='{"category_id": "28", "privacy_status": "public", "preferred_publish_hour_utc": 17, "made_for_kids": false}',
    )

    pkg = engine.generate_complete_upload_package(clip=clip, channel=ch)

    assert pkg["clip_id"] == "clip-abc12345"
    assert "curiosity_gap" in pkg["titles"]
    assert "shock_factor" in pkg["titles"]
    assert "question" in pkg["titles"]
    for t in pkg["titles"].values():
        assert len(t) <= 100
        assert "#Shorts" in t

    # Description must contain [c:<clip_id>] token
    assert "[c:clip-abc12345]" in pkg["description"]
    assert len(pkg["tags"]) >= 10
    assert pkg["category_id"] == "28"
    assert pkg["made_for_kids"] is False
    assert pkg["privacy_status"] == "public"
    assert "recommended_publish_at" in pkg
    assert "recommended_publish_at_local" in pkg
    assert "pinned_comment" in pkg
    assert "hook_quote" in pkg


def test_drip_feed_preserves_seo_and_channel_defaults():
    """Verify build_clip_metadata in drip_feed preserves rich generated SEO and channel defaults."""
    from shorts_engine.services.publishing.drip_feed import build_clip_metadata

    ch = Channel(
        id="ch-gaming",
        name="Gaming Zone",
        youtube_channel_id="UC1234567890123456789012",
        upload_defaults_json='{"category_id": "20", "privacy_status": "unlisted", "made_for_kids": false, "default_language": "en", "license": "youtube"}',
    )
    sv = SourceVideo(
        id="sv-gaming",
        youtube_video_id="gaming123",
        title="Epic Gaming Moments",
        channel_id=ch.id,
        channel=ch,
    )
    cand = ClipCandidate(
        id="cand-game",
        source_video_id=sv.id,
        source_video=sv,
        hook_text="Insane clutch moment you will not believe",
        start_ms=5000,
        end_ms=45000,
        score=9.8,
    )
    clip = Clip(
        id="clip-game999",
        clip_candidate_id=cand.id,
        candidate=cand,
        generated_title="INSANE 1v5 CLUTCH! 😱 #Shorts",
        generated_description="Watch how this clutch was pulled off!\n\nCredit: Gaming Zone\n\n#gaming #clutch #shorts",
        generated_tags='["#gaming", "#clutch", "#viral", "#epic", "#fyp"]',
    )

    meta = build_clip_metadata(clip)

    assert meta.title == "INSANE 1v5 CLUTCH! 😱 #Shorts"
    assert "Watch how this clutch was pulled off!" in meta.description
    assert "[c:clip-game999]" in meta.description
    assert "#gaming" in meta.tags
    assert "#clutch" in meta.tags
    assert meta.category_id == "20"
    assert meta.privacy_status == "unlisted"
    assert meta.made_for_kids is False
    assert meta.default_language == "en"
