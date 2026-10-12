"""
YouTube Data API Quota Tracker & Enterprise Quota Ledger.
Tracks daily quota units consumed (10,000 units/day default limit).
Enforces:
- Quota is per Google Cloud project, shared by all channels (gauge is global).
- Gauge is labeled 'estimate'.
- Reset is midnight America/Los_Angeles (Pacific Time, DST-correct).
- Documented costs: upload = 1,600 units, channels.list = 1 unit.
- Append-only DB ledger in table `quota_ledger`.
- Daily upload cap = floor((daily_limit - safety_reserve) / upload_cost) = 6 uploads/day.
- Manual calibration support via POST /api/quota/calibrate.
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Dict, Any, Tuple, Optional
from sqlalchemy.orm import Session

from shorts_engine.config.settings import settings
from shorts_engine.db.session import SessionLocal
from shorts_engine.db.models import QuotaLedger
from shorts_engine.utils.fs import atomic_write_json

logger = logging.getLogger(__name__)

VIDEOS_INSERT_COST_UNITS = getattr(settings, "QUOTA_VIDEOS_INSERT_COST", 100)
UPLOAD_COST_UNITS = VIDEOS_INSERT_COST_UNITS
VIDEOS_UPDATE_COST_UNITS = getattr(settings, "QUOTA_VIDEOS_UPDATE_COST", 50)
VIDEOS_DELETE_COST_UNITS = getattr(settings, "QUOTA_VIDEOS_DELETE_COST", 50)
SEARCH_LIST_COST_UNITS = getattr(settings, "QUOTA_SEARCH_LIST_COST", 100)
CHANNELS_LIST_COST_UNITS = getattr(settings, "QUOTA_CHANNELS_LIST_COST", 1)
DEFAULT_SAFETY_RESERVE = getattr(settings, "QUOTA_SAFETY_RESERVE", 300)
PACIFIC_TZ = ZoneInfo("America/Los_Angeles")


def get_next_pacific_midnight() -> Tuple[datetime, str]:
    """Returns (next_midnight_utc, next_midnight_iso) for Pacific midnight reset."""
    now_pt = datetime.now(PACIFIC_TZ)
    midnight_pt = (now_pt + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    midnight_utc = midnight_pt.astimezone(timezone.utc).replace(tzinfo=None)
    return midnight_utc, midnight_pt.isoformat()


def get_pacific_day_bounds() -> Tuple[datetime, datetime, str]:
    """
    Returns (start_utc, end_utc, next_reset_iso) for the current Pacific day.
    Correctly accounts for Daylight Saving Time boundaries in America/Los_Angeles.
    """
    now_pt = datetime.now(PACIFIC_TZ)
    start_pt = now_pt.replace(hour=0, minute=0, second=0, microsecond=0)
    end_pt = start_pt + timedelta(days=1)

    start_utc = start_pt.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = end_pt.astimezone(timezone.utc).replace(tzinfo=None)
    return start_utc, end_utc, end_pt.isoformat()


class QuotaTracker:
    _alerted_thresholds = set()

    @classmethod
    def reset_alert_cooldown(cls) -> None:
        """Clears alerted thresholds set for test isolation and cooldown reset."""
        cls._alerted_thresholds.clear()

    def __init__(self, state_file: Optional[Path] = None):
        self.is_custom_state_file = state_file is not None
        self.state_file = state_file or settings.STORAGE_DIR / "quota_state.json"
        self.daily_limit = getattr(settings, "DAILY_QUOTA_LIMIT", 10000)
        self.safety_reserve = getattr(settings, "QUOTA_SAFETY_RESERVE", DEFAULT_SAFETY_RESERVE)

    @property
    def lock_file(self) -> Path:
        return self.state_file.with_name(self.state_file.name + ".lock")

    @property
    def daily_upload_cap(self) -> int:
        """
        Calculates daily upload cap across all channels combined:
        Respects separate videos.insert bucket limit and unit budget:
        min(insert_bucket_limit, floor((daily_limit - safety_reserve) / upload_cost))
        """
        insert_bucket_limit = getattr(settings, "QUOTA_DAILY_VIDEOS_INSERT_LIMIT", 50)
        upload_cost = getattr(settings, "QUOTA_VIDEOS_INSERT_COST", 100)
        effective_budget = max(0, self.daily_limit - self.safety_reserve)
        unit_cap = effective_budget // max(1, upload_cost)
        return min(insert_bucket_limit, unit_cap)

    def record_usage(
        self,
        units: int,
        operation: str,
        channel_id: Optional[str] = None,
        job_id: Optional[str] = None,
        db: Optional[Session] = None,
    ) -> int:
        """
        Appends an immutable entry to `quota_ledger` or isolated state file.
        """
        if self.state_file:
            try:
                today_str = datetime.now(PACIFIC_TZ).date().isoformat()
                current_file_used = 0
                if self.state_file.exists():
                    data = json.loads(self.state_file.read_text(encoding="utf-8"))
                    if data.get("date") == today_str:
                        current_file_used = int(data.get("used_units", 0) or 0)
                new_used = current_file_used + units
                atomic_write_json(self.state_file, {"date": today_str, "used_units": new_used}, indent=2)
            except Exception:
                pass

        def _execute(session: Session) -> int:
            entry = QuotaLedger(
                units=units,
                operation=operation,
                channel_id=channel_id,
                job_id=job_id,
                ts=datetime.utcnow(),
            )
            session.add(entry)
            session.commit()
            logger.info(
                "Quota ledger recorded %d units for '%s' (channel: %s, job: %s)",
                units, operation, channel_id, job_id
            )
            return units

        if db is not None:
            return _execute(db)
        elif not self.is_custom_state_file:
            with SessionLocal() as session:
                return _execute(session)
        else:
            return units

    def record_publish(
        self,
        cost: int = UPLOAD_COST_UNITS,
        channel_id: Optional[str] = None,
        job_id: Optional[str] = None,
        db: Optional[Session] = None,
    ) -> int:
        """Records video publish cost in the ledger and checks for quota threshold alerts."""
        self.record_usage(
            units=cost,
            operation="youtube.videos.insert",
            channel_id=channel_id,
            job_id=job_id,
            db=db,
        )
        self.check_and_alert_quota(db=db)
        return self.get_used_units(db=db)

    def record_channels_list(
        self,
        channel_id: Optional[str] = None,
        job_id: Optional[str] = None,
        db: Optional[Session] = None,
    ) -> int:
        """Records channels.list identity guard cost (1 unit) in the ledger."""
        self.record_usage(
            units=CHANNELS_LIST_COST_UNITS,
            operation="youtube.channels.list",
            channel_id=channel_id,
            job_id=job_id,
            db=db,
        )
        return CHANNELS_LIST_COST_UNITS

    def record_videos_update(
        self,
        cost: int = VIDEOS_UPDATE_COST_UNITS,
        channel_id: Optional[str] = None,
        job_id: Optional[str] = None,
        db: Optional[Session] = None,
    ) -> int:
        """Records videos.update cost (50 units) in the ledger."""
        self.record_usage(
            units=cost,
            operation="youtube.videos.update",
            channel_id=channel_id,
            job_id=job_id,
            db=db,
        )
        self.check_and_alert_quota(db=db)
        return cost

    def record_videos_delete(
        self,
        cost: Optional[int] = None,
        channel_id: Optional[str] = None,
        job_id: Optional[str] = None,
        db: Optional[Session] = None,
    ) -> int:
        """Records videos.delete cost (50 units default) in the ledger."""
        del_cost = cost if cost is not None else getattr(settings, "QUOTA_VIDEOS_DELETE_COST", 50)
        self.record_usage(
            units=del_cost,
            operation="youtube.videos.delete",
            channel_id=channel_id,
            job_id=job_id,
            db=db,
        )
        self.check_and_alert_quota(db=db)
        return del_cost

    def record_search_list(
        self,
        cost: int = SEARCH_LIST_COST_UNITS,
        channel_id: Optional[str] = None,
        job_id: Optional[str] = None,
        db: Optional[Session] = None,
    ) -> int:
        """Records search.list cost (100 units) in the ledger."""
        self.record_usage(
            units=cost,
            operation="youtube.search.list",
            channel_id=channel_id,
            job_id=job_id,
            db=db,
        )
        self.check_and_alert_quota(db=db)
        return cost

    def get_used_units(self, db: Optional[Session] = None) -> int:
        """
        Calculates total quota units consumed during the current Pacific day
        by querying the append-only quota ledger, reconciling with PublishLog and state file.
        """
        if db is None and self.is_custom_state_file:
            if self.state_file and self.state_file.exists():
                try:
                    data = json.loads(self.state_file.read_text(encoding="utf-8"))
                    today_str = datetime.now(PACIFIC_TZ).date().isoformat()
                    if data.get("date") == today_str:
                        return int(data.get("used_units", 0) or 0)
                except Exception:
                    pass
            return 0

        start_utc, end_utc, _ = get_pacific_day_bounds()

        def _calculate(session: Session) -> int:
            try:
                entries = session.query(QuotaLedger).filter(
                    QuotaLedger.ts >= start_utc,
                    QuotaLedger.ts < end_utc,
                ).all()
                ledger_sum = 0
                if isinstance(entries, (list, tuple)):
                    ledger_sum = sum(int(getattr(e, "units", 0) or 0) for e in entries)

                # Reconcile with PublishLog records
                db_units = 0
                try:
                    from shorts_engine.db.models import PublishLog
                    db_pub_count = session.query(PublishLog).filter(
                        PublishLog.published_at >= start_utc,
                        PublishLog.published_at < end_utc,
                        PublishLog.status.in_(["SUCCESS", "DRY_RUN", "PUBLISHED"]),
                    ).count()
                    db_units = int(db_pub_count or 0) * UPLOAD_COST_UNITS
                except Exception:
                    pass

                file_units = 0
                if self.state_file and self.state_file.exists():
                    try:
                        data = json.loads(self.state_file.read_text(encoding="utf-8"))
                        today_str = datetime.now(PACIFIC_TZ).date().isoformat()
                        if data.get("date") == today_str:
                            file_units = int(data.get("used_units", 0) or 0)
                    except Exception:
                        pass

                return max(ledger_sum, db_units, file_units)
            except (TypeError, ValueError, Exception):
                return 0

        if db is not None:
            return _calculate(db)
        else:
            with SessionLocal() as session:
                return _calculate(session)

    def get_uploads_today(self, db: Optional[Session] = None) -> int:
        """
        Returns number of successful/in-progress uploads executed during the current Pacific day.
        """
        if db is None and self.is_custom_state_file:
            used = self.get_used_units(db=None)
            return used // UPLOAD_COST_UNITS
        start_utc, end_utc, _ = get_pacific_day_bounds()

        def _calculate(session: Session) -> int:
            try:
                # Count from quota_ledger entries with videos.insert or from publish_jobs
                ledger_uploads = session.query(QuotaLedger).filter(
                    QuotaLedger.ts >= start_utc,
                    QuotaLedger.ts < end_utc,
                    QuotaLedger.operation == "youtube.videos.insert",
                ).count()
                db_pub_count = 0
                try:
                    from shorts_engine.db.models import PublishLog
                    db_pub_count = session.query(PublishLog).filter(
                        PublishLog.published_at >= start_utc,
                        PublishLog.published_at < end_utc,
                        PublishLog.status.in_(["SUCCESS", "DRY_RUN", "PUBLISHED"]),
                    ).count()
                except Exception:
                    pass
                return max(int(ledger_uploads or 0), int(db_pub_count or 0))
            except (TypeError, ValueError, Exception):
                return 0

        if db is not None:
            return _calculate(db)
        else:
            with SessionLocal() as session:
                return _calculate(session)

    def can_publish(
        self,
        db: Optional[Session] = None,
        cost: int = UPLOAD_COST_UNITS,
    ) -> Tuple[bool, int, str]:
        """
        Checks whether publishing is permitted under quota and daily cap.
        Returns (can_publish: bool, remaining_units: int, reason: str).
        """
        start_utc, end_utc, next_reset_iso = get_pacific_day_bounds()
        used = self.get_used_units(db=db)
        used = int(used) if isinstance(used, (int, float)) else 0
        remaining = max(0, self.daily_limit - used)
        uploads_today = self.get_uploads_today(db=db)
        uploads_today = int(uploads_today) if isinstance(uploads_today, (int, float)) else 0
        cap = self.daily_upload_cap

        # Check upload count cap
        if uploads_today >= cap:
            msg = (
                f"Daily YouTube upload cap reached: {uploads_today}/{cap} uploads today. "
                f"Next upload available at Pacific midnight ({next_reset_iso})."
            )
            logger.warning(msg)
            return False, remaining, msg

        # Check quota units budget
        if remaining < cost:
            msg = (
                f"Daily YouTube quota exhausted (estimate)! Used: {used}/{self.daily_limit} units. "
                f"Required: {cost} units. Resets at Pacific midnight ({next_reset_iso})."
            )
            logger.warning(msg)
            return False, remaining, msg

        return True, remaining, "Quota and daily upload slot available"

    def get_usage_metrics(self, db: Optional[Session] = None) -> Dict[str, Any]:
        """
        Returns comprehensive quota metrics, labelled as estimate, with reset countdown.
        """
        start_utc, end_utc, next_reset_iso = get_pacific_day_bounds()
        used = self.get_used_units(db=db)
        used = int(used) if isinstance(used, (int, float)) else 0
        remaining = max(0, self.daily_limit - used)
        uploads_today = self.get_uploads_today(db=db)
        uploads_today = int(uploads_today) if isinstance(uploads_today, (int, float)) else 0
        cap = self.daily_upload_cap
        pct = round((used / self.daily_limit) * 100, 1) if self.daily_limit > 0 else 0.0

        if self.state_file:
            try:
                today_str = datetime.now(PACIFIC_TZ).date().isoformat()
                atomic_write_json(self.state_file, {"date": today_str, "used_units": used}, indent=2)
            except Exception:
                pass

        return {
            "used_units": used,
            "remaining_units": remaining,
            "daily_limit": self.daily_limit,
            "safety_reserve": self.safety_reserve,
            "daily_upload_cap": cap,
            "uploads_today": uploads_today,
            "remaining_uploads_today": max(0, cap - uploads_today),
            "pct": pct,
            "is_estimate": True,
            "label": "Estimated Global Project Quota",
            "warning": pct >= (getattr(settings, "QUOTA_WARNING_THRESHOLD", 0.85) * 100) or uploads_today >= cap,
            "reset_at_iso": next_reset_iso,
            "timezone": "America/Los_Angeles (Pacific Time)",
        }

    def calibrate(self, used_units_target: int, db: Optional[Session] = None) -> Dict[str, Any]:
        """
        Operator calibration from Google Cloud Console.
        Calculates delta between target and current ledger sum for today,
        and records a calibration entry in `quota_ledger`.
        """
        def _cal(session: Session) -> Dict[str, Any]:
            current_used = self.get_used_units(db=session)
            delta = used_units_target - current_used
            if delta != 0:
                self.record_usage(
                    units=delta,
                    operation="calibration",
                    channel_id=None,
                    job_id=None,
                    db=session,
                )
            return self.get_usage_metrics(db=session)

        if db is not None:
            return _cal(db)
        else:
            with SessionLocal() as session:
                return _cal(session)

    def check_and_alert_quota(
        self,
        db: Optional[Session] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        """
        Evaluates YouTube Data API quota utilization against the daily cap (10,000 units/day default).
        Dispatches proactive alerts to PipelineAlerter:
        - WARNING at >= 85% utilization (or configured settings.QUOTA_WARNING_THRESHOLD)
        - CRITICAL at >= 100% utilization (quota exhausted)
        Enforces Pacific-day deduplication so alerts are not repeatedly fired unless
        utilization escalates from WARNING to CRITICAL or force=True.
        """
        metrics = self.get_usage_metrics(db=db)
        pct = metrics.get("pct", 0.0)
        warning_thresh = getattr(settings, "QUOTA_WARNING_THRESHOLD", 0.85) * 100.0

        alert_needed = False
        level = "INFO"
        if pct >= 100.0 or metrics.get("remaining_units", 0) <= 0:
            alert_needed = True
            level = "CRITICAL"
        elif pct >= warning_thresh:
            alert_needed = True
            level = "WARNING"

        if not alert_needed and not force:
            return {"alert_sent": False, "level": None, "metrics": metrics}

        now_pt = datetime.now(PACIFIC_TZ)
        today_pt_str = now_pt.strftime("%Y-%m-%d")
        cooldown_key = f"{today_pt_str}:{level}"

        if not force and cooldown_key in QuotaTracker._alerted_thresholds:
            logger.debug("Quota alert for %s already sent today. Skipping duplicate.", cooldown_key)
            return {"alert_sent": False, "level": level, "deduplicated": True, "metrics": metrics}

        try:
            from shorts_engine.services.monitoring.alerter import PipelineAlerter
            alerter = PipelineAlerter()
            title = f"YouTube Quota {level.capitalize()} ({pct:.1f}% Used)"
            message = (
                f"YouTube Data API v3 daily quota utilization reached {pct:.1f}% "
                f"({metrics['used_units']}/{metrics['daily_limit']} units). "
                f"Remaining units: {metrics['remaining_units']}. "
                f"Uploads executed today: {metrics['uploads_today']}/{metrics['daily_upload_cap']}. "
                f"Quota resets at midnight Pacific Time ({metrics['reset_at_iso']})."
            )
            context = {
                "used_units": metrics["used_units"],
                "daily_limit": metrics["daily_limit"],
                "remaining_units": metrics["remaining_units"],
                "pct": pct,
                "uploads_today": metrics["uploads_today"],
                "daily_upload_cap": metrics["daily_upload_cap"],
                "reset_at_iso": metrics["reset_at_iso"],
                "timezone": metrics["timezone"],
            }
            dispatched = alerter.alert(
                title=title,
                message=message,
                level=level,
                context=context,
            )
            QuotaTracker._alerted_thresholds.add(cooldown_key)
            return {"alert_sent": True, "dispatched": dispatched, "level": level, "metrics": metrics}
        except Exception as e:
            logger.warning("Failed to dispatch quota alert: %s", e)
            return {"alert_sent": False, "error": str(e), "metrics": metrics}
