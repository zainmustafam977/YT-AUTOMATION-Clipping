"""R4-5.2 — STRICT_MODE behaviour."""
import pytest

from shorts_engine.services.strict_mode import (
    StrictModeViolation,
    strict_mode_guard,
    fallback_log,
    clear_fallback_log,
    is_strict,
)


class _S:
    """Test settings object that overrides STRICT_MODE."""
    def __init__(self, value):
        self.STRICT_MODE = value


@pytest.fixture(autouse=True)
def _reset_log():
    clear_fallback_log()
    yield
    clear_fallback_log()


def test_strict_mode_raises():
    with pytest.raises(StrictModeViolation) as exc:
        with strict_mode_guard(
            "llm_router",
            preferred="call gemini-2.0-flash",
            used_when_lenient="call gpt-4o-mini",
            reason="no gemini key configured",
            action="set GEMINI_API_KEYS in .env",
            settings=_S(True),
        ):
            pass
    assert "llm_router" in str(exc.value)
    assert "gemini" in str(exc.value)
    assert "ACTION" in str(exc.value)


def test_lenient_mode_records_fallback():
    with strict_mode_guard(
        "thumbnail_resolver",
        preferred="return 404 (no file)",
        used_when_lenient="return branded SVG",
        reason="no thumbnail file at expected path",
        action="rerun thumbnail generation",
        settings=_S(False),
    ):
        pass
    log = fallback_log()
    assert len(log) == 1
    assert log[0].component == "thumbnail_resolver"
    assert "branded SVG" in log[0].used


def test_lenient_mode_does_not_raise():
    # Should not raise.
    with strict_mode_guard(
        "encoder",
        preferred="raise (no GPU)",
        used_when_lenient="use CPU",
        reason="no NVENC / QSV / AMF detected",
        action="install a GPU driver",
        settings=_S(False),
    ):
        pass


def test_default_is_strict_when_settings_unavailable(monkeypatch):
    """If the user passes no settings, the helper falls through to the
    global PipelineSettings. In a dev/test env that's lenient; in
    production it's strict. We force production here.
    """
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "long-enough-test-password-1234")
    monkeypatch.setenv("DASHBOARD_SECRET_KEY", "x" * 64)
    import importlib
    import shorts_engine.config.settings as s
    importlib.reload(s)
    try:
        with pytest.raises(StrictModeViolation):
            with strict_mode_guard(
                "x", preferred="raise", used_when_lenient="fallback",
                reason="r", settings=None,
            ):
                pass
    finally:
        # Restore.
        monkeypatch.delenv("ENV", raising=False)
        monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
        monkeypatch.delenv("DASHBOARD_SECRET_KEY", raising=False)
        importlib.reload(s)


def test_is_strict_with_settings():
    assert is_strict(_S(True)) is True
    assert is_strict(_S(False)) is False


def test_fallback_log_multiple_entries():
    with strict_mode_guard("a", preferred="x", used_when_lenient="y", reason="r", settings=_S(False)):
        pass
    with strict_mode_guard("b", preferred="x", used_when_lenient="y", reason="r", settings=_S(False)):
        pass
    log = fallback_log()
    assert len(log) == 2
    assert {r.component for r in log} == {"a", "b"}


def test_clear_fallback_log():
    with strict_mode_guard("a", preferred="x", used_when_lenient="y", reason="r", settings=_S(False)):
        pass
    assert len(fallback_log()) == 1
    clear_fallback_log()
    assert len(fallback_log()) == 0


def test_strict_mode_violation_attributes():
    e = StrictModeViolation("comp", "msg", "do X")
    assert e.component == "comp"
    assert e.action == "do X"
    assert "do X" in str(e)


def test_strict_mode_in_prod_env_default(monkeypatch):
    """When ENV=production, STRICT_MODE defaults to True even if not set."""
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "long-enough-test-password-1234")
    monkeypatch.setenv("DASHBOARD_SECRET_KEY", "x" * 64)
    import importlib
    import shorts_engine.config.settings as s
    importlib.reload(s)
    try:
        from shorts_engine.config.settings import settings as app_settings
        assert app_settings.STRICT_MODE is True
    finally:
        monkeypatch.delenv("ENV", raising=False)
        monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
        monkeypatch.delenv("DASHBOARD_SECRET_KEY", raising=False)
        importlib.reload(s)
