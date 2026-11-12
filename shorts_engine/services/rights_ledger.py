"""Rights / licence attestation ledger (F-17).

Append-only record of the licence basis under which a clip or source video may
be published. Publication flows can require an attestation before upload.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from sqlalchemy.orm import Query, Session

from shorts_engine.db.models import RightsAttestation

logger = logging.getLogger(__name__)

ALLOWED_LICENSE_TYPES = frozenset(
    {
        "owned",
        "youtube",
        "cc-by",
        "cc-by-sa",
        "cc-by-nc",
        "public-domain",
        "licensed",
        "permission-granted",
    }
)


class RightsLedgerError(ValueError):
    """Raised when an attestation is invalid."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def record_attestation(
    db: Session,
    *,
    attestor: str,
    license_type: str,
    clip_id: Optional[str] = None,
    source_video_id: Optional[str] = None,
    license_url: Optional[str] = None,
    note: Optional[str] = None,
) -> RightsAttestation:
    """Append a rights attestation for a clip or source video."""
    if not clip_id and not source_video_id:
        raise RightsLedgerError("MISSING_TARGET", "clip_id or source_video_id is required")
    if not attestor or not attestor.strip():
        raise RightsLedgerError("MISSING_ATTESTOR", "attestor is required")
    normalized = (license_type or "").strip().lower()
    if normalized not in ALLOWED_LICENSE_TYPES:
        raise RightsLedgerError(
            "INVALID_LICENSE",
            f"license_type must be one of {sorted(ALLOWED_LICENSE_TYPES)}",
        )

    record = RightsAttestation(
        clip_id=clip_id,
        source_video_id=source_video_id,
        license_type=normalized,
        license_url=license_url,
        attestor=attestor.strip(),
        note=note,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    logger.info(
        "Rights attestation recorded id=%s license=%s clip=%s source=%s",
        record.id,
        normalized,
        clip_id,
        source_video_id,
    )
    return record


def list_attestations(
    db: Session,
    *,
    clip_id: Optional[str] = None,
    source_video_id: Optional[str] = None,
) -> List[RightsAttestation]:
    """Return attestations for a clip or source video, newest first."""
    query = db.query(RightsAttestation)
    if clip_id:
        query = query.filter(RightsAttestation.clip_id == clip_id)
    if source_video_id:
        query = query.filter(RightsAttestation.source_video_id == source_video_id)
    return query.order_by(RightsAttestation.created_at.desc()).all()


def has_attestation(
    db: Session, *, clip_id: Optional[str] = None, source_video_id: Optional[str] = None
) -> bool:
    """True when at least one attestation exists for the given target."""
    if not clip_id and not source_video_id:
        return False
    query: Query = db.query(RightsAttestation.id)
    if clip_id:
        query = query.filter(RightsAttestation.clip_id == clip_id)
    if source_video_id:
        query = query.filter(RightsAttestation.source_video_id == source_video_id)
    return query.first() is not None
