"""Cost engine (E-010 + F-20).

* Record cost entries (per LLM call, render, etc.)
* Per-clip roll-up
* Per-channel roll-up
* Soft + hard budget with alert
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from shorts_engine.db.models import CostBudget, CostEntry


logger = logging.getLogger(__name__)


UNIT_COST_USD = {
    # LLM
    "llm_input_groq": 0.0000005,
    "llm_output_groq": 0.0000015,
    "llm_input_gemini": 0.0000005,
    "llm_output_gemini": 0.0000015,
    "llm_input_openai": 0.000005,
    "llm_output_openai": 0.000015,
    # TTS
    "tts_char": 0.000016,
    # Render
    "render_second": 0.001,
    # YouTube API (50 unit chunks = 1 quota)
    "youtube_quota_unit": 0.0,  # free tier; kept for accounting
}


class CostError(Exception):
    def __init__(self, message: str, code: str = "COST_ERROR") -> None:
        super().__init__(message)
        self.code = code


@dataclass
class RollUp:
    total: float
    count: int
    by_kind: Dict[str, float] = field(default_factory=dict)


class CostService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def record(
        self,
        kind: str,
        units: float,
        clip_id: Optional[str] = None,
        job_id: Optional[str] = None,
        actor: Optional[str] = None,
        unit_cost_usd: Optional[float] = None,
    ) -> CostEntry:
        if units < 0:
            raise CostError("units must be >= 0", code="COST_BAD_UNITS")
        if unit_cost_usd is None:
            unit_cost_usd = UNIT_COST_USD.get(kind, 0.0)
        e = CostEntry(
            id=str(__import__("uuid").uuid4()),
            job_id=job_id,
            clip_id=clip_id,
            kind=kind,
            units=units,
            unit_cost_usd=unit_cost_usd,
            total_cost_usd=units * unit_cost_usd,
            actor=actor,
            created_at=datetime.utcnow(),
        )
        self.db.add(e)
        self.db.commit()
        return e

    def rollup_clip(self, clip_id: str) -> RollUp:
        rows = (
            self.db.query(CostEntry)
            .filter_by(clip_id=clip_id)
            .all()
        )
        return self._rollup(rows)

    def rollup_channel(
        self,
        channel_id: str,
        since: Optional[datetime] = None,
    ) -> RollUp:
        # cost is on clips, which belong to source_videos, which
        # belong to channels.
        from shorts_engine.db.models import Clip, ClipCandidate, SourceVideo
        q = (
            self.db.query(CostEntry)
            .join(Clip, Clip.id == CostEntry.clip_id)
            .join(ClipCandidate, ClipCandidate.id == Clip.clip_candidate_id)
            .join(SourceVideo, SourceVideo.id == ClipCandidate.source_video_id)
            .filter(SourceVideo.channel_id == channel_id)
        )
        if since is not None:
            q = q.filter(CostEntry.created_at >= since)
        return self._rollup(q.all())

    def rollup_global(
        self,
        since: Optional[datetime] = None,
    ) -> RollUp:
        q = self.db.query(CostEntry)
        if since is not None:
            q = q.filter(CostEntry.created_at >= since)
        return self._rollup(q.all())

    def _rollup(self, rows: List[CostEntry]) -> RollUp:
        by_kind: Dict[str, float] = {}
        total = 0.0
        for r in rows:
            by_kind[r.kind] = by_kind.get(r.kind, 0.0) + r.total_cost_usd
            total += r.total_cost_usd
        return RollUp(total=round(total, 6), count=len(rows), by_kind=by_kind)

    # ------------------------------------------------------------------
    # Budget
    # ------------------------------------------------------------------
    def set_budget(
        self,
        scope: str,
        soft_limit_usd: float,
        hard_limit_usd: float,
        scope_id: Optional[str] = None,
        period: str = "daily",
    ) -> CostBudget:
        if soft_limit_usd < 0 or hard_limit_usd < 0:
            raise CostError("limits must be >= 0", code="COST_BAD_LIMIT")
        if soft_limit_usd > hard_limit_usd:
            raise CostError(
                "soft limit must be <= hard limit", code="COST_BAD_LIMIT"
            )
        existing = (
            self.db.query(CostBudget)
            .filter_by(scope=scope, scope_id=scope_id, period=period)
            .first()
        )
        if existing is not None:
            existing.soft_limit_usd = soft_limit_usd
            existing.hard_limit_usd = hard_limit_usd
            self.db.commit()
            return existing
        b = CostBudget(
            id=str(__import__("uuid").uuid4()),
            scope=scope,
            scope_id=scope_id,
            soft_limit_usd=soft_limit_usd,
            hard_limit_usd=hard_limit_usd,
            period=period,
        )
        self.db.add(b)
        self.db.commit()
        return b

    def check_budget(
        self,
        scope: str = "global",
        scope_id: Optional[str] = None,
        period: str = "daily",
    ) -> Dict[str, Any]:
        since = self._since(period)
        if scope == "global":
            rollup = self.rollup_global(since=since)
        else:
            rollup = self.rollup_channel(scope_id, since=since) if scope_id else self.rollup_global(since=since)
        budget = (
            self.db.query(CostBudget)
            .filter_by(scope=scope, scope_id=scope_id, period=period)
            .first()
        )
        result: Dict[str, Any] = {
            "scope": scope,
            "scope_id": scope_id,
            "period": period,
            "since": since.isoformat() if since else None,
            "spent": rollup.total,
            "soft_limit": budget.soft_limit_usd if budget else None,
            "hard_limit": budget.hard_limit_usd if budget else None,
            "alert": None,
            "hard_breached": False,
        }
        if budget is not None:
            if rollup.total >= budget.hard_limit_usd:
                result["alert"] = "HARD_BREACH"
                result["hard_breached"] = True
            elif rollup.total >= budget.soft_limit_usd:
                result["alert"] = "SOFT_BREACH"
            else:
                result["alert"] = "OK"
        return result

    def _since(self, period: str) -> datetime:
        now = datetime.utcnow()
        if period == "daily":
            return now - timedelta(days=1)
        if period == "weekly":
            return now - timedelta(weeks=1)
        if period == "monthly":
            return now - timedelta(days=30)
        return now - timedelta(days=1)
