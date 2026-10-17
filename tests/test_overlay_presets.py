"""Tests for E-011 presets + E-012 export/import with SHA-256."""
import json
import sqlite3
import pytest

from shorts_engine.services.overlay.spec import OverlaySpec, OverlayLayer, LayerType, TextStyle
from shorts_engine.services.overlay.presets import (
    BUILTIN_PRESETS,
    list_builtin_presets,
    get_builtin_preset,
    upsert_preset,
    export_preset,
    export_all,
    import_preset,
    preset_sha256,
    seed_builtins_if_empty,
)


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE overlay_preset (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name VARCHAR(128) NOT NULL UNIQUE,
            description TEXT,
            spec_json TEXT NOT NULL,
            spec_sha256 VARCHAR(64) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_by VARCHAR(64),
            is_builtin INTEGER NOT NULL DEFAULT 0
        );
    """)
    yield conn
    conn.close()


def test_builtin_presets_present():
    names = list_builtin_presets()
    assert "builtin:title-card" in names
    assert "builtin:lower-third" in names
    assert "builtin:cta-endcard" in names
    assert len(names) == 3


def test_get_builtin_returns_spec():
    spec = get_builtin_preset("builtin:title-card")
    assert spec is not None
    assert len(spec.layers) == 2


def test_get_builtin_missing():
    assert get_builtin_preset("nope") is None


def test_preset_sha256_stable():
    spec = OverlaySpec(name="x", base_w=100, base_h=100, layers=[
        OverlayLayer(layer_type=LayerType.TEXT, text="A", text_style=TextStyle()),
    ])
    s1 = preset_sha256(spec)
    s2 = preset_sha256(spec)
    assert s1 == s2
    assert len(s1) == 64  # SHA-256 hex


def test_preset_sha256_changes_on_edit():
    s1 = OverlaySpec(name="x", base_w=100, base_h=100)
    s2 = OverlaySpec(name="x", base_w=200, base_h=100)
    assert preset_sha256(s1) != preset_sha256(s2)


def test_upsert_and_get(db):
    spec = OverlaySpec(name="my-preset", base_w=720, base_h=1280, layers=[
        OverlayLayer(layer_type=LayerType.TEXT, text="hi", text_style=TextStyle()),
    ])
    rid = upsert_preset(db, spec, created_by="alice")
    assert rid > 0
    payload = export_preset(db, "my-preset")
    assert payload["name"] == "my-preset"
    assert payload["spec_sha256"] == preset_sha256(spec)
    assert payload["spec"]["layers"][0]["text"] == "hi"


def test_upsert_updates_existing(db):
    spec = OverlaySpec(name="x", base_w=100, base_h=100)
    rid1 = upsert_preset(db, spec)
    spec.description = "updated"
    rid2 = upsert_preset(db, spec)
    assert rid1 == rid2  # same row
    assert export_preset(db, "x")["description"] == "updated"


def test_export_missing_raises(db):
    with pytest.raises(KeyError):
        export_preset(db, "nope")


def test_export_all_returns_all(db):
    upsert_preset(db, OverlaySpec(name="a"))
    upsert_preset(db, OverlaySpec(name="b"))
    upsert_preset(db, OverlaySpec(name="c"))
    all_ = export_all(db)
    assert {p["name"] for p in all_} == {"a", "b", "c"}


def test_import_valid_preset(db):
    spec = OverlaySpec(name="imported", base_w=100, base_h=100)
    payload = {
        "name": "imported",
        "description": "",
        "spec": spec.to_dict(),
        "spec_sha256": preset_sha256(spec),
    }
    rid, verified = import_preset(db, payload)
    assert verified
    assert rid > 0
    assert export_preset(db, "imported")["name"] == "imported"


def test_import_tampered_sha_rejected(db):
    spec = OverlaySpec(name="tampered", base_w=100, base_h=100)
    payload = {
        "name": "tampered",
        "spec": spec.to_dict(),
        "spec_sha256": "0" * 64,  # wrong
    }
    with pytest.raises(ValueError) as exc:
        import_preset(db, payload)
    assert "tampered" in str(exc.value).lower() or "mismatch" in str(exc.value).lower()


def test_import_unverified_accepted(db):
    spec = OverlaySpec(name="no-sha", base_w=100, base_h=100)
    payload = {"name": "no-sha", "spec": spec.to_dict()}
    rid, verified = import_preset(db, payload, verify_sha=False)
    assert not verified
    assert rid > 0


def test_import_missing_keys_rejected(db):
    with pytest.raises(ValueError):
        import_preset(db, {"name": "x"})


def test_seed_builtins_if_empty_inserts(db):
    n = seed_builtins_if_empty(db)
    assert n == 3
    all_names = {p["name"] for p in export_all(db)}
    assert "builtin:title-card" in all_names
    assert "builtin:lower-third" in all_names
    assert "builtin:cta-endcard" in all_names


def test_seed_builtins_idempotent(db):
    seed_builtins_if_empty(db)
    n = seed_builtins_if_empty(db)
    assert n == 0


def test_preset_export_import_round_trip(db):
    """E2E-style: export one preset, then import it under a new name."""
    spec = OverlaySpec(name="src", base_w=720, base_h=1280, layers=[
        OverlayLayer(layer_type=LayerType.TEXT, text="hello", x=0.5, y=0.5,
                     text_style=TextStyle(size=64, color="#FFFFFF", outline_color="#000000", outline_width=3)),
    ])
    upsert_preset(db, spec, created_by="alice")
    payload = export_preset(db, "src")
    payload["name"] = "dest"
    rid, verified = import_preset(db, payload)
    assert verified
    assert export_preset(db, "dest")["spec"]["layers"][0]["text"] == "hello"
