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

logger = logging.getLogger(__name__)

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
    """
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

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
        root_logger.addHandler(file_handler)

    # Propagate uvicorn and app loggers
    for log_name in ["shorts_engine", "uvicorn", "uvicorn.access", "uvicorn.error"]:
        l = logging.getLogger(log_name)
        l.setLevel(level)


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
            if hasattr(settings, key):
                current_attr = getattr(settings, key)
                target_type = type(current_attr) if current_attr is not None else str
                try:
                    if val is None:
                        converted_val = None
                    elif target_type == bool and isinstance(val, str):
                        converted_val = val.lower() in ("true", "1", "yes", "on")
                    elif isinstance(val, list):
                        converted_val = [str(x) for x in val]
                    elif current_attr is None:
                        converted_val = val
                    else:
                        converted_val = target_type(val)
                    setattr(settings, key, converted_val)
                    applied[key] = converted_val
                except (ValueError, TypeError):
                    logger.warning("Could not convert runtime setting %s to type %s", key, target_type)
        return applied
    except Exception as e:
        logger.warning("Failed to reload runtime settings from %s: %e", RUNTIME_CONFIG_PATH, e)
        return {}


def save_runtime_settings(updates: Dict[str, Any]) -> Dict[str, Any]:
    """
    Persists safe runtime parameter updates to storage/config/runtime_settings.json
    and applies them directly to the active settings singleton.
    """
    RUNTIME_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

    current = {}
    if RUNTIME_CONFIG_PATH.exists():
        try:
            with open(RUNTIME_CONFIG_PATH, "r", encoding="utf-8") as f:
                current = json.load(f)
        except Exception:
            current = {}

    current.update(updates)

    with open(RUNTIME_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(current, f, indent=2)

    applied = reload_runtime_settings()
    logger.info("Saved and applied %d runtime settings overrides to %s", len(applied), RUNTIME_CONFIG_PATH)
    return applied
