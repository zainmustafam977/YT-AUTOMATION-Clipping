"""
Tests verifying single-source-of-truth configuration consistency (R2-01 & F-03).
"""

import pytest
from pydantic import ValidationError
from shorts_engine.config.settings import PipelineSettings


def test_segment_bounds_enforced():
    settings = PipelineSettings()
    assert settings.MIN_SEGMENTS_PER_VIDEO == 3
    assert settings.MAX_SEGMENTS_PER_VIDEO == 5
    assert settings.TARGET_DURATION_MIN_SEC == 15
    assert settings.TARGET_DURATION_MAX_SEC == 58


def test_segment_upper_bound_constraint():
    # Attempting to configure > 20 segments must fail validation
    with pytest.raises(ValidationError):
        PipelineSettings(MAX_SEGMENTS_PER_VIDEO=21)

    # Configuring up to 20 segments is permitted
    valid_settings = PipelineSettings(MIN_SEGMENTS_PER_VIDEO=15, MAX_SEGMENTS_PER_VIDEO=20)
    assert valid_settings.MIN_SEGMENTS_PER_VIDEO == 15
    assert valid_settings.MAX_SEGMENTS_PER_VIDEO == 20


def test_min_greater_than_max_fails():
    with pytest.raises(ValidationError):
        PipelineSettings(MIN_SEGMENTS_PER_VIDEO=6, MAX_SEGMENTS_PER_VIDEO=4)

    with pytest.raises(ValidationError):
        PipelineSettings(TARGET_DURATION_MIN_SEC=70, TARGET_DURATION_MAX_SEC=50)


def test_production_default_password_rejected():
    # Production refuses to boot with default credentials
    with pytest.raises(ValidationError, match="Insecure DASHBOARD_PASSWORD"):
        PipelineSettings(ENV="production", DASHBOARD_PASSWORD="change-me")

    # Production also rejects short/weak passwords (e.g. "1234")
    with pytest.raises(ValidationError, match="Insecure DASHBOARD_PASSWORD"):
        PipelineSettings(ENV="production", DASHBOARD_PASSWORD="1234")

    # Production rejects default or short session secret key (< 32 chars)
    with pytest.raises(ValidationError, match="Insecure DASHBOARD_SECRET_KEY"):
        PipelineSettings(
            ENV="production",
            DASHBOARD_PASSWORD="strong_random_pass_123!",
            DASHBOARD_SECRET_KEY="change-me-in-prod",
        )

    # Production succeeds with real non-default credentials (>=12 char password, >=32 char secret)
    prod_settings = PipelineSettings(
        ENV="production",
        DASHBOARD_PASSWORD="strong_random_pass_123!",
        DASHBOARD_SECRET_KEY="cryptographically_secure_random_key_abc_12345",
    )
    assert prod_settings.DASHBOARD_PASSWORD == "strong_random_pass_123!"


def test_production_default_postgres_password_rejected():
    """Production strictly refuses to boot if POSTGRES_PASSWORD is default or insecure."""
    with pytest.raises(ValidationError, match="Insecure POSTGRES_PASSWORD"):
        PipelineSettings(
            ENV="production",
            DASHBOARD_PASSWORD="strong_random_pass_123!",
            DASHBOARD_SECRET_KEY="cryptographically_secure_random_key_abc_12345",
            POSTGRES_PASSWORD="ytpassword_secret_change_me",
        )

    with pytest.raises(ValidationError, match="Insecure POSTGRES_PASSWORD"):
        PipelineSettings(
            ENV="production",
            DASHBOARD_PASSWORD="strong_random_pass_123!",
            DASHBOARD_SECRET_KEY="cryptographically_secure_random_key_abc_12345",
            POSTGRES_PASSWORD="short",
        )


def test_production_embedded_postgres_password_rejected():
    """Production strictly refuses to boot if default password is embedded in DATABASE_URL."""
    with pytest.raises(ValidationError, match="Insecure password embedded in DATABASE_URL"):
        PipelineSettings(
            ENV="production",
            DASHBOARD_PASSWORD="strong_random_pass_123!",
            DASHBOARD_SECRET_KEY="cryptographically_secure_random_key_abc_12345",
            DATABASE_URL="postgresql://ytuser:ytpassword_secret_change_me@postgres:5432/ytshorts",
        )



# ---------------------------------------------------------------------------
# Shipped default operator password (SEV-2, docs/audit/VERIFICATION_REPORT.md)
# ---------------------------------------------------------------------------

STRONG_PASSWORD = "correct-horse-battery-staple-42!"
STRONG_SECRET_KEY = "cryptographically_secure_random_key_abc_12345"


def test_production_rejects_shipped_default_password(monkeypatch):
    """Production must refuse to boot on the committed default, unconditionally.

    This is deliberately NOT gated behind ENFORCE_SECRET_ENTROPY: an optional
    flag is not a sufficient control for a real credential in source.
    """
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    monkeypatch.delenv("ENFORCE_SECRET_ENTROPY", raising=False)

    with pytest.raises(ValidationError, match="shipped default"):
        PipelineSettings(
            ENV="production",
            DASHBOARD_PASSWORD="AdminPass12345!",
            DASHBOARD_SECRET_KEY=STRONG_SECRET_KEY,
        )


def test_production_requires_explicitly_provided_password(monkeypatch):
    """Production must not silently inherit the field default."""
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    monkeypatch.delenv("ENFORCE_SECRET_ENTROPY", raising=False)

    # The default *is* the shipped credential, so the more actionable
    # "shipped default" error fires first. Either rejection is acceptable here;
    # what matters is that production never boots on an inherited default.
    with pytest.raises(ValidationError, match="shipped default|explicitly provided"):
        PipelineSettings(
            ENV="production",
            DASHBOARD_SECRET_KEY=STRONG_SECRET_KEY,
        )


def test_strict_mode_rejects_shipped_default_password(monkeypatch):
    """ENFORCE_SECRET_ENTROPY=1 rejects the default even outside production."""
    monkeypatch.setenv("ENFORCE_SECRET_ENTROPY", "1")
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)

    with pytest.raises(ValidationError, match="shipped default"):
        PipelineSettings(
            ENV="development",
            DASHBOARD_PASSWORD="AdminPass12345!",
            DASHBOARD_SECRET_KEY=STRONG_SECRET_KEY,
        )


def test_production_accepts_properly_configured_secret(monkeypatch):
    """A genuinely configured secret must still boot."""
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    monkeypatch.delenv("ENFORCE_SECRET_ENTROPY", raising=False)

    prod = PipelineSettings(
        ENV="production",
        DASHBOARD_PASSWORD=STRONG_PASSWORD,
        DASHBOARD_SECRET_KEY=STRONG_SECRET_KEY,
    )
    assert prod.DASHBOARD_PASSWORD == STRONG_PASSWORD


def test_rotated_password_is_not_treated_as_default(monkeypatch):
    """After rotation the new value must validate in strict mode."""
    monkeypatch.setenv("ENFORCE_SECRET_ENTROPY", "1")
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)

    prod = PipelineSettings(
        ENV="production",
        DASHBOARD_PASSWORD=STRONG_PASSWORD,
        DASHBOARD_SECRET_KEY=STRONG_SECRET_KEY,
    )
    assert prod.DASHBOARD_PASSWORD != "AdminPass12345!"
