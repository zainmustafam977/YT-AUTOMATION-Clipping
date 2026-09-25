from pathlib import Path
import yaml
import pytest


def test_ci_workflow_has_timeout_minutes_on_all_jobs():
    """All jobs in .github/workflows/ci.yml must enforce timeout-minutes <= 15 to prevent hung runners."""
    ci_file = Path(".github/workflows/ci.yml")
    assert ci_file.exists(), "CI workflow file not found"

    content = ci_file.read_text(encoding="utf-8")
    data = yaml.safe_load(content)

    assert "jobs" in data, "No jobs defined in CI workflow"
    jobs = data["jobs"]
    assert len(jobs) > 0

    for job_name, job_config in jobs.items():
        assert "timeout-minutes" in job_config, f"Job '{job_name}' is missing timeout-minutes"
        assert job_config["timeout-minutes"] <= 15, f"Job '{job_name}' timeout-minutes must be <= 15"
