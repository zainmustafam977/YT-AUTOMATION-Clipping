"""
YouTube Data API Quota Tracker: protects against daily quota exhaustion (10k units/day limit).
Each video insert consumes ~1,600 units. Queues jobs when threshold is reached.
"""

import json
import logging
from datetime import datetime, date
from pathlib import Path
from typing import Dict, Any, Tuple, Optional
import portalocker
from shorts_engine.config.settings import settings
from shorts_engine.utils.fs import atomic_write_json

logger = logging.getLogger(__name__)

UPLOAD_COST_UNITS = 1600


class QuotaTracker:
    def __init__(self, state_file: Optional[Path] = None):
        self.state_file = state_file or settings.STORAGE_DIR / "quota_state.json"
        self.daily_limit = settings.DAILY_QUOTA_LIMIT

    @property
    def lock_file(self) -> Path:
        return self.state_file.with_name(self.state_file.name + ".lock")

    def _load_state(self, db: Optional[Any] = None) -> Dict[str, Any]:
        today_str = date.today().isoformat()
        used_units = 0
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                if data.get("date") == today_str:
                    used_units = int(data.get("used_units", 0))
            except Exception:
                pass

        if db is not None:
            try:
                from shorts_engine.db.models import PublishLog
                start_of_today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
                db_pub_count = db.query(PublishLog).filter(
                    PublishLog.published_at >= start_of_today,
                    PublishLog.status.in_(["SUCCESS", "DRY_RUN", "PUBLISHED"]),
                ).count()
                db_units = db_pub_count * UPLOAD_COST_UNITS
                if db_units > used_units:
                    used_units = db_units
                    self._save_state({"date": today_str, "used_units": used_units})
            except Exception as e:
                logger.debug("Could not reconcile quota with DB: %s", e)

        return {"date": today_str, "used_units": used_units}

    def _save_state(self, state: Dict[str, Any]) -> None:
        atomic_write_json(self.state_file, state, indent=2)

    def can_publish(self, db: Optional[Any] = None) -> Tuple[bool, int, str]:
        """
        Returns (can_publish: bool, remaining_units: int, reason: str).
        """
        with portalocker.Lock(str(self.lock_file), timeout=10.0):
            state = self._load_state(db=db)
            used = state.get("used_units", 0)
            remaining = max(0, self.daily_limit - used)

            if remaining < UPLOAD_COST_UNITS:
                msg = (
                    f"Daily YouTube quota exhausted! Used: {used}/{self.daily_limit} units. "
                    f"Required: {UPLOAD_COST_UNITS}. Queuing for next quota reset."
                )
                logger.warning(msg)
                return False, remaining, msg

            usage_ratio = used / float(self.daily_limit) if self.daily_limit > 0 else 0.0
            if usage_ratio >= settings.QUOTA_WARNING_THRESHOLD:
                logger.warning("YouTube quota usage alert: %d%% used (%d/%d)", int(usage_ratio * 100), used, self.daily_limit)

            return True, remaining, "Quota available"

    def get_usage_metrics(self, db: Optional[Any] = None) -> Dict[str, Any]:
        """Calculates active usage metrics, percentages, and alert flags."""
        with portalocker.Lock(str(self.lock_file), timeout=10.0):
            state = self._load_state(db=db)
            used = state.get("used_units", 0)
            remaining = max(0, self.daily_limit - used)
            pct = round((used / self.daily_limit) * 100, 1) if self.daily_limit > 0 else 0.0
            return {
                "used_units": used,
                "remaining_units": remaining,
                "daily_limit": self.daily_limit,
                "pct": pct,
                "warning": pct >= (settings.QUOTA_WARNING_THRESHOLD * 100),
                "date": state.get("date"),
            }

    def record_publish(self, cost: int = UPLOAD_COST_UNITS, db: Optional[Any] = None) -> int:
        """Records consumption of quota units under cross-process file lock."""
        with portalocker.Lock(str(self.lock_file), timeout=10.0):
            state = self._load_state(db=db)
            state["used_units"] = state.get("used_units", 0) + cost
            self._save_state(state)
            logger.info("Recorded %d quota units. Total used today: %d/%d", cost, state["used_units"], self.daily_limit)
            return state["used_units"]
