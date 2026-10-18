"""R4-3.5 — Playwright dead-control audit (live, owner-only).

This test requires Playwright and a running dev server. It is gated by
``SHORTS_ENGINE_RUN_PLAYWRIGHT=1`` so CI does not require a browser.

Run with:
    SHORTS_ENGINE_RUN_PLAYWRIGHT=1 \
    VITE_BASE=http://localhost:5173 \
        pytest tests/ui/test_dead_controls.py -v
"""
import os
import sys
import time
import pytest

_RUN = os.environ.get("SHORTS_ENGINE_RUN_PLAYWRIGHT") == "1"
_BASE = os.environ.get("VITE_BASE", "http://localhost:5173")


pytestmark = pytest.mark.skipif(
    not _RUN,
    reason="set SHORTS_ENGINE_RUN_PLAYWRIGHT=1 to enable the live Playwright audit",
)


def _have_playwright():
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
        return True
    except ImportError:
        return False


pytestmark = pytest.mark.skipif(
    not _have_playwright(),
    reason="playwright not installed",
)


def test_dead_controls_on_home():
    from playwright.sync_api import sync_playwright
    failures = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context()
        page = ctx.new_page()
        console_errors = []
        page.on("console", lambda msg: console_errors.append(msg.text)
                if msg.type == "error" else None)
        failed_requests = []
        page.on("requestfailed", lambda r: failed_requests.append(str(r.url)))
        page.goto(_BASE + "/app")
        # Enumerate every interactive element.
        buttons = page.query_selector_all("button")
        links = page.query_selector_all("a[href]")
        forms = page.query_selector_all("form")
        for b in buttons:
            if b.is_disabled():
                continue
            # Try to click; if it doesn't change state, mark KNOWN-NOOP.
            before = page.url
            try:
                b.click(timeout=2000)
            except Exception:
                pass
            after = page.url
            if before == after:
                # Stayed on the same URL; this might be fine (e.g. it opened
                # a modal) but we flag it.
                failures.append(f"button {b.inner_text()!r} clicked but URL unchanged")
        assert not console_errors, f"console errors: {console_errors}"
        assert not failed_requests, f"failed requests: {failed_requests}"
        assert not failures, "dead controls:\n" + "\n".join(failures)
        browser.close()
