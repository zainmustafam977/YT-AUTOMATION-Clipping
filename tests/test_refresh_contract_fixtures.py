"""R4-8.6 — Contract-fixture refresh tool tests."""
import os
import sys
import json
import pytest

from tools import refresh_contract_fixtures


def test_main_requires_env(monkeypatch):
    monkeypatch.delenv("SHORTS_ENGINE_REFRESH_FIXTURES", raising=False)
    monkeypatch.setattr(sys, "argv", ["refresh"])
    rc = refresh_contract_fixtures.main()
    assert rc == 2


def test_main_with_env_runs_stubs(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SHORTS_ENGINE_REFRESH_FIXTURES", "1")
    # Override FIXTURES_DIR so the stubs don't touch the real dir.
    fake = tmp_path / "fixtures"
    fake.mkdir()
    monkeypatch.setattr(refresh_contract_fixtures, "FIXTURES_DIR", fake)
    monkeypatch.setattr(sys, "argv", ["refresh"])
    rc = refresh_contract_fixtures.main()
    out = capsys.readouterr().out
    assert "Refreshing" in out
    assert "SKIPPED" in out
    assert rc == 0


def test_stamp_writes_expected_shape(tmp_path, monkeypatch):
    monkeypatch.setattr(refresh_contract_fixtures, "FIXTURES_DIR", tmp_path)
    refresh_contract_fixtures._stamp("test_fixture", {"hello": "world"})
    out = tmp_path / "test_fixture.json"
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["api_version"] == "v3"
    assert "recorded_at" in data
    assert data["hello"] == "world"
