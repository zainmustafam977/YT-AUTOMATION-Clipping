"""
Thread-safe AI Token & Cost Tracking Service.
Tracks input/output token volume and estimated USD inference expenditure per clip,
per provider (Groq, Gemini, OpenRouter, OpenAI), and per day.
Persists state atomically to storage/ai_token_costs.json.
"""

import json
import logging
import threading
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, List

from shorts_engine.config.settings import settings
from shorts_engine.utils.fs import atomic_write_json

logger = logging.getLogger(__name__)

# Standard Token Pricing Matrix (USD per 1,000,000 tokens)
# Source: Official provider developer pricing tiers (2025-2026 baselines)
DEFAULT_TOKEN_PRICING: Dict[str, Dict[str, float]] = {
    # Groq: Generous free tier default; LPU commercial rate fallback
    "groq": {
        "input_per_million": 0.05,
        "output_per_million": 0.08,
        "free_tier": True,
    },
    # Google Gemini: Gemini 1.5 / 2.0 / 2.5 Flash
    "gemini": {
        "input_per_million": 0.075,
        "output_per_million": 0.30,
        "free_tier": False,
    },
    # OpenRouter: Community free tier default
    "openrouter": {
        "input_per_million": 0.00,
        "output_per_million": 0.00,
        "free_tier": True,
    },
    # OpenAI: GPT-4o-mini
    "openai": {
        "input_per_million": 0.15,
        "output_per_million": 0.60,
        "free_tier": False,
    },
}


class AITokenCostTracker:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            # Allow isolated instances for unit tests if state_file is explicitly passed
            if kwargs.get("state_file") or (args and args[0]):
                instance = super(AITokenCostTracker, cls).__new__(cls)
                instance._initialized = False
                return instance
            if cls._instance is None:
                cls._instance = super(AITokenCostTracker, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self, state_file: Optional[Path] = None):
        target_path = Path(state_file or (Path(settings.STORAGE_DIR) / "ai_token_costs.json"))
        if getattr(self, "_initialized", False) and getattr(self, "state_file", None) == target_path:
            return
        self.state_file = target_path
        self._state_file = target_path
        self._mutex = threading.RLock()
        self._state: Dict[str, Any] = self._load_state()
        self._initialized = True

    @property
    def is_isolated(self) -> bool:
        """Returns True if this instance points to an isolated test state file."""
        default_file = Path(settings.STORAGE_DIR) / "ai_token_costs.json"
        return getattr(self, "state_file", None) != default_file

    def _load_state(self) -> Dict[str, Any]:
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception as e:
                logger.warning("Failed to load ai_token_costs.json, creating fresh: %s", e)

        return {
            "version": 1,
            "totals": {
                "total_tokens": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "estimated_cost_usd": 0.0,
                "total_calls": 0,
            },
            "providers": {},
            "clips": {},
            "daily": {},
        }

    def _save_state(self) -> None:
        try:
            atomic_write_json(self.state_file, self._state, indent=2)
        except Exception as e:
            logger.warning("Failed to persist ai_token_costs.json: %s", e)

    def calculate_cost(
        self,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> float:
        """
        Calculates estimated cost in USD for the provided token usage using the
        model pricing matrix in settings.
        """
        pricing = settings.get_llm_pricing(provider, model)
        input_rate = pricing.get("input", 0.0) / 1_000_000.0
        output_rate = pricing.get("output", 0.0) / 1_000_000.0

        cost = (input_tokens * input_rate) + (output_tokens * output_rate)
        return round(cost, 6)

    def record_usage(
        self,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        clip_id: Optional[str] = None,
        video_id: Optional[str] = None,
        stage: Optional[str] = None,
        job_id: Optional[str] = None,
        key_index: Optional[int] = None,
        key_masked: Optional[str] = None,
        db: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Records an LLM execution's token consumption, updates in-memory/JSON ledgers,
        and persists an audit record into the llm_usage_logs database table.
        """
        inp = max(0, int(input_tokens or 0))
        out = max(0, int(output_tokens or 0))
        tot = inp + out
        cost = self.calculate_cost(provider, model, inp, out)
        today_str = date.today().isoformat()
        now_iso = datetime.utcnow().isoformat()
        prov_key = provider.lower()

        with self._mutex:
            # 1. Update global totals
            totals = self._state.setdefault("totals", {
                "total_tokens": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "estimated_cost_usd": 0.0,
                "total_calls": 0,
            })
            totals["total_tokens"] += tot
            totals["input_tokens"] += inp
            totals["output_tokens"] += out
            totals["estimated_cost_usd"] = round(totals.get("estimated_cost_usd", 0.0) + cost, 6)
            totals["total_calls"] += 1

            # 2. Update provider totals
            providers = self._state.setdefault("providers", {})
            prov_data = providers.setdefault(prov_key, {
                "total_tokens": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "estimated_cost_usd": 0.0,
                "total_calls": 0,
                "models": {},
            })
            prov_data["total_tokens"] += tot
            prov_data["input_tokens"] += inp
            prov_data["output_tokens"] += out
            prov_data["estimated_cost_usd"] = round(prov_data.get("estimated_cost_usd", 0.0) + cost, 6)
            prov_data["total_calls"] += 1

            # Model breakdown under provider
            model_name = model or "default"
            mod_data = prov_data["models"].setdefault(model_name, {
                "total_tokens": 0,
                "estimated_cost_usd": 0.0,
                "calls": 0,
            })
            mod_data["total_tokens"] += tot
            mod_data["estimated_cost_usd"] = round(mod_data.get("estimated_cost_usd", 0.0) + cost, 6)
            mod_data["calls"] += 1

            # 3. Update daily ledger
            daily = self._state.setdefault("daily", {})
            day_data = daily.setdefault(today_str, {
                "total_tokens": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "estimated_cost_usd": 0.0,
                "total_calls": 0,
            })
            day_data["total_tokens"] += tot
            day_data["input_tokens"] += inp
            day_data["output_tokens"] += out
            day_data["estimated_cost_usd"] = round(day_data.get("estimated_cost_usd", 0.0) + cost, 6)
            day_data["total_calls"] += 1

            # 4. Update per-clip ledger if clip_id is provided
            if clip_id:
                clips = self._state.setdefault("clips", {})
                clip_data = clips.setdefault(str(clip_id), {
                    "clip_id": str(clip_id),
                    "video_id": str(video_id) if video_id else None,
                    "total_tokens": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "estimated_cost_usd": 0.0,
                    "total_calls": 0,
                    "calls": [],
                })
                clip_data["total_tokens"] += tot
                clip_data["input_tokens"] += inp
                clip_data["output_tokens"] += out
                clip_data["estimated_cost_usd"] = round(clip_data.get("estimated_cost_usd", 0.0) + cost, 6)
                clip_data["total_calls"] += 1
                clip_data["calls"].append({
                    "provider": prov_key,
                    "model": model,
                    "input_tokens": inp,
                    "output_tokens": out,
                    "total_tokens": tot,
                    "cost_usd": cost,
                    "stage": stage or "inference",
                    "job_id": job_id,
                    "key_index": key_index,
                    "timestamp": now_iso,
                })

            self._save_state()

        # 5. Persist to SQL database table LLMUsageLog
        try:
            from shorts_engine.db.models import LLMUsageLog
            from shorts_engine.db.session import SessionLocal

            own_session = False
            session = db
            if session is None:
                session = SessionLocal()
                own_session = True

            try:
                log_entry = LLMUsageLog(
                    provider=prov_key,
                    model=model or "default",
                    key_index=key_index,
                    key_masked=key_masked,
                    prompt_tokens=inp,
                    completion_tokens=out,
                    total_tokens=tot,
                    cost_usd=cost,
                    job_id=job_id,
                    video_id=video_id,
                    clip_id=clip_id,
                    stage=stage,
                    created_at=datetime.utcnow(),
                )
                session.add(log_entry)
                session.commit()
            except Exception as dbe:
                logger.warning("Failed to persist LLMUsageLog record to DB: %s", dbe)
                if own_session:
                    session.rollback()
            finally:
                if own_session:
                    session.close()
        except Exception as e:
            logger.debug("Database session unavailable for LLMUsageLog: %s", e)

        return {
            "provider": prov_key,
            "model": model,
            "input_tokens": inp,
            "output_tokens": out,
            "total_tokens": tot,
            "cost_usd": cost,
            "clip_id": clip_id,
            "video_id": video_id,
            "job_id": job_id,
            "key_index": key_index,
            "key_masked": key_masked,
            "stage": stage,
        }

    def get_clip_cost(self, clip_id: str) -> Dict[str, Any]:
        """
        Retrieves the aggregated token usage and USD cost for a specific clip.
        """
        with self._mutex:
            clips = self._state.get("clips", {})
            clip_info = clips.get(str(clip_id))
            if clip_info:
                return dict(clip_info)
            return {
                "clip_id": str(clip_id),
                "total_tokens": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "estimated_cost_usd": 0.0,
                "total_calls": 0,
                "calls": [],
            }

    def get_summary(self) -> Dict[str, Any]:
        """
        Returns full observability snapshot across totals, providers, daily, and clip counts.
        """
        with self._mutex:
            today_str = date.today().isoformat()
            daily_today = self._state.get("daily", {}).get(today_str, {
                "total_tokens": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "estimated_cost_usd": 0.0,
                "total_calls": 0,
            })
            return {
                "totals": dict(self._state.get("totals", {})),
                "today": dict(daily_today),
                "providers": dict(self._state.get("providers", {})),
                "total_tracked_clips": len(self._state.get("clips", {})),
                "pricing_tiers": DEFAULT_TOKEN_PRICING,
            }

    def get_cost_today(self, db: Optional[Any] = None) -> float:
        """
        Returns the total USD spent on LLM inference today (UTC).
        Queries LLMUsageLog table; falls back to in-memory/JSON tracker if DB empty or unavailable.
        """
        # For isolated tracker instances (e.g. tests), skip global SessionLocal if no db provided
        if db is None and self.is_isolated:
            with self._mutex:
                today_str = date.today().isoformat()
                day_data = self._state.get("daily", {}).get(today_str, {})
                return round(float(day_data.get("estimated_cost_usd", 0.0)), 6)

        db_cost = 0.0
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        try:
            from shorts_engine.db.models import LLMUsageLog
            from shorts_engine.db.session import SessionLocal
            from sqlalchemy import func

            own_session = False
            session = db
            if session is None:
                session = SessionLocal()
                own_session = True
            try:
                total_cost = (
                    session.query(func.coalesce(func.sum(LLMUsageLog.cost_usd), 0.0))
                    .filter(LLMUsageLog.created_at >= today_start)
                    .scalar()
                )
                if total_cost is not None:
                    db_cost = round(float(total_cost), 6)
            finally:
                if own_session:
                    session.close()
        except Exception as e:
            logger.debug("Failed querying LLMUsageLog for cost today: %s", e)

        # In-memory/JSON ledger cost
        with self._mutex:
            today_str = date.today().isoformat()
            day_data = self._state.get("daily", {}).get(today_str, {})
            mem_cost = round(float(day_data.get("estimated_cost_usd", 0.0)), 6)

        return max(db_cost, mem_cost)

    def get_cost_per_video(self, video_id: str, db: Optional[Any] = None) -> float:
        """
        Returns total USD spent on LLM inference for a given source video ID.
        """
        if not video_id:
            return 0.0

        if db is None and self.is_isolated:
            with self._mutex:
                total = 0.0
                for clip_info in self._state.get("clips", {}).values():
                    if str(clip_info.get("video_id")) == str(video_id):
                        total += float(clip_info.get("estimated_cost_usd", 0.0))
                return round(total, 6)

        db_cost = 0.0
        try:
            from shorts_engine.db.models import LLMUsageLog
            from shorts_engine.db.session import SessionLocal
            from sqlalchemy import func

            own_session = False
            session = db
            if session is None:
                session = SessionLocal()
                own_session = True
            try:
                total_cost = (
                    session.query(func.coalesce(func.sum(LLMUsageLog.cost_usd), 0.0))
                    .filter(LLMUsageLog.video_id == str(video_id))
                    .scalar()
                )
                if total_cost is not None:
                    db_cost = round(float(total_cost), 6)
            finally:
                if own_session:
                    session.close()
        except Exception as e:
            logger.debug("Failed querying LLMUsageLog for video cost: %s", e)

        # Fallback to in-memory/JSON clips
        with self._mutex:
            mem_cost = 0.0
            for clip_info in self._state.get("clips", {}).values():
                if str(clip_info.get("video_id")) == str(video_id):
                    mem_cost += float(clip_info.get("estimated_cost_usd", 0.0))

        return max(db_cost, round(mem_cost, 6))

    def get_rollups_by_video(self, db: Optional[Any] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Returns aggregated LLM expenditure rollups grouped by video_id.
        """
        results: List[Dict[str, Any]] = []

        if db is None and self.is_isolated:
            with self._mutex:
                video_map: Dict[str, Dict[str, Any]] = {}
                for clip_info in self._state.get("clips", {}).values():
                    vid = clip_info.get("video_id")
                    if not vid:
                        continue
                    v_entry = video_map.setdefault(vid, {
                        "video_id": vid,
                        "total_calls": 0,
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0,
                        "cost_usd": 0.0,
                    })
                    v_entry["total_calls"] += clip_info.get("total_calls", 1)
                    v_entry["prompt_tokens"] += clip_info.get("input_tokens", 0)
                    v_entry["completion_tokens"] += clip_info.get("output_tokens", 0)
                    v_entry["total_tokens"] += clip_info.get("total_tokens", 0)
                    v_entry["cost_usd"] = round(v_entry["cost_usd"] + clip_info.get("estimated_cost_usd", 0.0), 6)
                return sorted(video_map.values(), key=lambda x: x["cost_usd"], reverse=True)[:limit]

        try:
            from shorts_engine.db.models import LLMUsageLog
            from shorts_engine.db.session import SessionLocal
            from sqlalchemy import func

            own_session = False
            session = db
            if session is None:
                session = SessionLocal()
                own_session = True
            try:
                rows = (
                    session.query(
                        LLMUsageLog.video_id,
                        func.count(LLMUsageLog.id).label("total_calls"),
                        func.sum(LLMUsageLog.prompt_tokens).label("prompt_tokens"),
                        func.sum(LLMUsageLog.completion_tokens).label("completion_tokens"),
                        func.sum(LLMUsageLog.total_tokens).label("total_tokens"),
                        func.sum(LLMUsageLog.cost_usd).label("cost_usd"),
                    )
                    .filter(LLMUsageLog.video_id.isnot(None))
                    .group_by(LLMUsageLog.video_id)
                    .order_by(func.sum(LLMUsageLog.cost_usd).desc())
                    .limit(limit)
                    .all()
                )
                for r in rows:
                    results.append({
                        "video_id": r.video_id,
                        "total_calls": int(r.total_calls or 0),
                        "prompt_tokens": int(r.prompt_tokens or 0),
                        "completion_tokens": int(r.completion_tokens or 0),
                        "total_tokens": int(r.total_tokens or 0),
                        "cost_usd": round(float(r.cost_usd or 0.0), 6),
                    })
                return results
            finally:
                if own_session:
                    session.close()
        except Exception as e:
            logger.debug("Failed querying LLM rollups by video: %s", e)

        # Fallback to in-memory/JSON
        with self._mutex:
            video_map: Dict[str, Dict[str, Any]] = {}
            for clip_info in self._state.get("clips", {}).values():
                vid = clip_info.get("video_id")
                if not vid:
                    continue
                v_entry = video_map.setdefault(vid, {
                    "video_id": vid,
                    "total_calls": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "cost_usd": 0.0,
                })
                v_entry["total_calls"] += clip_info.get("total_calls", 0)
                v_entry["prompt_tokens"] += clip_info.get("input_tokens", 0)
                v_entry["completion_tokens"] += clip_info.get("output_tokens", 0)
                v_entry["total_tokens"] += clip_info.get("total_tokens", 0)
                v_entry["cost_usd"] = round(v_entry["cost_usd"] + clip_info.get("estimated_cost_usd", 0.0), 6)
            return sorted(video_map.values(), key=lambda x: x["cost_usd"], reverse=True)[:limit]

    def get_rollups_by_provider(self, db: Optional[Any] = None) -> List[Dict[str, Any]]:
        """
        Returns aggregated LLM expenditure rollups grouped by provider.
        """
        results: List[Dict[str, Any]] = []

        if db is None and self.is_isolated:
            with self._mutex:
                for prov, p_data in self._state.get("providers", {}).items():
                    results.append({
                        "provider": prov,
                        "total_calls": p_data.get("total_calls", 0),
                        "prompt_tokens": p_data.get("input_tokens", 0),
                        "completion_tokens": p_data.get("output_tokens", 0),
                        "total_tokens": p_data.get("total_tokens", 0),
                        "cost_usd": round(float(p_data.get("estimated_cost_usd", 0.0)), 6),
                    })
                return sorted(results, key=lambda x: x["cost_usd"], reverse=True)

        try:
            from shorts_engine.db.models import LLMUsageLog
            from shorts_engine.db.session import SessionLocal
            from sqlalchemy import func

            own_session = False
            session = db
            if session is None:
                session = SessionLocal()
                own_session = True
            try:
                rows = (
                    session.query(
                        LLMUsageLog.provider,
                        func.count(LLMUsageLog.id).label("total_calls"),
                        func.sum(LLMUsageLog.prompt_tokens).label("prompt_tokens"),
                        func.sum(LLMUsageLog.completion_tokens).label("completion_tokens"),
                        func.sum(LLMUsageLog.total_tokens).label("total_tokens"),
                        func.sum(LLMUsageLog.cost_usd).label("cost_usd"),
                    )
                    .group_by(LLMUsageLog.provider)
                    .order_by(func.sum(LLMUsageLog.cost_usd).desc())
                    .all()
                )
                for r in rows:
                    results.append({
                        "provider": r.provider,
                        "total_calls": int(r.total_calls or 0),
                        "prompt_tokens": int(r.prompt_tokens or 0),
                        "completion_tokens": int(r.completion_tokens or 0),
                        "total_tokens": int(r.total_tokens or 0),
                        "cost_usd": round(float(r.cost_usd or 0.0), 6),
                    })
                return results
            finally:
                if own_session:
                    session.close()
        except Exception as e:
            logger.debug("Failed querying LLM rollups by provider: %s", e)

        # Fallback to in-memory/JSON
        with self._mutex:
            for prov, p_data in self._state.get("providers", {}).items():
                results.append({
                    "provider": prov,
                    "total_calls": p_data.get("total_calls", 0),
                    "prompt_tokens": p_data.get("input_tokens", 0),
                    "completion_tokens": p_data.get("output_tokens", 0),
                    "total_tokens": p_data.get("total_tokens", 0),
                    "cost_usd": round(float(p_data.get("estimated_cost_usd", 0.0)), 6),
                })
            return sorted(results, key=lambda x: x["cost_usd"], reverse=True)

    def get_daily_rollups(self, days: int = 7, db: Optional[Any] = None) -> List[Dict[str, Any]]:
        """
        Returns aggregated LLM expenditure rollups grouped by day for the last N days.
        """
        results: List[Dict[str, Any]] = []

        if db is None and self.is_isolated:
            with self._mutex:
                daily = self._state.get("daily", {})
                for d_str, d_data in sorted(daily.items(), reverse=True)[:days]:
                    results.append({
                        "date": d_str,
                        "total_calls": d_data.get("total_calls", 0),
                        "prompt_tokens": d_data.get("input_tokens", 0),
                        "completion_tokens": d_data.get("output_tokens", 0),
                        "total_tokens": d_data.get("total_tokens", 0),
                        "cost_usd": round(float(d_data.get("estimated_cost_usd", 0.0)), 6),
                    })
                return results

        cutoff = datetime.utcnow() - timedelta(days=days)
        try:
            from shorts_engine.db.models import LLMUsageLog
            from shorts_engine.db.session import SessionLocal
            from sqlalchemy import func

            own_session = False
            session = db
            if session is None:
                session = SessionLocal()
                own_session = True
            try:
                rows = (
                    session.query(
                        func.date(LLMUsageLog.created_at).label("day"),
                        func.count(LLMUsageLog.id).label("total_calls"),
                        func.sum(LLMUsageLog.prompt_tokens).label("prompt_tokens"),
                        func.sum(LLMUsageLog.completion_tokens).label("completion_tokens"),
                        func.sum(LLMUsageLog.total_tokens).label("total_tokens"),
                        func.sum(LLMUsageLog.cost_usd).label("cost_usd"),
                    )
                    .filter(LLMUsageLog.created_at >= cutoff)
                    .group_by(func.date(LLMUsageLog.created_at))
                    .order_by(func.date(LLMUsageLog.created_at).desc())
                    .all()
                )
                for r in rows:
                    results.append({
                        "date": str(r.day),
                        "total_calls": int(r.total_calls or 0),
                        "prompt_tokens": int(r.prompt_tokens or 0),
                        "completion_tokens": int(r.completion_tokens or 0),
                        "total_tokens": int(r.total_tokens or 0),
                        "cost_usd": round(float(r.cost_usd or 0.0), 6),
                    })
                return results
            finally:
                if own_session:
                    session.close()
        except Exception as e:
            logger.debug("Failed querying daily LLM rollups: %s", e)

        # Fallback to in-memory/JSON
        with self._mutex:
            daily = self._state.get("daily", {})
            for d_str, d_data in sorted(daily.items(), reverse=True)[:days]:
                results.append({
                    "date": d_str,
                    "total_calls": d_data.get("total_calls", 0),
                    "prompt_tokens": d_data.get("input_tokens", 0),
                    "completion_tokens": d_data.get("output_tokens", 0),
                    "total_tokens": d_data.get("total_tokens", 0),
                    "cost_usd": round(float(d_data.get("estimated_cost_usd", 0.0)), 6),
                })
            return results

    def is_daily_budget_exceeded(self, additional_cost: float = 0.0, db: Optional[Any] = None) -> bool:
        """
        Returns True if the configured daily budget is exceeded.
        """
        budget = getattr(settings, "LLM_DAILY_BUDGET_USD", 0.0)
        if budget <= 0.0:
            return False
        spent_today = self.get_cost_today(db=db)
        return (spent_today + additional_cost) >= budget

    def reset_for_testing(self) -> None:
        """Clears memory and persisted file state for clean test isolation."""
        with self._mutex:
            self._state = {
                "version": 1,
                "totals": {
                    "total_tokens": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "estimated_cost_usd": 0.0,
                    "total_calls": 0,
                },
                "providers": {},
                "clips": {},
                "daily": {},
            }
            self._save_state()


# Module-level default singleton instance
token_tracker = AITokenCostTracker()

__all__ = [
    "AITokenCostTracker",
    "token_tracker",
    "DEFAULT_TOKEN_PRICING",
]
