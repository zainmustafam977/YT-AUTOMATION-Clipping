"""
SQLAlchemy ORM models implementing all 7 entities from Section 10 of SRS.
Incorporates duplicate prevention unique constraints and reconciled ClipStatus enum.
"""

import json
import uuid
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
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
from sqlalchemy.orm import relationship, validates, Session
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
    POLICY_RESTRICTED = "POLICY_RESTRICTED"
    PENDING_POLICY_REVIEW = "PENDING_POLICY_REVIEW"
    PURGED = "PURGED"


class ClipStatus(str, Enum):
    RENDERING = "RENDERING"
    QUALITY_CHECK = "QUALITY_CHECK"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    APPROVED = "APPROVED"
    SCHEDULED = "SCHEDULED"
    REJECTED = "REJECTED"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    FAILED_NEEDS_REVIEW = "FAILED_NEEDS_REVIEW"
    PURGED = "PURGED"


class UserRole(str, Enum):
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"


class PipelineJobStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    DONE = "DONE"
    FAILED = "FAILED"
    DEAD_LETTER = "DEAD_LETTER"
    QUARANTINED = "QUARANTINED"
    CANCELLED = "CANCELLED"

    def __eq__(self, other):
        if isinstance(other, str):
            val = self.value.lower()
            oth = other.lower()
            if (val in ("completed", "done") and oth in ("completed", "done")) or (val in ("dead_letter", "quarantined") and oth in ("dead_letter", "quarantined")):
                return True
            return val == oth
        if isinstance(other, PipelineJobStatus):
            val = self.value.lower()
            oth = other.value.lower()
            if (val in ("completed", "done") and oth in ("completed", "done")) or (val in ("dead_letter", "quarantined") and oth in ("dead_letter", "quarantined")):
                return True
            return val == oth
        if isinstance(other, Enum):
            if hasattr(other, "value") and isinstance(other.value, str):
                return self.value.lower() == other.value.lower()
            return False
        return super().__eq__(other)

    def __hash__(self):
        return hash(self.value)


class Channel(Base):
    __tablename__ = "channels"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    youtube_channel_id = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    title = Column(String(255), nullable=True)
    handle = Column(String(255), nullable=True)
    avatar_url = Column(String(1024), nullable=True)
    last_verified_at = Column(DateTime, nullable=True)
    watch_enabled = Column(Boolean, default=True, nullable=False)
    last_seen_video_id = Column(String(64), nullable=True)
    publish_target_channel_id = Column(String(64), nullable=True)
    human_review_required = Column(Boolean, default=True, nullable=False)
    # Holds per-channel OAuth token material only (JSON string with refresh_token, access_token, etc.).
    # Application-level OAuth client secrets must NEVER be stored in this column.
    encrypted_oauth_token = Column(Text, nullable=True)
    upload_defaults_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    source_videos = relationship("SourceVideo", back_populates="channel", cascade="all, delete-orphan")
    credential = relationship("ChannelCredential", back_populates="channel", uselist=False, cascade="all, delete-orphan")
    publish_slots = relationship("PublishSlot", back_populates="channel", cascade="all, delete-orphan")

    @property
    def display_title(self) -> str:
        return self.title or self.name or "Untitled Channel"

    @property
    def upload_defaults(self) -> dict:
        """Returns deserialized channel upload defaults dictionary."""
        if not self.upload_defaults_json:
            return {
                "privacy_status": "unlisted",
                "made_for_kids": False,
                "category_id": "22",
                "default_tags": ["#Shorts", "#Viral", "#Trending"],
                "default_language": "en",
                "license": "youtube",
                "embeddable": True,
                "daily_upload_quota": 4,
                "preferred_publish_hour_utc": 18,
                "auto_publish": not self.human_review_required,
            }
        try:
            return json.loads(self.upload_defaults_json)
        except Exception:
            return {}

    @upload_defaults.setter
    def upload_defaults(self, val: Optional[dict]) -> None:
        """Serializes dictionary to upload_defaults_json."""
        if not val:
            self.upload_defaults_json = None
        else:
            self.upload_defaults_json = json.dumps(val)


class ChannelCredential(Base):
    __tablename__ = "channel_credentials"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    channel_id = Column(String(36), ForeignKey("channels.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)
    encrypted_refresh_token = Column(Text, nullable=False)
    nonce = Column(String(64), nullable=True)
    scopes = Column(Text, nullable=True)
    status = Column(String(32), default="active", nullable=False)  # active, needs_auth, revoked, identity_mismatch
    verified_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    channel = relationship("Channel", back_populates="credential")


class OAuthState(Base):
    __tablename__ = "oauth_states"

    state = Column(String(128), primary_key=True)
    pkce_verifier = Column(String(256), nullable=False)
    intended_channel_id = Column(String(64), nullable=True)
    expected_handle = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False)


class PendingOAuthMismatch(Base):
    __tablename__ = "pending_oauth_mismatches"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    intended_channel_id = Column(String(64), nullable=True)
    authorized_channel_id = Column(String(64), nullable=False)
    authorized_title = Column(String(255), nullable=True)
    authorized_handle = Column(String(255), nullable=True)
    authorized_avatar_url = Column(String(1024), nullable=True)
    encrypted_token_data = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False)


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
    is_sensitive = Column(Boolean, default=False, nullable=False)
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

    @property
    def raw_transcript(self) -> Optional[str]:
        return self.word_level_json

    @raw_transcript.setter
    def raw_transcript(self, val: Optional[str]) -> None:
        self.word_level_json = val or ""


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
    # E-016 — caption editor
    caption_words_json = Column(Text, nullable=True)
    caption_edited_at = Column(DateTime, nullable=True)
    caption_edited_by = Column(String(64), nullable=True)
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

    @property
    def virality_score(self) -> float:
        return self.score

    @virality_score.setter
    def virality_score(self, val: float) -> None:
        self.score = val

    @property
    def reasoning(self) -> str:
        return self.reason

    @reasoning.setter
    def reasoning(self, val: str) -> None:
        self.reason = val


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
    is_sensitive = Column(Boolean, default=False, nullable=False)
    retry_count = Column(Integer, default=0, nullable=False)
    draft_title = Column(String(100), nullable=True)
    draft_description = Column(String(5000), nullable=True)
    draft_tags = Column(Text, nullable=True)
    draft_category_id = Column(String(16), nullable=True)
    draft_privacy_status = Column(String(16), nullable=True)
    draft_made_for_kids = Column(Boolean, nullable=True)
    draft_target_channel_id = Column(String(64), nullable=True)
    draft_scheduled_at = Column(DateTime, nullable=True)
    rejection_reason_code = Column(String(64), nullable=True, index=True)
    rejection_note = Column(Text, nullable=True)
    rejected_at = Column(DateTime, nullable=True)
    queued_at = Column(DateTime, nullable=True, index=True)
    source = Column(String(32), default="pipeline", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    candidate = relationship("ClipCandidate", back_populates="clip")
    publish_log = relationship("PublishLog", back_populates="clip", uselist=False, cascade="all, delete-orphan")
    platform_publish_logs = relationship("PlatformPublishLog", back_populates="clip", cascade="all, delete-orphan")
    publish_jobs = relationship("PublishJob", back_populates="clip", cascade="all, delete-orphan")
    publish_slots = relationship("PublishSlot", back_populates="clip")
    analytics_metrics = relationship("AnalyticsMetric", back_populates="clip", cascade="all, delete-orphan")

    @property
    def clip_candidate(self) -> Optional["ClipCandidate"]:
        return self.candidate

    @clip_candidate.setter
    def clip_candidate(self, val: Optional["ClipCandidate"]) -> None:
        self.candidate = val

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

    @property
    def rejection_reason(self) -> Optional[str]:
        """Backward-compatible rejection reason getter (prefers note/code, falls back to JSON)."""
        if self.rejection_note and self.rejection_reason_code:
            return f"[{self.rejection_reason_code}] {self.rejection_note}"
        if self.rejection_note:
            return self.rejection_note
        if self.rejection_reason_code:
            return self.rejection_reason_code
        if self.quality_check_result:
            try:
                qr = json.loads(self.quality_check_result)
                return qr.get("rejection_reason")
            except Exception:
                pass
        return None

    @rejection_reason.setter
    def rejection_reason(self, val: Optional[str]) -> None:
        """Dual-write setter for rejection reason."""
        self.rejection_note = val
        if val and not self.rejection_reason_code:
            self.rejection_reason_code = "MANUAL_REJECTION"
        if val and not self.rejected_at:
            self.rejected_at = datetime.utcnow()
        try:
            qr = json.loads(self.quality_check_result or "{}")
        except Exception:
            qr = {}
        if val:
            qr["rejection_reason"] = val
        else:
            qr.pop("rejection_reason", None)
        self.quality_check_result = json.dumps(qr)


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


class AnalyticsMetric(Base):
    """
    Per-clip performance metrics snapshot from YouTube Analytics API.
    Upserted daily per clip for A/B testing evaluation and dashboard reporting.
    """
    __tablename__ = "analytics_metrics"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    clip_id = Column(String(36), ForeignKey("clips.id", ondelete="CASCADE"), nullable=False, index=True)
    platform_video_id = Column(String(64), nullable=False, index=True)
    snapshot_date = Column(String(10), nullable=False, index=True)  # YYYY-MM-DD
    views = Column(Integer, default=0, nullable=False)
    impressions = Column(Integer, default=0, nullable=False)
    click_through_rate = Column(Float, default=0.0, nullable=False)
    average_view_duration = Column(Float, default=0.0, nullable=False)
    subscribers_gained = Column(Integer, default=0, nullable=False)
    fetched_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    clip = relationship("Clip", back_populates="analytics_metrics")

    __table_args__ = (
        UniqueConstraint("clip_id", "snapshot_date", name="uq_clip_snapshot_date"),
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "clip_id": self.clip_id,
            "platform_video_id": self.platform_video_id,
            "snapshot_date": self.snapshot_date,
            "views": self.views,
            "impressions": self.impressions,
            "click_through_rate": self.click_through_rate,
            "average_view_duration": self.average_view_duration,
            "subscribers_gained": self.subscribers_gained,
            "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
        }


class PlatformPublishLog(Base):
    """
    Audit log of multi-platform syndication attempts (YouTube, TikTok, Instagram Reels).
    Tracks external post IDs, permalinks, execution latency, and error diagnostics.
    """
    __tablename__ = "platform_publish_logs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    clip_id = Column(String(36), ForeignKey("clips.id", ondelete="CASCADE"), nullable=False, index=True)
    platform = Column(String(32), nullable=False, index=True)  # youtube, tiktok, instagram
    status = Column(String(32), default="SUCCESS", nullable=False)  # SUCCESS, FAILED, DRY_RUN
    platform_post_id = Column(String(128), nullable=True, index=True)
    permalink = Column(String(512), nullable=True)
    error_message = Column(Text, nullable=True)
    metadata_json = Column(Text, nullable=True)
    published_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    clip = relationship("Clip", back_populates="platform_publish_logs")


class PublishJobStatus(str, Enum):
    DRAFT = "DRAFT"
    SCHEDULED = "SCHEDULED"
    UPLOADING = "UPLOADING"
    PUBLISHED = "PUBLISHED"
    PAUSED_NEEDS_ATTENTION = "PAUSED_NEEDS_ATTENTION"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class PublishJob(Base):
    __tablename__ = "publish_jobs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    clip_id = Column(String(36), ForeignKey("clips.id", ondelete="CASCADE"), nullable=False, index=True)
    target_youtube_channel_id = Column(String(64), nullable=False, index=True)
    title = Column(String(100), nullable=False)
    description = Column(Text, nullable=False)
    tags = Column(Text, nullable=True)
    category_id = Column(String(16), default="22", nullable=False)
    privacy_status = Column(String(16), default="unlisted", nullable=False)
    made_for_kids = Column(Boolean, default=False, nullable=False)
    schedule_mode = Column(String(16), default="native", nullable=False)  # native | local
    scheduled_at = Column(DateTime, nullable=True, index=True)
    status = Column(String(32), default="DRAFT", nullable=False, index=True)
    attempts = Column(Integer, default=0, nullable=False)
    youtube_video_id = Column(String(64), nullable=True, index=True)
    upload_session_uri = Column(Text, nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    clip = relationship("Clip", back_populates="publish_jobs")


class PublishSlotStatus(str, Enum):
    RESERVED = "RESERVED"     # Slot allocated, upload in progress or queued
    SCHEDULED = "SCHEDULED"   # Uploaded to YouTube with publishAt set
    PUBLISHED = "PUBLISHED"   # Video went live / verified published
    CANCELLED = "CANCELLED"   # Un-approved or cancelled


class PublishSlot(Base):
    """
    Publish slot allocation table.
    Ensures collision-free, atomic scheduling per channel via UniqueConstraint(channel_id, slot_start).
    Supports platform-agnostic scheduling (YouTube native publishAt, TikTok/Instagram deferred jobs).
    """
    __tablename__ = "publish_slots"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    channel_id = Column(String(36), ForeignKey("channels.id", ondelete="CASCADE"), nullable=False, index=True)
    clip_id = Column(String(36), ForeignKey("clips.id", ondelete="SET NULL"), nullable=True, index=True)
    slot_start = Column(DateTime, nullable=False, index=True)
    platform = Column(String(32), default="youtube", nullable=False, index=True)
    status = Column(String(32), default=PublishSlotStatus.RESERVED.value, nullable=False, index=True)
    youtube_video_id = Column(String(64), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("channel_id", "slot_start", name="uq_publish_slots_channel_slot_start"),
    )

    channel = relationship("Channel", back_populates="publish_slots")
    clip = relationship("Clip", back_populates="publish_slots")


class PipelineJob(Base):
    __tablename__ = "pipeline_jobs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    job_type = Column(String(64), nullable=False, index=True)  # INGEST_VIDEO, RENDER_CLIP, PUBLISH_CLIP
    entity_type = Column(String(32), nullable=False)           # source_video, clip, channel
    entity_id = Column(String(64), nullable=False, index=True)
    status = Column(SQLEnum(PipelineJobStatus), default=PipelineJobStatus.PENDING, nullable=False, index=True)
    stage = Column(String(64), default="QUEUED", nullable=False)
    progress_pct = Column(Float, default=0.0, nullable=False)
    progress_detail = Column(Text, nullable=True)
    payload_json = Column(Text, nullable=True)
    result_json = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, default=0, nullable=False)
    attempt_count = Column(Integer, default=0, nullable=False)
    max_retries = Column(Integer, default=3, nullable=False)
    backoff_multiplier_sec = Column(Float, default=10.0, nullable=False)
    next_run_at = Column(DateTime, nullable=True, index=True)
    locked_by = Column(String(128), nullable=True, index=True)
    locked_at = Column(DateTime, nullable=True)
    heartbeat_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("entity_id", "stage", name="uq_pipeline_jobs_entity_stage"),
    )

    @property
    def subject_id(self) -> str:
        return self.entity_id

    @subject_id.setter
    def subject_id(self, val: str) -> None:
        self.entity_id = val

    @property
    def last_error(self) -> Optional[str]:
        return self.error_message

    @last_error.setter
    def last_error(self, val: Optional[str]) -> None:
        self.error_message = val

    @validates("status")
    def validate_status(self, key, value):
        if isinstance(value, str):
            u = value.upper()
            if u in PipelineJobStatus.__members__:
                return PipelineJobStatus[u]
        return value


class QuotaLedger(Base):
    __tablename__ = "quota_ledger"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ts = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    units = Column(Integer, nullable=False)
    operation = Column(String(64), nullable=False)  # 'channels.list', 'videos.insert', 'calibration'
    channel_id = Column(String(64), nullable=True)
    job_id = Column(String(36), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ts = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    actor = Column(String(64), default="system", nullable=False)
    event = Column(String(64), nullable=False)
    channel_id = Column(String(64), nullable=True)
    job_id = Column(String(36), nullable=True)
    details = Column(Text, nullable=True)


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


class ApiKey(Base):
    """
    Persisted multi-provider API key with leaky-bucket rate limits,
    daily token counters, and exponential cooldown tracking.
    """
    __tablename__ = "api_keys"

    id = Column(String(64), primary_key=True, default=generate_uuid)
    provider = Column(String(32), nullable=False, index=True)  # 'gemini', 'groq', 'openai', 'deepseek', 'anthropic'
    api_key_encrypted = Column(Text, nullable=False)
    nickname = Column(String(64), nullable=False)
    status = Column(String(32), default="ACTIVE", nullable=False, index=True)  # 'ACTIVE', 'RATE_LIMITED', 'COOLING_DOWN', 'EXHAUSTED', 'DISABLED'
    rpm_limit = Column(Integer, default=30, nullable=False)
    tpm_limit = Column(Integer, default=60000, nullable=False)
    rpd_limit = Column(Integer, default=14400, nullable=False)
    daily_request_count = Column(Integer, default=0, nullable=False)
    daily_token_count = Column(Integer, default=0, nullable=False)
    consecutive_errors = Column(Integer, default=0, nullable=False)
    cooldown_until = Column(DateTime, nullable=True)
    last_used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def is_cooling_down(self, now: Optional[datetime] = None) -> bool:
        if self.status != "COOLING_DOWN":
            return False
        curr_time = now or datetime.utcnow()
        return self.cooldown_until is not None and self.cooldown_until > curr_time

    def is_available(self, now: Optional[datetime] = None) -> bool:
        if self.status == "DISABLED" or self.status == "EXHAUSTED":
            return False
        if self.status == "COOLING_DOWN":
            curr_time = now or datetime.utcnow()
            return self.cooldown_until is not None and self.cooldown_until <= curr_time
        return self.status == "ACTIVE"


class CaptionPreset(Base):
    """
    Persisted Studio Caption Presets for typography, karaoke styling,
    safe-zone margins, and subtitle animations.
    """
    __tablename__ = "caption_presets"

    id = Column(String(64), primary_key=True, default=generate_uuid)
    name = Column(String(64), unique=True, nullable=False, index=True)
    description = Column(Text, nullable=True)
    font_family = Column(String(64), default="Arial Black", nullable=False)
    font_size = Column(Integer, default=56, nullable=False)
    primary_color = Column(String(16), default="&H00FFFFFF", nullable=False)
    highlight_color = Column(String(16), default="&H0000FFFF", nullable=False)
    outline_color = Column(String(16), default="&H00000000", nullable=False)
    outline_width = Column(Float, default=4.0, nullable=False)
    shadow_depth = Column(Float, default=2.0, nullable=False)
    shadow_color = Column(String(16), default="&H80000000", nullable=False)
    background_box = Column(Boolean, default=False, nullable=False)
    box_color = Column(String(16), default="&H80000000", nullable=False)
    margin_vertical = Column(Integer, default=420, nullable=False)
    max_words_per_line = Column(Integer, default=4, nullable=False)
    case_transform = Column(String(32), default="uppercase", nullable=False)
    scale_pop = Column(Boolean, default=True, nullable=False)
    active_scale = Column(Integer, default=115, nullable=False)
    is_builtin = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def to_dict(self) -> Dict[str, Any]:
        from shorts_engine.services.captioning.templates import ass_to_hex_color
        return {
            "id": self.id,
            "template_id": self.id,
            "name": self.name,
            "description": self.description or "",
            "font_family": self.font_family,
            "font_name": self.font_family,
            "font_size": self.font_size,
            "primary_color": self.primary_color,
            "highlight_color": self.highlight_color,
            "outline_color": self.outline_color,
            "primary_hex": ass_to_hex_color(self.primary_color),
            "highlight_hex": ass_to_hex_color(self.highlight_color),
            "outline_hex": ass_to_hex_color(self.outline_color),
            "outline_width": self.outline_width,
            "shadow_depth": self.shadow_depth,
            "shadow_color": self.shadow_color,
            "background_box": self.background_box,
            "box_color": self.box_color,
            "margin_vertical": self.margin_vertical,
            "max_words_per_line": self.max_words_per_line,
            "case_transform": self.case_transform,
            "scale_pop": self.scale_pop,
            "active_scale": self.active_scale,
            "is_builtin": self.is_builtin,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def to_ass_overrides(self) -> Dict[str, Any]:
        return {
            "font_name": self.font_family,
            "font_size": self.font_size,
            "primary_color": self.primary_color,
            "highlight_color": self.highlight_color,
            "outline_color": self.outline_color,
            "outline_width": self.outline_width,
            "shadow_depth": self.shadow_depth,
            "shadow_color": self.shadow_color,
            "background_box": self.background_box,
            "box_color": self.box_color,
            "margin_vertical": self.margin_vertical,
            "max_words_per_line": self.max_words_per_line,
            "case_transform": self.case_transform,
            "scale_pop": self.scale_pop,
            "active_scale": self.active_scale,
        }


class EventLog(Base):
    """
    Persistent audit log for platform events emitted via the event bus.
    Retains a 7-day queryable window for consumer synchronization and catch-up.
    """
    __tablename__ = "event_logs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    event_name = Column(String(64), nullable=False, index=True)
    entity_id = Column(String(64), nullable=True, index=True)
    entity_type = Column(String(32), nullable=True)
    payload_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    @property
    def payload(self) -> Dict[str, Any]:
        """Deserializes payload_json to a dictionary."""
        if not self.payload_json:
            return {}
        try:
            return json.loads(self.payload_json)
        except Exception:
            return {}

    @payload.setter
    def payload(self, val: Optional[Dict[str, Any]]) -> None:
        self.payload_json = json.dumps(val or {})

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "event_name": self.event_name,
            "entity_id": self.entity_id,
            "entity_type": self.entity_type,
            "payload": self.payload,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


def prune_old_event_logs(db: Session, retention_days: int = 7) -> int:
    """
    Deletes event logs older than the retention threshold (default 7 days).
    Returns the count of pruned rows.
    """
    cutoff = datetime.utcnow() - timedelta(days=retention_days)
    deleted_count = (
        db.query(EventLog)
        .filter(EventLog.created_at < cutoff)
        .delete(synchronize_session=False)
    )
    db.commit()
    return deleted_count


class LLMUsageLog(Base):
    """
    Persistent audit ledger for tracking all LLM inference calls, token volume,
    and USD expenditure across providers, models, and API keys.
    """
    __tablename__ = "llm_usage_logs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    provider = Column(String(64), nullable=False, index=True)
    model = Column(String(128), nullable=False, index=True)
    key_index = Column(Integer, nullable=True)
    key_masked = Column(String(64), nullable=True)
    prompt_tokens = Column(Integer, default=0, nullable=False)
    completion_tokens = Column(Integer, default=0, nullable=False)
    total_tokens = Column(Integer, default=0, nullable=False)
    cost_usd = Column(Float, default=0.0, nullable=False)
    job_id = Column(String(64), nullable=True, index=True)
    video_id = Column(String(64), nullable=True, index=True)
    clip_id = Column(String(64), nullable=True, index=True)
    stage = Column(String(64), nullable=True, index=True)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "provider": self.provider,
            "model": self.model,
            "key_index": self.key_index,
            "key_masked": self.key_masked,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": self.cost_usd,
            "job_id": self.job_id,
            "video_id": self.video_id,
            "clip_id": self.clip_id,
            "stage": self.stage,
        }


# ----------------------------------------------------------------------
# Batch engine (E-001..E-010 + Round 3 BATCH-01..BATCH-25)
# ----------------------------------------------------------------------
class BatchJobState(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class BatchJob(Base):
    __tablename__ = "batch_jobs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    operation = Column(String(64), nullable=False, index=True)
    total_items = Column(Integer, nullable=False, default=0)
    ok_count = Column(Integer, nullable=False, default=0)
    failed_count = Column(Integer, nullable=False, default=0)
    skipped_count = Column(Integer, nullable=False, default=0)
    actor = Column(String(64), nullable=False, default="system")
    state = Column(
        SQLEnum(BatchJobState),
        default=BatchJobState.PENDING,
        nullable=False,
        index=True,
    )
    params_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)

    @property
    def params(self) -> dict:
        if not self.params_json:
            return {}
        try:
            return json.loads(self.params_json)
        except Exception:
            return {}

    @params.setter
    def params(self, val: Optional[dict]) -> None:
        if not val:
            self.params_json = None
        else:
            self.params_json = json.dumps(val)


class BatchItemLock(Base):
    """Per-item exclusive lock for concurrent batches."""
    __tablename__ = "batch_item_locks"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    batch_id = Column(String(36), nullable=False, index=True)
    operation = Column(String(64), nullable=False)
    item_id = Column(String(64), nullable=False, index=True)
    acquired_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    released_at = Column(DateTime, nullable=True, index=True)


class BatchAuditLog(Base):
    """Append-only audit log of every batch op + per-item result."""
    __tablename__ = "batch_audit_log"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    batch_id = Column(String(36), nullable=False, index=True)
    operation = Column(String(64), nullable=False, index=True)
    item_id = Column(String(64), nullable=False, index=True)
    actor = Column(String(64), nullable=False, default="system")
    status = Column(String(16), nullable=False)  # OK / FAILED / SKIPPED
    message = Column(Text, nullable=True)
    before_json = Column(Text, nullable=True)
    after_json = Column(Text, nullable=True)
    idempotency_key = Column(String(64), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "batch_id": self.batch_id,
            "operation": self.operation,
            "item_id": self.item_id,
            "actor": self.actor,
            "status": self.status,
            "message": self.message,
            "idempotency_key": self.idempotency_key,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ----------------------------------------------------------------------
# Webhooks (E-008)
# ----------------------------------------------------------------------
class Webhook(Base):
    __tablename__ = "webhooks"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    name = Column(String(128), nullable=False)
    url = Column(String(1024), nullable=False)
    secret_hash = Column(String(64), nullable=False)
    events_json = Column(Text, nullable=False, default="[]")
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    created_by = Column(String(64), nullable=False, default="system")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    @property
    def events(self) -> list:
        try:
            return json.loads(self.events_json or "[]")
        except Exception:
            return []

    @events.setter
    def events(self, val: list) -> None:
        self.events_json = json.dumps(val)


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    webhook_id = Column(String(36), ForeignKey("webhooks.id", ondelete="CASCADE"), nullable=False, index=True)
    event = Column(String(64), nullable=False, index=True)
    status = Column(String(32), nullable=False, index=True)
    http_status = Column(Integer, nullable=True)
    duration_ms = Column(Integer, nullable=False, default=0)
    attempts = Column(Integer, nullable=False, default=1)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    webhook = relationship("Webhook")


class WebhookDeadLetter(Base):
    __tablename__ = "webhook_dead_letters"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    webhook_id = Column(String(36), ForeignKey("webhooks.id", ondelete="CASCADE"), nullable=False, index=True)
    event = Column(String(64), nullable=False)
    body = Column(Text, nullable=False)
    last_status = Column(Integer, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


# ----------------------------------------------------------------------
# Settings I/O (E-009)
# ----------------------------------------------------------------------
class SettingsExport(Base):
    __tablename__ = "settings_exports"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    payload_json = Column(Text, nullable=False)
    schema_version = Column(Integer, nullable=False, default=1)
    created_by = Column(String(64), nullable=False, default="system")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


# ----------------------------------------------------------------------
# Cost roll-up (E-010)
# ----------------------------------------------------------------------
class CostEntry(Base):
    __tablename__ = "cost_entries"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    job_id = Column(String(64), nullable=True, index=True)
    clip_id = Column(String(36), ForeignKey("clips.id", ondelete="CASCADE"), nullable=True, index=True)
    kind = Column(String(32), nullable=False, index=True)  # llm_input, llm_output, tts, render, etc.
    units = Column(Float, nullable=False, default=0)
    unit_cost_usd = Column(Float, nullable=False, default=0)
    total_cost_usd = Column(Float, nullable=False, default=0)
    actor = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    @property
    def total(self) -> float:
        return self.units * self.unit_cost_usd


class CostBudget(Base):
    __tablename__ = "cost_budgets"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    scope = Column(String(32), nullable=False, default="global", index=True)  # global, channel
    scope_id = Column(String(64), nullable=True, index=True)
    soft_limit_usd = Column(Float, nullable=False, default=10.0)
    hard_limit_usd = Column(Float, nullable=False, default=20.0)
    period = Column(String(16), nullable=False, default="daily")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


# ----------------------------------------------------------------------
# Caption editor (E-016)
# ----------------------------------------------------------------------
# (Columns added directly to ClipCandidate in migration 008 — see models.)


# ----------------------------------------------------------------------
# A/B testing (E-017)
# ----------------------------------------------------------------------
class ABTestStatus2(str, Enum):
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"


class ABTest2(Base):
    __tablename__ = "ab_tests_2"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    clip_id = Column(String(36), ForeignKey("clips.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)
    metric = Column(String(16), default="ctr", nullable=False)
    status = Column(String(16), default="RUNNING", nullable=False, index=True)
    rotation_seconds = Column(Integer, default=21600, nullable=False)
    winner_variant_id = Column(String(36), nullable=True)
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    variants = relationship(
        "ABVariant2", cascade="all, delete-orphan",
        order_by="ABVariant2.created_at",
    )


class ABVariant2(Base):
    __tablename__ = "ab_variants_2"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    test_id = Column(String(36), ForeignKey("ab_tests_2.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(100), nullable=False)
    description = Column(String(5000), nullable=True)
    tags = Column(Text, nullable=True)
    is_control = Column(Boolean, default=False, nullable=False)
    impressions = Column(Integer, default=0, nullable=False)
    views = Column(Integer, default=0, nullable=False)
    last_published_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ABMetricSnapshot2(Base):
    __tablename__ = "ab_metric_snapshots_2"

    id = Column(Integer, primary_key=True, autoincrement=True)
    test_id = Column(String(36), ForeignKey("ab_tests_2.id", ondelete="CASCADE"), nullable=False, index=True)
    variant_id = Column(String(36), ForeignKey("ab_variants_2.id", ondelete="CASCADE"), nullable=False, index=True)
    captured_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    impressions = Column(Integer, default=0, nullable=False)
    views = Column(Integer, default=0, nullable=False)
    ctr = Column(Float, default=0.0, nullable=False)


# ----------------------------------------------------------------------
# Saved views + search (EASE-02, E-021)
# ----------------------------------------------------------------------
class SavedView(Base):
    __tablename__ = "saved_views"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    name = Column(String(128), nullable=False)
    expression = Column(Text, nullable=False, default="")
    owner = Column(String(64), nullable=False, default="system")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class OperatorGuide(Base):
    __tablename__ = "operator_guides"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    slug = Column(String(128), nullable=False, unique=True, index=True)
    title = Column(String(255), nullable=False)
    body = Column(Text, nullable=False)
    order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)




# ----------------------------------------------------------------------
# Overlay engine (E-011)
# ----------------------------------------------------------------------
class OverlayPreset(Base):
    __tablename__ = "overlay_preset"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False, unique=True, index=True)
    description = Column(Text, nullable=False, default="")
    spec_json = Column(Text, nullable=False)
    spec_sha256 = Column(String(64), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_by = Column(String(64), nullable=False, default="system")
    is_builtin = Column(Integer, nullable=False, default=0)


class OverlayRenderAudit(Base):
    __tablename__ = "overlay_render_audit"

    id = Column(Integer, primary_key=True, autoincrement=True)
    candidate_id = Column(Integer, nullable=True, index=True)
    preset_id = Column(Integer, nullable=True, index=True)
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    finished_at = Column(DateTime, nullable=True)
    ok = Column(Integer, nullable=False, default=0)
    error = Column(Text, nullable=True)
    ffmpeg_cmd = Column(Text, nullable=True)
    output_path = Column(String(1024), nullable=True)
    contrast_ratio = Column(Float, nullable=True)
    bytes_written = Column(Integer, nullable=True)
