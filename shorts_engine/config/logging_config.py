"""
Centralized Logging & Runtime Configuration Module.
Configures console output and rotating file logging to storage/logs/shorts_engine.log.
Manages persistent runtime settings overrides stored in storage/config/runtime_settings.json.
"""

import os
import sys
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict, Any

from shorts_engine.config.settings import settings
from shorts_engine.utils.fs import atomic_write_json

import re

logger = logging.getLogger(__name__)

SENSITIVE_KEY_PATTERN = re.compile(
    r"(key|secret|token|password|passwd|credential|auth)",
    re.IGNORECASE,
)


def is_sensitive_key(key_name: str) -> bool:
    """Returns True if key name matches sensitive patterns (key, secret, token, password, etc.)."""
    return bool(SENSITIVE_KEY_PATTERN.search(str(key_name)))


def redact_sensitive_data(val: Any) -> Any:
    """
    Recursively traverses dictionaries, lists, and tuples, redacting any value
    whose key matches sensitive patterns with '[REDACTED]'.
    """
    if isinstance(val, dict):
        redacted = {}
        for k, v in val.items():
            if is_sensitive_key(str(k)):
                redacted[k] = "[REDACTED]"
            else:
                redacted[k] = redact_sensitive_data(v)
        return redacted
    elif isinstance(val, list):
        return [redact_sensitive_data(item) for item in val]
    elif isinstance(val, tuple):
        return tuple(redact_sensitive_data(item) for item in val)
    return val


class SensitiveDataFilter(logging.Filter):
    """
    Logging filter that intercepts LogRecords and redacts any argument or formatted
    payload containing fields matching key/secret/token/password patterns.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            if isinstance(record.args, dict):
                record.args = redact_sensitive_data(record.args)
            elif isinstance(record.args, tuple):
                record.args = tuple(redact_sensitive_data(a) for a in record.args)
            elif isinstance(record.args, list):
                record.args = [redact_sensitive_data(a) for a in record.args]
        return True


RUNTIME_CONFIG_PATH = settings.STORAGE_DIR / "config" / "runtime_settings.json"
LOGS_DIR = settings.STORAGE_DIR / "logs"
LOG_FILE_PATH = LOGS_DIR / "shorts_engine.log"
TEST_LOG_FILE_PATH = LOGS_DIR / "shorts_engine_test.log"


def is_test_environment() -> bool:
    """Detects whether code is running inside pytest or a test execution context."""
    return bool(
        "pytest" in sys.modules
        or os.environ.get("PYTEST_CURRENT_TEST")
        or settings.ENV.lower() in ("test", "testing")
    )


def get_log_file_path() -> Path:
    """Returns the appropriate log file path based on execution environment."""
    if is_test_environment():
        return TEST_LOG_FILE_PATH
    return LOG_FILE_PATH


def setup_logging(level: int = logging.INFO) -> None:
    """
    Initializes standard Python logging with dual output:
    1. Console (sys.stdout) for live terminal feedback.
    2. RotatingFileHandler writing to storage/logs/shorts_engine.log (or shorts_engine_test.log during tests).
    Applies SensitiveDataFilter to prevent secrets leaking to logs.
    """
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    sensitive_filter = SensitiveDataFilter()
    root_logger.addFilter(sensitive_filter)

    active_log_file = get_log_file_path()

    # Avoid duplicate handlers on re-init
    has_file = any(
        isinstance(h, RotatingFileHandler) and Path(getattr(h, "baseFilename", "")).name == active_log_file.name
        for h in root_logger.handlers
    )
    has_stream = any(isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler) for h in root_logger.handlers)

    if not has_stream:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        console_handler.setLevel(level)
        console_handler.addFilter(sensitive_filter)
        root_logger.addHandler(console_handler)

    if not has_file:
        file_handler = RotatingFileHandler(
            str(active_log_file),
            maxBytes=10 * 1024 * 1024,  # 10 MB per log file
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(level)
        file_handler.addFilter(sensitive_filter)
        root_logger.addHandler(file_handler)

    # Propagate uvicorn and app loggers
    for log_name in ["shorts_engine", "uvicorn", "uvicorn.access", "uvicorn.error"]:
        log_inst = logging.getLogger(log_name)
        log_inst.setLevel(level)
        log_inst.addFilter(sensitive_filter)


# Sentinel: distinguishes "key does not exist on settings" from "applied as
# None", so callers can tell those apart without catching exceptions.
_NOT_APPLIED = object()


def _apply_runtime_override(key: str, val: Any) -> Any:
    """Coerce `val` to the type settings already uses for `key` and apply it.

    Returns the converted value, or _NOT_APPLIED if the key is unknown or the
    value could not be coerced. Shared by reload (from disk) and save (for the
    values deliberately kept off disk) so both paths coerce identically.
    """
    if not hasattr(settings, key):
        return _NOT_APPLIED

    current_attr = getattr(settings, key)
    target_type = type(current_attr) if current_attr is not None else str
    try:
        if val is None:
            converted_val = None
        elif target_type is bool and isinstance(val, str):
            converted_val = val.lower() in ("true", "1", "yes", "on")
        elif isinstance(val, list):
            converted_val = [str(x) for x in val]
        elif current_attr is None:
            converted_val = val
        else:
            converted_val = target_type(val)
    except (ValueError, TypeError):
        logger.warning("Could not convert runtime setting %s to type %s", key, target_type)
        return _NOT_APPLIED

    setattr(settings, key, converted_val)
    return converted_val


def reload_runtime_settings() -> Dict[str, Any]:
    """
    Reads persistent settings from storage/config/runtime_settings.json and
    applies them to the active in-memory settings singleton.
    Safe to call repeatedly in worker loops or before pipeline execution.
    """
    # Prevent live operational configuration overrides from polluting isolated test runs
    if is_test_environment() and RUNTIME_CONFIG_PATH == (settings.STORAGE_DIR / "config" / "runtime_settings.json"):
        return {}

    if not RUNTIME_CONFIG_PATH.exists():
        return {}

    try:
        with open(RUNTIME_CONFIG_PATH, "r", encoding="utf-8") as f:
            overrides = json.load(f)

        applied = {}
        for key, val in overrides.items():
            converted = _apply_runtime_override(key, val)
            if converted is not _NOT_APPLIED:
                applied[key] = converted
        return applied
    except Exception as e:
        logger.warning("Failed to reload runtime settings from %s: %e", RUNTIME_CONFIG_PATH, e)
        return {}


def save_runtime_settings(updates: Dict[str, Any]) -> Dict[str, Any]:
    """
    Persists safe runtime parameter updates to storage/config/runtime_settings.json
    and applies them directly to the active settings singleton.
    Sensitive credentials (matching key/secret/token/password patterns) are strictly
    excluded from disk persistence.
    """
    RUNTIME_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

    current = {}
    if RUNTIME_CONFIG_PATH.exists():
        try:
            with open(RUNTIME_CONFIG_PATH, "r", encoding="utf-8") as f:
                current = json.load(f)
        except Exception:
            current = {}

    # Purge any legacy sensitive keys that might have existed in current file
    current = {k: v for k, v in current.items() if not is_sensitive_key(k)}

    # Exclude sensitive updates from on-disk persistence
    safe_updates = {k: v for k, v in updates.items() if not is_sensitive_key(k)}
    current.update(safe_updates)

    atomic_write_json(RUNTIME_CONFIG_PATH, current, indent=2)

    applied = reload_runtime_settings()

    # Sensitive values are deliberately excluded from disk above, but they must
    # still take effect for this process. Previously they were dropped entirely:
    # an operator could save an API key in the dashboard, see "Settings saved
    # and applied", and the value would never reach settings because the reload
    # only reads back what was written. Keep them off disk, apply them here.
    for key, val in updates.items():
        if is_sensitive_key(key):
            converted = _apply_runtime_override(key, val)
            if converted is not _NOT_APPLIED:
                applied[key] = converted

    logger.info("Saved and applied %d runtime settings overrides to %s", len(applied), RUNTIME_CONFIG_PATH)
    return applied
