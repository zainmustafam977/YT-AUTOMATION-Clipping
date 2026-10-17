"""E2E S7: A/B test create -> advance -> snapshot -> pick winner -> finalize."""
import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shorts_engine.db.session import Base
from shorts_engine.db.models import (
    Channel, Clip, ClipCandidate, ClipStatus, SourceVideo,
)
from shorts_engine.services.abtesting import ABTestService


def test_s7_ab_test_full_lifecycle():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    suffix = uuid.uuid4().hex[:8]
    ch = Channel(id=f"ch{suffix}", youtube_channel_id=f"UC{suffix}", name="C")
    db.add(ch)
    sv = SourceVideo(
        id=f"sv{suffix}", channel_id=ch.id, youtube_video_id=f"yt{suffix}",
        title="t", duration_sec=60,
    )
    db.add(sv)
    cand = ClipCandidate(
        id=f"cand{suffix}", source_video_id=sv.id,
        start_ms=0, end_ms=5000, score=0.5, reason="x",
    )
    db.add(cand)
    db.flush()
    db.add(Clip(id=f"c{suffix}", clip_candidate_id=cand.id, status=ClipStatus.WAITING_APPROVAL))
    db.commit()

    svc = ABTestService(db)

    # 1. Create test.
    test = svc.create(f"c{suffix}", [{"title": "A"}, {"title": "B"}], actor="s7")
    assert test.status == "RUNNING"

    # 2. Advance.
    nxt = svc.advance(test.id)
    assert nxt.title in ("A", "B")

    # 3. Snapshot.
    a = next(v for v in test.variants if v.title == "A")
    b = next(v for v in test.variants if v.title == "B")
    svc.record_snapshot(test.id, a.id, 5000, 250)
    svc.record_snapshot(test.id, b.id, 5000, 400)

    # 4. Pick winner.
    winner = svc.pick_winner(test.id, min_impressions=1000)
    assert winner is not None
    assert winner.id == b.id

    # 5. Finalize.
    final = svc.finalize(test.id)
    assert final is not None
    assert final.id == b.id
    db.refresh(test)
    assert test.status == "COMPLETED"
