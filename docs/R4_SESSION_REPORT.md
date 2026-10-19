# R4 Session Report — YT-AUTOMATION-Clipping

**Branch:** `arena/c08e735f-yt-automation-clipping`
**Head:** `a8b7277` (DOC-10) — 40 commits on branch. All three workflows green, including all four matrix legs.
**Base:** `27a3133ed47f99481342e1f483161970f16e26be`
**Date:** 2026-10-19

> **Why this file lives in the repo:** the sandbox home directory was reset
> twice mid-session, destroying `/home/user/FINAL_REPORT_R4.md` and
> `/home/user/WINDOWS_ACCEPTANCE.fixed.md`. The repository survived both
> resets. This report and the corrected Windows procedure are therefore kept
> here, under `docs/`, so they persist.
>
> Note this is a hand-written session report. It is **not** the generated
> `docs/agent/FINAL_REPORT.md`, which is produced by `tools/gen_agent_docs.py`
> and is never hand-edited.

---

## Overall status: 8 of 9 completion criteria DONE-VERIFIED

One criterion is **BLOCKED** (Windows acceptance, needs a disposable VM), one
item is **DEFERRED** (Playwright browsers), and two items are owner decisions I
deliberately did not make unilaterally. See §7.

**The single most consequential change of this session was not a code fix.** It
was making CI failures observable at all. For four rounds the Pytest jobs were
red and unreadable: the Actions logs endpoint returns 0 bytes with the token
available here, and `gh run view --log-failed` is blocked at
`results-receiver.actions.githubusercontent.com`. Adding `::error`
workflow-command annotations (R4-17) produced the first readable failure
message, and every remaining root cause fell out of that.

---

## 1. GitHub state verification (read-only)

| Check | Result |
|---|---|
| `main` | **Untouched.** `origin/main` = `82ecaa3` |
| PR #6 | **OPEN, unmerged.** `mergeCommit = null`. Not merged, not closed, not edited. No PR to `main` created. |
| Conflict source | PR #6's head branch and `main` both contain a commit titled *"ci(gates): fail-closed dependency audit…"* with **different content and a different sha**. GitHub compares patch content, so it flags a conflict; locally the tree is clean because nothing needs resolving until someone chooses how to combine them. |
| Branch pushed | `arena/c08e735f-yt-automation-clipping` only |

**Status: CONFIRMED.**

---

## 2. CI / quality gates made meaningful

| Gate | Before | Now |
|---|---|---|
| ruff | `\|\| true` (never failed) | Explicit documented scope, **fail-closed** |
| pip-audit | schedule-only + `\|\| echo` | Runs on PR/push, `-r requirements{,-dev}.txt`, **fail-closed** |
| Failure visibility | none (logs unreadable) | `::error` annotations + job summary |
| Exit-code integrity | `tee` swallowed pytest's exit code | `set -o pipefail` throughout |

**Status: DONE-VERIFIED.**

The dependency audit is scoped with `-r` deliberately: without it, `pip-audit`
audits the *runner image's* pip and setuptools, which is noise rather than
signal about project dependencies. Test-only tooling lives in
`requirements-dev.txt` and is audited as its own file, so project dependencies
and test tooling are reported separately.

An earlier note claimed "zero check runs" on the branch. That was a
misreading — runs exist; what was missing was any *readable output* from them.

---

## 3. Ledger validation and generated docs

- `tools/check_agent_ledger.py` enforces a strict `round4` schema
  (`{id, type, title, status, evidence, tests, files, commits}`) and rejects
  placeholder evidence including `<this>`, `tbd`, `n/a`, and `-`.
- **All cited commit ids are full 40-char shas verified present in this
  repository.** One id written from memory was short and wrong; the checker
  caught it (`never invent commit ids`) and it was replaced with the real
  object. No id in the ledger is fabricated.
- The checker now detects a **shallow clone** and names the remedy
  (`git fetch --unshallow` / `fetch-depth: 0`) instead of accusing invented
  commit ids. That misdiagnosis is what made four prior rounds read as a
  ledger-integrity problem when it was a CI configuration problem.
  (This fired correctly again after the second sandbox reset left the repo
  shallow — validation that the fix works.)
- Generated docs regenerated with `tools/gen_agent_docs.py`.
  **Idempotence verified:** consecutive runs are md5-identical.
  `FINAL_REPORT.md` and `FILE_MANIFEST.md` were never hand-edited.
- Final state: **180 rows, checker `OK`.**

**Status: DONE-VERIFIED.**

---

## 4. Root causes found and fixed

All were invisible until R4-17 made failures readable.

| Row | Defect | Status |
|---|---|---|
| R4-17 | CI failures unobservable | DONE-VERIFIED |
| R4-18 | Shallow checkout broke the ledger check in **4 jobs across 3 workflows** | DONE-VERIFIED |
| R4-19 | Blank `*_API_KEYS=` entries in `.env.example` made bootstrapped installs unable to `import settings` at all | DONE-VERIFIED |
| R4-20 | Module shadowed by a same-named singleton (`hotfolder_watcher`) | DONE-VERIFIED |
| R4-21 | `tomllib` is 3.11+, breaking collection on the 3.10 legs | DONE-VERIFIED |
| R4-22 | `time.sleep(0.05)` as a cross-thread ordering guarantee | DONE-VERIFIED |
| R4-23 | POSIX/PowerShell mismatch silently broke both Windows legs | DONE-VERIFIED |
| R4-24 | All workflows green | DONE-VERIFIED |
| R4-25 | Intermittent matrix failure — HuggingFace download at test time | DONE-VERIFIED |

**R4-16 (`CI Pytest failures — root cause not identified`) is DONE-VERIFIED.**
Its network-timeout hypothesis was **wrong**; the actual cause was one test,
`tests/test_fresh_eyes.py::test_ledger_consistency`, failing because the
checkout was shallow. The hypothesis is struck out in the ledger rather than
quietly dropped.

R4-19, R4-20 and R4-21 are **pre-existing defects present at base `27a3133`**,
not regressions introduced by this branch.

Each fix is guarded by a test, and each guard was **sabotage-verified** — the
defect was reintroduced, the test was observed to fail, and the original
restored.

### The intermittent failure — root cause found and fixed (R4-25)

Two matrix legs failed **non-reproducibly**:

| Commit | Failed leg | Note |
|---|---|---|
| `23f5d97` | windows Py3.10 | ubuntu Py3.10 passed |
| `5b9754e` | ubuntu Py3.10 | windows Py3.10 passed |

Different legs, different commits, no overlap — and neither commit touches
product or test code. That pointed at an external dependency, not a regression.

**Blind spot first.** CI-4's reporting only annotated lines matching `FAILED`
or `ERROR`. When pytest exits non-zero *without* reaching per-test reporting,
nothing is emitted and the job reads as a bare *"Process completed with exit
code 1"*. With runner logs unreadable, that failure had no channel at all.
**CI-13** closed it: on a non-zero exit with no `FAILED`/`ERROR` line, annotate
the raw tail. Verified in isolation across three cases.

**Root cause (CI-14).** Exactly one test is marked `network`:
`test_pipeline_runner_processes_local_ingested_video`. It calls
`PipelineRunner.run_pipeline_for_video`, which initialises
`TranscriptionService` and **downloads a faster-whisper model from the
HuggingFace Hub at test time**. Reproduced locally:

```
Initializing TranscriptionService (model=base, device=cpu, compute_type=int8)
Failed to load faster-whisper model: Got: ConnectError: TLS/SSL connection has been closed
...we cannot find the appropriate snapshot folder ... Please check your internet connection
```

Whether it passes tracks a third party's availability and rate limiting, not
the code under test. The correlation confirmed it:

| Workflow | Network filter | Flake history |
|---|---|---|
| `agent.yml` (Agent Gates) | `-m "not network"` | **never flaked**, across every run |
| `ci.yml` (Industrial) | *none* | **failed twice** |
| `ci-cd.yml` | *none* | same exposure |

**Fix — quarantine, not deletion.** `ci.yml` and `ci-cd.yml` now pass
`-m "not network"`, matching what `agent.yml` already did by design. The
network tests are **not dropped**: a new `network-tests` job runs `-m network`
nightly (cron `17 3 * * *`) and on manual dispatch. It is a real gate that
fails the workflow when it breaks — it just runs on a cadence where a
third-party outage is distinguishable from a regression. On push runs it
correctly reports `skipped`.

Three guards added, all sabotage-verified: the matrix and `ci-cd.yml` steps
must carry the filter, and the `network-tests` job must exist and be gated.

**A guard bug worth recording:** `test_matrix_job_excludes_network_tests`
initially **passed against its own sabotage**. The explanatory comment I wrote
in the run block contains the literal `-m "not network"`, and the assertion
matched the *comment* rather than the command — a guard that goes green on the
exact regression it exists to catch. It now strips shell comment lines before
asserting, and was re-verified against two separate sabotages.

### Follow-up audit: are there other undeclared external dependencies?

Having found one test with an undeclared external dependency, I checked for
more. Two independent lines of evidence say the rest of the suite is hermetic:

1. **Empirical:** the full 1111-test filtered suite passed locally with the
   sandbox's network blocked. Any real outbound call would have failed.
2. **Static:** every hardcoded URL in `tests/` is a private/loopback/example
   address or an SSRF-protection fixture — `10.x`, `172.16.x`, `192.168.x`,
   `169.254.169.254`, `attacker.com`, `evil-youtube.com`, `notyoutube.com`,
   `hooks.example.com`, `proxy1.internal`. No live endpoints.

**Conclusion: no other test has this defect.** The one that did was already
correctly marked `network`; the bug was that CI ignored the marker.

---

## 5. Test results

### Local (complete environment)

```
1111 passed, 23 skipped, 1 deselected in 244s   exit 0
```

Command: `python3 -m pytest tests -q -m "not network" -p no:cacheprovider`

**Honest caveat: 23 tests are skipped, not executed.** 18 are browser-backed E2E
(no Playwright browser installable here) plus 5 platform/other. Green means
*nothing failed*, not *everything ran*.

Getting here required completing the environment rather than widening skips.
`ffmpeg` and `ffprobe` were both absent. No apt candidate exists; `ffprobe` came
from the `ffmpeg_binaries` wheel and `ffmpeg` from `imageio-ffmpeg`, giving a
matched static pair. **Ten earlier local failures traced entirely to the missing
`ffprobe`** — `health.py:111-124` requires both binaries for readiness, so
`/readyz` returned 503 and cascaded into quality-gate and rendering tests.
Those were environment gaps, never product defects, and no product code was
altered to accommodate them.

### CI

| Workflow | Result |
|---|---|
| Agent Gates (R3) — Pytest, Ruff Lint, mypy Ratchet, Ledger Consistency, Dependency Audit, Sabotage Checks, Scale (1k), E2E Scenarios | **8/8 success** |
| Industrial CI/CD & Reliability Gate — Security CVE Scan, Code Quality, Docker Build, Test Suite Matrix ×4 (ubuntu/windows × 3.10/3.11), Network Tests (skipped on push) | **success** |
| CI/CD Pipeline — Lint & Test Suite | **success** |

Full clean runs on `c714e8e` (CI-14), `68a23ca` (DOC-6) and `204ced5`
(DOC-8), including all four matrix legs.

### A second red run, and why it is not a regression

`cdd7632` (DOC-9, a markdown-only change to this report) produced three
failing matrix legs plus one cancelled. Investigated rather than assumed:

- **Ruled out — stale manifest.** `FILE_MANIFEST.md` lists files by name, not
  by hash, so editing a doc does not invalidate it. Confirmed directly: the
  ledger checker returns `OK` at `cdd7632`.
- **Ruled out — job timeout.** The ubuntu legs failed after ~6-7 minutes,
  well under the 15-minute limit. Only the cancelled windows Py3.10 leg ran
  the full 15.
- **Ruled out — the commit itself.** In the same run, `Agent Gates (R3)`
  **passed** on that exact commit, running the same filtered suite on
  ubuntu/Py3.11. `CI/CD Pipeline` also passed. Security, Code Quality, Docker
  and the network job all passed. Only the four matrix legs in that one
  workflow run failed.

Three legs failing simultaneously across two operating systems and two Python
versions, while an equivalent single-OS run of the same tests passes, is a
transient runner-pool problem rather than a code defect.

**CONFIRMED by re-run.** `a8b7277` (same content, re-triggered) came back
fully green — all four matrix legs plus every other job. The `cdd7632`
failure was infrastructure, not the commit.

**One transient red commit, recorded for honesty.** `df4c908` (DOC-7) failed
CI. I added two new files under `docs/` without regenerating
`docs/agent/FILE_MANIFEST.md`, which is a generated file. The ledger checker
requires it to be in sync, so `test_fresh_eyes.py::test_ledger_consistency`
failed and both Agent Gates and CI/CD Pipeline went red:

```
FAILED tests/test_fresh_eyes.py::test_ledger_consistency - AssertionError: Loading ledger ...
```

`204ced5` regenerated the manifest and everything returned to green. This is
worth recording rather than hiding: I pushed a commit in a knowingly
inconsistent state, and the gate caught it — which is precisely what the gate
is for. The branch therefore contains one red commit in its history; the head
is green.

### Other gates

| Gate | Result |
|---|---|
| `ruff check .` | All checks passed |
| `tools/check_mypy_ratchet.py` | `OK: mypy errors 0 <= baseline 0` |
| `tools/check_agent_ledger.py` | `OK`, 180 rows |
| `git diff --check` | Clean |
| `tests/test_ci_workflow_hardening.py` | 8 passed |

**Status: DONE-VERIFIED.**

---

## 6. Task 6 — `.gitignore` / `pyproject.toml` / `setup.ps1` / `setup.bat`

Scope finding first: **only `pyproject.toml` was modified on this branch.**
`.gitignore` was modified only by my SAFE-1 fix; `setup.ps1` and `setup.bat`
are untouched and identical to base `27a3133`. All were reviewed anyway.

### `pyproject.toml` — one line, and it is required

```diff
     "watchdog>=4.0.0",
+    "fonttools>=4.47.0",
 ]
```

Not gratuitous: an existing test,
`test_requirements_and_pyproject_declare_the_same_dependencies`, requires
parity between `requirements.txt` and `pyproject.toml`, and
`fonttools>=4.47.0` was already at `requirements.txt:57` (pre-existing).
**Sabotage-verified:** removing the line makes that test fail.

Note: no Python file imports `fonttools` and no installed distribution requires
it. It is an unused direct dependency in *both* files — a pre-existing
condition this change propagates rather than introduces. Removing it from both
is a separate cleanup, left alone as out of scope.

### `setup.bat` — safe

A thin launcher that detects `pwsh`, forwards arguments, and propagates the
exit code. `-ExecutionPolicy Bypass` is scoped to that one script invocation
and is what makes double-click setup work. Low risk, documented in-file.

### `setup.ps1` — reviewed; both destructive operations are guarded

| Line | Operation | Verdict |
|---|---|---|
| 315 | `Remove-Item $VENV_DIR -Recurse -Force` | **Guarded.** Runs only when `Test-VenvHealth` returns false; a healthy venv is left alone via early `return`. Targets a regenerable venv, not user data. |
| 637 | `Stop-Process -Id $pid8000 -Force` | **Guarded.** Prompts via `Read-Host "… [y/N]"` and kills only on explicit `y`/`Y`. Default is no. |

No registry writes (`HKLM`/`HKCU`/`Set-ItemProperty`) and no hardcoded
`C:\`/`D:\` deletion targets were found.

### Data-safety finding: a proprietary Microsoft font is tracked in Git

`setup.ps1:534-545` copies `C:\Windows\Fonts\seguiemj.ttf` into
`assets/fonts/seguiemj.ttf`, **which is a tracked path**. On any Windows
machine, running setup makes the tree dirty with a **12.9 MB proprietary
Microsoft font** that is not ours to redistribute.

**Pre-existing** — tracked at base `27a3133`. This branch did not add it and
has no commits touching it.

**Fix applied (SAFE-1):** an exact-path ignore rule in `.gitignore` with an
in-file comment. Verified with `git check-ignore -v --no-index` that the
pattern matches, and that the sibling `DejaVuSans*.ttf` files (which are ours
to ship) are unaffected.

Nothing depends on the bundled binary:
- `setup.ps1` re-copies it from the system Fonts directory when present.
- Tests reference the font by **name** (`DEFAULT_EMOJI_FONT`), never by this
  file — confirmed: no test reads the `.ttf`.

**Honest limitation:** because the file is *already tracked*, this rule has no
effect until it is untracked — Git does not ignore tracked paths. Purging it
from history would be a rewrite, out of scope and forbidden here. Untracking is
left to the owner: removing a tracked asset is a product decision, not a
cleanup. Exact steps in §7.

### Open question for the owner: `requires-python` vs the CI matrix

`pyproject.toml` declares `requires-python = ">=3.11"`, but `ci.yml`'s
`test-matrix` runs `["3.10", "3.11"]`. **Pre-existing, not introduced here.**

Both legs pass, so this is harmless and strictly additive coverage: we test one
interpreter more than we claim to support. I deliberately did **not** change
it — dropping 3.10 removes working coverage, and changing `requires-python` to
`>=3.10` would claim support that is neither declared nor verified. Two
reversible options, owner's call:

- **Drop `"3.10"` from the matrix** — aligns CI with the declared floor, halves
  matrix time. Cost: loses the early warning that caught R4-21.
- **Set `requires-python = ">=3.10"`** — commits to supporting 3.10.

---

## 7. Completion criteria

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | Branch clean except documented untracked artifacts | **DONE-VERIFIED** | `git status --short` empty |
| 2 | CI runs with actual successful results | **DONE-VERIFIED** | Full clean runs on `c714e8e`, `68a23ca`, `204ced5`, `a8b7277`: 7/7, 8/8, 1/1 — §5 (one genuine red commit and one infra-only red run, both disclosed) |
| 3 | ruff + dep-audit fail-closed | **DONE-VERIFIED** | §2; both sabotage-verified |
| 4 | Ledger validation strict, docs reproducible | **DONE-VERIFIED** | 180 rows OK; md5-identical regeneration |
| 5 | Stable collection, fresh reproducible results | **DONE-VERIFIED** | 1111 passed / 23 skipped / 1 deselected; 23 skips declared |
| 6 | Windows procedure safe and honestly labeled | **BLOCKED** | below |
| 7 | No secrets / protected artifacts staged | **DONE-VERIFIED** | §9 |
| 8 | `git diff --check` clean, accurate report | **DONE-VERIFIED** | Clean; caveats stated inline |
| 9 | `main` untouched, PR #6 unmerged | **DONE-VERIFIED** | §1 |

### BLOCKED — Windows acceptance

The corrected procedure cannot be executed here: it requires a **disposable
Windows VM**. The standing instruction is explicit — never run the existing
procedure on the everyday machine. It was reviewed and corrected but never run,
so it is **unverified by execution**. See `docs/WINDOWS_ACCEPTANCE.corrected.md`.

Corrections (from review, **not** from a successful run):

| # | Hazard | Correction |
|---|---|---|
| 1 | `HKLM` registry writes | Scope to `HKCU`, or state the elevation requirement explicitly and prompt |
| 2 | Unconditional `Stop-Process` | Prompt first, default **no** |
| 3 | Deleting `D:\Program Files` | Never delete a drive-root path; remove only the specific install directory, after confirming it is not the system Program Files |
| 4 | §9.3 job-ID-vs-PID bug | Stop treating a batch job id as a process id; resolve the PID explicitly |

**Manual steps for the owner:** provision a disposable Windows 10/11 VM,
snapshot it, run the corrected procedure there, revert the snapshot afterwards.
Do not run it on the everyday machine.

### DEFERRED — Playwright browser download

18 E2E tests remain skipped: no browser binary could be downloaded in this
sandbox. Three acquisition routes were attempted and exhausted (the 3-approach
limit); the last safe state was preserved and the tests were made to **skip
cleanly** rather than fail, so they misreport nothing.

**Manual steps:** run `playwright install chromium` on a machine with
unrestricted network access, then run the suite without `-m "not network"` to
exercise them.

### Owner decision — untracking `seguiemj.ttf`

```bash
git rm --cached assets/fonts/seguiemj.ttf   # stops tracking; file stays on disk
git commit -m "chore: stop tracking the bundled Windows emoji font"
```

The `.gitignore` rule from SAFE-1 then takes effect and prevents re-adding.
This removes 12.9 MB from the index; the blob stays in history until a history
rewrite, which is out of scope.

### Owner decision — `requires-python` vs the 3.10 matrix

See §6. Both options are reversible.

---

## 8. What I did not do

- Did not touch, push to, or merge into `main`.
- Did not merge or close PR #6, and created no new PR to `main`.
- Did not resolve PR #6's conflicts, rebase onto `main`, or graft histories.
- Did not use `--no-verify`, disable CI, or add blanket suppression.
- Did not invent a commit id, an API, or a test result.
- Did not weaken a check to make it pass.
- Did not run the Windows acceptance procedure on this machine.
- Did not claim the 23 skipped tests were executed.
- Did not silently drop the network test — it was quarantined to a nightly job
  that still fails the workflow when it genuinely breaks.

---

## 9. Secrets

No secrets are staged or committed. `.env` is covered by `.gitignore` and no
`.env` file is tracked. `storage/api_key_usage.json`, `storage/quota_state.json`
and `storage/*cookies*.txt` are all ignored. Nothing in this session required a
*"ROTATE THIS SECRET"* item — the credential defect found earlier (SEC-1,
shipping usable admin credentials in source) is already fixed and committed at
`fa06d19`.

---

## 10. Environment notes and sandbox resets

The sandbox home directory was reset **twice** mid-session. Effects and
handling:

1. **First reset** destroyed `/home/user/.venv` (Python environment) and both
   hand-written deliverables. The repository survived.
2. **Second reset** rewound the local branch pointer to base `27a3133` while
   leaving the working tree intact, and left the repo shallow.

**Repair performed (no forbidden git operations):** verified the working tree
was byte-identical to `68a23ca` (file fingerprint `21ebbad1…` matched
before/after), then `git update-ref` to move the branch pointer and
`git read-tree HEAD` to refresh the index. **No file was modified and no data
was lost.** `git reset`, `git clean`, `git checkout --` and `git restore` were
not used.

The repo was also left shallow by the reset; `git fetch --unshallow` restored
full history, which the ledger checker then confirmed (`OK`, 180 rows). The
checker's shallow-clone message fired correctly beforehand — incidental
validation of the `f52483c` fix.

Deliverables were moved into `docs/` so they survive further resets.

---

## 11. Commit list (this session, oldest → newest)

```
fa06d19  SEC-1   stop shipping usable admin credentials in source
2a86310  CI-1    fail-closed dependency audit + in-memory DB isolation
47e462d  TEST-1  guard Windows-only tests
a2f9549  FIX-1   three product defects found while repairing the suite
8fe3049  TEST-2  correct five tests asserting the wrong contract
9b15a73  DOC-1   11 round4 ledger rows
2991cb7  CI-2    give the ledger gate the git history it needs
45574d3  TEST-3  E2E skips instead of failing; two stale tests fixed
bdedb12  DOC-2   ledger rows for the final fixes
08f0b98  TEST-4  dry-run test must opt into the preview flag
4ed5abb  DOC-3   record the unresolved CI failure and dependency drift
1ab9a44  CI-3    publish the failure list to the job summary
0b69355  CI-4    surface failures as queryable annotations   <-- the unlock
34e3727  CI-5    fix the root cause of the CI Pytest failure
b38a689  CI-6    surface matrix failures as annotations
1c8a41d  FIX-2   bootstrapped installs could not import settings
cf3ab73  CI-7    full history for the matrix job + hermetic password tests
842d735  FIX-3   tomllib is 3.11+
4066c14  CI-8    run the summary step under bash
28f13f8  CI-9    pin the pytest step to bash
6651c3f  FIX-4   patch the module object, not a dotted string
d644306  TEST-5  deterministic concurrency test
9203659  CI-10   surface CI/CD Pipeline failures as annotations
0a112c6  CI-11   full history for the CI/CD Pipeline job
f52483c  LEDGER  say "shallow clone", not "invented commit ids"
90d585c  CI-12   fold reporting into the pytest step (Windows)
f97c180  TEST-6  guard the POSIX shell pin
23f5d97  DOC-4   eight root-cause rows; R4-16 resolved
5b9754e  SAFE-1  never commit the bundled Segoe UI Emoji font
7d433f7  CI-13   annotate the blind spot (non-zero exit, no FAILED line)
dc5fa60  DOC-5   record the intermittent failure and the reporting gap
c714e8e  CI-14   quarantine the network test; fix the intermittent matrix leg
68a23ca  DOC-6   record the root cause (R4-25 DONE-VERIFIED)
df4c908  DOC-7   move the report + corrected Windows procedure into docs/
204ced5  DOC-8   regenerate FILE_MANIFEST (fixes the red DOC-7 commit)
cdd7632  DOC-9   disclose the transient red DOC-7 commit
a8b7277  DOC-10  record the cdd7632 investigation; re-trigger CI
```

40 commits total on the branch. All pushed to
`arena/c08e735f-yt-automation-clipping` only.
