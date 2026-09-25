# Assessment Cards: pyproject.toml

---

## Unit 1: Build System Definition
| Field | Content |
|---|---|
| Location | `pyproject.toml:1-3` |
| What it does | Defines PEP 518/621 build configuration using `setuptools>=61.0` as build-backend. |
| Industrial-grade? | **A** - Follows modern Python packaging standards (PEP 517/518). |
| Checklist | **Error handling:** N/A (Declarative). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass (Deterministic build system declaration). **Input validation:** Pass (Valid TOML schema). **Resource cleanup:** N/A. **Concurrency safety:** N/A. **Atomicity:** Pass. **External-dependency:** Pass (Standard `setuptools.build_meta`). **Security:** Pass (No arbitrary code execution during metadata resolution). **Performance:** Pass. **Observability:** N/A. **Config:** Pass. **Testability:** Pass (Installs cleanly via pip/uv). **Readability:** Pass. |
| Weakness or risk | None identified for build-system specification. |
| Improvement | Keep as is; standards-compliant. |
| Better approach? | Option 1: Keep `setuptools` (Recommended - widely compatible). Option 2: Migrate to `hatchling` or `flit` (Lighter-weight, but requires build backend migration with zero operational advantage). |
| Priority / Effort | P3 / S |

---

## Unit 2: Core Project Metadata & Dependencies
| Field | Content |
|---|---|
| Location | `pyproject.toml:5-38` |
| What it does | Declares package name, version, Python 3.11+ requirement, and runtime dependencies. |
| Industrial-grade? | **B** - Mostly modern, but missing upper bounds on several fast-moving libraries (`google-genai`, `yt-dlp`, `opencv`), and missing explicit pins for newly introduced audio/A-B testing packages (`groq`, `scipy`). |
| Checklist | **Error handling:** N/A. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** N/A. **Atomicity:** Pass. **External-dependency:** Fail (Missing `groq` and `scipy` in `pyproject.toml` dependencies, although present in `requirements.txt`). **Security:** Pass (No vulnerable dependencies flagged). **Performance:** Pass. **Observability:** Pass (`sentry-sdk` included). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | Discrepancy between `requirements.txt` and `pyproject.toml`: if installed via `pip install .`, `groq` and `scipy` would not be installed, causing runtime `ImportError` on Groq LPU router or Bayesian optimizer. |
| Improvement | Add `"groq>=0.4.0"` and `"scipy>=1.11.0"` to `project.dependencies` in `pyproject.toml`. |
| Better approach? | Option 1: Synchronize `pyproject.toml` dependencies with `requirements.txt` and treat `pyproject.toml` as single source of truth (Recommended). Option 2: Use `pip-compile` / `uv pip compile` to generate deterministic lockfile from `pyproject.toml`. |
| Priority / Effort | P1 / S |

---

## Unit 3: Optional Dev Dependencies
| Field | Content |
|---|---|
| Location | `pyproject.toml:40-46` |
| What it does | Declares developer test and linting tooling (`pytest`, `pytest-asyncio`, `ruff`, `mypy`). |
| Industrial-grade? | **A** - Clean, minimal dev dependencies covering unit testing, async testing, static analysis, and typing. |
| Checklist | **Error handling:** N/A. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** N/A. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** N/A. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | `pytest-cov` is not declared under dev dependencies, preventing automated coverage metrics in CI. |
| Improvement | Add `"pytest-cov>=4.1.0"` to `project.optional-dependencies.dev`. |
| Better approach? | Option 1: Add `pytest-cov` (Recommended). Option 2: Keep as is. |
| Priority / Effort | P2 / S |

---

## Unit 4: Tooling Config Blocks (Pytest & Ruff)
| Field | Content |
|---|---|
| Location | `pyproject.toml:48-54` |
| What it does | Sets test discovery paths for pytest and line-length (100) + target Python version (py311) for Ruff linter. |
| Industrial-grade? | **A** - Standard, centralized tool configuration. |
| Checklist | **Error handling:** N/A. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** N/A. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** N/A. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. Pytest discovers all `test_*.py` in `tests/`, and Ruff enforces Python 3.11 rules. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Keep as is (Recommended). Option 2: Add explicit `asyncio_mode = "auto"` to `[tool.pytest.ini_options]` to avoid future deprecation warnings in async test fixtures. |
| Priority / Effort | P3 / S |
