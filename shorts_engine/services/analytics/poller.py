"""
YouTube Analytics Polling Service.
Pulls per-clip performance metrics into the `analytics_metrics` table on a schedule.
Feeds views, impressions, CTR, view velocity, and retention into the A/B winner daemon
and dashboard without direct platform API calls from consumers.
"""

import uuid
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session

from shorts_engine.db.models import Clip, ClipStatus, Channel, AnalyticsMetric, PipelineJob
from shorts_engine.services.publishing.worker import (
    build_youtube_analytics_client,
    fetch_video_analytics_reports,
    get_channel_refresh_token,
)
from shorts_engine.services.queue.durable_queue import durable_job_queue

logger = logging.getLogger(__name__)


class AnalyticsPoller:
    """
    Polls YouTube Analytics API for published clips, idempotently upserts metrics into
    `analytics_metrics`, and updates clip view velocity & A/B variant performance.
    """

    def __init__(self, db: Optional[Session] = None):
        self._db = db

    @staticmethod
    def upsert_metric(
        db: Session,
        clip_id: str,
        platform_video_id: str,
        snapshot_date: str,
        views: int,
        impressions: int,
        click_through_rate: float,
        average_view_duration: float,
        subscribers_gained: int,
    ) -> AnalyticsMetric:
        """
        Idempotent upsert of a daily metric snapshot on (clip_id, snapshot_date).
        If record exists, updates metric values in place; otherwise inserts new row.
        """
        existing = (
            db.query(AnalyticsMetric)
            .filter_by(clip_id=clip_id, snapshot_date=snapshot_date)
            .first()
        )

        if existing:
            existing.platform_video_id = platform_video_id
            existing.views = views
            existing.impressions = impressions
            existing.click_through_rate = click_through_rate
            existing.average_view_duration = average_view_duration
            existing.subscribers_gained = subscribers_gained
            existing.fetched_at = datetime.utcnow()
            metric = existing
        else:
            metric = AnalyticsMetric(
                clip_id=clip_id,
                platform_video_id=platform_video_id,
                snapshot_date=snapshot_date,
                views=views,
                impressions=impressions,
                click_through_rate=click_through_rate,
                average_view_duration=average_view_duration,
                subscribers_gained=subscribers_gained,
                fetched_at=datetime.utcnow(),
            )
            db.add(metric)

        db.commit()
        db.refresh(metric)
        return metric

    def poll_clip_analytics(
        self,
        db: Session,
        clip: Clip,
        lookback_days: int = 7,
        client: Optional[Any] = None,
    ) -> List[AnalyticsMetric]:
        """
        Pulls last N days of metrics for a published clip from YouTube Analytics API,
        upserts snapshots into `analytics_metrics`, and updates clip view velocity & variations.
        """
        yt_id = None
        if clip.publish_log and clip.publish_log.youtube_video_id:
            yt_id = clip.publish_log.youtube_video_id
        elif clip.analytics and clip.analytics.get("youtube_video_id"):
            yt_id = clip.analytics.get("youtube_video_id")

        if not yt_id:
            logger.debug("Clip %s has no YouTube video ID; skipping analytics poll.", clip.id)
            return []

        # Obtain channel credentials
        channel = None
        if clip.candidate and clip.candidate.source_video and clip.candidate.source_video.channel:
            channel = clip.candidate.source_video.channel
        if not channel:
            channel = db.query(Channel).first()

        analytics_client = client
        if analytics_client is None:
            if not channel:
                logger.warning("No channel configured for clip %s; cannot build analytics client.", clip.id)
                return []
            refresh_token = get_channel_refresh_token(channel, db)
            if not refresh_token:
                logger.warning("No refresh token available for channel %s; skipping live poll.", channel.id)
                return []
            try:
                analytics_client = build_youtube_analytics_client(refresh_token)
            except Exception as e:
                logger.error("Failed to build YouTube Analytics client for channel %s: %s", channel.id, e)
                return []

        # Compute date range
        end_date = datetime.utcnow().strftime("%Y-%m-%d")
        start_date = (datetime.utcnow() - timedelta(days=max(1, lookback_days))).strftime("%Y-%m-%d")

        try:
            reports = fetch_video_analytics_reports(
                client=analytics_client,
                video_id=yt_id,
                start_date=start_date,
                end_date=end_date,
            )
        except Exception as e:
            logger.error("Error fetching analytics report for video %s (clip %s): %s", yt_id, clip.id, e)
            return []

        upserted: List[AnalyticsMetric] = []
        for r in reports:
            snap_date = str(r.get("day") or r.get("date") or r.get("snapshot_date") or end_date)
            views = int(r.get("views") or 0)
            
            # Extract impressions and CTR
            ctr = float(
                r.get("clickThroughRate")
                or r.get("click_through_rate")
                or r.get("annotationClickThroughRate")
                or r.get("ctr")
                or 0.0
            )
            raw_imp = r.get("impressions")
            if raw_imp is not None:
                impressions = int(raw_imp)
            elif ctr > 0.0 and views > 0:
                impressions = int(views / ctr)
            elif views > 0:
                impressions = int(views * 20)  # estimate ~5% CTR baseline
            else:
                impressions = 0

            avg_dur = float(r.get("averageViewDuration") or r.get("average_view_duration") or 0.0)
            subs = int(r.get("subscribersGained") or r.get("subscribers_gained") or 0)

            m = self.upsert_metric(
                db=db,
                clip_id=clip.id,
                platform_video_id=yt_id,
                snapshot_date=snap_date,
                views=views,
                impressions=impressions,
                click_through_rate=ctr,
                average_view_duration=avg_dur,
                subscribers_gained=subs,
            )
            upserted.append(m)

        # Refresh and aggregate performance metrics for winner_daemon decision logic
        all_snapshots = (
            db.query(AnalyticsMetric)
            .filter_by(clip_id=clip.id)
            .order_by(AnalyticsMetric.snapshot_date.asc())
            .all()
        )

        if all_snapshots:
            total_views = sum(s.views for s in all_snapshots)
            total_impressions = sum(s.impressions for s in all_snapshots)
            overall_ctr = round(total_views / total_impressions, 4) if total_impressions > 0 else 0.0
            latest_avg_dur = all_snapshots[-1].average_view_duration
            total_subs = sum(s.subscribers_gained for s in all_snapshots)

            # Calculate current view velocity (views per hour)
            current_velocity = 0.0
            if len(all_snapshots) >= 2:
                # Delta between last 2 snapshots across 24h
                views_delta = max(0, all_snapshots[-1].views)
                current_velocity = round(views_delta / 24.0, 2)
            elif len(all_snapshots) == 1:
                # Initial snapshot estimate
                current_velocity = round(all_snapshots[0].views / 24.0, 2)

            analytics = clip.analytics or {}
            analytics["views"] = total_views
            analytics["impressions"] = total_impressions
            analytics["click_through_rate"] = overall_ctr
            analytics["current_velocity"] = current_velocity
            if "baseline_velocity" not in analytics or float(analytics.get("baseline_velocity", 0.0)) <= 0.0:
                analytics["baseline_velocity"] = current_velocity
            analytics["average_view_duration"] = latest_avg_dur
            analytics["subscribers_gained"] = total_subs
            analytics["youtube_video_id"] = yt_id
            analytics["last_polled_at"] = datetime.utcnow().isoformat()
            clip.analytics = analytics

            # Sync active variation performance
            variations = clip.variations
            if variations:
                for var in variations:
                    if var.get("is_active"):
                        # Active variant accumulates current observed performance
                        var["impressions"] = total_impressions
                        var["clicks"] = total_views
                clip.variations = variations

            db.commit()

        return upserted

    def poll_all_published_clips(
        self,
        db: Session,
        lookback_days: int = 7,
        client: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Polls analytics for all published clips across all channels and upserts rows.
        """
        published_clips = (
            db.query(Clip)
            .filter(
                (Clip.status == ClipStatus.PUBLISHED)
                | (Clip.publish_log != None)  # noqa: E711
            )
            .all()
        )

        polled_count = 0
        total_records = 0

        for clip in published_clips:
            try:
                records = self.poll_clip_analytics(
                    db=db,
                    clip=clip,
                    lookback_days=lookback_days,
                    client=client,
                )
                if records:
                    polled_count += 1
                    total_records += len(records)
            except Exception as e:
                logger.error("Error polling analytics for clip %s: %s", clip.id, e, exc_info=True)

        return {
            "polled_clips_count": polled_count,
            "total_records_upserted": total_records,
            "timestamp": datetime.utcnow().isoformat(),
        }

    @staticmethod
    def schedule_next_poll(db: Session, delay_hours: float = 1.0) -> PipelineJob:
        """
        Schedules the next hourly analytics poll job via the durable queue run_at mechanism.
        Ensures continuous execution without an external OS cron daemon.
        """
        next_run_at = datetime.utcnow() + timedelta(hours=delay_hours)
        poll_id = f"poll_{uuid.uuid4().hex[:12]}"
        job = durable_job_queue.enqueue(
            job_type="analytics_poll",
            entity_id=poll_id,
            entity_type="system",
            stage="analytics_poll",
            payload={"lookback_days": 7, "scheduled_interval_hours": delay_hours},
            next_run_at=next_run_at,
            db=db,
        )
        logger.info("Scheduled next analytics_poll job %s at %s UTC", job.id, next_run_at.isoformat())
        return job
