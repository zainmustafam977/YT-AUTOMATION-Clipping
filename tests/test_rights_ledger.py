import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shorts_engine.db.session import Base
from shorts_engine.services.rights_ledger import (
    RightsLedgerError,
    has_attestation,
    list_attestations,
    record_attestation,
)


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_record_and_query_attestation(db):
    rec = record_attestation(
        db, attestor="ops@example.com", license_type="CC-BY", clip_id="clip-1"
    )
    assert rec.id
    assert rec.license_type == "cc-by"
    assert has_attestation(db, clip_id="clip-1") is True
    assert [r.id for r in list_attestations(db, clip_id="clip-1")] == [rec.id]


def test_missing_target_rejected(db):
    with pytest.raises(RightsLedgerError) as exc:
        record_attestation(db, attestor="ops", license_type="owned")
    assert exc.value.code == "MISSING_TARGET"


def test_invalid_license_rejected(db):
    with pytest.raises(RightsLedgerError) as exc:
        record_attestation(db, attestor="ops", license_type="whatever", clip_id="c")
    assert exc.value.code == "INVALID_LICENSE"


def test_missing_attestor_rejected(db):
    with pytest.raises(RightsLedgerError) as exc:
        record_attestation(db, attestor="  ", license_type="owned", clip_id="c")
    assert exc.value.code == "MISSING_ATTESTOR"


def test_source_video_scope_and_absence(db):
    record_attestation(db, attestor="ops", license_type="licensed", source_video_id="sv-1")
    assert has_attestation(db, source_video_id="sv-1") is True
    assert has_attestation(db, clip_id="other") is False
    assert has_attestation(db) is False
