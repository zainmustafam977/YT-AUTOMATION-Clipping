import os
import json
import time
import pytest
from unittest.mock import patch

def test_fs_is_strict_leaf_module():
    """Verify that shorts_engine.utils.fs imports only standard library, zero project imports."""
    import shorts_engine.utils.fs as fs_mod

    # Inspect module globals and imported modules
    for name, val in fs_mod.__dict__.items():
        if hasattr(val, "__module__") and val.__module__:
            assert not val.__module__.startswith("shorts_engine.config"), f"Illegal import from config: {name}"
            assert not val.__module__.startswith("shorts_engine.services"), f"Illegal import from services: {name}"
            assert not val.__module__.startswith("shorts_engine.db"), f"Illegal import from db: {name}"

def test_atomic_write_json_success(tmp_path):
    from shorts_engine.utils.fs import atomic_write_json

    target = tmp_path / "subdir" / "state.json"
    data = {"status": "ok", "count": 42, "items": ["a", "b"]}
    atomic_write_json(target, data)

    assert target.exists()
    with open(target, "r", encoding="utf-8") as f:
        loaded = json.load(f)
    assert loaded == data

def test_atomic_write_json_cleanup_on_error(tmp_path):
    from shorts_engine.utils.fs import atomic_write_json

    target = tmp_path / "bad.json"
    # Object that cannot be serialized to JSON
    bad_data = {"unserializable": object()}

    with pytest.raises(TypeError):
        atomic_write_json(target, bad_data)

    assert not target.exists()
    # Check that no leftover temp files exist in tmp_path
    temp_files = list(tmp_path.glob(".*.tmp.*"))
    assert len(temp_files) == 0

def test_atomic_replace_retries_on_permission_error(tmp_path):
    from shorts_engine.utils.fs import atomic_replace

    src = tmp_path / "src.tmp"
    dst = tmp_path / "dst.json"
    src.write_text("test")

    attempt_count = 0
    original_replace = os.replace

    def mock_replace(s, d):
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count < 3:
            raise PermissionError("Access is denied (simulated Windows lock)")
        return original_replace(s, d)

    with patch("os.replace", side_effect=mock_replace):
        atomic_replace(src, dst, max_retries=5)

    assert attempt_count == 3
    assert dst.exists()
    assert dst.read_text() == "test"

def test_atomic_replace_raises_immediately_on_non_permission_error(tmp_path):
    from shorts_engine.utils.fs import atomic_replace

    src = tmp_path / "src.tmp"
    dst = tmp_path / "dst.json"
    src.write_text("test")

    with patch("os.replace", side_effect=FileNotFoundError("Simulated missing file")):
        with pytest.raises(FileNotFoundError):
            atomic_replace(src, dst, max_retries=5)

def test_clean_temp_files(tmp_path):
    from shorts_engine.utils.fs import clean_temp_files

    old_tmp = tmp_path / ".test.tmp.123"
    old_tmp.write_text("old")
    new_tmp = tmp_path / ".test.tmp.456"
    new_tmp.write_text("new")

    # Set old_tmp mtime to 2 hours ago
    two_hours_ago = time.time() - 7200
    os.utime(old_tmp, (two_hours_ago, two_hours_ago))

    removed = clean_temp_files(tmp_path, max_age_sec=3600)
    assert removed == 1
    assert not old_tmp.exists()
    assert new_tmp.exists()
