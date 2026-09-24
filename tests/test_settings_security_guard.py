import logging
import pytest
from shorts_engine.config.settings import PipelineSettings


def test_settings_logs_warning_when_enforce_entropy_false_and_placeholders_present(caplog):
    """Verify that when ENFORCE_SECRET_ENTROPY is False, placeholder keys log a warning instead of aborting."""
    with caplog.at_level(logging.WARNING):
        s = PipelineSettings(
            ENV="development",
            ENFORCE_SECRET_ENTROPY=False,
            MASTER_ENCRYPTION_KEY="yt_shorts_dev_master_secret_key_32_bytes_min!",
            ENCRYPTION_SALT="yt_shorts_kdf_salt_2026_secure!",
            DASHBOARD_SECRET_KEY="change-me-in-prod",
        )
    assert s is not None
    assert any("INSECURE STARTUP" in record.message for record in caplog.records)


def test_production_rejects_placeholder_secrets():
    """Verify that when ENFORCE_SECRET_ENTROPY is True, placeholder keys raise ValueError."""
    with pytest.raises(ValueError, match="INSECURE STARTUP"):
        PipelineSettings(
            ENV="production",
            ENFORCE_SECRET_ENTROPY=True,
            MASTER_ENCRYPTION_KEY="yt_shorts_dev_master_secret_key_32_bytes_min!",
            DASHBOARD_SECRET_KEY="strong_dashboard_secret_key_that_is_32_bytes_long!",
            DASHBOARD_PASSWORD="AStrongPassword123!",
        )


def test_production_accepts_strong_secrets():
    """Verify that when ENFORCE_SECRET_ENTROPY is True and keys are strong, initialization succeeds."""
    s = PipelineSettings(
        ENV="production",
        ENFORCE_SECRET_ENTROPY=True,
        MASTER_ENCRYPTION_KEY="custom_production_master_encryption_key_32b!",
        ENCRYPTION_SALT="custom_production_salt_at_least_16b!",
        DASHBOARD_SECRET_KEY="custom_production_dashboard_secret_key_32b!",
        DASHBOARD_PASSWORD="VeryStrongProductionPassword123!",
    )
    assert s.ENFORCE_SECRET_ENTROPY is True
