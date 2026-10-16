"""Sabotage check 12: secret redaction in webhook payloads.

The redact() function must remove all SECRET_KEYS. The sabotage
test patches the function to pass through and asserts the test
fails (i.e. demonstrates the secret is leaked).
"""
from shorts_engine.services.webhooks import redact, REDACTED


def test_redact_removes_known_secret_keys():
    payload = {
        "user": "alice",
        "access_token": "secret-1",
        "refresh_token": "secret-2",
        "api_key": "secret-3",
    }
    red = redact(payload)
    assert red["access_token"] == REDACTED
    assert red["refresh_token"] == REDACTED
    assert red["api_key"] == REDACTED
    assert red["user"] == "alice"


def test_redact_handles_nested():
    payload = {"a": {"b": {"password": "x"}}, "list": [{"secret": "y"}]}
    red = redact(payload)
    assert red["a"]["b"]["password"] == REDACTED
    assert red["list"][0]["secret"] == REDACTED


def test_sabotage_redaction_disabled_leaks_secrets(monkeypatch):
    """SABOTAGE: with redact() stubbed to identity, the secret
    would appear in the payload that is logged or sent.
    """
    import shorts_engine.services.webhooks as wm
    # Get the ORIGINAL redact before monkey-patching.
    original = wm.redact
    monkeypatch.setattr(wm, "redact", lambda x: x)
    payload = {"access_token": "real-secret-123"}
    leaked = wm.redact(payload)
    assert leaked["access_token"] == "real-secret-123"
    # The original (un-patched) redact would have replaced it.
    assert original({"access_token": "x"})["access_token"] == "[REDACTED]"
