"""
Bayesian A/B Winner Promotion Daemon:
Background service that monitors published YouTube Shorts with multi-variant headlines.
Applies Bayesian Beta-Binomial conjugate updating, checks 95% confidence thresholds,
and automatically updates live YouTube video titles to promote winning variants or
triggers emergency circuit breaker rollbacks when view velocity collapses > 50%.
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session

from shorts_engine.config.settings import settings
from shorts_engine.db.session import SessionLocal
from shorts_engine.db.models import Clip, ClipStatus, PublishLog
from shorts_engine.services.ab_testing.bandit import BayesianVariantOptimizer, VariantEvaluationResult
from shorts_engine.services.publishing.client import YouTubePublisher

logger = logging.getLogger(__name__)


def evaluate_and_promote_clip(
    db: Session,
    clip: Clip,
    optimizer: Optional[BayesianVariantOptimizer] = None,
    publisher: Optional[YouTubePublisher] = None,
) -> Optional[VariantEvaluationResult]:
    """
    Evaluates a single published clip's A/B variations and executes
    winner promotion or circuit breaker rollback if warranted.
    """
    if clip.status != ClipStatus.PUBLISHED:
        return None

    variations = clip.variations
    if not variations or len(variations) < 2:
        return None

    opt = optimizer or BayesianVariantOptimizer()
    pub = publisher or YouTubePublisher(db_session=db)

    analytics = clip.analytics or {}
    baseline_velocity = float(analytics.get("baseline_velocity", 0.0))
    current_velocity = float(analytics.get("current_velocity", 0.0))

    eval_result = opt.evaluate_variations(
        variations=variations,
        current_metrics=analytics,
        baseline_velocity=baseline_velocity,
        current_velocity=current_velocity,
    )

    publish_log = db.query(PublishLog).filter_by(clip_id=clip.id).first()
    youtube_video_id = publish_log.youtube_video_id if publish_log else None
    channel = clip.candidate.source_video.channel if (clip.candidate and clip.candidate.source_video) else None
    encrypted_token = channel.encrypted_oauth_token if channel else None

    if eval_result.action == "PROMOTE":
        winner_idx = eval_result.winning_variant_index
        new_title = eval_result.winning_title or variations[winner_idx].get("title")

        logger.info(
            "Promoting variant #%d ('%s') as winner for clip %s with %.1f%% confidence.",
            winner_idx, new_title, clip.id, eval_result.confidence * 100
        )

        # 1. Update title on YouTube
        if youtube_video_id:
            pub.update_video_title(
                youtube_video_id=youtube_video_id,
                new_title=new_title,
                encrypted_oauth_token=encrypted_token,
                dry_run=settings.DRY_RUN_PUBLISHING,
            )

        # 2. Update variations state in DB
        for i, var in enumerate(variations):
            if i == winner_idx:
                var["is_active"] = True
                var["promoted_at"] = datetime.utcnow().isoformat()
                var["confidence"] = eval_result.confidence
            else:
                var["is_active"] = False

        clip.variations = variations
        clip.generated_title = new_title

        # Record event in analytics log
        analytics["last_promotion"] = {
            "variant_index": winner_idx,
            "title": new_title,
            "promoted_at": datetime.utcnow().isoformat(),
            "confidence": eval_result.confidence,
        }
        # Update baseline velocity to current velocity before switch
        analytics["baseline_velocity"] = current_velocity
        clip.analytics = analytics
        db.commit()

    elif eval_result.action == "ROLLBACK":
        baseline_idx = eval_result.winning_variant_index or 0
        baseline_title = eval_result.winning_title or variations[baseline_idx].get("title")

        logger.warning(
            "CIRCUIT BREAKER: Rolling back clip %s to baseline title '%s'.",
            clip.id, baseline_title
        )

        # 1. Revert title on YouTube
        if youtube_video_id:
            pub.update_video_title(
                youtube_video_id=youtube_video_id,
                new_title=baseline_title,
                encrypted_oauth_token=encrypted_token,
                dry_run=settings.DRY_RUN_PUBLISHING,
            )

        # 2. Reset active flag
        for i, var in enumerate(variations):
            var["is_active"] = (i == baseline_idx)

        clip.variations = variations
        clip.generated_title = baseline_title

        analytics["circuit_breaker_rollback"] = {
            "reverted_at": datetime.utcnow().isoformat(),
            "previous_velocity": current_velocity,
            "baseline_velocity": baseline_velocity,
        }
        clip.analytics = analytics
        db.commit()

    return eval_result


def check_and_promote_ab_winners(db: Session) -> List[str]:
    """
    Scans all published clips with variations and runs Bayesian evaluation.
    Returns list of clip IDs that had actions taken (promoted or rolled back).
    """
    published_clips = (
        db.query(Clip)
        .filter(Clip.status == ClipStatus.PUBLISHED)
        .all()
    )

    actioned_clip_ids: List[str] = []
    optimizer = BayesianVariantOptimizer()
    publisher = YouTubePublisher(db_session=db)

    for clip in published_clips:
        try:
            res = evaluate_and_promote_clip(db, clip, optimizer=optimizer, publisher=publisher)
            if res and res.action in ("PROMOTE", "ROLLBACK"):
                actioned_clip_ids.append(clip.id)
        except Exception as e:
            logger.error("Error evaluating A/B variations for clip %s: %s", clip.id, e, exc_info=True)

    return actioned_clip_ids


class ABWinnerPromotionDaemon:
    """Async background worker that periodically evaluates A/B variations and promotes winners."""

    def __init__(self, poll_interval_seconds: Optional[int] = None):
        self.poll_interval = poll_interval_seconds or getattr(settings, "AB_WINNER_CHECK_INTERVAL_SECONDS", 120)
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self):
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("ABWinnerPromotionDaemon started with %ds poll interval.", self.poll_interval)

    async def stop(self):
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("ABWinnerPromotionDaemon stopped.")

    async def _run_loop(self):
        while self._running:
            try:
                with SessionLocal() as db:
                    check_and_promote_ab_winners(db)
            except Exception as e:
                logger.error("Error in ABWinnerPromotionDaemon poll loop: %s", e)

            try:
                await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                break
