"""
SQLAlchemy ORM models implementing all 7 entities from Section 10 of SRS.
Incorporates duplicate prevention unique constraints and reconciled ClipStatus enum.
"""

import json
import uuid
from typing import Optional, Dict, Any
from datetime import datetime
from enum import Enum
from sqlalchemy import (
    Column,
    String,
    Integer,
    Float,
    Boolean,
    Text,
    DateTime,
    ForeignKey,
    UniqueConstraint,
    Enum as SQLEnum,
)
from sqlalchemy.orm import relationship
from shorts_engine.db.session import Base


def generate_uuid() -> str:
    return str(uuid.uuid4())


class SourceVideoStatus(str, Enum):
    DETECTED = "DETECTED"
    DOWNLOADING = "DOWNLOADING"
    TRANSCRIBING = "TRANSCRIBING"
    ANALYZING = "ANALYZING"
    SEGMENTS_SELECTED = "SEGMENTS_SELECTED"
    DONE = "DONE"
    FAILED = "FAILED"


class ClipStatus(str, Enum):
    RENDERING = "RENDERING"
    QUALITY_CHECK = "QUALITY_CHECK"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"


class UserRole(str, Enum):
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"


class Channel(Base):
    __tablename__ = "channels"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    youtube_channel_id = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    watch_enabled = Column(Boolean, default=True, nullable=False)
    last_seen_video_id = Column(String(64), nullable=True)
    publish_target_channel_id = Column(String(64), nullable=True)
    human_review_required = Column(Boolean, default=True, nullable=False)
    encrypted_oauth_token = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    source_videos = relationship("SourceVideo", back_populates="channel", cascade="all, delete-orphan")


class SourceVideo(Base):
    __tablename__ = "source_videos"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    channel_id = Column(String(36), ForeignKey("channels.id", ondelete="CASCADE"), nullable=False, index=True)
    youtube_video_id = Column(String(64), nullable=False, index=True)
    title = Column(String(500), nullable=False)
    duration_sec = Column(Integer, default=0, nullable=False)
    status = Column(
        SQLEnum(SourceVideoStatus),
        default=SourceVideoStatus.DETECTED,
        nullable=False,
        index=True,
    )
    downloaded_path = Column(String(1024), nullable=True)
    audio_path = Column(String(1024), nullable=True)
    error_message = Column(Text, nullable=True)
    runtime_overrides_json = Column(Text, nullable=True)
    retry_count = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    channel = relationship("Channel", back_populates="source_videos")
    transcript = relationship("Transcript", back_populates="source_video", uselist=False, cascade="all, delete-orphan")
    candidates = relationship("ClipCandidate", back_populates="source_video", cascade="all, delete-orphan")

    @property
    def runtime_overrides(self) -> dict:
        """Returns deserialized per-video runtime overrides or empty dict if none set."""
        if not self.runtime_overrides_json:
            return {}
        try:
            return json.loads(self.runtime_overrides_json)
        except Exception:
            return {}

    @runtime_overrides.setter
    def runtime_overrides(self, val: Optional[dict]) -> None:
        """Serializes dictionary to runtime_overrides_json."""
        if not val:
            self.runtime_overrides_json = None
        else:
            self.runtime_overrides_json = json.dumps(val)

    __table_args__ = (
        UniqueConstraint("channel_id", "youtube_video_id", name="uq_channel_source_video"),
    )


class Transcript(Base):
    __tablename__ = "transcripts"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    source_video_id = Column(String(36), ForeignKey("source_videos.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)
    word_level_json = Column(Text, nullable=False)  # JSON-encoded array of word timestamps
    language = Column(String(16), default="en", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    source_video = relationship("SourceVideo", back_populates="transcript")


class ClipCandidate(Base):
    __tablename__ = "clip_candidates"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    source_video_id = Column(String(36), ForeignKey("source_videos.id", ondelete="CASCADE"), nullable=False, index=True)
    start_ms = Column(Integer, nullable=False)
    end_ms = Column(Integer, nullable=False)
    score = Column(Float, nullable=False)
    reason = Column(Text, nullable=False)
    hook_text = Column(Text, nullable=True)
    payoff_text = Column(Text, nullable=True)
    variations_json = Column(Text, nullable=True)        # JSON-encoded list of A/B title and hook variants
    selected = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    source_video = relationship("SourceVideo", back_populates="candidates")
    clip = relationship("Clip", back_populates="candidate", uselist=False, cascade="all, delete-orphan")

    @property
    def variations(self) -> list:
        """Returns deserialized A/B title and hook variations."""
        if not self.variations_json:
            return []
        try:
            return json.loads(self.variations_json)
        except Exception:
            return []

    @variations.setter
    def variations(self, val: Optional[list]) -> None:
        if not val:
            self.variations_json = None
        else:
            self.variations_json = json.dumps(val)


class Clip(Base):
    __tablename__ = "clips"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    clip_candidate_id = Column(String(36), ForeignKey("clip_candidates.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)
    rendered_path = Column(String(1024), nullable=True)
    caption_style = Column(Text, nullable=True)   # JSON-encoded styling
    sfx_tags_json = Column(Text, nullable=True)   # JSON-encoded SFX cues
    status = Column(
        SQLEnum(ClipStatus),
        default=ClipStatus.RENDERING,
        nullable=False,
        index=True,
    )
    quality_check_result = Column(Text, nullable=True)  # JSON-encoded results of checks
    generated_title = Column(String(100), nullable=True)
    generated_description = Column(String(5000), nullable=True)
    generated_tags = Column(Text, nullable=True)         # JSON-encoded array of hashtags
    scheduled_publish_at = Column(DateTime, nullable=True, index=True)
    analytics_json = Column(Text, nullable=True)          # JSON-encoded view, like, retention metrics
    variations_json = Column(Text, nullable=True)         # JSON-encoded active/selected A/B variations
    retry_count = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    candidate = relationship("ClipCandidate", back_populates="clip")
    publish_log = relationship("PublishLog", back_populates="clip", uselist=False, cascade="all, delete-orphan")

    @property
    def variations(self) -> list:
        """Returns deserialized A/B title and hook variations (falls back to candidate's)."""
        if self.variations_json:
            try:
                return json.loads(self.variations_json)
            except Exception:
                pass
        if self.candidate and self.candidate.variations:
            return self.candidate.variations
        return []

    @variations.setter
    def variations(self, val: Optional[list]) -> None:
        if not val:
            self.variations_json = None
        else:
            self.variations_json = json.dumps(val)

    @property
    def analytics(self) -> dict:
        """Returns deserialized YouTube performance metrics."""
        if not self.analytics_json:
            return {}
        try:
            return json.loads(self.analytics_json)
        except Exception:
            return {}

    @analytics.setter
    def analytics(self, val: Optional[dict]) -> None:
        """Serializes performance dictionary to analytics_json."""
        if not val:
            self.analytics_json = None
        else:
            self.analytics_json = json.dumps(val)


class PublishLog(Base):
    __tablename__ = "publish_log"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    clip_id = Column(String(36), ForeignKey("clips.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)
    youtube_video_id = Column(String(64), nullable=True, index=True)
    published_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    title = Column(Text, nullable=False)
    description = Column(Text, nullable=False)
    tags_json = Column(Text, nullable=True)
    status = Column(String(32), default="SUCCESS", nullable=False)
    error_message = Column(Text, nullable=True)

    clip = relationship("Clip", back_populates="publish_log")

    __table_args__ = (
        UniqueConstraint("clip_id", "youtube_video_id", name="uq_clip_publish"),
    )


class SFXLibrary(Base):
    __tablename__ = "sfx_library"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    tag = Column(String(64), unique=True, nullable=False, index=True)  # whoosh, pop, ding, reveal, punch
    file_path = Column(String(1024), nullable=False)
    license_note = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(256), nullable=False)
    role = Column(SQLEnum(UserRole), default=UserRole.OPERATOR, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
