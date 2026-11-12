"""Tests for E-011 overlay HTTP routes."""

import pytest

from fastapi.testclient import TestClient

from shorts_engine.db.session import engine, SessionLocal, Base
from shorts_engine.db import models  # noqa: F401  (register models)
from shorts_engine.dashboard.app import app
from shorts_engine.dashboard.auth import (
    create_session_token,
    generate_csrf_token,
    SESSION_COOKIE_NAME,
)
from shorts_engine.db.models import User, UserRole


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    # Build the schema (in-memory sqlite).
    Base.metadata.create_all(engine)
    yield


@pytest.fixture(scope="module")
def auth_token():
    """Session token for an operator account; overlay routes require auth."""
    db = SessionLocal()
    user = db.query(User).filter_by(username="overlay_route_test_admin").first()
    if not user:
        user = User(
            username="overlay_route_test_admin",
            password_hash="dummy_hash",
            role=UserRole.ADMIN,
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    token = create_session_token(user.id)
    db.close()
    return token


@pytest.fixture
def client(auth_token):
    c = TestClient(app, headers={"X-CSRF-Token": generate_csrf_token(auth_token)})
    c.cookies.set(SESSION_COOKIE_NAME, auth_token)
    return c


def test_list_presets_seeds_builtins(client):
    r = client.get("/api/overlays/presets")
    assert r.status_code == 200
    data = r.json()
    assert "presets" in data
    assert "builtins" in data
    # After first call, builtins should be seeded.
    assert any(p["is_builtin"] for p in data["presets"])


def test_get_preset_by_name(client):
    r = client.get("/api/overlays/presets/builtin:title-card")
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "builtin:title-card"
    assert "spec" in data
    assert "spec_sha256" in data


def test_get_missing_preset_404(client):
    r = client.get("/api/overlays/presets/no-such-preset")
    assert r.status_code == 404


def test_upsert_then_get(client):
    payload = {
        "name": "user-preset-1",
        "description": "test",
        "spec": {
            "name": "user-preset-1",
            "description": "test",
            "base_w": 1080,
            "base_h": 1920,
            "layers": [
                {
                    "layer_type": "text",
                    "text": "Hello",
                    "x": 0.5, "y": 0.5, "z": 0,
                    "text_style": {
                        "font": "DejaVuSans-Bold",
                        "size": 48,
                        "color": "#FFFFFF",
                        "outline_color": "#000000",
                        "outline_width": 2,
                        "shadow": False,
                    },
                }
            ],
        },
        "created_by": "tester",
    }
    r = client.post("/api/overlays/presets", json=payload)
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "user-preset-1"
    # Now fetch.
    r2 = client.get("/api/overlays/presets/user-preset-1")
    assert r2.status_code == 200


def test_upsert_invalid_returns_400(client):
    payload = {
        "name": "bad-preset",
        "description": "",
        "spec": {
            "name": "bad-preset",
            "base_w": 1080, "base_h": 1920,
            "layers": [
                {"layer_type": "text", "x": 2.0, "y": 0.5, "z": 0}  # x out of bounds, missing text
            ],
        },
    }
    r = client.post("/api/overlays/presets", json=payload)
    assert r.status_code == 400
    assert "validation_errors" in r.json()["detail"]


def test_compile_route(client):
    r = client.post("/api/overlays/presets/builtin:title-card/compile")
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "builtin:title-card"
    assert "drawtext" in data["filter_chain"]


def test_compile_with_duration(client):
    r = client.post("/api/overlays/presets/builtin:title-card/compile?video_duration=10")
    assert r.status_code == 200
    data = r.json()
    assert "enable=" in data["filter_chain"]


def test_compile_missing_preset_404(client):
    r = client.post("/api/overlays/presets/missing/compile")
    assert r.status_code == 404


def test_validate_preset(client):
    r = client.post("/api/overlays/presets/builtin:title-card/validate")
    assert r.status_code == 200
    data = r.json()
    assert data["valid"] is True
    assert data["errors"] == []


def test_validate_inline_spec(client):
    body = {
        "spec": {
            "name": "inline",
            "base_w": 100, "base_h": 100,
            "layers": [{"layer_type": "text", "text": "ok", "x": 0.5, "y": 0.5, "z": 0,
                        "text_style": {"color": "#FFF", "outline_color": "#000", "size": 12}}],
        }
    }
    r = client.post("/api/overlays/presets/none/validate", json=body)
    assert r.status_code == 200
    data = r.json()
    assert data["valid"] is True


def test_validate_missing_args_400(client):
    r = client.post("/api/overlays/presets//validate")
    assert r.status_code in (400, 404, 405, 422)


def test_export_all(client):
    r = client.get("/api/overlays/presets/export-all")
    assert r.status_code == 200
    data = r.json()
    assert "presets" in data
    assert len(data["presets"]) >= 3


def test_import_preset(client):
    payload = {
        "name": "imported-test",
        "description": "from-import",
        "spec": {
            "name": "imported-test",
            "base_w": 720, "base_h": 1280,
            "layers": [
                {"layer_type": "text", "text": "imported", "x": 0.5, "y": 0.5, "z": 0,
                 "text_style": {"color": "#FFF", "outline_color": "#000", "size": 24}}
            ],
        },
        "spec_sha256": "deadbeef" * 8,  # wrong on purpose
    }
    r = client.post("/api/overlays/presets/import", json={"payload": payload, "verify_sha": True})
    assert r.status_code == 400


def test_import_unverified_preset(client):
    from shorts_engine.services.overlay.spec import OverlaySpec, OverlayLayer, LayerType, TextStyle
    from shorts_engine.services.overlay.presets import preset_sha256
    spec = OverlaySpec(name="unverified-import", base_w=720, base_h=1280, layers=[
        OverlayLayer(layer_type=LayerType.TEXT, text="hi", x=0.5, y=0.5, text_style=TextStyle()),
    ])
    payload = {
        "name": "unverified-import",
        "spec": spec.to_dict(),
        "spec_sha256": preset_sha256(spec),
    }
    r = client.post("/api/overlays/presets/import", json={"payload": payload, "verify_sha": True})
    assert r.status_code == 200
    data = r.json()
    assert data["verified"] is True


def test_preview_endpoint(client):
    r = client.get("/api/overlays/preview/builtin:title-card?t=0&ascii=true")
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "builtin:title-card"
    assert "layers" in data
    assert "ascii" in data


def test_preview_missing_preset_404(client):
    r = client.get("/api/overlays/preview/missing")
    assert r.status_code == 404


def test_seed_builtins_route(client):
    r = client.post("/api/overlays/seed-builtins")
    assert r.status_code == 200
    data = r.json()
    assert "seeded" in data


def test_audit_endpoint(client):
    r = client.get("/api/overlays/audit")
    assert r.status_code == 200
    data = r.json()
    assert "audit" in data


def test_audit_filtered(client):
    r = client.get("/api/overlays/audit?candidate_id=1")
    assert r.status_code == 200


def test_overlay_routes_require_authentication():
    """All overlay endpoints must reject anonymous callers."""
    anon = TestClient(app)
    assert anon.get("/api/overlays/presets").status_code == 401
    assert anon.get("/api/overlays/audit").status_code == 401
    assert anon.post("/api/overlays/seed-builtins").status_code == 401


def test_overlay_mutations_require_csrf(auth_token):
    """A session cookie alone must not authorise a state-changing call."""
    c = TestClient(app)
    c.cookies.set(SESSION_COOKIE_NAME, auth_token)
    assert c.post("/api/overlays/seed-builtins").status_code == 403
