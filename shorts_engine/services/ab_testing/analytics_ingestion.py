"""
Two-Tier Analytics Ingestion & Rubric Refinement.
- Tier 1: Real-time stats (views, likes, comments) via YouTube Data API v3.
- Tier 2: Deep retention (average view percentage) via YouTube Analytics API with graceful degradation.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class VideoPerformanceMetrics(BaseModel):
    video_id: str
    view_count: int = 0
    like_count: int = 0
    comment_count: int = 0
    # Tier 2 Deep Retention (24-48h delay, requires yt-analytics.readonly scope)
    average_view_percentage: Optional[float] = None
    average_view_duration_sec: Optional[float] = None
    tier2_available: bool = False

    @property
    def engagement_rate(self) -> float:
        if self.view_count == 0:
            return 0.0
        return round(((self.like_count + self.comment_count) / self.view_count) * 100, 2)

    @property
    def composite_score(self) -> float:
        """
        Computes composite performance score weighting views, engagement, and retention.
        If Tier 2 retention is not yet available, weights views and engagement proportionally.
        """
        if self.view_count == 0:
            return 0.0

        view_factor = min(1.0, self.view_count / 10000.0)
        eng_factor = min(1.0, self.engagement_rate / 10.0)

        if self.average_view_percentage is not None:
            retention_factor = min(1.0, self.average_view_percentage / 100.0)
            return round((view_factor * 0.3) + (eng_factor * 0.3) + (retention_factor * 0.4), 4)
        else:
            return round((view_factor * 0.5) + (eng_factor * 0.5), 4)


class AnalyticsIngestionService:
    """
    Ingests performance statistics for published YouTube Shorts using a two-tier model.
    Guarantees graceful degradation if YouTube Analytics API scope is absent or delayed.
    """

    def __init__(self, youtube_data_client=None, youtube_analytics_client=None):
        self.data_client = youtube_data_client
        self.analytics_client = youtube_analytics_client

    def fetch_metrics(
        self,
        video_id: str,
        clip: Optional[Any] = None,
        published_at: Optional[datetime] = None,
    ) -> VideoPerformanceMetrics:
        """
        Fetches combined Tier 1 and Tier 2 metrics for a video ID.
        """
        # 1. Tier 1: Real-time Data API v3 statistics
        tier1_data = self._fetch_tier1(video_id)

        # 2. Tier 2: YouTube Analytics API deep retention
        tier2_data = self._fetch_tier2(video_id, clip=clip, published_at=published_at)

        return VideoPerformanceMetrics(
            video_id=video_id,
            view_count=tier1_data.get("view_count", 0),
            like_count=tier1_data.get("like_count", 0),
            comment_count=tier1_data.get("comment_count", 0),
            average_view_percentage=tier2_data.get("average_view_percentage"),
            average_view_duration_sec=tier2_data.get("average_view_duration_sec"),
            tier2_available=tier2_data.get("available", False),
        )

    def _fetch_tier1(self, video_id: str) -> Dict[str, int]:
        """Fetches view_count, like_count, comment_count from Data API v3."""
        if not self.data_client:
            return {"view_count": 0, "like_count": 0, "comment_count": 0}

        try:
            req = self.data_client.videos().list(part="statistics", id=video_id)
            resp = req.execute()
            items = resp.get("items", [])
            if not items:
                return {"view_count": 0, "like_count": 0, "comment_count": 0}

            stats = items[0].get("statistics", {})
            return {
                "view_count": int(stats.get("viewCount", 0)),
                "like_count": int(stats.get("likeCount", 0)),
                "comment_count": int(stats.get("commentCount", 0)),
            }
        except Exception as e:
            logger.warning(f"Tier 1 metrics fetch failed for video {video_id}: {e}")
            return {"view_count": 0, "like_count": 0, "comment_count": 0}

    def _fetch_tier2(
        self,
        video_id: str,
        clip: Optional[Any] = None,
        published_at: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Fetches deep audience retention metrics from YouTube Analytics API.
        Gracefully handles 403 Forbidden, missing scope, or empty report data.
        """
        if not self.analytics_client:
            logger.debug(f"Analytics client unconfigured; skipping Tier 2 for video {video_id}")
            return {"available": False, "average_view_percentage": None, "average_view_duration_sec": None}

        # Resolve dynamic date window anchored at clip's publish date
        pub_dt = published_at
        if pub_dt is None and clip is not None:
            pub_log = getattr(clip, "publish_log", None)
            if pub_log:
                pub_dt = getattr(pub_log, "published_at", None)
            elif isinstance(clip, datetime):
                pub_dt = clip

        now_utc = datetime.now(timezone.utc)
        if pub_dt:
            if pub_dt.tzinfo is None:
                pub_dt = pub_dt.replace(tzinfo=timezone.utc)
            start_d = pub_dt.astimezone(timezone.utc).date()
        else:
            start_d = (now_utc - timedelta(days=30)).date()

        end_d = now_utc.date()
        if start_d > end_d:
            start_d = end_d

        try:
            req = self.analytics_client.reports().query(
                ids="channel==MINE",
                startDate=start_d.strftime("%Y-%m-%d"),
                endDate=end_d.strftime("%Y-%m-%d"),
                metrics="averageViewPercentage,averageViewDuration",
                dimensions="video",
                filters=f"video=={video_id}",
            )
            resp = req.execute()
            rows = resp.get("rows", [])
            if not rows:
                # Video may be too recent (< 24-48h processing delay)
                logger.info(f"Tier 2 retention data not yet processed for video {video_id} (24-48h lag).")
                return {"available": False, "average_view_percentage": None, "average_view_duration_sec": None}

            row = rows[0]
            # row format: [video_id, averageViewPercentage, averageViewDuration]
            avg_pct = float(row[1]) if len(row) > 1 else None
            avg_dur = float(row[2]) if len(row) > 2 else None
            return {
                "available": True,
                "average_view_percentage": avg_pct,
                "average_view_duration_sec": avg_dur,
            }
        except Exception as e:
            # Missing scope or permission error -> Gracefully degrade
            logger.warning(
                f"YouTube Analytics API scope (yt-analytics.readonly) unavailable or returned error for video {video_id}: {e}. "
                "Degrading gracefully to Tier 1 statistics only."
            )
            return {"available": False, "average_view_percentage": None, "average_view_duration_sec": None}

    def rank_archetypes(self, variant_metrics: Dict[str, VideoPerformanceMetrics]) -> List[Dict[str, Any]]:
        """
        Ranks title archetypes by their composite performance scores.
        Used to feed back high-performing prompt rubrics into Gemini.
        """
        rankings = []
        for archetype, metrics in variant_metrics.items():
            rankings.append({
                "archetype": archetype,
                "score": metrics.composite_score,
                "views": metrics.view_count,
                "engagement_rate": metrics.engagement_rate,
                "retention_pct": metrics.average_view_percentage,
            })
        rankings.sort(key=lambda x: x["score"], reverse=True)
        return rankings
