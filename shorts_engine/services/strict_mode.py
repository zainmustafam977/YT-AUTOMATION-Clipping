"""R4-5.2 — STRICT_MODE helper.

Wraps every "fallback" site with a single helper that:
  * reads the `STRICT_MODE` setting,
  * in strict mode (production default), raises ``StrictModeViolation`` with
    a clear message,
  * in permissive mode (development), executes the ``default_behaviour``
    branch and logs a WARNING explaining what was used instead.

The guard is also responsible for adding the fallback to the needs-attention
queue, so the operator can see "your config has 3 fallbacks active" in the
dashboard.
"""
from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


_STRICT_MODE_ATTR = "STRICT_MODE"


class StrictModeViolation(RuntimeError):
    """Raised when a code path would fall back but STRICT_MODE is on.

    The message always includes:
      * the component name (e.g. "llm_router"),
      * the preferred behaviour,
      * a one-line action the operator can take.
    """

    def __init__(self, component: str, message: str, action: str = ""):
        self.component = component
        self.action = action
        full = f"[{component}] {message}"
        if action:
            full += f" — ACTION: {action}"
        super().__init__(full)


@dataclass
class FallbackRecord:
    component: str
    preferred: str
    used: str
    reason: str


# In-memory log of fallbacks observed this session; surfaced in needs-attention.
_fallback_log: list[FallbackRecord] = []


def fallback_log() -> list[FallbackRecord]:
    """Returns a snapshot of all fallbacks observed so far."""
    return list(_fallback_log)


def clear_fallback_log() -> None:
    """Resets the log (useful for tests)."""
    _fallback_log.clear()


@contextlib.contextmanager
def strict_mode_guard(
    component: str,
    *,
    preferred: str,
    used_when_lenient: str,
    reason: str,
    action: str = "",
    settings=None,
):
    """Context manager: in strict mode, raise. In lenient mode, log + record.

    The ``reason`` explains WHY the fallback would fire (e.g. "no API key
    configured"); the ``preferred`` is what we'd do in strict mode;
    ``used_when_lenient`` is what we'll actually do in lenient mode.
    """
    strict = _is_strict(settings)
    if strict:
        raise StrictModeViolation(component, preferred, action)
    rec = FallbackRecord(
        component=component,
        preferred=preferred,
        used=used_when_lenient,
        reason=reason,
    )
    _fallback_log.append(rec)
    logger.warning(
        "FALLBACK [%s]: %s (using %s) — %s",
        component, reason, used_when_lenient, action or "no action",
    )
    try:
        yield rec
    finally:
        pass


def _is_strict(settings=None) -> bool:
    """Returns True if STRICT_MODE is on. Defaults to True in production."""
    if settings is not None:
        return bool(getattr(settings, _STRICT_MODE_ATTR, True))
    # Default: read from PipelineSettings; falls back to True if env is prod.
    try:
        from shorts_engine.config.settings import settings as app_settings
        return bool(getattr(app_settings, _STRICT_MODE_ATTR, True))
    except Exception:
        # If we can't load settings, default to strict (safe by default).
        return True


def is_strict(settings=None) -> bool:
    """Public read-only access to STRICT_MODE."""
    return _is_strict(settings)
