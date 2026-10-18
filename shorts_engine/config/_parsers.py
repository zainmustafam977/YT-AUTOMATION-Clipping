"""R4-1.5 — Tolerant parsers for env-style list / JSON / CSV settings.

Pydantic-settings v2 JSON-decodes list/dict fields by default, so an empty
``GEMINI_API_KEYS=`` (or whitespace, or CSV) raises a JSON error. These
helpers normalize such inputs to ``[]`` / ``{}`` cleanly, or raise a clear,
actionable error.

Use::

    from shorts_engine.config._parsers import (
        tolerant_list, tolerant_json, tolerant_csv, TolerantEnvSource,
    )

    class S(BaseSettings):
        model_config = SettingsConfigDict(...)
        @classmethod
        def settings_customise_sources(cls, settings_cls, init_settings,
                                       env_settings, dotenv_settings,
                                       file_secret_settings):
            return (init_settings, TolerantEnvSource(env_settings),
                    dotenv_settings, file_secret_settings)
"""
from __future__ import annotations

import json
import os
from typing import Any, List


# The set of field names that should be parsed as lists.
LIST_FIELDS = frozenset({
    "GEMINI_API_KEYS", "GROQ_API_KEYS", "OPENAI_API_KEYS", "OPENROUTER_API_KEYS",
    "PUBLISH_SLOT_TIMES_LOCAL", "INGESTION_PROXY_POOL",
})

# The set of field names that should be parsed as JSON objects.
JSON_FIELDS = frozenset({
    "LLM_MODEL_PRICING",
})


def tolerant_list(value: Any) -> List[Any]:
    """Parse a list setting tolerantly.

    Accepts:
      * already-list value (returned unchanged)
      * None / empty string / whitespace -> []
      * JSON array string ('["a","b"]') -> list
      * JSON object string ('{"a":1}') -> raises (must be a list)
      * CSV string ('a,b,c') -> ['a','b','c']
      * Bare string ('a') -> ['a']
      * anything else -> raises ValueError with a clear message
    """
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, (int, float, bool)):
        return [value]
    if not isinstance(value, str):
        raise ValueError(
            f"Cannot parse list value of type {type(value).__name__}: {value!r}"
        )
    s = value.strip()
    if not s:
        return []
    # Try JSON first if it looks like JSON (any bracket/brace).
    if s[0] in "[{":
        try:
            parsed = json.loads(s)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Failed to JSON-parse list setting: {value!r} — {e}. "
                "Use a JSON array, CSV ('a,b,c'), or leave empty."
            ) from e
        if not isinstance(parsed, list):
            raise ValueError(
                f"List setting parsed as {type(parsed).__name__}, not a list: {value!r}"
            )
        return parsed
    # CSV fallback.
    parts = [p.strip() for p in s.split(",") if p.strip()]
    return parts


def tolerant_json(value: Any) -> Any:
    """Parse a JSON-typed setting tolerantly.

    Accepts:
      * already-dict/list value (returned unchanged)
      * None / empty string / whitespace -> {}
      * JSON object/array string
    """
    if value is None:
        return {}
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        raise ValueError(
            f"Cannot parse JSON value of type {type(value).__name__}: {value!r}"
        )
    s = value.strip()
    if not s:
        return {}
    try:
        return json.loads(s)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Failed to JSON-parse setting: {value!r} — {e}. "
            "Use a JSON object/array, or leave empty."
        ) from e


def tolerant_csv(value: Any) -> List[str]:
    """Parse a comma-separated string setting.

    Accepts:
      * None / empty / whitespace -> []
      * 'a,b,c' -> ['a','b','c']
      * already-list -> returned unchanged
    """
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x) for x in value]
    if not isinstance(value, str):
        raise ValueError(
            f"Cannot parse CSV value of type {type(value).__name__}: {value!r}"
        )
    s = value.strip()
    if not s:
        return []
    return [p.strip() for p in s.split(",") if p.strip()]


def env_present(name: str) -> bool:
    """Returns True if env var is set and not just whitespace."""
    return bool(os.environ.get(name, "").strip())


# ---------------------------------------------------------------------------
# Custom EnvSource that pre-cleans list / JSON values BEFORE pydantic-settings
# tries to JSON-decode them. This is the only way to fix empty-string
# ``GEMINI_API_KEYS=`` without subclassing pydantic-settings' internals.
# ---------------------------------------------------------------------------

try:
    from pydantic_settings.sources import EnvSettingsSource
    _BASE_SOURCE = EnvSettingsSource
except ImportError:  # pragma: no cover
    try:
        from pydantic_settings.sources.providers.env import EnvSettingsSource as _Base  # type: ignore
        _BASE_SOURCE = _Base
    except ImportError:  # pragma: no cover
        _BASE_SOURCE = object


class TolerantEnvSource(_BASE_SOURCE):  # type: ignore[misc, valid-type]
    """An EnvSettingsSource that cleans known list / JSON fields first.

    Pydantic-settings' default source JSON-decodes list / dict fields. This
    subclass overrides :meth:`prepare_field_value` so that, for fields listed
    in :data:`LIST_FIELDS` and :data:`JSON_FIELDS`, empty / whitespace / CSV
    inputs are normalized to ``[]`` / ``{}`` cleanly instead of raising
    JSONDecodeError.
    """

    def __init__(self, settings_cls=None, *, list_fields=LIST_FIELDS, json_fields=JSON_FIELDS, **kwargs):
        self._list_fields = set(list_fields)
        self._json_fields = set(json_fields)
        if settings_cls is not None:
            try:
                super().__init__(settings_cls, **kwargs)
            except TypeError:
                try:
                    super().__init__(settings_cls)  # type: ignore[call-arg]
                except Exception:
                    pass

    def prepare_field_value(self, field_name, field, field_value, value_is_complex):  # type: ignore[override]
        # For known list fields, intercept strings before the parent tries to
        # JSON-decode them. This handles empty, whitespace, CSV, and JSON-array
        # forms.
        if field_name in self._list_fields and isinstance(field_value, str):
            return tolerant_list(field_value)  # type: ignore[return-value]
        if field_name in self._json_fields and isinstance(field_value, str):
            return tolerant_json(field_value)  # type: ignore[return-value]
        return super().prepare_field_value(field_name, field, field_value, value_is_complex)

    def __repr__(self) -> str:
        return "TolerantEnvSource(list_fields=%d, json_fields=%d)" % (
            len(self._list_fields), len(self._json_fields),
        )

