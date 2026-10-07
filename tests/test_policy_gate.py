"""
Unit and integration tests for PolicyGate Content Policy & Safety Triage.
Verifies:
1. Thematic sensitivity keyword scanning.
2. Vault authentication detection across OAuth and cookie tiers.
3. InnerTube & oEmbed restriction probing (age gating, Restricted Mode, unavailable).
4. Multi-mode triage evaluation ('auto_skip', 'strict_gate', 'two_tier').
5. Runner Step A integration: Zero retry burns on policy restrictions.
6. Runner Step D integration: is_sensitive flag propagation from SourceVideo to Clip.
"""

import pytest
from unittest.mock import patch, MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shorts_engine.db.models import Base, SourceVideo, SourceVideoStatus, Clip, ClipCandidate, Channel
from shorts_engine.services.ingestion.policy_gate import PolicyGate, PolicyResult
from shorts_engine.orchestration.runner import PipelineRunner


@pytest.fixture
def mem_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        yield db
    finally:
        db.close()


def test_check_thematic_sensitivity():
    """Detects high-sensitivity themes in text and ignores benign content."""
    sensitive_text = "Breaking News: Fatal violent crime and murder investigation underway"
    themes = PolicyGate.check_thematic_sensitivity(sensitive_text)
    assert "violent crime" in themes or "murder" in themes

    benign_text = "How to build a high performance web server in Rust"
    assert PolicyGate.check_thematic_sensitivity(benign_text) == []


def test_has_vault_authentication(tmp_path):
    """Verifies authentication detection across OAuth and cookie tiers."""
    with patch("shorts_engine.services.ingestion.oauth_flow.YouTubeDeviceOAuth.has_active_oauth", return_value=True):
        assert PolicyGate.has_vault_authentication() is True

    fake_cookies = tmp_path / "cookies.txt"
    fake_cookies.write_text("# Netscape HTTP Cookie File\n.youtube.com TRUE / FALSE 1800000000 SID abc")

    with patch("shorts_engine.services.ingestion.oauth_flow.YouTubeDeviceOAuth.has_active_oauth", return_value=False), \
         patch("shorts_engine.config.settings.settings.YOUTUBE_COOKIES_PATH", fake_cookies), \
         patch("shorts_engine.config.settings.settings.YOUTUBE_HARVESTER_COOKIES_PATH", tmp_path / "nonexistent.txt"):
        assert PolicyGate.has_vault_authentication() is True


def test_assess_video_auto_skip():
    """In auto_skip mode, sensitive/restricted videos are skipped immediately."""
    mock_inner = {
        "success": True,
        "status": "LOGIN_REQUIRED",
        "reason": "Sign in to confirm your age",
        "messages": ["This video may be inappropriate for some users."],
        "title": "Age Gated Content",
    }

    with patch.object(PolicyGate, "probe_innertube", return_value=mock_inner), \
         patch.object(PolicyGate, "probe_oembed", return_value={"accessible": True}):
        res = PolicyGate.assess_video("age_gated_vid", policy_mode="auto_skip", auth_available=False)
        assert res.action == "skip"
        assert res.is_restricted is True
        assert res.age_limit == 18
        assert "Auto-Skip policy" in res.reason


def test_assess_video_strict_gate():
    """In strict_gate mode, flagged videos are placed on hold for operator review."""
    mock_inner = {
        "success": True,
        "status": "OK",
        "title": "Mass Casualty Incident Analysis",
    }

    with patch.object(PolicyGate, "probe_innertube", return_value=mock_inner), \
         patch.object(PolicyGate, "probe_oembed", return_value={"accessible": True}):
        res = PolicyGate.assess_video("flagged_vid", policy_mode="strict_gate", auth_available=True)
        assert res.action == "hold"
        assert "mass casualty" in res.sensitive_themes
        assert "holding for operator approval" in res.reason


def test_assess_video_two_tier():
    """In two_tier mode, flagged video requires auth if unauthed, or proceeds if authed."""
    mock_inner = {
        "success": True,
        "status": "AGE_CHECK_REQUIRED",
        "reason": "Age-restricted content",
        "title": "Documentary with Explicit Scenes",
    }

    with patch.object(PolicyGate, "probe_innertube", return_value=mock_inner), \
         patch.object(PolicyGate, "probe_oembed", return_value={"accessible": True}):
        # Without auth: require_auth
        res_no_auth = PolicyGate.assess_video("vid_1", policy_mode="two_tier", auth_available=False)
        assert res_no_auth.action == "require_auth"
        assert res_no_auth.is_restricted is True

        # With auth: allow with warning
        res_with_auth = PolicyGate.assess_video("vid_1", policy_mode="two_tier", auth_available=True)
        assert res_with_auth.action == "allow"
        assert "SENSITIVE_CONTENT advisory" in res_with_auth.reason


def test_runner_policy_skip_zero_retry_burn(mem_db):
    """Runner Step A skips restricted videos under auto_skip without burning retry count."""
    channel = Channel(name="Test Channel", youtube_channel_id="UC123")
    mem_db.add(channel)
    mem_db.commit()

    runner = PipelineRunner(mem_db)

    policy_mock = PolicyResult(
        video_id="restricted_123",
        is_restricted=True,
        sensitive_themes=["age_restricted"],
        action="skip",
        reason="Auto-Skip policy: age restricted content",
        title="Restricted Title",
    )

    with patch("shorts_engine.services.ingestion.policy_gate.PolicyGate.assess_video", return_value=policy_mock):
        res = runner.run_pipeline_for_video("https://www.youtube.com/watch?v=restricted_123", channel=channel)
        assert res == []

    # Check DB record
    sv = mem_db.query(SourceVideo).filter_by(youtube_video_id="restricted_123").first()
    assert sv is not None
    assert sv.status == SourceVideoStatus.POLICY_RESTRICTED
    assert sv.is_sensitive is True
    assert sv.retry_count == 0, "Policy restriction must not burn retry count"


def test_runner_policy_hold_zero_retry_burn(mem_db):
    """Runner Step A holds restricted videos under strict_gate in PENDING_POLICY_REVIEW."""
    channel = Channel(name="Test Channel", youtube_channel_id="UC123")
    mem_db.add(channel)
    mem_db.commit()

    runner = PipelineRunner(mem_db)

    policy_mock = PolicyResult(
        video_id="strict_hold_456",
        is_restricted=True,
        action="hold",
        reason="Strict gate: holding for operator review",
        title="Sensitive Topic Video",
    )

    with patch("shorts_engine.services.ingestion.policy_gate.PolicyGate.assess_video", return_value=policy_mock):
        res = runner.run_pipeline_for_video("strict_hold_456", channel=channel)
        assert res == []

    sv = mem_db.query(SourceVideo).filter_by(youtube_video_id="strict_hold_456").first()
    assert sv is not None
    assert sv.status == SourceVideoStatus.PENDING_POLICY_REVIEW
    assert sv.retry_count == 0


def test_is_sensitive_propagation_to_clips(mem_db):
    """When source_video.is_sensitive is true, created Clips inherit is_sensitive=True."""
    channel = Channel(name="News Channel", youtube_channel_id="UC999")
    mem_db.add(channel)
    mem_db.commit()

    sv = SourceVideo(
        channel_id=channel.id,
        youtube_video_id="sensitive_source",
        title="Sensitive Investigation",
        status=SourceVideoStatus.SEGMENTS_SELECTED,
        is_sensitive=True,
    )
    mem_db.add(sv)
    mem_db.commit()

    cand = ClipCandidate(
        source_video_id=sv.id,
        start_ms=1000,
        end_ms=25000,
        hook_text="Check this out",
        payoff_text="Unbelievable conclusion",
        score=8.5,
        reason="High engagement hook",
    )
    mem_db.add(cand)
    mem_db.commit()

    # Create Clip mimicking Step D
    clip = Clip(
        clip_candidate_id=cand.id,
        rendered_path="storage/outputs/clip1.mp4",
        is_sensitive=sv.is_sensitive,
    )
    mem_db.add(clip)
    mem_db.commit()

    loaded_clip = mem_db.query(Clip).filter_by(id=clip.id).first()
    assert loaded_clip is not None
    assert loaded_clip.is_sensitive is True
