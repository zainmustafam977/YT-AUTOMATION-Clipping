"""
Rate Limiting Middleware & Dependency for Dashboard Endpoints.
Implements sliding-window per-user request throttling.
"""

import time
import logging
from collections import defaultdict, deque
from typing import Dict, Deque
from fastapi import HTTPException, status, Depends

from shorts_engine.dashboard.auth import get_current_user
from shorts_engine.db.models import User
from shorts_engine.config.settings import settings

logger = logging.getLogger(__name__)


class IngestionRateLimiter:
    """
    Sliding window rate limiter keyed by authenticated user ID.
    Enforces maximum requests per 60-second window.
    """

    def __init__(self, limit_per_minute: int = 5):
        self.limit_per_minute = limit_per_minute
        self.requests: Dict[str, Deque[float]] = defaultdict(deque)

    def check_rate_limit(self, user_id: str, limit: int = None) -> bool:
        max_requests = limit if limit is not None else self.limit_per_minute
        now = time.time()
        window_start = now - 60.0

        timestamps = self.requests[user_id]
        while timestamps and timestamps[0] < window_start:
            timestamps.popleft()

        if len(timestamps) >= max_requests:
            return False

        timestamps.append(now)
        return True

    def reset(self):
        """Resets all recorded rate limit windows (for testing)."""
        self.requests.clear()


ingestion_rate_limiter = IngestionRateLimiter(
    limit_per_minute=getattr(settings, "INGESTION_RATE_LIMIT_PER_MINUTE", 5)
)


def rate_limit_ingestion(user: User = Depends(get_current_user)) -> User:
    """
    FastAPI dependency ensuring caller does not exceed the ingestion rate limit.
    Raises HTTP 429 Too Many Requests on violation.
    """
    limit = getattr(settings, "INGESTION_RATE_LIMIT_PER_MINUTE", 5)
    allowed = ingestion_rate_limiter.check_rate_limit(str(user.id), limit=limit)
    if not allowed:
        logger.warning(
            "User %s exceeded ingestion rate limit (%d req/min)",
            user.username,
            limit,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded: maximum {limit} ingestion requests per minute.",
        )
    return user
