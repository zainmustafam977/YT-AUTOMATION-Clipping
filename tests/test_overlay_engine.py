"""Tests for E-011 engine: apply preset to candidate + audit."""
import os
import sqlite3
import tempfile

import pytest

from shorts_engine.services.overlay.spec import OverlaySpec, OverlayLayer, LayerType, TextStyle
from shorts_engine.services.overlay.presets import upsert_preset, seed_builtins_if_empty
from shorts_engine.services.overlay.engine import (
    apply_preset_to_candidate,
    batch_apply_preset,
    list_audit,
    last_audit_for_candidate,
)


@pytest.fixture
def db(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "overlay.db"))
    conn.executescript("""
        CREATE TABLE clip_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_path TEXT
        );
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
        CREATE TABLE overlay_render_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id INTEGER,
            preset_id INTEGER,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            finished_at TIMESTAMP,
            ok INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            ffmpeg_cmd TEXT,
            output_path TEXT,
            contrast_ratio REAL,
            bytes_written INTEGER
        );
    """)
    # Insert one candidate.
    conn.execute("INSERT INTO clip_candidates (id, video_path) VALUES (1, '/tmp/does-not-exist.mp4')")
    conn.execute("INSERT INTO clip_candidates (id, video_path) VALUES (2, '/tmp/also-missing.mp4')")
    conn.commit()
    yield conn
    conn.close()


def _preset_with_text(name="t", text="Hello"):
    return OverlaySpec(name=name, base_w=1080, base_h=1920, layers=[
        OverlayLayer(layer_type=LayerType.TEXT, text=text, x=0.5, y=0.5, z=0,
                     text_style=TextStyle(size=48, color="#FFFFFF", outline_color="#000000")),
    ])


def test_apply_preset_records_audit_dry_run(db, tmp_path):
    upsert_preset(db, _preset_with_text("t1"))
    out = str(tmp_path / "out.mp4")
    row = apply_preset_to_candidate(db, preset_id=1, candidate_id=1, output_path=out, allow_dry_run=True)
    assert row.ok is True
    audit = list_audit(db, candidate_id=1)
    assert len(audit) == 1
    assert audit[0]["ok"] is True
    assert audit[0]["contrast_ratio"] is not None
    assert audit[0]["ffmpeg_cmd"]


def test_apply_preset_to_missing_candidate_raises(db, tmp_path):
    upsert_preset(db, _preset_with_text("t1"))
    with pytest.raises(KeyError) as exc:
        apply_preset_to_candidate(db, preset_id=1, candidate_id=999, output_path="/tmp/out.mp4")
    assert "999" in str(exc.value)


def test_apply_missing_preset_raises(db):
    with pytest.raises(KeyError):
        apply_preset_to_candidate(db, preset_id=999, candidate_id=1, output_path="/tmp/x.mp4")


def test_apply_writes_audit_even_on_dry_run(db, tmp_path):
    upsert_preset(db, _preset_with_text("t1"))
    apply_preset_to_candidate(db, preset_id=1, candidate_id=1,
                              output_path=str(tmp_path / "out.mp4"))
    audit = list_audit(db)
    assert len(audit) == 1
    assert audit[0]["ok"] is True
    assert audit[0]["ffmpeg_cmd"]


def test_batch_apply_creates_one_audit_per_candidate(db, tmp_path):
    upsert_preset(db, _preset_with_text("t1"))
    out_dir = str(tmp_path / "out")
    rows = batch_apply_preset(db, preset_id=1, candidate_ids=[1, 2], output_dir=out_dir)
    assert len(rows) == 2
    assert all(r.ok for r in rows)
    audit = list_audit(db)
    assert len(audit) == 2
    candidate_ids = sorted(a["candidate_id"] for a in audit)
    assert candidate_ids == [1, 2]


def test_last_audit_for_candidate(db, tmp_path):
    upsert_preset(db, _preset_with_text("t1"))
    apply_preset_to_candidate(db, 1, 1, str(tmp_path / "a.mp4"))
    apply_preset_to_candidate(db, 1, 1, str(tmp_path / "b.mp4"))
    last = last_audit_for_candidate(db, 1)
    assert last is not None
    # Two audits for candidate 1, last in DESC order.
    all_ = list_audit(db, candidate_id=1)
    assert last["id"] == all_[0]["id"]


def test_audit_includes_contrast_ratio_for_text(db, tmp_path):
    upsert_preset(db, _preset_with_text("t1", text="contrast-test"))
    apply_preset_to_candidate(db, 1, 1, str(tmp_path / "a.mp4"))
    last = last_audit_for_candidate(db, 1)
    assert last["contrast_ratio"] is not None
    assert last["contrast_ratio"] > 1.0


def test_audit_contrast_none_for_no_text_layers(db, tmp_path):
    from shorts_engine.services.overlay.spec import ShapeStyle
    spec = OverlaySpec(name="shape-only", layers=[
        OverlayLayer(layer_type=LayerType.SHAPE, x=0, y=0, z=0, shape=ShapeStyle()),
    ])
    upsert_preset(db, spec)
    apply_preset_to_candidate(db, 1, 1, str(tmp_path / "a.mp4"))
    last = last_audit_for_candidate(db, 1)
    assert last["contrast_ratio"] is None


def test_seed_builtins_then_apply(db, tmp_path):
    seed_builtins_if_empty(db)
    # builtin:title-card is id=1 (first inserted)
    row = apply_preset_to_candidate(db, 1, 1, str(tmp_path / "x.mp4"))
    assert row.ok is True


def test_apply_writes_path_to_audit(db, tmp_path):
    upsert_preset(db, _preset_with_text("t1"))
    op = str(tmp_path / "explicit.mp4")
    row = apply_preset_to_candidate(db, 1, 1, op)
    audit = last_audit_for_candidate(db, 1)
    assert audit["output_path"] == op  # recorded even on dry-run
    assert row.output_path == op
