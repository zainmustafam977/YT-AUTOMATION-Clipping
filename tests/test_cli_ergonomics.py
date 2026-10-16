"""Tests for E-023 CLI ergonomics."""
import json
import pytest

from shorts_engine.cli.ergonomics import (
    run_cli, shell_completions, get_version, get_status_json,
    parse_filter, matches,
    EXIT_OK, EXIT_TIMEOUT, EXIT_NOT_FOUND, EXIT_VALIDATION,
)


def test_run_cli_echoes_known_flag():
    """When --json is supported, parsed_json should be populated."""
    # Use Python itself to avoid the cli.py dependency: `python -c "print(1)"` doesn't
    # accept --json, but `python -c "import json; print(json.dumps({'ok':1}))"` does
    # at the JSON-parse level.
    res = run_cli(
        ["python", "-c", "import json; print(json.dumps({'ok':1}))"],
        json_output=True,
    )
    assert res.parsed_json == {"ok": 1}


def test_run_cli_no_json_flag():
    res = run_cli(["python", "-c", "print('hello')"])
    assert res.parsed_json is None
    assert "hello" in res.stdout


def test_run_cli_captures_stderr():
    res = run_cli(["python", "-c", "import sys; sys.stderr.write('err')"])
    assert "err" in res.stderr


def test_run_cli_yes_flag_passes_through():
    """The --yes flag is passed to the subprocess; we just verify it runs."""
    res = run_cli(
        ["python", "-c", "import sys; sys.exit(0)"],
        yes=True,
    )
    assert res.returncode == EXIT_OK


def test_run_cli_dry_run_marks_result():
    res = run_cli(["python", "-c", "import sys; sys.exit(0)"], dry_run=True)
    assert res.dry_run is True


def test_run_cli_filter_passes_through():
    res = run_cli(
        ["python", "-c", "import sys; sys.exit(0)"],
        filter_expr="status=ready",
    )
    assert res.returncode == EXIT_OK


def test_run_cli_nonzero_return_code():
    res = run_cli(["python", "-c", "import sys; sys.exit(7)"])
    assert res.returncode == 7


def test_run_cli_timeout():
    res = run_cli(
        ["python", "-c", "import time; time.sleep(5)"],
        timeout=1,
    )
    assert res.returncode == EXIT_TIMEOUT


def test_shell_completions_bash():
    out = shell_completions("bash")
    assert "complete -W" in out
    assert "status" in out
    assert "process" in out


def test_shell_completions_unsupported_shell():
    out = shell_completions("powershell")
    assert "unsupported" in out


def test_get_version_returns_string():
    v = get_version()
    assert isinstance(v, str)
    assert len(v) > 0


def test_get_status_json_shape():
    s = get_status_json()
    assert "version" in s
    assert "python" in s
    assert "platform" in s


def test_parse_filter_empty():
    assert parse_filter("") == {}


def test_parse_filter_single():
    assert parse_filter("status=ready") == {"status": "ready"}


def test_parse_filter_multiple():
    assert parse_filter("status=ready,channel=ai") == {"status": "ready", "channel": "ai"}


def test_parse_filter_strips_whitespace():
    assert parse_filter(" status = ready ") == {"status": "ready"}


def test_parse_filter_invalid_token():
    with pytest.raises(ValueError):
        parse_filter("just-a-key")


def test_matches_exact():
    assert matches({"status": "ready"}, {"status": "ready"}) is True
    assert matches({"status": "draft"}, {"status": "ready"}) is False


def test_matches_multiple_keys():
    rec = {"status": "ready", "channel": "ai"}
    assert matches(rec, {"status": "ready"}) is True
    assert matches(rec, {"status": "ready", "channel": "ai"}) is True
    assert matches(rec, {"status": "ready", "channel": "x"}) is False


def test_matches_missing_field():
    assert matches({"status": "ready"}, {"nonexistent": "x"}) is False


def test_matches_empty_filter():
    assert matches({"any": "thing"}, {}) is True


def test_exit_codes_distinct():
    """Verify the canonical exit codes are well-known values."""
    assert EXIT_OK == 0
    assert EXIT_TIMEOUT != EXIT_OK
    assert EXIT_NOT_FOUND != EXIT_OK
    assert EXIT_VALIDATION != EXIT_OK
