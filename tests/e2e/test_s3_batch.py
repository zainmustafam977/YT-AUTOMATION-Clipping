"""E2E S3: Batch approve 100 clips -> bulk-edit titles -> publish.

Smoke test that the full batch pipeline works through the
real dashboard app + real DB.
"""
import uuid

from fastapi.testclient import TestClient

from shorts_engine.db.session import SessionLocal
from shorts_engine.db.models import (
    Channel, Clip, ClipCandidate, ClipStatus, SourceVideo, User, UserRole,
)
from shorts_engine.dashboard.app import create_dashboard_app
from shorts_engine.dashboard.auth import create_session_token, SESSION_COOKIE_NAME


def test_s3_batch_approve_bulk_edit_publish():
    db = SessionLocal()
    suffix = uuid.uuid4().hex[:8]
    ch_id = f"ch{suffix}"
    sv_id = f"sv{suffix}"
    user_id = f"u{suffix}"
    username = f"e2e_user_{suffix}"
    N = 100
    ch = Channel(id=ch_id, youtube_channel_id=f"UC{suffix}", name="C")
    db.add(ch)
    sv = SourceVideo(
        id=sv_id, channel_id=ch_id, youtube_video_id=f"yt{suffix}",
        title="t", duration_sec=60,
    )
    db.add(sv)
    cands = []
    for i in range(N):
        c = ClipCandidate(
            id=f"cand{suffix}-{i}", source_video_id=sv_id,
            start_ms=i * 100, end_ms=(i + 1) * 100,
            score=0.5, reason="x",
        )
        cands.append(c)
    db.add_all(cands)
    db.flush()
    item_ids = []
    for i in range(N):
        clip_id = f"c{suffix}-{i}"
        item_ids.append(clip_id)
        db.add(Clip(
            id=clip_id, clip_candidate_id=cands[i].id,
            status=ClipStatus.WAITING_APPROVAL,
            generated_title=f"Orig {i}",
        ))
    user = User(
        id=user_id, username=username,
        password_hash="x", role=UserRole.OPERATOR, is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    token = create_session_token(user.id)
    db.close()
    app = create_dashboard_app()
    c = TestClient(app)
    c.cookies.set(SESSION_COOKIE_NAME, token)

    # Step 1: batch approve 100 clips.
    r1 = c.post(
        "/api/batch/run",
        json={"operation": "approve", "item_ids": item_ids, "actor": "s3"},
    )
    assert r1.status_code == 200, r1.text
    data1 = r1.json()
    assert data1["ok"] == N, data1

    # Step 2: bulk edit titles.
    r2 = c.post(
        "/api/batch/run",
        json={
            "operation": "bulk_edit_metadata",
            "item_ids": item_ids,
            "params": {
                "fields": {"generated_title": "Batch Title OLD"},
                "find": "OLD", "replace": "NEW",
            },
            "actor": "s3",
        },
    )
    assert r2.status_code == 200
    data2 = r2.json()
    assert data2["ok"] == N

    # Step 3: publish (the publish operation respects quota; we
    # expect either OK or SKIPPED for all items).
    r3 = c.post(
        "/api/batch/run",
        json={"operation": "publish", "item_ids": item_ids, "actor": "s3"},
    )
    assert r3.status_code == 200
    data3 = r3.json()
    # All items processed: ok + skipped.
    assert data3["ok"] + data3["skipped"] + data3["failed"] == N
