"""
Unit tests for Context-Aware Dynamic Model Router (Task 2.2).
Verifies:
1. Long-form and high-token video routing to Gemini 2.0 Flash.
2. Multilingual content routing to Gemini 2.0 Flash.
3. Short, high-speed viral content routing to Groq LPU.
4. Mid-tier standard content routing.
5. Vault-integrated candidate chain fallback and key acquisition.
6. Graceful failover to algorithmic heuristic when all external AI keys are unavailable.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shorts_engine.db.models import Base
from shorts_engine.services.ai.key_vault import ApiKeyVault
from shorts_engine.services.ai.model_router import (
    DynamicModelRouter,
    VideoAnalysisContext,
)
from shorts_engine.services.events.bus import global_event_bus


@pytest.fixture
def test_db():
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


@pytest.fixture(autouse=True)
def clean_events():
    global_event_bus.clear()
    yield
    global_event_bus.clear()


def test_route_long_form_content():
    """Verifies long-form podcasts (>15 min or >12k tokens) route to Gemini."""
    # 20 minutes (1200s), 15000 tokens
    ctx = VideoAnalysisContext(
        duration_sec=1200.0,
        transcript_token_count=15000,
        detected_language="en",
    )
    route = DynamicModelRouter.select_optimal_model(ctx)
    assert route.provider == "gemini"
    assert "gemini-2.0-flash" in route.model_name
    assert "1M+" in route.rationale
    assert route.fallback_provider == "openai"


def test_route_non_english_content():
    """Verifies non-English audio routes to Gemini for multilingual competence."""
    ctx = VideoAnalysisContext(
        duration_sec=180.0,
        transcript_token_count=1500,
        detected_language="es",  # Spanish
    )
    route = DynamicModelRouter.select_optimal_model(ctx)
    assert route.provider == "gemini"
    assert "Spanish" in route.rationale or "'es'" in route.rationale
    assert route.fallback_provider == "openai"


def test_route_short_punchy_viral_content():
    """Verifies short viral videos (<5 min, <4k tokens, English) route to Groq LPU."""
    ctx = VideoAnalysisContext(
        duration_sec=120.0,
        transcript_token_count=1800,
        detected_language="en",
    )
    route = DynamicModelRouter.select_optimal_model(ctx)
    assert route.provider == "groq"
    assert "llama-3.3-70b-versatile" in route.model_name
    assert "Groq LPU" in route.rationale
    assert route.fallback_provider == "gemini"


def test_route_mid_tier_content():
    """Verifies mid-length video (8 min, 6000 tokens) routes to Gemini 2.0 Flash."""
    ctx = VideoAnalysisContext(
        duration_sec=480.0,
        transcript_token_count=6000,
        detected_language="en",
    )
    route = DynamicModelRouter.select_optimal_model(ctx)
    assert route.provider == "gemini"
    assert route.fallback_provider == "groq"


def test_select_model_with_vault_primary_success(tmp_path, test_db):
    """Verifies acquiring key from vault when primary provider is available."""
    vault = ApiKeyVault(db=test_db, vault_key_path=tmp_path / "vault.key")
    vault.add_key(provider="groq", raw_key="groq_key_val", nickname="Groq Fast")

    ctx = VideoAnalysisContext(duration_sec=90.0, transcript_token_count=1200, detected_language="en")
    route, key = DynamicModelRouter.select_model_with_vault(ctx, vault)

    assert route.provider == "groq"
    assert key is not None
    assert key.provider == "groq"
    assert key.nickname == "Groq Fast"


def test_select_model_with_vault_fallback_chain(tmp_path, test_db):
    """Verifies automatic fallback to secondary provider when primary is unavailable."""
    vault = ApiKeyVault(db=test_db, vault_key_path=tmp_path / "vault.key")
    # Only Gemini key exists; Groq key is absent
    vault.add_key(provider="gemini", raw_key="gemini_key_val", nickname="Gemini Backup")

    # Short video would normally pick Groq, but Groq has no key in vault
    ctx = VideoAnalysisContext(duration_sec=90.0, transcript_token_count=1200, detected_language="en")
    route, key = DynamicModelRouter.select_model_with_vault(ctx, vault)

    # Should fall back to Gemini
    assert route.provider == "gemini"
    assert key is not None
    assert key.provider == "gemini"
    assert "Fell back" in route.rationale
    # Next fallback in the chain should be openai (not self-referential gemini)
    assert route.fallback_provider == "openai"
    assert route.fallback_model == DynamicModelRouter.OPENAI_MODEL


def test_select_model_with_vault_all_exhausted_heuristic_fallback(tmp_path, test_db):
    """Verifies fallback to algorithmic heuristic when all AI providers in vault are exhausted."""
    vault = ApiKeyVault(db=test_db, vault_key_path=tmp_path / "vault.key")
    # No keys added to vault

    ctx = VideoAnalysisContext(duration_sec=120.0, transcript_token_count=1500, detected_language="en")
    route, key = DynamicModelRouter.select_model_with_vault(ctx, vault)

    assert route.provider == "heuristic"
    assert key is None
    assert "algorithmic heuristic fallback" in route.rationale

    # Check EventBus warning event
    events = global_event_bus.get_history(category="analysis")
    assert len(events) >= 1
    assert events[-1].level == "WARNING"
    assert "Heuristic Fallback" in events[-1].title


def test_route_english_dialect_variants():
    """Verifies English dialect variants (en-US, en-GB, en_US) are treated as English."""
    for dial in ("en-US", "en-GB", "en_US", "eng", "ENGLISH"):
        ctx = VideoAnalysisContext(
            duration_sec=60.0,
            transcript_token_count=800,
            detected_language=dial,
        )
        route = DynamicModelRouter.select_optimal_model(ctx)
        # Short English content should route to Groq LPU, not Gemini
        assert route.provider == "groq"
        assert "llama-3.3-70b-versatile" in route.model_name


def test_route_negative_and_zero_bounds_clamping():
    """Verifies negative duration or token counts are safely clamped to 0."""
    ctx = VideoAnalysisContext(
        duration_sec=-100.0,
        transcript_token_count=-500,
        detected_language="en",
    )
    route = DynamicModelRouter.select_optimal_model(ctx)
    assert route.estimated_tokens >= 2000
    assert route.provider == "groq"
