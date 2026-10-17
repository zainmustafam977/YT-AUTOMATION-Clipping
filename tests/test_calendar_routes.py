"""Tests for E-029 content calendar HTTP routes."""
import pytest

from fastapi.testclient import TestClient

from shorts_engine.db.session import engine, Base
from shorts_engine.db import models  # noqa: F401
from shorts_engine.dashboard.app import app


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def client():
    return TestClient(app)


def test_calendar_view_loads(client):
    r = client.get("/calendar")
    # May return 200 (template) or 401 (auth) — either is acceptable as long as
    # the route is registered.
    assert r.status_code in (200, 401, 403, 302)


def test_calendar_events_endpoint(client):
    r = client.get("/api/calendar/events")
    assert r.status_code in (200, 401, 403, 302)


def test_calendar_slots_endpoint(client):
    r = client.get("/api/schedule/slots")
    assert r.status_code in (200, 401, 403, 302)


def test_calendar_reschedule_endpoint_exists(client):
    """Just verify the reschedule route is registered; a 405 means method-not-allowed
    for a GET on a POST-only route, which is fine; 404 means it's missing."""
    r = client.get("/api/calendar/reschedule/1")
    # If the route exists as POST-only, GET should be 405.
    assert r.status_code in (405, 401, 403, 302)
