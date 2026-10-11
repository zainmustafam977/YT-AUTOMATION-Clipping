"""
Regression tests for settings-update route audit logging sanitization and redaction filter.
Enforces Amendment A6:
- The settings-update route cannot log any field whose name matches key/secret/token/password patterns.
- SensitiveDataFilter intercepts and redacts sensitive dicts and arguments across log records.
- save_runtime_settings() refuses to persist sensitive keys to storage/config/runtime_settings.json.
"""

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from shorts_engine.config.logging_config import (
    redact_sensitive_data,
    SensitiveDataFilter,
    save_runtime_settings,
    RUNTIME_CONFIG_PATH,
)
from shorts_engine.dashboard.app import app
from shorts_engine.dashboard.auth import require_operator, verify_csrf
from shorts_engine.dashboard.routes.queue import get_db
from shorts_engine.db.models import User, UserRole


def test_redact_sensitive_data_recursive_masking():
    """Verifies recursive masking of any field matching key/secret/token/password/credential/auth patterns."""
    raw_payload = {
        "regular_setting": "safe_value",
        "nested_block": {
            "duration": 45,
            "api_key": "raw_sensitive_key_value",
            "oauth_token": "raw_sensitive_token_value",
        },
        "GROQ_API_KEY": "gsk_123456789",
        "client_secret": "secret_abc_xyz",
        "db_password": "super_secret_password",
        "credential_payload": {"nested_auth": "auth_header_value"},
        "safe_list": ["item1", {"auth_token": "token_inside_list"}],
    }

    sanitized = redact_sensitive_data(raw_payload)

    assert sanitized["regular_setting"] == "safe_value"
    assert sanitized["nested_block"]["duration"] == 45
    assert sanitized["nested_block"]["api_key"] == "[REDACTED]"
    assert sanitized["nested_block"]["oauth_token"] == "[REDACTED]"
    assert sanitized["GROQ_API_KEY"] == "[REDACTED]"
    assert sanitized["client_secret"] == "[REDACTED]"
    assert sanitized["db_password"] == "[REDACTED]"
    assert sanitized["credential_payload"] == "[REDACTED]"
    assert sanitized["safe_list"][0] == "item1"
    assert sanitized["safe_list"][1]["auth_token"] == "[REDACTED]"


def test_sensitive_data_filter_intercepts_log_records():
    """Verifies that SensitiveDataFilter intercepts LogRecords and replaces sensitive arguments."""
    test_logger = logging.getLogger("test_redaction_logger")
    test_logger.setLevel(logging.INFO)
    test_logger.handlers = []

    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(message)s")
    handler.setFormatter(formatter)

    sens_filter = SensitiveDataFilter()
    handler.addFilter(sens_filter)
    test_logger.addHandler(handler)

    record_dict = {
        "model": "gemini-3.8-flash",
        "groq_api_key": "leaked_groq_key_999",
        "refresh_token": "leaked_refresh_token_888",
    }

    record = test_logger.makeRecord(
        name="test_redaction_logger",
        level=logging.INFO,
        fn="test_file.py",
        lno=42,
        msg="Settings: %s",
        args=(record_dict,),
        exc_info=None,
    )

    filtered = sens_filter.filter(record)
    assert filtered is True
    args_dict = record.args if isinstance(record.args, dict) else record.args[0]
    assert args_dict["groq_api_key"] == "[REDACTED]"
    assert args_dict["refresh_token"] == "[REDACTED]"
    assert args_dict["model"] == "gemini-3.8-flash"
    assert "[REDACTED]" in record.getMessage()
    assert "leaked_groq_key_999" not in record.getMessage()


def test_save_runtime_settings_excludes_sensitive_keys(tmp_path, monkeypatch):
    """Verifies save_runtime_settings() excludes sensitive keys from disk persistence."""
    custom_cfg_path = tmp_path / "runtime_settings.json"
    import shorts_engine.config.logging_config as lc
    monkeypatch.setattr(lc, "RUNTIME_CONFIG_PATH", custom_cfg_path)

    test_updates = {
        "TARGET_DURATION_MIN_SEC": 22,
        "LLM_MODEL_NAME": "gemini-3.8-flash",
        "GROQ_API_KEY": "should_not_persist_raw_groq_key",
        "GEMINI_API_KEYS": ["key1", "key2"],
        "CLIENT_SECRET": "should_not_persist_secret",
    }

    applied = save_runtime_settings(test_updates)

    assert custom_cfg_path.exists()
    persisted = json.loads(custom_cfg_path.read_text(encoding="utf-8"))

    # Non-sensitive keys persisted
    assert persisted["TARGET_DURATION_MIN_SEC"] == 22
    assert persisted["LLM_MODEL_NAME"] == "gemini-3.8-flash"

    # Sensitive keys strictly excluded from disk
    assert "GROQ_API_KEY" not in persisted
    assert "GEMINI_API_KEYS" not in persisted
    assert "CLIENT_SECRET" not in persisted


def test_settings_update_route_redacts_audit_logs(caplog):
    """
    Amendment A6 Regression Test:
    Asserts the settings-update route (/settings) cannot log any field whose name
    matches key/secret/token/password patterns.
    """
    mock_operator = User(
        id="usr_op_1",
        username="admin_operator",
        role=UserRole.OPERATOR,
        is_active=True,
    )
    mock_db = MagicMock()

    app.dependency_overrides[require_operator] = lambda: mock_operator
    app.dependency_overrides[verify_csrf] = lambda: True
    app.dependency_overrides[get_db] = lambda: mock_db

    secret_groq_key = "gsk_test_super_secret_groq_12345"
    secret_gemini_key = "AIzaSy_test_secret_gemini_67890"

    client = TestClient(app)

    try:
        with caplog.at_level(logging.INFO):
            response = client.post(
                "/settings",
                data={
                    "target_duration_min_sec": 20,
                    "target_duration_max_sec": 50,
                    "min_segments_per_video": 2,
                    "max_segments_per_video": 4,
                    "caption_margin_vertical": 400,
                    "max_retry_count": 3,
                    "quota_warning_threshold": 0.85,
                    "llm_model_name": "gemini-3.8-flash",
                    "groq_api_key": secret_groq_key,
                    "gemini_api_key": secret_gemini_key,
                    "csrf_token": "mock-csrf-token",
                },
                headers={"Accept": "application/json"},
            )

        assert response.status_code == 200
        json_data = response.json()
        assert json_data["status"] == "success"

        # 1. Assert raw secret values never appear anywhere in logged output
        for record in caplog.records:
            assert secret_groq_key not in record.message, f"Raw secret leaked into log record: {record.message}"
            assert secret_gemini_key not in record.message, f"Raw secret leaked into log record: {record.message}"

        # 2. Assert logged audit record contains [REDACTED] for sensitive fields
        audit_records = [
            r.message for r in caplog.records
            if "updated runtime settings" in r.message
        ]
        assert len(audit_records) > 0, "Expected audit log record for runtime settings update"
        for audit_msg in audit_records:
            assert "[REDACTED]" in audit_msg
            assert secret_groq_key not in audit_msg
            assert secret_gemini_key not in audit_msg

        # 3. Assert HTTP JSON response also sanitized
        updated_settings = json_data.get("updated_settings", {})
        if "GROQ_API_KEY" in updated_settings:
            assert updated_settings["GROQ_API_KEY"] == "[REDACTED]"
        if "GEMINI_API_KEY" in updated_settings:
            assert updated_settings["GEMINI_API_KEY"] == "[REDACTED]"

    finally:
        app.dependency_overrides.clear()
