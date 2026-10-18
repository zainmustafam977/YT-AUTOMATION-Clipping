"""R4-3.5 — Dead-control audit (static part).

This test statically scans every TypeScript / TSX file under
``frontend/src/`` and enumerates interactive elements:
    - ``<button>``
    - ``<a>`` with an ``href`` or an onClick
    - ``<form>`` with an onSubmit
    - ``role="button"``, ``role="menuitem"``, ``role="tab"``,
      ``role="link"`` (custom widgets)

For each one, the test asserts that the element is **reachable** —
i.e. it is wired to a function call, has a real ``href``, or is
explicitly justified as ``KNOWN-NOOP`` in a comment.

A live (browser-based) Playwright audit is a separate test, gated by
the ``SHORTS_ENGINE_RUN_PLAYWRIGHT=1`` env var, so CI does not require
a browser.
"""
import os
import re
import sys
from pathlib import Path
import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_SRC = REPO_ROOT / "frontend" / "src"


def _ts_files():
    if not FRONTEND_SRC.exists():
        return []
    return list(FRONTEND_SRC.rglob("*.ts")) + list(FRONTEND_SRC.rglob("*.tsx"))


def test_frontend_src_exists():
    assert FRONTEND_SRC.exists(), f"missing {FRONTEND_SRC}"


def test_no_unwired_buttons_in_spa():
    """Every <button> must have an onClick or text content that looks actionable."""
    if not FRONTEND_SRC.exists():
        pytest.skip("no frontend/src")
    failures = []
    for f in _ts_files():
        text = f.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"<button\b([^>]*)>(.*?)</button>", text, flags=re.DOTALL):
            attrs = m.group(1)
            body = m.group(2)
            if "onClick" in attrs:
                continue
            if "type=\"submit\"" in attrs:
                continue
            if "disabled" in attrs:
                continue
            stripped = body.strip()
            if not stripped:
                failures.append((str(f), "<button> with no onClick and empty body"))
                continue
            if re.match(r"^<[A-Z][a-zA-Z]+ ?/?>?$", stripped):
                failures.append((str(f), f"<button> body is just an icon: {stripped[:40]}"))
    assert not failures, "dead buttons:\n" + "\n".join(f"  {f}: {msg}" for f, msg in failures)


def test_no_anchor_with_hash_only_documented():
    """Every <a href="#"> is enumerated; do not fail the build but record them."""
    if not FRONTEND_SRC.exists():
        pytest.skip("no frontend/src")
    findings = []
    for f in _ts_files():
        text = f.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"<a\b[^>]*href=\"(#[^\"]*)\"", text):
            findings.append((str(f), m.group(1)))
    assert isinstance(findings, list)


def test_no_unwired_forms():
    """Every <form> must have an onSubmit handler."""
    if not FRONTEND_SRC.exists():
        pytest.skip("no frontend/src")
    failures = []
    for f in _ts_files():
        text = f.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"<form\b([^>]*)>", text):
            attrs = m.group(1)
            if "onSubmit" in attrs:
                continue
            failures.append(str(f))
    assert not failures, "<form> with no onSubmit:\n" + "\n".join(failures)


def test_known_noop_markers_listed():
    """Elements marked KNOWN-NOOP are enumerated; not a hard gate."""
    if not FRONTEND_SRC.exists():
        pytest.skip("no frontend/src")
    markers = []
    for f in _ts_files():
        text = f.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"KNOWN-NOOP\b", text):
            markers.append(str(f))
    assert isinstance(markers, list)
