"""Tests for E-016 caption editor."""
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shorts_engine.db.session import Base
from shorts_engine.db.models import (
    Channel, Clip, ClipCandidate, ClipStatus, SourceVideo, Transcript,
)
from shorts_engine.services.captioning.editor import CaptionEditor, CaptionEditError


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
    db.add(Transcript(
        id="tx1", source_video_id="sv1",
        word_level_json=json.dumps([
            {"word": "hello", "start_ms": 100, "end_ms": 400},
            {"word": "world", "start_ms": 500, "end_ms": 800},
        ]),
    ))
    db.flush()
    db.add(Clip(id="c1", clip_candidate_id="cand1", status=ClipStatus.WAITING_APPROVAL))
    db.commit()
    yield db
    db.close()


def test_get_words_regenerates_from_transcript(db_session):
    ed = CaptionEditor(db_session)
    words = ed.get_words("c1")
    assert [w["word"] for w in words] == ["hello", "world"]


def test_set_words_persists(db_session):
    ed = CaptionEditor(db_session)
    new = [{"word": "x", "start_ms": 0, "end_ms": 100}]
    ed.set_words("c1", new, actor="u1")
    words = ed.get_words("c1")
    assert words == new


def test_set_words_validates(db_session):
    ed = CaptionEditor(db_session)
    with pytest.raises(CaptionEditError, match="end_ms"):
        ed.set_words("c1", [{"word": "x", "start_ms": 500, "end_ms": 100}])


def test_set_words_rejects_non_list(db_session):
    ed = CaptionEditor(db_session)
    with pytest.raises(CaptionEditError, match="list"):
        ed.set_words("c1", "not a list")  # type: ignore[arg-type]


def test_set_words_rejects_missing_keys(db_session):
    ed = CaptionEditor(db_session)
    with pytest.raises(CaptionEditError, match="missing"):
        ed.set_words("c1", [{"word": "x"}])


def test_reset_to_transcript(db_session):
    ed = CaptionEditor(db_session)
    ed.set_words("c1", [{"word": "fake", "start_ms": 0, "end_ms": 50}], actor="u1")
    ed.reset_to_transcript("c1", actor="u1")
    words = ed.get_words("c1")
    assert words[0]["word"] == "hello"


def test_unknown_clip_raises(db_session):
    with pytest.raises(CaptionEditError, match="clip not found"):
        CaptionEditor(db_session).get_words("nope")
