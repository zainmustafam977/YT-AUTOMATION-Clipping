"""R4-8.6 — YouTube OAuth contract tests.

Uses a recorded fixture (no live network) to verify that the OAuth flow
exchanges an authorization code for the same response shape the YouTube
API currently documents.
"""
import json
import os
from pathlib import Path
import pytest


FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict:
    p = FIXTURES_DIR / f"{name}.json"
    if not p.exists():
        pytest.skip(f"fixture {name} not present (record first)")
    return json.loads(p.read_text(encoding="utf-8"))


def test_youtube_token_response_shape():
    """The YouTube OAuth token response has the fields we rely on."""
    fx = _load_fixture("youtube_token_response")
    for field in ("access_token", "expires_in", "refresh_token", "scope", "token_type"):
        assert field in fx, f"missing field: {field}"


def test_youtube_token_response_api_version():
    """Fixture must declare the API version it was recorded against."""
    fx = _load_fixture("youtube_token_response")
    assert "api_version" in fx
    # Hard-coded expected version; refresh fixture if it changes.
    assert fx["api_version"] == "v3"


def test_youtube_channels_list_shape():
    """The channels.list?mine=true response has the expected shape."""
    fx = _load_fixture("youtube_channels_list_mine")
    assert "items" in fx
    assert isinstance(fx["items"], list)
    if fx["items"]:
        first = fx["items"][0]
        assert "id" in first
        assert "snippet" in first
        assert "title" in first["snippet"]


def test_youtube_video_upload_shape():
    """The video insert response has the expected fields."""
    fx = _load_fixture("youtube_video_insert_response")
    assert "id" in fx
    assert "status" in fx
    # Acceptable YouTube uploadStatus values (per YouTube API v3 docs).
    assert fx["status"].get("uploadStatus") in (
        "uploading", "uploaded", "processing", "processed", "failed", "deleted",
    )
