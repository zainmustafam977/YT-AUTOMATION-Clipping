"""Smoke tests for the `batch` CLI command group."""
from typer.testing import CliRunner

from cli import app


runner = CliRunner()


def test_batch_help():
    result = runner.invoke(app, ["batch", "--help"])
    assert result.exit_code == 0
    assert "preview" in result.output
    assert "run" in result.output
    assert "list" in result.output


def test_batch_preview_unknown_op():
    result = runner.invoke(app, ["batch", "preview", "nope", "c1"])
    # Should not crash the CLI process; may fail due to DB but help/parse OK
    assert result.exit_code in (0, 1)