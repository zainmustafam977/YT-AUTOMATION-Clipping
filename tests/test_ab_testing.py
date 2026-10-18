"""Tests for E-017 A/B testing (Wilson score, round-robin, snapshot, finalize)."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shorts_engine.db.session import Base
from shorts_engine.db.models import (
    Channel, Clip, ClipCandidate, ClipStatus, SourceVideo,
)
from shorts_engine.services.abtesting import (
    ABTestService, ABTestError, wilson_lower_bound, wilson_upper_bound,
)


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    ch = Channel(id="ch1", youtube_channel_id="UC1", name="C")
    db.add(ch)
    sv = SourceVideo(
        id="sv1", channel_id="ch1", youtube_video_id="yt1",
        title="t", duration_sec=60,
    )
    db.add(sv)
    cand = ClipCandidate(
        id="cand1", source_video_id="sv1",
        start_ms=0, end_ms=5000, score=0.5, reason="x",
    )
    db.add(cand)
    db.flush()
    db.add(Clip(id="c1", clip_candidate_id="cand1", status=ClipStatus.WAITING_APPROVAL))
    db.commit()
    yield db
    db.close()


def test_wilson_lower_bound_zero():
    assert wilson_lower_bound(0, 0) == 0.0


def test_wilson_lower_bound_known():
    # 50/100, z=1.96 -> approx 0.40-0.41
    assert 0.39 < wilson_lower_bound(100, 50) < 0.42


def test_wilson_upper_above_lower():
    assert wilson_lower_bound(1000, 200) < wilson_upper_bound(1000, 200)


def test_create_with_two_variants(db_session):
    svc = ABTestService(db_session)
    test = svc.create("c1", [{"title": "A"}, {"title": "B"}])
    assert test.status == "RUNNING"
    assert len(test.variants) == 2
    assert any(v.is_control for v in test.variants)


def test_create_rejects_single_variant(db_session):
    with pytest.raises(ABTestError, match="2 variants"):
        ABTestService(db_session).create("c1", [{"title": "A"}])


def test_create_rejects_unknown_clip(db_session):
    with pytest.raises(ABTestError, match="clip not found"):
        ABTestService(db_session).create("nope", [{"title": "A"}, {"title": "B"}])


def test_create_rejects_short_rotation(db_session):
    with pytest.raises(ABTestError, match="rotation"):
        ABTestService(db_session).create("c1", [{"title": "A"}, {"title": "B"}], rotation_seconds=10)


def test_advance_round_robin(db_session):
    svc = ABTestService(db_session)
    test = svc.create("c1", [{"title": "A"}, {"title": "B"}, {"title": "C"}])
    assert svc.advance(test.id).title == "B"
    assert svc.advance(test.id).title == "C"
    assert svc.advance(test.id).title == "A"


def test_record_snapshot(db_session):
    svc = ABTestService(db_session)
    test = svc.create("c1", [{"title": "A"}, {"title": "B"}])
    v_a = next(v for v in test.variants if v.title == "A")
    svc.record_snapshot(test.id, v_a.id, 1000, 100)
    db_session.refresh(v_a)
    assert v_a.impressions == 1000
    assert v_a.views == 100


def test_record_snapshot_rejects_negative(db_session):
    svc = ABTestService(db_session)
    test = svc.create("c1", [{"title": "A"}, {"title": "B"}])
    v_a = next(v for v in test.variants if v.title == "A")
    with pytest.raises(ABTestError, match=">= 0"):
        svc.record_snapshot(test.id, v_a.id, -1, 0)


def test_pick_winner_with_significant_challenger(db_session):
    svc = ABTestService(db_session)
    test = svc.create("c1", [{"title": "C"}, {"title": "H"}])
    control = next(v for v in test.variants if v.is_control)
    challenger = next(v for v in test.variants if not v.is_control)
    svc.record_snapshot(test.id, control.id, 5000, 250)
    svc.record_snapshot(test.id, challenger.id, 5000, 400)
    winner = svc.pick_winner(test.id, min_impressions=1000)
    assert winner is not None
    assert winner.id == challenger.id


def test_pick_winner_too_thin(db_session):
    svc = ABTestService(db_session)
    test = svc.create("c1", [{"title": "C"}, {"title": "H"}])
    control = next(v for v in test.variants if v.is_control)
    challenger = next(v for v in test.variants if not v.is_control)
    svc.record_snapshot(test.id, control.id, 50, 5)
    svc.record_snapshot(test.id, challenger.id, 50, 4)
    assert svc.pick_winner(test.id, min_impressions=1000) is None


def test_finalize_pins_winner_to_clip(db_session):
    svc = ABTestService(db_session)
    test = svc.create("c1", [{"title": "C"}, {"title": "H"}])
    control = next(v for v in test.variants if v.is_control)
    challenger = next(v for v in test.variants if not v.is_control)
    svc.record_snapshot(test.id, control.id, 5000, 250)
    svc.record_snapshot(test.id, challenger.id, 5000, 400)
    winner = svc.finalize(test.id)
    assert winner is not None
    db_session.refresh(test)
    assert test.status == "COMPLETED"
    clip = db_session.query(Clip).filter_by(id="c1").first()
    assert clip.generated_title == "H"
