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
from sqlalchemy.orm import relationship, validates
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


class ClipStatus(str, Enum):
    RENDERING = "RENDERING"
    QUALITY_CHECK = "QUALITY_CHECK"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    FAILED_NEEDS_REVIEW = "FAILED_NEEDS_REVIEW"


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
    draft_version = Column(Integer, default=1, nullable=False)
    source = Column(String(32), default="pipeline", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    candidate = relationship("ClipCandidate", back_populates="clip")
    publish_log = relationship("PublishLog", back_populates="clip", uselist=False, cascade="all, delete-orphan")
    platform_publish_logs = relationship("PlatformPublishLog", back_populates="clip", cascade="all, delete-orphan")
    publish_jobs = relationship("PublishJob", back_populates="clip", cascade="all, delete-orphan")

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


