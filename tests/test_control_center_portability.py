import os
from pathlib import Path


def test_control_center_scripts_contain_zero_hardcoded_drive_paths():
    """All Control Center batch scripts must use relative path resolution with no hardcoded machine paths (PLAN-P2-01 / F-41..47)."""
    control_center_dir = Path("Shorts_Engine_Control_Center")
    assert control_center_dir.exists(), "Control center directory must exist"

    bat_files = list(control_center_dir.rglob("*.bat"))
    assert len(bat_files) >= 9, f"Expected at least 9 batch scripts, found: {len(bat_files)}"

    for bat_file in bat_files:
        content = bat_file.read_text(encoding="utf-8", errors="ignore")
        assert "D:\\Projects" not in content, f"Hardcoded path 'D:\\Projects' found in {bat_file}"
        assert "shorts_m3_audit" not in content, f"Hardcoded machine path 'shorts_m3_audit' found in {bat_file}"
        # Must contain relative resolution
        assert "%~dp0" in content, f"Script {bat_file} must use %~dp0 relative directory resolution"
