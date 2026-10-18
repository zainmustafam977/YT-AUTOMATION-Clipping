from pathlib import Path
import yaml


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


def _audit_steps(data):
    """Return every dependency-audit step in ci.yml jobs, as (job, step)."""
    out = []
    for job_name, job in data.get("jobs", {}).items():
        for step in job.get("steps", []) or []:
            blob = f"{step.get('name', '')} {step.get('run', '')}".lower()
            if "pip-audit" in blob or "npm audit" in blob:
                out.append((job_name, step))
    return out


def test_dependency_audit_step_exists_and_can_fail_the_build():
    """The dependency audit must be able to fail CI.

    A previous version of this workflow ran pip-audit behind `if: false`
    (never scheduled on a pull request) *and* `|| echo ...` (exit code
    discarded) *and* `continue-on-error: true`. Any one of the three is
    enough to turn a vulnerable dependency into a green build, so all
    three are asserted absent.
    """
    data = yaml.safe_load(Path(".github/workflows/ci.yml").read_text(encoding="utf-8"))
    steps = _audit_steps(data)
    assert steps, "ci.yml has no dependency-audit step"

    for job_name, step in steps:
        assert step.get("if") != "false", (
            f"Job '{job_name}' step '{step.get('name')}' is disabled with `if: false`; "
            "the audit can never run or fail."
        )
        assert not step.get("continue-on-error"), (
            f"Job '{job_name}' step '{step.get('name')}' sets continue-on-error, "
            "so a failed audit still reports success."
        )
        run = step.get("run", "") or ""
        assert "|| true" not in run and "|| echo" not in run, (
            f"Job '{job_name}' step '{step.get('name')}' discards its own exit code "
            f"(`|| true` / `|| echo`): {run.strip()}"
        )


def test_dependency_audit_runs_on_pull_requests():
    """A schedule-only audit leaves pull requests unreviewed.

    The old workflow only ran the audit on a weekly cron, so a vulnerable
    dependency merged on Monday was not reported until the weekend.
    """
    data = yaml.safe_load(Path(".github/workflows/ci.yml").read_text(encoding="utf-8"))
    triggers = data.get(True) or data.get("on") or {}
    assert "pull_request" in triggers, (
        "ci.yml does not trigger on pull_request, so the dependency audit "
        "cannot block a vulnerable change from merging."
    )


def test_ledger_job_checks_out_full_history():
    """The ledger gate needs git history to validate cited commit ids.

    check_agent_ledger.py runs `git cat-file -t <oid>` on every commit a
    ledger row cites. actions/checkout@v4 defaults to fetch-depth: 1, which
    fetches only the tip commit, so every ancestor oid cited by a round4 row
    reports as "does not exist in this repository (never invent commit ids)" -
    a false failure that reads as a fabricated commit.

    Observed on run 39169764361: Ledger Consistency failed with exactly that
    while Dependency Audit, Ruff and mypy all passed.
    """
    data = yaml.safe_load(Path(".github/workflows/agent.yml").read_text(encoding="utf-8"))
    jobs = data.get("jobs", {})
    assert "ledger" in jobs, "agent.yml has no `ledger` job"

    checkouts = [
        step for step in jobs["ledger"].get("steps", [])
        if str(step.get("uses", "")).startswith("actions/checkout")
    ]
    assert checkouts, "the ledger job does not check out the repository"

    depth = checkouts[0].get("with", {}).get("fetch-depth", 1)
    assert depth == 0, (
        f"ledger job checks out with fetch-depth: {depth}. It must be 0, so "
        "cited commit ids resolve."
    )
