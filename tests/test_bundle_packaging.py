import zipfile
from pathlib import Path
import pytest

from tools.build_dist_bundle import should_exclude_file, create_distribution_bundle


def test_should_exclude_fnmatch_allow_before_deny():
    """Verify that allow-before-deny preserves .env.example while denying all sensitive patterns."""
    # Allow rule must take precedence
    assert should_exclude_file(".env.example") is False
    assert should_exclude_file("subfolder/.env.example") is False

    # Deny rules
    assert should_exclude_file(".env") is True
    assert should_exclude_file(".env.local") is True
    assert should_exclude_file(".env.production") is True
    assert should_exclude_file("private.key") is True
    assert should_exclude_file("cert.pem") is True
    assert should_exclude_file("storage/shorts_automation.db") is True
    assert should_exclude_file("storage/database.db-wal") is True
    assert should_exclude_file("storage/database.db-shm") is True
    assert should_exclude_file("test.sqlite3") is True
    assert should_exclude_file("client_secret_12345.json") is True
    assert should_exclude_file("credentials_backup.json") is True
    assert should_exclude_file("token_oauth2.json") is True
    assert should_exclude_file("yt_cookie.txt") is True

    # Standard project files must NOT be excluded
    assert should_exclude_file("cli.py") is False
    assert should_exclude_file("shorts_engine/config/settings.py") is False


def test_zip_bundle_strictly_excludes_secrets_and_preserves_example(tmp_path):
    """
    Creates an isolated folder containing all sensitive patterns and verifies
    that creating a zip bundle physically excludes all sensitive files while keeping .env.example.
    """
    src_dir = tmp_path / "project"
    src_dir.mkdir()
    (src_dir / "storage").mkdir()

    # Create dummy files
    files_to_create = [
        ".env.example",  # MUST BE INCLUDED
        "app.py",        # MUST BE INCLUDED
        ".env",
        ".env.local",
        ".env.staging",
        "secret.key",
        "auth.pem",
        "storage/app.db",
        "storage/app.db-wal",
        "storage/app.db-shm",
        "local.sqlite",
        "client_secret_oauth.json",
        "credentials_v2.json",
        "token_store.json",
        "browser_cookie.txt",
    ]

    for rel_path in files_to_create:
        p = src_dir / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"dummy content for {rel_path}")

    out_zip = tmp_path / "test_bundle.zip"
    packaged_count = create_distribution_bundle(src_dir, out_zip)

    assert out_zip.exists()
    assert packaged_count == 2  # Only .env.example and app.py

    with zipfile.ZipFile(out_zip, "r") as zf:
        names = zf.namelist()
        # Forward-slash normalized names
        normalized_names = [n.replace("\\", "/") for n in names]

        assert ".env.example" in normalized_names
        assert "app.py" in normalized_names

        # Verify zero sensitive files entered the archive
        assert ".env" not in normalized_names
        assert ".env.local" not in normalized_names
        assert ".env.staging" not in normalized_names
        assert "secret.key" not in normalized_names
        assert "auth.pem" not in normalized_names
        assert "storage/app.db" not in normalized_names
        assert "storage/app.db-wal" not in normalized_names
        assert "storage/app.db-shm" not in normalized_names
        assert "local.sqlite" not in normalized_names
        assert "client_secret_oauth.json" not in normalized_names
        assert "credentials_v2.json" not in normalized_names
        assert "token_store.json" not in normalized_names
        assert "browser_cookie.txt" not in normalized_names
