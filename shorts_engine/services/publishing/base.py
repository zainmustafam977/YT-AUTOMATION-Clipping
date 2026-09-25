"""
Base interface and standardized contracts for Multi-Platform Video Syndication.
Follows the Strategy design pattern, decoupling platform-specific publishing logic
from clip orchestration, scheduling, and database logging.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple


@dataclass
class PlatformPublishResult:
    """Standardized result envelope returned across all platform publishers."""
    platform: str
    success: bool
    post_id: Optional[str] = None
    permalink: Optional[str] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    published_at: datetime = field(default_factory=datetime.utcnow)
    dry_run: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "platform": self.platform,
            "success": self.success,
            "post_id": self.post_id,
            "permalink": self.permalink,
            "error": self.error,
            "metadata": self.metadata,
            "published_at": self.published_at.isoformat(),
            "dry_run": self.dry_run,
        }


class BasePlatformPublisher(ABC):
    """
    Abstract base strategy defining requirements for publishing clips
    to external social video platforms (YouTube Shorts, TikTok, Instagram Reels).
    """

    @property
    @abstractmethod
    def platform_name(self) -> str:
        """Normalized unique platform identifier: 'youtube', 'tiktok', 'instagram'."""
        pass

    @abstractmethod
    def publish(
        self,
        video_path: Path,
        title: str,
        description: str,
        tags: Optional[List[str]] = None,
        scheduled_time: Optional[datetime] = None,
        dry_run: Optional[bool] = None,
        **kwargs: Any,
    ) -> PlatformPublishResult:
        """
        Publishes a rendered vertical short video to the target platform.

        Args:
            video_path: Path to validated 9:16 MP4 file.
            title: Title / headline for the post.
            description: Body text / caption including hashtags.
            tags: Optional list of keyword tags.
            scheduled_time: Optional target publish timestamp.
            dry_run: Override flag for simulation mode.
            **kwargs: Platform-specific options (e.g. privacy level, cover image).

        Returns:
            PlatformPublishResult with status, post ID, permalink, or error details.
        """
        pass

    @abstractmethod
    def health_check(self) -> Tuple[bool, str]:
        """
        Validates API tokens, credentials, and endpoint reachability.

        Returns:
            (healthy: bool, message: str)
        """
        pass
