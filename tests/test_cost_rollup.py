"""Tests for E-010 cost engine."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shorts_engine.db.session import Base
from shorts_engine.db.models import (
    Channel,
    Clip,
    ClipCandidate,
    ClipStatus,
    SourceVideo,
)
from shorts_engine.services.cost import CostService, CostError


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
    cand2 = ClipCandidate(
        id="cand2", source_video_id="sv1",
        start_ms=5000, end_ms=10000, score=0.5, reason="x",
    )
    db.add(cand2)
    db.flush()
    db.add(Clip(id="c1", clip_candidate_id="cand1", status=ClipStatus.WAITING_APPROVAL))
    db.add(Clip(id="c2", clip_candidate_id="cand2", status=ClipStatus.WAITING_APPROVAL))
    db.commit()
    yield db
    db.close()


def test_record_creates_entry(db_session):
    s = CostService(db_session)
    e = s.record("llm_input_groq", units=1000, clip_id="c1", actor="u1")
    assert e.total_cost_usd == 0.0005  # 1000 * 5e-7
    assert e.actor == "u1"


def test_record_rejects_negative_units(db_session):
    with pytest.raises(CostError, match="units"):
        CostService(db_session).record("llm_input_groq", units=-1)


def test_rollup_clip(db_session):
    s = CostService(db_session)
    s.record("llm_input_groq", units=1000, clip_id="c1")
    s.record("llm_output_groq", units=500, clip_id="c1")
    s.record("llm_input_groq", units=200, clip_id="c2")
    r1 = s.rollup_clip("c1")
    r2 = s.rollup_clip("c2")
    assert r1.count == 2
    assert r2.count == 1
    assert r1.by_kind["llm_input_groq"] == pytest.approx(0.0005, rel=1e-6)
    assert r1.by_kind["llm_output_groq"] == pytest.approx(0.00075, rel=1e-6)


def test_rollup_channel(db_session):
    s = CostService(db_session)
    s.record("llm_input_groq", units=1000, clip_id="c1")
    s.record("llm_input_groq", units=2000, clip_id="c2")
    r = s.rollup_channel("ch1")
    assert r.count == 2
    assert r.total == pytest.approx(0.0015, rel=1e-6)


def test_rollup_global(db_session):
    s = CostService(db_session)
    s.record("llm_input_groq", units=1000, clip_id="c1")
    r = s.rollup_global()
    assert r.count == 1


def test_budget_set_and_check_soft(db_session):
    s = CostService(db_session)
    s.set_budget("global", soft_limit_usd=0.0003, hard_limit_usd=0.001, period="daily")
    s.record("llm_input_groq", units=1000, clip_id="c1")
    result = s.check_budget(scope="global", period="daily")
    assert result["alert"] == "SOFT_BREACH"


def test_budget_check_hard_breach(db_session):
    s = CostService(db_session)
    s.set_budget("global", soft_limit_usd=0.0001, hard_limit_usd=0.0002, period="daily")
    s.record("llm_input_groq", units=1000, clip_id="c1")
    result = s.check_budget(scope="global", period="daily")
    assert result["alert"] == "HARD_BREACH"
    assert result["hard_breached"] is True


def test_budget_check_ok(db_session):
    s = CostService(db_session)
    s.set_budget("global", soft_limit_usd=10.0, hard_limit_usd=20.0, period="daily")
    s.record("llm_input_groq", units=10, clip_id="c1")
    result = s.check_budget(scope="global", period="daily")
    assert result["alert"] == "OK"


def test_budget_rejects_invalid_limits(db_session):
    s = CostService(db_session)
    with pytest.raises(CostError, match="soft"):
        s.set_budget("global", soft_limit_usd=10.0, hard_limit_usd=5.0)
    with pytest.raises(CostError, match=">= 0"):
        s.set_budget("global", soft_limit_usd=-1.0, hard_limit_usd=10.0)
