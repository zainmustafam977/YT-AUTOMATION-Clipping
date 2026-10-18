"""R4-9.1 — Benchmark tool tests."""
import json
import os
import platform
import sys
import pytest

from tools.benchmark import (
    detect_machine, run_benchmark, BenchmarkReport, StageResult,
    MachineInfo, _synthetic_video, stage_key_vault_decrypt,
)


def test_detect_machine_returns_info():
    m = detect_machine()
    assert isinstance(m, MachineInfo)
    assert m.os
    assert m.python
    assert m.cpu_count > 0


def test_detect_machine_with_explicit_ffmpeg():
    m = detect_machine(ffmpeg_path="ffmpeg")
    # ffmpeg_path is either set (real ffmpeg) or None (not installed).
    assert m.ffmpeg_path is None or isinstance(m.ffmpeg_path, str)


def test_synthetic_video_no_ffmpeg(tmp_path, monkeypatch):
    """When ffmpeg is missing, _synthetic_video should not crash."""
    if os.system("which ffmpeg >/dev/null 2>&1") != 0:
        pytest.skip("ffmpeg not installed")
    p = _synthetic_video(str(tmp_path), seconds=1)
    assert os.path.exists(p)
    assert p.endswith(".mp4")


def test_run_benchmark_returns_report():
    r = run_benchmark()
    assert isinstance(r, BenchmarkReport)
    assert r.finished_at > r.started_at
    # 4 ffmpeg stages + 1 key-vault stage = 5.
    assert len(r.stages) == 5
    names = [s.name for s in r.stages]
    assert "ffprobe-metadata" in names
    assert "ffmpeg-transcode-720p" in names
    assert "ffmpeg-drawtext" in names
    assert "ffmpeg-loudnorm-pass1" in names
    assert "key-vault-decrypt" in names


def test_run_benchmark_skip_deps():
    r = run_benchmark(skip_deps=True)
    # All ffmpeg stages should be marked UNVERIFIED (ok=False) but not crash.
    ffmpeg_stages = [s for s in r.stages if "ffmpeg" in s.name or "ffprobe" in s.name]
    assert all(not s.ok for s in ffmpeg_stages)


def test_stage_result_to_dict():
    s = StageResult(name="x", ok=True, duration_sec=0.5, per_minute_of_input=10.0)
    d = s.__dict__
    assert d["name"] == "x"
    assert d["per_minute_of_input"] == 10.0


def test_report_to_dict():
    r = run_benchmark()
    d = r.to_dict()
    assert "machine" in d
    assert "stages" in d
    assert "duration_sec" in d


def test_report_to_markdown():
    r = run_benchmark()
    md = r.to_markdown()
    assert "# Benchmark report" in md
    assert "## Stages" in md
    assert "| Stage |" in md


def test_stage_key_vault_decrypt_runs():
    """Even if the key vault is misconfigured, the stage returns a result."""
    s = stage_key_vault_decrypt()
    assert s.name == "key-vault-decrypt"
    # ok or not, but the stage returned a result and didn't raise.
    assert isinstance(s.ok, bool)
    assert s.duration_sec > 0


def test_main_with_markdown(capsys):
    from tools.benchmark import main
    rc = main(["--markdown"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "# Benchmark report" in out


def test_main_with_out(tmp_path):
    from tools.benchmark import main
    p = tmp_path / "report.json"
    rc = main(["--out", str(p)])
    assert rc == 0
    assert p.exists()
    payload = json.loads(p.read_text())
    assert "machine" in payload
    assert "stages" in payload


def test_benchmark_marks_unverified_no_ffmpeg():
    """If ffmpeg is missing, every ffmpeg stage should be UNVERIFIED."""
    r = run_benchmark(ffmpeg_path="/no/such/ffmpeg-binary")
    if r.machine.ffmpeg_path is None:
        ffmpeg_stages = [s for s in r.stages if "ffmpeg" in s.name or "ffprobe" in s.name]
        assert all(not s.ok for s in ffmpeg_stages)
        assert any("ffmpeg not" in s.detail for s in ffmpeg_stages)
