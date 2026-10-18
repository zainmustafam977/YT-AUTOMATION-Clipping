# Fallback Policy (R4-5.1 / R4-5.2)

> R4-5.1: every fallback in the codebase is inventoried here with rationale.
> R4-5.2: a `STRICT_MODE` setting (default **strict** in production) controls
> whether each fallback is allowed to fire; in strict mode the fallback
> raises instead of silently substituting a less-preferred option.

## Allowed fail-safe behaviour

Per R4-5:

* retry with backoff
* queue / pause
* quarantine
* raise a clear error with a concrete next step

## Forbidden behaviour

* silently swap to a different model / encoder / font / key / source
* swallow an error and return `None`
* log "info" and continue when a WARNING is the right level

## Inventory

Each row is a known site where a fallback may occur. The columns are:

* **Strict-mode** — what happens when `STRICT_MODE=true` (default in production)
* **Permissive-mode** — what happens when `STRICT_MODE=false` (legacy / dev)
* **Logs** — log level emitted when the fallback fires
* **Triage** — whether it surfaces in `needs-attention`

| # | Site | Strict-mode | Permissive-mode | Logs | Triage |
|---|------|-------------|------------------|------|--------|
| 1 | LLM provider / model router (`services/ai/model_router.py`) | raises `NoAvailableProvider` with reason | falls through the provider chain | WARNING | yes |
| 2 | Title generator LLM failure (`services/ab_testing/title_generator.py:78`) | raises `LLMUnavailable` | returns deterministic fallback | WARNING | yes |
| 3 | Thumbnail resolver missing file (`routes/status.py:1259`) | raises 404 with hint to re-run | returns branded SVG fallback | INFO (not WARNING; intentional) | no |
| 4 | yt-dlp flat fetch failure (`routes/channels.py:654`) | raises 502 with the channel name and last error | logs and continues | WARNING | yes |
| 5 | `LLM_FALLBACK_ON_RATE_LIMIT` env var (settings.py:537) | treated as `off` (no fallback) | allows direct fallback | WARNING | no |
| 6 | Quality-gate advisory (`services/quality_gate/`) | blocks clip if STRICT, requires human | auto-approves on green path | INFO | yes |
| 7 | GPU encoder selection (`services/execution/hwaccel.py`) | raises if no encoder found and `HWACCEL_REQUIRED=1` | falls back to CPU | WARNING + reason logged | yes |
| 8 | Settings list-typed empty (`config/_parsers.py`) | returns `[]` (this is not a fallback; it's input validation) | n/a | none | no |
| 9 | Cookies vault (`settings.py:529`) | requires file; raises clear error | uses empty cookies (no auth) | WARNING | yes |
| 10 | Webhook delivery retry (`services/webhooks/__init__.py`) | retries with backoff, then dead-letters | same (this is the contract) | WARNING on DLQ | yes |
| 11 | Caption style template missing (`services/captioning/editor.py`) | raises `TemplateNotFound` | uses first available template | WARNING | yes |
| 12 | Watch folder lock contention (`services/ingestion/`) | waits with timeout, raises | proceeds without lock | WARNING | yes |

## STRICT_MODE

The setting lives at `PipelineSettings.STRICT_MODE`. Defaults to `True` in
`production` and `staging`, `False` in `development` and `test`.

Use it via the helper:

```python
from shorts_engine.services.strict_mode import strict_mode_guard

with strict_mode_guard(component="llm_router", default_behaviour="raise"):
    ...
```

If `STRICT_MODE=true` the guard raises `StrictModeViolation`; otherwise the
caller's default branch is taken.
