"""Tests for E-008 webhook engine (SSRF, redaction, signature, retries)."""
import json

import pytest
import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shorts_engine.db.session import Base
from shorts_engine.db.models import WebhookDeadLetter
from shorts_engine.services.webhooks import (
    WebhookEngine, WebhookError, redact, redact_url, sign_payload, validate_url,
)


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    yield db
    db.close()


# ----------------------------------------------------------------------
# Redaction
# ----------------------------------------------------------------------
def test_redact_replaces_secret_keys():
    payload = {
        "user": "alice",
        "access_token": "secret-value",
        "nested": {"api_key": "k", "ok": 1},
        "list": [{"password": "p"}],
    }
    red = redact(payload)
    assert red["user"] == "alice"
    assert red["access_token"] == "[REDACTED]"
    assert red["nested"]["api_key"] == "[REDACTED]"
    assert red["nested"]["ok"] == 1
    assert red["list"][0]["password"] == "[REDACTED]"


def test_redact_url_strips_userinfo():
    assert "@" not in redact_url("https://user:pass@host.example.com/x")
    assert "host.example.com" in redact_url("https://user:pass@host.example.com/x")


# ----------------------------------------------------------------------
# URL validation
# ----------------------------------------------------------------------
def test_validate_url_rejects_non_http_schemes():
    with pytest.raises(WebhookError, match="scheme"):
        validate_url("file:///etc/passwd")


def test_validate_url_rejects_loopback():
    with pytest.raises(WebhookError, match="SSRF"):
        validate_url("http://127.0.0.1:80/x")


def test_validate_url_rejects_private_ip():
    with pytest.raises(WebhookError, match="SSRF"):
        validate_url("http://10.0.0.1/x")


def test_validate_url_rejects_metadata():
    with pytest.raises(WebhookError, match="SSRF"):
        validate_url("http://169.254.169.254/x")


def test_validate_url_rejects_link_local():
    with pytest.raises(WebhookError, match="SSRF"):
        validate_url("http://[fe80::1]/x")


def test_validate_url_accepts_public_https():
    validate_url("https://hooks.example.com/path")


def test_validate_url_rejects_empty():
    with pytest.raises(WebhookError, match="empty"):
        validate_url("")


# ----------------------------------------------------------------------
# CRUD
# ----------------------------------------------------------------------
def test_create_minimum_ok(db_session):
    eng = WebhookEngine(db_session)
    wh = eng.create(
        name="test", url="https://hooks.example.com/p",
        secret="a" * 16, events=["clip.approved"],
    )
    assert wh.id is not None
    assert wh.events == ["clip.approved"]
    assert wh.is_active is True


def test_create_rejects_short_secret(db_session):
    with pytest.raises(WebhookError, match="secret"):
        WebhookEngine(db_session).create(
            name="x", url="https://hooks.example.com/p",
            secret="short", events=["x.y"],
        )


def test_create_rejects_no_events(db_session):
    with pytest.raises(WebhookError, match="event"):
        WebhookEngine(db_session).create(
            name="x", url="https://hooks.example.com/p",
            secret="a" * 16, events=[],
        )


def test_create_rejects_bad_url(db_session):
    with pytest.raises(WebhookError, match="SSRF"):
        WebhookEngine(db_session).create(
            name="x", url="http://127.0.0.1:9",
            secret="a" * 16, events=["x.y"],
        )


# ----------------------------------------------------------------------
# Delivery (mocked HTTP)
# ----------------------------------------------------------------------
def _make_webhook(db_session, url="https://hooks.example.com/p"):
    return WebhookEngine(db_session).create(
        name="t", url=url, secret="a" * 16, events=["clip.approved"]
    )


def test_emit_sends_signed_payload(db_session, monkeypatch):
    _make_webhook(db_session)  # creates wh for side effects
    captured = {}

    def fake_post(self, url, content=None, headers=None, **kw):
        captured["url"] = url
        captured["content"] = content
        captured["headers"] = headers or {}
        r = httpx.Response(200, request=httpx.Request("POST", url))
        return r

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    eng = WebhookEngine(db_session)
    eng._signing_key = "a" * 16
    results = eng.emit("clip.approved", {"clip_id": "c1", "access_token": "secret"})
    assert len(results) == 1
    assert results[0].status == "OK"
    # Signature is sha256=<hex>
    assert captured["headers"]["X-Webhook-Signature"].startswith("sha256=")
    # Body must be JSON; secret must be redacted.
    body = json.loads(captured["content"].decode("utf-8"))
    assert body["event"] == "clip.approved"
    assert body["payload"]["clip_id"] == "c1"
    assert body["payload"]["access_token"] == "[REDACTED]"


def test_emit_does_not_retry_on_4xx(db_session, monkeypatch):
    _make_webhook(db_session)  # creates wh for side effects
    calls = {"n": 0}

    def fake_post(self, url, **kw):
        calls["n"] += 1
        return httpx.Response(404, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    eng = WebhookEngine(db_session)
    eng._signing_key = "a" * 16
    results = eng.emit("clip.approved", {"x": 1}, max_retries=3)
    assert calls["n"] == 1
    assert results[0].status == "FAILED_PERMANENT"


def test_emit_retries_on_5xx_then_dead_letters(db_session, monkeypatch):
    wh = _make_webhook(db_session)
    calls = {"n": 0}

    def fake_post(self, url, **kw):
        calls["n"] += 1
        return httpx.Response(503, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    eng = WebhookEngine(db_session)
    eng._signing_key = "a" * 16
    results = eng.emit("clip.approved", {"x": 1}, max_retries=2)
    assert calls["n"] == 2
    assert results[0].status == "FAILED_RETRYABLE"
    dl = db_session.query(WebhookDeadLetter).filter_by(webhook_id=wh.id).all()
    assert len(dl) == 1
    assert dl[0].last_status == 503


def test_emit_retries_on_network_error(db_session, monkeypatch):
    _make_webhook(db_session)  # creates wh for side effects
    calls = {"n": 0}

    def fake_post(self, url, **kw):
        calls["n"] += 1
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    eng = WebhookEngine(db_session)
    eng._signing_key = "a" * 16
    results = eng.emit("clip.approved", {"x": 1}, max_retries=2)
    assert calls["n"] == 2
    assert results[0].status == "FAILED_RETRYABLE"


def test_emit_respects_event_subscription(db_session, monkeypatch):
    _make_webhook(db_session)  # creates wh for side effects
    captured = {"called": False}

    def fake_post(self, url, **kw):
        captured["called"] = True
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    eng = WebhookEngine(db_session)
    eng._signing_key = "a" * 16
    results = eng.emit("clip.rejected", {"x": 1})  # not in events list
    assert results == []
    assert captured["called"] is False


def test_emit_url_allowlist_filter(db_session, monkeypatch):
    _make_webhook(db_session)  # creates wh for side effects
    eng = WebhookEngine(db_session)
    eng._signing_key = "a" * 16
    # allowlist excludes our webhook url
    results = eng.emit("clip.approved", {"x": 1}, url_allowlist=["https://other.example.com/"])
    assert results == []


# ----------------------------------------------------------------------
# Signature
# ----------------------------------------------------------------------
def test_sign_payload_deterministic():
    body = b"hello"
    s1 = sign_payload("a" * 16, body)
    s2 = sign_payload("a" * 16, body)
    assert s1 == s2
    assert len(s1) == 64  # hex sha256


def test_sign_payload_changes_with_secret():
    body = b"hello"
    assert sign_payload("a" * 16, body) != sign_payload("b" * 16, body)
