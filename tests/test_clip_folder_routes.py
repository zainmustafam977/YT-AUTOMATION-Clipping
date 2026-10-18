"""R4-1.3 — /api/clips/{id}/open-folder HTTP route tests."""
import os
import pytest

from fastapi.testclient import TestClient

from shorts_engine.db.session import engine, Base, SessionLocal
from shorts_engine.db import models  # noqa: F401
from shorts_engine.db.models import (
    Channel, SourceVideo, SourceVideoStatus, Clip, ClipStatus,
    ClipCandidate, generate_uuid,
)
from shorts_engine.dashboard.app import app


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def clip_with_path(tmp_path):
    """Insert a clip + candidate + source_video + channel with a real
    output path. Returns (clip_id, file_path).
    """
    p = tmp_path / f"clip_{generate_uuid()}.mp4"
    p.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 50)
    with SessionLocal() as s:
        ch = Channel(id=generate_uuid(), youtube_channel_id="yt-" + generate_uuid(), name="test")
        s.add(ch)
        s.commit()
        s.refresh(ch)
        sv = SourceVideo(
            id=generate_uuid(),
            channel_id=ch.id,
            youtube_video_id="vid-" + generate_uuid(),
            title="t",
            duration_sec=60,
            status=SourceVideoStatus.DETECTED,
        )
        s.add(sv)
        s.commit()
        s.refresh(sv)
        cand = ClipCandidate(
            id=generate_uuid(), source_video_id=sv.id,
            start_ms=0, end_ms=1000, score=0.5, reason="test",
        )
        s.add(cand)
        s.commit()
        s.refresh(cand)
        c = Clip(
            id=generate_uuid(),
            clip_candidate_id=cand.id,
            rendered_path=str(p),
            status=ClipStatus.APPROVED,
        )
        s.add(c)
        s.commit()
        s.refresh(c)
        return c.id, str(p)


def test_open_folder_missing_clip_returns_404(client):
    r = client.get("/api/clips/no-such-id/open-folder")
    assert r.status_code == 404


def test_open_folder_real_clip(client, clip_with_path):
    cid, expected_path = clip_with_path
    r = client.get(f"/api/clips/{cid}/open-folder")
    assert r.status_code == 200
    data = r.json()
    assert "argv" in data
    assert "kind" in data
    # The actual path is in the resolved file_path (not necessarily the
    # rendered_path; could be normalised). The argv is a list.
    assert isinstance(data["argv"], list)
    assert data["kind"] in ("windows_explorer_select", "macos_finder_reveal", "linux_xdg_open", "missing")


def test_open_folder_no_execute(client, clip_with_path):
    """Default behaviour: returns command without executing."""
    cid, _ = clip_with_path
    r = client.get(f"/api/clips/{cid}/open-folder")
    assert r.status_code == 200
    data = r.json()
    # No execution info in default response.
    assert "executed" not in data
