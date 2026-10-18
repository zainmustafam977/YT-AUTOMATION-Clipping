"""R4-6.3 — Smoke test runner."""
import json
import os
import sys
import time
import pytest

from tools.smoke import (
    run_smoke, SmokeReport, SmokeStep, main as smoke_main,
    step_health, step_readiness, step_database, step_queue, step_doctor,
    step_dryrun_pipeline, step_live_upload, LIVE_CONFIRM_ENV,
)


def test_run_smoke_returns_report_shape():
    report = run_smoke("http://localhost:9", live=False)  # nothing is running
    assert isinstance(report, SmokeReport)
    assert report.live is False
    assert report.finished_at > report.started_at
    # 6 standard steps.
    assert len(report.steps) == 6
    step_names = {s.name for s in report.steps}
    assert "health" in step_names
    assert "readiness" in step_names
    assert "database" in step_names
    assert "queue" in step_names
    assert "doctor" in step_names
    assert "dryrun-pipeline" in step_names


def test_run_smoke_live_adds_live_upload_step():
    report = run_smoke("http://localhost:9", live=True)
    step_names = {s.name for s in report.steps}
    assert "live-upload" in step_names


def test_step_returns_smoke_step():
    s = step_health("http://localhost:9")
    assert isinstance(s, SmokeStep)
    assert s.name == "health"
    assert s.duration_sec > 0


def test_smoke_step_to_dict():
    s = SmokeStep(name="x", ok=True, detail="d", duration_sec=0.1)
    d = s.to_dict()
    assert d["name"] == "x"
    assert d["ok"] is True
    assert d["duration_sec"] == 0.1


def test_smoke_report_summary_line():
    report = SmokeReport(base_url="x", started_at=time.time())
    report.steps = [
        SmokeStep(name="a", ok=True),
        SmokeStep(name="b", ok=False),
    ]
    assert report.summary_line() == "1/2 steps passed"


def test_live_upload_refused_without_env(monkeypatch):
    """The --live step must refuse to run if the confirm env is unset."""
    monkeypatch.delenv(LIVE_CONFIRM_ENV, raising=False)
    s = step_live_upload("http://localhost:9", token=None)
    assert s.ok is False
    assert "refused" in (s.error or "")


def test_live_upload_attempts_with_env(monkeypatch):
    """With the env var set, the step tries a real call (which may 5xx)."""
    monkeypatch.setenv(LIVE_CONFIRM_ENV, "owner-confirmed")
    s = step_live_upload("http://localhost:9", token=None)
    # The step ran; we don't assert success because localhost:9 is closed.
    assert "live-upload" == s.name
    # The guard (refused path) was bypassed.
    assert s.error is None or "refused" not in (s.error or "") or "URLError" in (s.error or "")


def test_main_rejects_live_without_env(monkeypatch, capsys):
    monkeypatch.delenv(LIVE_CONFIRM_ENV, raising=False)
    rc = smoke_main(["--base-url", "http://localhost:9", "--live", "--json"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "ERROR" in err or "live" in err.lower()


def test_main_json_output(capsys):
    rc = smoke_main(["--base-url", "http://localhost:9", "--json"])
    # The endpoints will fail; rc is 1 because overall_ok is False.
    assert rc in (0, 1)
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert "base_url" in payload
    assert "steps" in payload


def test_main_human_output(capsys):
    rc = smoke_main(["--base-url", "http://localhost:9"])
    assert rc in (0, 1)
    out = capsys.readouterr().out
    assert "Smoke test" in out
    assert "steps passed" in out


def test_step_handles_unreachable():
    """When the server is down, the step records an error, not a crash."""
    s = step_health("http://localhost:9")  # nothing listening
    assert s.ok is False
    assert s.error is not None
