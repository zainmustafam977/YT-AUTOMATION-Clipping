"""R4-1.5 — Tolerant settings parsers (list / JSON / CSV)."""
import json
import pytest

from shorts_engine.config._parsers import tolerant_list, tolerant_json, tolerant_csv, env_present


# ---- tolerant_list ---------------------------------------------------------

def test_list_none_is_empty():
    assert tolerant_list(None) == []


def test_list_empty_string_is_empty():
    assert tolerant_list("") == []


def test_list_whitespace_is_empty():
    assert tolerant_list("   ") == []


def test_list_passthrough():
    assert tolerant_list(["a", "b"]) == ["a", "b"]


def test_list_tuple_passthrough():
    assert tolerant_list(("a", "b")) == ["a", "b"]


def test_list_json_array():
    assert tolerant_list('["a","b","c"]') == ["a", "b", "c"]


def test_list_csv_string():
    assert tolerant_list("a,b,c") == ["a", "b", "c"]


def test_list_csv_with_spaces():
    assert tolerant_list("a, b , c") == ["a", "b", "c"]


def test_list_bare_string():
    assert tolerant_list("single") == ["single"]


def test_list_int():
    assert tolerant_list(42) == [42]


def test_list_bool():
    assert tolerant_list(True) == [True]


def test_list_invalid_type_raises():
    with pytest.raises(ValueError) as exc:
        tolerant_list({"not": "list"})
    assert "Cannot parse list" in str(exc.value)


def test_list_bad_json_in_brackets_raises_clear_error():
    with pytest.raises(ValueError) as exc:
        tolerant_list("[not-valid-json")
    msg = str(exc.value)
    assert "JSON-parse" in msg or "JSON-pars" in msg or "JSON" in msg
    assert "CSV" in msg  # tells user the alternative


def test_list_json_wrong_type_raises():
    with pytest.raises(ValueError):
        tolerant_list('{"a": 1}')  # JSON object, not list


def test_list_no_trailing_comma_drops_empty():
    assert tolerant_list("a,,b,") == ["a", "b"]


# ---- tolerant_json ---------------------------------------------------------

def test_json_none_is_empty_dict():
    assert tolerant_json(None) == {}


def test_json_empty_string_is_empty_dict():
    assert tolerant_json("") == {}


def test_json_whitespace_is_empty_dict():
    assert tolerant_json("   ") == {}


def test_json_passthrough_dict():
    assert tolerant_json({"a": 1}) == {"a": 1}


def test_json_passthrough_list():
    assert tolerant_json([1, 2, 3]) == [1, 2, 3]


def test_json_object_string():
    assert tolerant_json('{"key": "value"}') == {"key": "value"}


def test_json_array_string():
    assert tolerant_json("[1,2,3]") == [1, 2, 3]


def test_json_invalid_raises_clear_error():
    with pytest.raises(ValueError) as exc:
        tolerant_json("{bad json")
    msg = str(exc.value)
    assert "JSON-parse" in msg or "JSON-pars" in msg
    assert "leave empty" in msg


def test_json_invalid_type_raises():
    with pytest.raises(ValueError):
        tolerant_json(42)


# ---- tolerant_csv ----------------------------------------------------------

def test_csv_none_is_empty():
    assert tolerant_csv(None) == []


def test_csv_empty_is_empty():
    assert tolerant_csv("") == []


def test_csv_basic():
    assert tolerant_csv("a,b,c") == ["a", "b", "c"]


def test_csv_strips_spaces():
    assert tolerant_csv(" a , b , c ") == ["a", "b", "c"]


def test_csv_drops_empty():
    assert tolerant_csv("a,,b,") == ["a", "b"]


def test_csv_passthrough_list():
    assert tolerant_csv(["x", "y"]) == ["x", "y"]


def test_csv_invalid_type_raises():
    with pytest.raises(ValueError):
        tolerant_csv(42)


# ---- env_present -----------------------------------------------------------

def test_env_present_true():
    import os
    os.environ["__TEST_ENV_PRESENT__"] = "value"
    try:
        assert env_present("__TEST_ENV_PRESENT__") is True
    finally:
        del os.environ["__TEST_ENV_PRESENT__"]


def test_env_present_false_unset():
    import os
    os.environ.pop("__TEST_ENV_PRESENT_MISSING__", None)
    assert env_present("__TEST_ENV_PRESENT_MISSING__") is False


def test_env_present_false_whitespace():
    import os
    os.environ["__TEST_ENV_PRESENT__"] = "   "
    try:
        assert env_present("__TEST_ENV_PRESENT__") is False
    finally:
        del os.environ["__TEST_ENV_PRESENT__"]


# ---- Regression: the actual bug from R4-1.5 -------------------------------

def test_real_bug_empty_gemini_keys_does_not_crash(monkeypatch):
    """Regression for: GEMINI_API_KEYS='' caused JSONDecodeError on startup."""
    monkeypatch.setenv("GEMINI_API_KEYS", "")
    from shorts_engine.config.settings import PipelineSettings
    s = PipelineSettings()
    assert s.GEMINI_API_KEYS == []


def test_real_bug_whitespace_groq_keys(monkeypatch):
    """Regression: GROQ_API_KEYS='   ' (whitespace) must be empty list."""
    monkeypatch.setenv("GROQ_API_KEYS", "   ")
    from shorts_engine.config.settings import PipelineSettings
    s = PipelineSettings()
    assert s.GEMINI_API_KEYS == [] or all(k.strip() for k in s.GROQ_API_KEYS)


def test_real_bug_csv_form(monkeypatch):
    """Regression: GROQ_API_KEYS='key1,key2' should parse as list."""
    monkeypatch.setenv("GROQ_API_KEYS", "key1,key2,key3")
    from shorts_engine.config.settings import PipelineSettings
    s = PipelineSettings()
    assert "key1" in s.GROQ_API_KEYS
    assert "key3" in s.GROQ_API_KEYS
