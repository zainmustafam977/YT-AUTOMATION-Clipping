"""List/mapping settings must survive the formats a `.env` actually contains.

Regression cover for a crash at import time: pydantic-settings JSON-decodes
complex fields before validation, so a `.env` copied from `.env.example` (whose
API key pools are blank) raised ``SettingsError`` and took down every module
that imports settings -- the whole test suite among them. The documented
comma- and newline-separated forms failed the same way.
"""

import pytest

from shorts_engine.config.settings import (
    PipelineSettings,
    parse_env_list,
    parse_env_mapping,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, []),
        ("", []),
        ("   ", []),
        ("\n\t\n", []),
        ("single", ["single"]),
        ("  padded  ", ["padded"]),
        ("a,b", ["a", "b"]),
        ("a, b ,c", ["a", "b", "c"]),
        ("a,b,", ["a", "b"]),
        ("a\nb", ["a", "b"]),
        ("a\r\nb", ["a", "b"]),
        ('["a","b"]', ["a", "b"]),
        ('["a", "b"]', ["a", "b"]),
        ('["a", ""]', ["a"]),
        (["a", " b "], ["a", "b"]),
        (("a", "b"), ["a", "b"]),
    ],
)
def test_parse_env_list_formats(raw, expected):
    assert parse_env_list(raw) == expected


def test_parse_env_mapping_accepts_json_and_mappings():
    assert parse_env_mapping('{"groq": {"m": {"input": 1.0}}}') == {
        "groq": {"m": {"input": 1.0}}
    }
    mapping = {"gemini": {"m": {"input": 0.1, "output": 0.2}}}
    assert parse_env_mapping(mapping) == mapping
    assert parse_env_mapping(None) is None


def test_parse_env_mapping_rejects_empty_and_invalid():
    # An empty table would silently zero out spend accounting, so fail loudly.
    with pytest.raises(ValueError):
        parse_env_mapping("")
    with pytest.raises(ValueError):
        parse_env_mapping("not json")


@pytest.mark.parametrize(
    "field",
    [
        "GEMINI_API_KEYS",
        "GROQ_API_KEYS",
        "OPENROUTER_API_KEYS",
        "OPENAI_API_KEYS",
        "PUBLISH_SLOT_TIMES_LOCAL",
        "INGESTION_PROXY_POOL",
    ],
)
def test_blank_env_value_yields_empty_list(monkeypatch, field):
    monkeypatch.setenv(field, "")
    assert getattr(PipelineSettings(_env_file=None), field) == []


def test_env_key_pool_accepts_comma_separated(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEYS", "key-1,key-2")
    assert PipelineSettings(_env_file=None).GEMINI_API_KEYS == ["key-1", "key-2"]


def test_env_key_pool_accepts_newline_separated(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEYS", "key-1\nkey-2")
    assert PipelineSettings(_env_file=None).GROQ_API_KEYS == ["key-1", "key-2"]


def test_env_key_pool_still_accepts_json_array(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEYS", '["key-1", "key-2"]')
    assert PipelineSettings(_env_file=None).OPENROUTER_API_KEYS == ["key-1", "key-2"]


def test_env_pricing_json_object(monkeypatch):
    monkeypatch.setenv(
        "LLM_MODEL_PRICING", '{"groq": {"model": {"input": 0.1, "output": 0.2}}}'
    )
    pricing = PipelineSettings(_env_file=None).LLM_MODEL_PRICING
    assert pricing["groq"]["model"] == {"input": 0.1, "output": 0.2}


def test_env_without_key_pools_uses_defaults(monkeypatch):
    for field in ("GEMINI_API_KEYS", "GROQ_API_KEYS", "OPENROUTER_API_KEYS", "OPENAI_API_KEYS"):
        monkeypatch.delenv(field, raising=False)
    settings = PipelineSettings(_env_file=None)
    assert settings.GEMINI_API_KEYS == []
    assert settings.PUBLISH_SLOT_TIMES_LOCAL == ["10:00", "18:00"]
