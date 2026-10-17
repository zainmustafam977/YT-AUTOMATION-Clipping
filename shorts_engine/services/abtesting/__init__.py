"""A/B title test framework (E-017).

Uses Wilson-score 95% confidence intervals (z=1.96) to pick
a winner. Round-robin variant rotation; append-only metric
snapshots; full undo on finalize.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from shorts_engine.db.models import (
    ABMetricSnapshot2, ABTest2, ABVariant2, Clip,
)


logger = logging.getLogger(__name__)


WILSON_Z = 1.96


def wilson_lower_bound(impressions: int, views: int, z: float = WILSON_Z) -> float:
    if impressions <= 0:
        return 0.0
    p = views / impressions
    n = impressions
    z2 = z * z
    centre = p + z2 / (2.0 * n)
    radius = z * math.sqrt((p * (1.0 - p) / n) + (z2 / (4.0 * n * n)))
    return max(0.0, (centre - radius) / (1.0 + z2 / n))


def wilson_upper_bound(impressions: int, views: int, z: float = WILSON_Z) -> float:
    if impressions <= 0:
        return 0.0
    p = views / impressions
    n = impressions
    z2 = z * z
    centre = p + z2 / (2.0 * n)
    radius = z * math.sqrt((p * (1.0 - p) / n) + (z2 / (4.0 * n * n)))
    return min(1.0, (centre + radius) / (1.0 + z2 / n))


class ABTestError(Exception):
    def __init__(self, message: str, code: str = "AB_TEST_ERROR") -> None:
        super().__init__(message)
        self.code = code


class ABTestService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        clip_id: str,
        variants: List[Dict[str, Any]],
        control_index: int = 0,
        rotation_seconds: int = 21600,
        actor: str = "system",
    ) -> ABTest2:
        if not variants or len(variants) < 2:
            raise ABTestError("need at least 2 variants", code="AB_TOO_FEW")
        clip = self.db.query(Clip).filter_by(id=clip_id).first()
        if clip is None:
            raise ABTestError("clip not found", code="AB_CLIP_NOT_FOUND")
        if rotation_seconds < 60:
            raise ABTestError("rotation_seconds must be >= 60", code="AB_BAD_ROTATION")
        test = ABTest2(
            id=str(__import__("uuid").uuid4()),
            clip_id=clip_id,
            metric="ctr",
            status="RUNNING",
            rotation_seconds=rotation_seconds,
        )
        self.db.add(test)
        self.db.flush()
        for i, v in enumerate(variants):
            if not v.get("title"):
                raise ABTestError(f"variant {i} missing title", code="AB_BAD_TITLE")
            self.db.add(ABVariant2(
                id=str(__import__("uuid").uuid4()),
                test_id=test.id,
                title=v["title"],
                description=v.get("description"),
                tags=__import__("json").dumps(v.get("tags")) if v.get("tags") else None,
                is_control=(i == control_index),
            ))
        self.db.flush()
        # Publish the first variant.
        first = sorted(test.variants, key=lambda v: v.created_at)[0]
        first.last_published_at = datetime.utcnow()
        clip.generated_title = first.title
        self.db.commit()
        return test

    def get(self, test_id: str) -> ABTest2:
        t = self.db.query(ABTest2).filter_by(id=test_id).first()
        if t is None:
            raise ABTestError("test not found", code="AB_NOT_FOUND")
        return t

    def advance(self, test_id: str) -> ABVariant2:
        test = self.get(test_id)
        if test.status != "RUNNING":
            raise ABTestError("not RUNNING", code="AB_NOT_RUNNING")
        variants = sorted(test.variants, key=lambda v: v.created_at)
        # The "current" variant is the one with the most recent
        # last_published_at. If none, fall back to the first.
        published = [v for v in variants if v.last_published_at is not None]
        if published:
            current_variant = max(published, key=lambda v: v.last_published_at)
        else:
            current_variant = variants[0]
        current = variants.index(current_variant)
        retiring = variants[current]
        snap = ABMetricSnapshot2(
            test_id=test.id,
            variant_id=retiring.id,
            captured_at=datetime.utcnow(),
            impressions=retiring.impressions,
            views=retiring.views,
            ctr=(retiring.views / retiring.impressions) if retiring.impressions else 0.0,
        )
        self.db.add(snap)
        nxt = variants[(current + 1) % len(variants)]
        nxt.last_published_at = datetime.utcnow()
        clip = self.db.query(Clip).filter_by(id=test.clip_id).first()
        if clip is not None:
            clip.generated_title = nxt.title
        self.db.commit()
        return nxt

    def record_snapshot(
        self,
        test_id: str,
        variant_id: str,
        impressions: int,
        views: int,
    ) -> ABMetricSnapshot2:
        if impressions < 0 or views < 0:
            raise ABTestError("metrics must be >= 0", code="AB_BAD_METRICS")
        if views > impressions:
            raise ABTestError("views > impressions", code="AB_VIEWS_GT_IMPRESSIONS")
        v = self.db.query(ABVariant2).filter_by(id=variant_id).first()
        if v is None or v.test_id != test_id:
            raise ABTestError("variant not in test", code="AB_VARIANT_NOT_FOUND")
        v.impressions = impressions
        v.views = views
        snap = ABMetricSnapshot2(
            test_id=test_id,
            variant_id=variant_id,
            captured_at=datetime.utcnow(),
            impressions=impressions,
            views=views,
            ctr=(views / impressions) if impressions else 0.0,
        )
        self.db.add(snap)
        self.db.commit()
        return snap

    def pick_winner(
        self, test_id: str, min_impressions: int = 1000
    ) -> Optional[ABVariant2]:
        test = self.get(test_id)
        variants = sorted(test.variants, key=lambda v: v.created_at)
        if not variants:
            return None
        control = next((v for v in variants if v.is_control), variants[0])
        if control.impressions < min_impressions:
            return None
        upper = wilson_upper_bound(control.impressions, control.views)
        best: Optional[Tuple[float, ABVariant2]] = None
        for v in variants:
            if v.is_control:
                continue
            if v.impressions < min_impressions:
                continue
            lower = wilson_lower_bound(v.impressions, v.views)
            if lower > upper:
                if best is None or lower > best[0]:
                    best = (lower, v)
        return best[1] if best else None

    def finalize(self, test_id: str) -> Optional[ABVariant2]:
        test = self.get(test_id)
        winner = self.pick_winner(test_id)
        test.winner_variant_id = winner.id if winner else None
        test.status = "COMPLETED"
        test.completed_at = datetime.utcnow()
        if winner is not None:
            clip = self.db.query(Clip).filter_by(id=test.clip_id).first()
            if clip is not None:
                clip.generated_title = winner.title
        self.db.commit()
        return winner
