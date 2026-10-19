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


def _checkout_depth(job: dict):
    for step in job.get("steps", []) or []:
        if str(step.get("uses", "")).startswith("actions/checkout"):
            return step.get("with", {}).get("fetch-depth", 1)
    return None


def test_jobs_running_the_ledger_checker_check_out_full_history():
    """Any job that can run check_agent_ledger.py needs full git history.

    That script validates every cited commit id with `git cat-file -t <oid>`.
    actions/checkout@v4 defaults to fetch-depth: 1, which fetches only the tip
    commit, so every ancestor oid cited by a round4 row reports as
    "does not exist in this repository (never invent commit ids)" - a false
    failure that reads like a fabricated commit id.

    Two jobs reach the checker:
      * `ledger` - runs it directly. Observed on run 39169764361: Ledger
        Consistency failed with exactly that while Dependency Audit, Ruff and
        mypy all passed.
      * `pytest` - runs the whole suite, which includes
        tests/test_fresh_eyes.py::test_ledger_consistency, which shells out
        to the same script. Observed on run 39175287890: this was the single
        failure in a run where every other gate passed.
    """
    data = yaml.safe_load(Path(".github/workflows/agent.yml").read_text(encoding="utf-8"))
    jobs = data.get("jobs", {})

    for name in ("ledger", "pytest"):
        assert name in jobs, f"agent.yml has no `{name}` job"
        depth = _checkout_depth(jobs[name])
        assert depth is not None, f"the `{name}` job does not check out the repository"
        assert depth == 0, (
            f"`{name}` job checks out with fetch-depth: {depth}. It must be 0 "
            "so that cited commit ids resolve - this job runs "
            "tools/check_agent_ledger.py, either directly or via "
            "tests/test_fresh_eyes.py."
        )


# POSIX-only shell syntax. If any of these appear in a `run:` block, the step
# must pin `shell: bash`, because windows-latest defaults to PowerShell and
# will fail immediately on every one of them.
_POSIX_MARKERS = (
    "set -o pipefail",
    "PIPESTATUS",
    "2>&1",
    "tee ",
    "$((",
    "exit $",
)


def test_posix_run_steps_pin_bash_shell():
    """Steps using POSIX shell syntax must pin `shell: bash`.

    windows-latest runs `shell: pwsh` by default. In January 2026 both Windows
    matrix legs failed instantly with exit code 1 and collected no test results
    at all, because a reporting step used `set -o pipefail` and `tee`. The
    failure looked like broken tests; it was a shell dialect mismatch.

    Regression guard: any step in ci.yml whose script uses POSIX syntax must
    declare `shell: bash`.
    """
    ci_file = Path(".github/workflows/ci.yml")
    assert ci_file.exists(), "CI workflow file not found"

    data = yaml.safe_load(ci_file.read_text(encoding="utf-8"))
    offenders = []

    for job_name, job in data.get("jobs", {}).items():
        for step in job.get("steps", []) or []:
            run = step.get("run") or ""
            if not run:
                continue
            if any(marker in run for marker in _POSIX_MARKERS):
                if step.get("shell") != "bash":
                    offenders.append(
                        f"job '{job_name}' step '{step.get('name', '<unnamed>')}' "
                        f"uses POSIX syntax but shell={step.get('shell', '<default pwsh>')!r}"
                    )

    assert not offenders, (
        "Steps using POSIX shell syntax must pin `shell: bash` "
        "(windows-latest defaults to PowerShell):\n  " + "\n  ".join(offenders)
    )


def test_matrix_job_excludes_network_tests():
    """The blocking matrix must not run network-dependent tests.

    Exactly one test is marked `network`:
    test_pipeline_runner_processes_local_ingested_video. It downloads a
    faster-whisper model from the HuggingFace Hub at test time, so whether it
    passes tracks that hub's availability and rate limiting rather than the
    code under test. Left in the blocking matrix it failed intermittently on
    unrelated commits - windows Py3.10 on one run, ubuntu Py3.10 on the next,
    while the other leg passed - which is unactionable noise.

    agent.yml already excludes these by design; ci.yml did not, and ci-cd.yml
    does not. The quarantined `network-tests` job in ci.yml still runs them
    nightly and still fails the workflow when they genuinely break.

    Regression guard: removing `-m "not network"` from the matrix step
    silently reintroduces the intermittency.
    """
    ci_file = Path(".github/workflows/ci.yml")
    data = yaml.safe_load(ci_file.read_text(encoding="utf-8"))

    job = data["jobs"]["test-matrix"]
    steps = job.get("steps", []) or []

    # Strip shell comments before asserting. The run block carries a comment
    # explaining this very flag, and matching against the raw block lets that
    # comment satisfy the assertion after the flag itself is deleted - a
    # guard that goes green on the exact regression it exists to catch.
    def command_lines(script: str):
        return [
            ln for ln in script.splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]

    invocations = []
    for step in steps:
        for line in command_lines(step.get("run") or ""):
            if "pytest tests" in line:
                invocations.append((step.get("name", "<unnamed>"), line))

    assert invocations, "No pytest invocation found in the test-matrix job"

    for step_name, line in invocations:
        assert '-m "not network"' in line, (
            "The test-matrix pytest invocation must pass -m \"not network\" so "
            "the blocking matrix stays hermetic. Network-dependent tests belong "
            f"in the quarantined `network-tests` job. Step '{step_name}' ran:\n"
            f"  {line}"
        )


def test_network_tests_are_quarantined_not_dropped():
    """The network tests must still run somewhere, on a non-blocking cadence.

    Excluding them from the matrix is not allowed to become quietly deleting
    them. They must have a job that (a) selects `-m network` and (b) is gated
    to schedule or manual dispatch, so an external outage cannot block a PR
    but a genuine breakage still fails the workflow.
    """
    ci_file = Path(".github/workflows/ci.yml")
    data = yaml.safe_load(ci_file.read_text(encoding="utf-8"))

    job = data["jobs"].get("network-tests")
    assert job is not None, (
        "A `network-tests` job must exist so the network-marked tests are "
        "quarantined rather than dropped"
    )

    runs = "\n".join((s.get("run") or "") for s in job.get("steps", []) or [])
    assert "-m network" in runs, "The network-tests job must select -m network"

    gate = job.get("if") or ""
    assert "schedule" in gate or "workflow_dispatch" in gate, (
        "The network-tests job must be gated to schedule/workflow_dispatch, "
        "not run on every push"
    )


def test_ci_cd_pipeline_excludes_network_tests():
    """The CI/CD Pipeline must not run network-dependent tests either.

    Same defect class as the test-matrix job: ci-cd.yml ran `pytest -v tests/`
    unfiltered, so the HuggingFace-backed test could fail this pipeline too.
    """
    ci_file = Path(".github/workflows/ci-cd.yml")
    data = yaml.safe_load(ci_file.read_text(encoding="utf-8"))

    def command_lines(script: str):
        return [
            ln for ln in script.splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]

    invocations = []
    for job_name, job in data.get("jobs", {}).items():
        for step in job.get("steps", []) or []:
            for line in command_lines(step.get("run") or ""):
                if "pytest" in line and "tests/" in line:
                    invocations.append((job_name, step.get("name", "<unnamed>"), line))

    assert invocations, "No pytest invocation found in ci-cd.yml"

    for job_name, step_name, line in invocations:
        assert '-m "not network"' in line, (
            "The ci-cd.yml pytest invocation must pass -m \"not network\" so the "
            f"pipeline stays hermetic. Job '{job_name}' step '{step_name}' ran:\n"
            f"  {line}"
        )
