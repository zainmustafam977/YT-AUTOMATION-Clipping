import pytest
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shorts_engine.db.session import Base
from shorts_engine.services.publishing.quota import (
    QuotaTracker,
    get_pacific_day_bounds,
    UPLOAD_COST_UNITS,
    CHANNELS_LIST_COST_UNITS,
)
from shorts_engine.db.models import QuotaLedger


@pytest.fixture
def memory_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_pacific_day_bounds_dst():
    start_utc, end_utc, reset_iso = get_pacific_day_bounds()
    assert end_utc > start_utc
    diff = end_utc - start_utc
    # A standard day is 24 hours, during DST transitions 23 or 25 hours
    assert diff.total_seconds() in (23 * 3600, 24 * 3600, 25 * 3600)
    assert "T" in reset_iso


def test_daily_upload_cap_defaults():
    tracker = QuotaTracker()
    assert tracker.daily_upload_cap == 6  # floor((10000 - 300) / 1600)


def test_record_usage_and_ledger(memory_db):
    tracker = QuotaTracker()
    assert tracker.get_used_units(db=memory_db) == 0

    tracker.record_channels_list(channel_id="ch_1", db=memory_db)
    assert tracker.get_used_units(db=memory_db) == 1

    tracker.record_publish(cost=1600, channel_id="ch_1", job_id="job_1", db=memory_db)
    assert tracker.get_used_units(db=memory_db) == 1601
    assert tracker.get_uploads_today(db=memory_db) == 1


def test_daily_cap_enforcement(memory_db):
    tracker = QuotaTracker()
    # Simulate 6 uploads today
    for i in range(6):
        can, rem, msg = tracker.can_publish(db=memory_db)
        assert can is True
        tracker.record_publish(cost=1600, channel_id="ch_1", job_id=f"job_{i}", db=memory_db)

    # 7th upload must be blocked by daily cap
    can, rem, msg = tracker.can_publish(db=memory_db)
    assert can is False
    assert "Daily YouTube upload cap reached: 6/6 uploads today" in msg


def test_calibration(memory_db):
    tracker = QuotaTracker()
    tracker.record_publish(cost=1600, db=memory_db)
    assert tracker.get_used_units(db=memory_db) == 1600

    # Calibrate to 3200 (e.g. from Google Cloud console)
    metrics = tracker.calibrate(used_units_target=3200, db=memory_db)
    assert metrics["used_units"] == 3200
    assert tracker.get_used_units(db=memory_db) == 3200

    # Verify append-only: two ledger rows exist (1600 + 1600 delta)
    rows = memory_db.query(QuotaLedger).all()
    assert len(rows) == 2
    assert rows[1].operation == "calibration"
    assert rows[1].units == 1600
