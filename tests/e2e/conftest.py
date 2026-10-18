"""Shared fixtures for the Playwright-driven end-to-end tests.

The tests in this directory drive a real browser. When the browser binary
is not installed they cannot run at all, and previously reported as
failures whose traceback ("BrowserType.launch: Executable doesn't
exist") looked like a product defect.

They are now skipped with that exact reason instead, so a red build means
a real regression rather than a missing browser on the runner. This is a
conditional skip, not a blanket marker: where the browser IS installed the
tests run unchanged.
"""
from __future__ import annotations

import os

import pytest

_CACHE: dict[str, tuple[bool, str]] = {}


def _chromium_available() -> tuple[bool, str]:
    """Return (available, reason). Cached - probing costs a process spawn."""
    if "result" in _CACHE:
        return _CACHE["result"]

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        _CACHE["result"] = (False, f"playwright is not installed ({exc})")
        return _CACHE["result"]

    try:
        with sync_playwright() as p:
            path = p.chromium.executable_path
    except Exception as exc:  # noqa: BLE001 - any failure means "not usable"
        _CACHE["result"] = (False, f"chromium could not be resolved ({exc})")
        return _CACHE["result"]

    if not path or not os.path.exists(path):
        _CACHE["result"] = (
            False,
            "chromium binary is not installed - run "
            "`python -m playwright install chromium` "
            f"(expected at: {path or '<unresolved>'})",
        )
        return _CACHE["result"]

    _CACHE["result"] = (True, "")
    return _CACHE["result"]


@pytest.fixture(scope="session", autouse=True)
def _require_browser() -> None:
    """Skip every test in tests/e2e/ unless a real chromium is present."""
    available, reason = _chromium_available()
    if not available:
        pytest.skip(f"browser-backed E2E unavailable: {reason}", allow_module_level=True)
