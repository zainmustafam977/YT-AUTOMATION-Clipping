"""
Configuration and settings for YouTube Shorts Automation Platform.
Implements unified single source of truth for all pipeline constants.
"""

import os
import logging
from pathlib import Path
from typing import Any, Annotated, Optional, List, Dict
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Shipped default operator password (SEV-2 - see docs/audit/VERIFICATION_REPORT.md
# and docs/audit/findings.yaml). It is a real credential-shaped value that was
# committed to source, so it must be treated as burned:
#   ROTATE THIS SECRET - if any deployment ever used it, change it everywhere.
# The literal is retained ONLY so startup can detect and refuse to boot on it.
# Do not reintroduce it as a fallback anywhere else in the codebase.
LEGACY_DEFAULT_DASHBOARD_PASSWORD = "AdminPass12345!"

logger = logging.getLogger(__name__)


def _parse_key_pool(value: Any) -> List[str]:
    """Parse an API-key pool value, tolerating the blank shipped template.

    .env.example ships the pool entries empty (GEMINI_API_KEYS=,
    GROQ_API_KEYS=, ...) and setup_environment.py copies that template
    verbatim to .env. Recent pydantic-settings JSON-decodes complex fields
    at the source and raises
      SettingsError: error parsing value for field ... from source
      "DotEnvSettingsSource"
    on an empty string, so the application cannot even be imported after a
    bootstrap - every test touching settings then dies during collection.

    The fields are annotated NoDecode so the raw string reaches this
    validator instead of being rejected upstream. Blank means "no keys",
    which is the field default. JSON arrays and comma-separated values are
    both accepted for real key lists.
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value).strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            import json

            parsed = json.loads(text)
        except ValueError as exc:
            raise ValueError(f"not a valid JSON list: {text!r}") from exc
        if not isinstance(parsed, list):
            raise ValueError(f"expected a JSON list, got {type(parsed).__name__}")
        return [str(v).strip() for v in parsed if str(v).strip()]
    return [part.strip() for part in text.split(",") if part.strip()]


class PipelineSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # General App Config
    APP_NAME: str = "YouTube Shorts Automation"
    ENV: str = Field(default="development", description="development, staging, or production")
    DEBUG: bool = False
    
    # Base paths
    ROOT_DIR: Path = Path(__file__).resolve().parent.parent.parent
    STORAGE_DIR: Path = ROOT_DIR / "storage"
    TEMP_DIR: Path = ROOT_DIR / "storage" / "temp"
    OUTPUT_DIR: Path = ROOT_DIR / "storage" / "outputs"
    DOWNLOADS_DIR: Path = ROOT_DIR / "storage" / "downloads"
    RAW_DIR: Path = ROOT_DIR / "storage" / "downloads"
    SFX_DIR: Path = ROOT_DIR / "assets" / "sfx_library"
    MODELS_DIR: Path = ROOT_DIR / "storage" / "models"
    FONTS_DIR: Path = ROOT_DIR / "assets" / "fonts"

    # Database
    DATABASE_URL: str = Field(default="sqlite:///./storage/shorts_automation.db")
    POSTGRES_PASSWORD: Optional[str] = Field(default=None, description="PostgreSQL password if using postgres")

    # Security & Encryption (Fernet via SECRETS_MASTER_KEY)
    SECRETS_MASTER_KEY: Optional[str] = Field(
        default=None,
        description="Master key used by secretbox for Fernet symmetric encryption of OAuth tokens and API secrets",
    )
    MASTER_ENCRYPTION_KEY: Optional[str] = Field(
        default=None,
        description="Legacy master key (deprecated, use SECRETS_MASTER_KEY)",
    )
    ENCRYPTION_SALT: Optional[str] = Field(
        default=None,
        description="Salt for PBKDF2 key derivation (legacy)",
    )
    ENFORCE_SECRET_ENTROPY: bool = Field(
        default=False,
        description="Strictly disallow placeholder keys and enforce high entropy across all secrets at startup",
    )

    # Multi-Provider LLM Settings (Groq, Gemini, OpenRouter, OpenAI, Heuristic fallback)
    LLM_PROVIDER: str = Field(
        default="auto",
        description="LLM provider strategy: 'auto', 'groq', 'gemini', 'openrouter', 'openai'",
    )
    LLM_MODEL_NAME: str = Field(
        default="gemini-3.8-flash",
        description="Swappable Gemini model identifier (e.g. gemini-3.8-flash, gemini-3-flash)",
    )
    GEMINI_PACING_SEC: float = Field(
        default=4.0,
        ge=1.0,
        le=10.0,
        description="Minimum client-side pacing interval in seconds per Gemini key (15 RPM Free Tier ceiling guard)",
    )
    GEMINI_TIMEOUT_SEC: float = Field(
        default=25.0,
        ge=5.0,
        le=120.0,
        description="Network socket and completion timeout in seconds for Gemini API requests",
    )
    USE_GROQ_WHISPER: bool = Field(
        default=False,
        description="Enable high-speed cloud Whisper transcription via Groq (whisper-large-v3-turbo, 28.8k ASD free)",
    )
    GEMINI_API_KEY: Optional[str] = Field(default=None, description="Google Gemini API Key")
    @field_validator("GEMINI_API_KEYS", mode="before")
    @classmethod
    def _validate_gemini_api_keys(cls, v: Any) -> Any:
        return _parse_key_pool(v)

    GEMINI_API_KEYS: Annotated[List[str], NoDecode] = Field(default_factory=list, description="Pool of Gemini API keys for load balancing & 429 failover")
    GROQ_API_KEY: Optional[str] = Field(default=None, description="Groq API Key (high-speed LPU inference)")
    @field_validator("GROQ_API_KEYS", mode="before")
    @classmethod
    def _validate_groq_api_keys(cls, v: Any) -> Any:
        return _parse_key_pool(v)

    GROQ_API_KEYS: Annotated[List[str], NoDecode] = Field(default_factory=list, description="Pool of Groq API keys for load balancing & 429 failover")
    GROQ_MODEL_NAME: str = Field(
        default="openai/gpt-oss-120b",
        description="Groq model identifier (e.g. openai/gpt-oss-120b, qwen/qwen3.8-27b)",
    )
    OPENROUTER_API_KEY: Optional[str] = Field(default=None, description="OpenRouter API Key for multi-model fallback")
    @field_validator("OPENROUTER_API_KEYS", mode="before")
    @classmethod
    def _validate_openrouter_api_keys(cls, v: Any) -> Any:
        return _parse_key_pool(v)

    OPENROUTER_API_KEYS: Annotated[List[str], NoDecode] = Field(default_factory=list, description="Pool of OpenRouter API keys for load balancing & 429 failover")
    OPENROUTER_MODEL_NAME: str = Field(
        default="meta-llama/llama-3.3-70b-instruct:free",
        description="OpenRouter model identifier (e.g. meta-llama/llama-3.3-70b-instruct:free, google/gemini-2.0-flash-exp:free)",
    )
    OPENAI_API_KEY: Optional[str] = Field(default=None, description="OpenAI API Key for backup / failover")
    @field_validator("OPENAI_API_KEYS", mode="before")
    @classmethod
    def _validate_openai_api_keys(cls, v: Any) -> Any:
        return _parse_key_pool(v)

    OPENAI_API_KEYS: Annotated[List[str], NoDecode] = Field(default_factory=list, description="Pool of OpenAI API keys for load balancing & 429 failover")
    OPENAI_MODEL_NAME: str = Field(
        default="gpt-4o-mini",
        description="OpenAI model identifier (e.g. gpt-4o-mini, gpt-4o)",
    )
    GROQ_TOKEN_THRESHOLD: int = Field(
        default=5500,
        ge=1000,
        le=32000,
        description="Estimated prompt tokens threshold above which requests bypass Groq and route directly to Gemini (1M context)",
    )
    LLM_FALLBACK_ON_RATE_LIMIT: bool = Field(
        default=True,
        description="Automatically failover to secondary provider when primary returns HTTP 429",
    )
    LLM_TEMPERATURE: float = Field(
        default=0.2,
        ge=0.0,
        le=1.0,
        description="Sampling temperature for viral hook generation",
    )
    LLM_DAILY_BUDGET_USD: float = Field(
        default=5.0,
        ge=0.0,
        description="Daily spend limit in USD for LLM inference; refuses calls and defers jobs when exceeded",
    )
    LLM_MODEL_PRICING: Dict[str, Dict[str, Dict[str, float]]] = Field(
        default_factory=lambda: {
            "groq": {
                "openai/gpt-oss-120b": {"input": 0.15, "output": 0.60},
                "openai/gpt-oss-20b": {"input": 0.075, "output": 0.30},
                "qwen/qwen3.8-27b": {"input": 0.20, "output": 0.60},
                "groq/compound-mini": {"input": 0.05, "output": 0.08},
                "groq/compound": {"input": 0.10, "output": 0.15},
                "_default": {"input": 0.05, "output": 0.08},
            },
            "gemini": {
                "gemini-3.8-flash": {"input": 0.075, "output": 0.30},
                "gemini-3.6-flash": {"input": 0.075, "output": 0.30},
                "gemini-2.5-flash": {"input": 0.075, "output": 0.30},
                "gemini-2.0-flash": {"input": 0.075, "output": 0.30},
                "gemini-1.5-flash": {"input": 0.075, "output": 0.30},
                "gemini-2.5-pro": {"input": 1.25, "output": 5.00},
                "gemini-1.5-pro": {"input": 1.25, "output": 5.00},
                "_default": {"input": 0.075, "output": 0.30},
            },
            "openrouter": {
                "meta-llama/llama-3.3-70b-instruct:free": {"input": 0.0, "output": 0.0},
                "google/gemini-2.0-flash-exp:free": {"input": 0.0, "output": 0.0},
                "liquid/lfm-2.5-2.6b:free": {"input": 0.0, "output": 0.0},
                "_default": {"input": 0.0, "output": 0.0},
            },
            "openai": {
                "gpt-4o-mini": {"input": 0.15, "output": 0.60},
                "gpt-4o": {"input": 2.50, "output": 10.00},
                "_default": {"input": 0.15, "output": 0.60},
            },
        },
        description="LLM pricing matrix (USD per 1,000,000 tokens) mapped by provider and model",
    )

    # Transcription & Face Tracking
    WHISPER_MODEL_SIZE: str = Field(
        default="base",
        description="Whisper model size: tiny, base, small, medium, large-v3",
    )
    WHISPER_BEAM_SIZE: int = Field(
        default=5,
        ge=1,
        le=10,
        description="Beam search width for Whisper transcription decoding",
    )
    WHISPER_VAD_FILTER: bool = Field(
        default=True,
        description="Enable Silero VAD speech filter for timestamp accuracy",
    )
    WHISPER_LANGUAGE: str = Field(
        default="en",
        description="Default spoken language code or 'auto'",
    )
    WHISPER_TEMPERATURE: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Decoding temperature for transcription",
    )
    FACE_TRACKING_CONFIDENCE: float = Field(
        default=0.6,
        ge=0.1,
        le=1.0,
        description="Minimum confidence threshold for MediaPipe face tracking",
    )

    # Segment Limits (Aligned across selection & reporting)
    MIN_SEGMENTS_PER_VIDEO: int = Field(default=3, ge=1, le=20)
    MAX_SEGMENTS_PER_VIDEO: int = Field(default=5, ge=1, le=20)

    # Duration Boundaries (Unified across AI selection & quality gate)
    TARGET_DURATION_MIN_SEC: int = Field(default=15, ge=5, le=180)
    TARGET_DURATION_MAX_SEC: int = Field(default=58, ge=10, le=180)
    EXTENDED_SHORTS_ALLOWED: bool = False
    MAX_EXTENDED_DURATION_SEC: int = 180

    # Video & Reframing
    TARGET_WIDTH: int = 1080
    TARGET_HEIGHT: int = 1920
    VIDEO_SCALE_ALGORITHM: str = Field(
        default="lanczos",
        description="Scaling interpolation algorithm: lanczos, bicubic, spline, bilinear",
    )
    VIDEO_ENCODING_CRF: int = Field(
        default=18,
        ge=15,
        le=28,
        description="Visually lossless H.264 CRF quality level (18 = broadcast standard)",
    )
    VIDEO_ENCODING_PRESET: str = Field(
        default="medium",
        description="Encoder speed preset: ultrafast, fast, medium, slow, p4..p7",
    )
    VIDEO_TARGET_FPS: int = Field(
        default=30,
        ge=24,
        le=60,
        description="Target framerate for rendered short video",
    )
    SMOOTHING_FACTOR: float = Field(default=0.85, ge=0.1, le=0.99)
    SAFE_ZONE_MIN_X: float = Field(default=0.15, description="Safe frame margin 15%")
    SAFE_ZONE_MAX_X: float = Field(default=0.85, description="Safe frame margin 85%")

    # Safe Zones & UI Collision Dead Zones (1080x1920 canvas)
    # YouTube Shorts native UI covers bottom 20% (Y > 80% / 1536px, 384px height) and right action bar
    UI_DEADZONE_BOTTOM_Y_RATIO: float = Field(
        default=0.80,
        description="Rejection boundary: bottom 384px (Y > 80% / 1536px) reserved for title/channel UI",
    )
    UI_DEADZONE_RIGHT_X_RATIO: float = Field(
        default=0.85,
        description="Rejection boundary: right 15% (X > 85% / 918px) reserved for like/share/remix buttons",
    )
    CAPTION_MARGIN_VERTICAL: int = Field(
        default=420,
        description="ASS subtitle margin from bottom: 420px (Y = 78.1% / 1500px), providing a 36px buffer above the 384px UI dead zone",
    )

    # Captioning & Typography
    DEFAULT_CAPTION_PRESET: str = Field(
        default="hormozi",
        description="Default caption style template: hormozi, mrbeast, neon, minimalist, high_contrast_box",
    )
    CAPTION_FONT_NAME: str = "DejaVu Sans"
    CAPTION_FONT_FILE: Path = ROOT_DIR / "assets" / "fonts" / "DejaVuSans-Bold.ttf"
    CAPTION_FONT_SIZE: int = 24
    CAPTION_PRIMARY_COLOR: str = "&H00FFFFFF"  # White
    CAPTION_HIGHLIGHT_COLOR: str = "&H0000FFFF"  # Yellow / Neon
    CAPTION_OUTLINE_COLOR: str = "&H00000000"  # Black
    CAPTION_OUTLINE_WIDTH: int = 3
    ENABLE_LUMINANCE_SAMPLING: bool = True
    ENABLE_HOOK_OVERLAY: bool = Field(
        default=False,
        description="Enable visual hook text overlay during the opening 3 seconds of rendered Shorts",
    )


    # Audio Mastering & Loudness (EBU R128)
    AUDIO_BITRATE: str = Field(
        default="320k",
        description="Master audio AAC bitrate: 128k, 192k, 256k, 320k",
    )
    AUDIO_SAMPLE_RATE: int = Field(
        default=48000,
        description="Master audio sampling frequency in Hz: 44100, 48000",
    )
    TARGET_LUFS: float = -14.0
    TRUE_PEAK_DBFS: float = -1.0
    MIN_SPEECH_SNR_DB: float = 8.0
    ENABLE_SFX: bool = True
    ENABLE_AUDIO_DUCKING: bool = Field(
        default=True,
        description="Enable speech-activated background music sidechain ducking",
    )
    DUCKING_ATTENUATION_DB: float = Field(
        default=-14.0,
        description="Attenuation applied to background music during active speech (dB)",
    )
    DUCKING_ATTACK_MS: int = Field(
        default=15,
        ge=1,
        le=500,
        description="Sidechain compressor attack time in milliseconds",
    )
    DUCKING_RELEASE_MS: int = Field(
        default=350,
        ge=50,
        le=2000,
        description="Sidechain compressor release time in milliseconds",
    )
    SPEECH_HPF_CUTOFF_HZ: int = Field(
        default=80,
        ge=20,
        le=300,
        description="High-pass filter cutoff frequency to eliminate room rumble",
    )
    VOCAL_PRESENCE_FREQ_HZ: int = Field(
        default=3000,
        ge=1000,
        le=8000,
        description="Vocal presence shelf/peaking filter frequency in Hz",
    )
    VOCAL_PRESENCE_GAIN_DB: float = Field(
        default=2.5,
        ge=0.0,
        le=12.0,
        description="Vocal presence shelf/peaking filter boost in dB",
    )
    LOUDNESS_TWO_PASS: bool = Field(
        default=True,
        description="Enable ITU-R BS.1770-4 two-pass loudness normalization",
    )
    HARDWARE_ACCELERATION_ENABLED: bool = Field(
        default=True,
        description="Enable automatic hardware acceleration (NVENC, QSV, AMF) for video rendering",
    )
    HARDWARE_ENCODER_OVERRIDE: Optional[str] = Field(
        default=None,
        description="Explicit encoder override: h264_nvenc, h264_qsv, h264_amf, libx264",
    )
    MUSIC_LIBRARY_DIR: Path = Path("assets/music")

    # Monitoring & Alerting
    ALERT_WEBHOOK_URL: Optional[str] = Field(default=None, description="Incoming Generic Alert Webhook URL")
    SLACK_WEBHOOK_URL: Optional[str] = Field(default=None, description="Incoming Slack Webhook URL")
    DISCORD_WEBHOOK_URL: Optional[str] = Field(default=None, description="Discord Webhook URL")
    TELEGRAM_BOT_TOKEN: Optional[str] = Field(default=None, description="Telegram Bot Token")
    TELEGRAM_CHAT_ID: Optional[str] = Field(default=None, description="Telegram Chat ID")
    SENTRY_DSN: Optional[str] = Field(default=None, description="Sentry DSN for error telemetry")
    DISK_ALERT_THRESHOLD_PCT: float = Field(default=80.0, description="Disk usage alert threshold percentage")
    QUOTA_REMAINING_ALERT_THRESHOLD_PCT: float = Field(default=20.0, description="Quota remaining alert threshold percentage")
    RETENTION_RAW_SOURCE_DAYS: int = Field(
        default=14,
        ge=1,
        description="Days to retain raw source videos after all its clips are published",
    )
    RETENTION_INTERMEDIATES_DAYS: int = Field(
        default=3,
        ge=0,
        description="Days to retain intermediate assets (crop plans, subtitles, temp chunks) after final render is published",
    )
    RETENTION_KEEP_FINAL_RENDERS: bool = Field(
        default=True,
        description="Whether to keep final rendered clips permanently (never auto-deleted by default)",
    )

    # Publishing & Quotas (Dec 2025 revisions)
    DRY_RUN_PUBLISHING: bool = Field(default=True, description="When true, simulates upload without calling YouTube API")
    DEFAULT_PRIVACY_STATUS: str = Field(default="unlisted", description="unlisted, private, or public")
    DAILY_QUOTA_LIMIT: int = 10000
    QUOTA_SAFETY_RESERVE: int = 300
    QUOTA_WARNING_THRESHOLD: float = 0.80  # Alert when 80% quota is used
    QUOTA_VIDEOS_INSERT_COST: int = Field(default=1600, description="Quota cost for videos.insert (official YouTube API v3)")
    QUOTA_VIDEOS_UPDATE_COST: int = Field(default=50, description="Quota cost for videos.update")
    QUOTA_VIDEOS_DELETE_COST: int = Field(default=50, description="Quota cost for videos.delete")
    QUOTA_CHANNELS_LIST_COST: int = Field(default=1, description="Quota cost for channels.list")
    QUOTA_SEARCH_LIST_COST: int = Field(default=100, description="Quota cost for search.list")
    QUOTA_DAILY_VIDEOS_INSERT_LIMIT: int = Field(default=6, description="Daily limit of videos.insert calls (derived from daily quota: (10000 - 300) // 1600 = 6)")

    # Publish Slots & Scheduling Policy
    PUBLISH_SLOT_TIMES_LOCAL: List[str] = Field(
        default=["10:00", "18:00"],
        description="Daily release times in channel local time (e.g. 2 per day at 10:00 and 18:00)",
    )
    PUBLISH_DEFAULT_TIMEZONE: str = Field(
        default="America/New_York",
        description="Default timezone for scheduling slots if not set by channel",
    )
    PUBLISH_MIN_SLOT_LEAD_MINUTES: int = Field(
        default=15,
        description="Minimum buffer minutes ahead of current time for slot selection",
    )
    DRIP_FEED_ENABLED: bool = Field(
        default=False,
        description="Deprecated: Drip-feed daemon replaced by native publishAt and publish slots",
    )
    DRIP_FEED_POLL_INTERVAL_SECONDS: int = Field(
        default=60,
        ge=10,
        le=3600,
        description="Deprecated polling interval in seconds",
    )
    PEXELS_API_KEY: Optional[str] = Field(
        default=None,
        description="Pexels API key for automated stock B-roll video downloads",
    )
    ENABLE_BROLL_OVERLAYS: bool = Field(
        default=False,
        description="Enable automated B-roll stock footage overlay injection",
    )

    # Multi-Platform Syndication (TikTok, Instagram Reels)
    TIKTOK_CLIENT_KEY: Optional[str] = None
    TIKTOK_CLIENT_SECRET: Optional[str] = None
    TIKTOK_ACCESS_TOKEN: Optional[str] = None
    TIKTOK_OPEN_ID: Optional[str] = None
    INSTAGRAM_ACCESS_TOKEN: Optional[str] = None
    INSTAGRAM_USER_ID: Optional[str] = None
    ENABLED_SYNDICATION_PLATFORMS: list = Field(
        default=["youtube"],
        description="List of enabled syndication platforms: youtube, tiktok, instagram",
    )

    # Bayesian Multi-Armed Bandit A/B Winner Promotion & Quota-Safe Title Swapping
    ENABLE_AB_TESTING: bool = Field(
        default=True,
        description="Enable autonomous generation of 3 title and thumbnail variants per clip",
    )
    AB_ENABLE_TITLE_SWAPPING: bool = Field(
        default=True,
        description="Master kill-switch flag to enable or disable all live title swapping",
    )
    AB_WINNER_CHECK_INTERVAL_SECONDS: int = Field(
        default=120,
        ge=30,
        le=3600,
        description="Interval in seconds between A/B winner evaluations",
    )
    AB_MIN_IMPRESSIONS: int = Field(
        default=1000,
        ge=10,
        description="Minimum impressions required before considering an A/B variation for winner promotion",
    )
    AB_MIN_TEST_DURATION_HOURS: float = Field(
        default=72.0,
        ge=1.0,
        description="Minimum test duration in hours before a title swap decision is permitted",
    )
    AB_MAX_TITLE_SWAPS: int = Field(
        default=1,
        ge=1,
        description="Maximum number of title swaps allowed per video to avoid algorithm thrashing and quota burns",
    )
    AB_RELATIVE_MARGIN_THRESHOLD: float = Field(
        default=0.10,
        ge=0.01,
        le=1.0,
        description="Minimum relative CTR margin challenger must exceed active variant by (e.g. 0.10 for +10%)",
    )
    AB_CONFIDENCE_THRESHOLD: float = Field(
        default=0.95,
        ge=0.80,
        le=0.999,
        description="Confidence threshold required to declare a winning title",
    )
    AB_ROLLBACK_VELOCITY_DROP: float = Field(
        default=0.50,
        ge=0.10,
        le=0.90,
        description="Circuit breaker threshold: velocity drop ratio triggering emergency rollback",
    )


    # Detection & WebSub
    RSS_POLL_INTERVAL_MINUTES: int = 10
    WEBSUB_HUB_URL: str = "https://pubsubhubbub.appspot.com/subscribe"
    WEBSUB_LEASE_SECONDS: int = 86400           # 24h default
    WEBSUB_RENEW_THRESHOLD: float = 0.80        # Renew at 80% of lease lifetime
    WEBSUB_CALLBACK_URL: Optional[str] = None
    WEBSUB_SECRET: Optional[str] = None

    # Fault tolerance
    MAX_RETRY_COUNT: int = Field(default=3, ge=1, le=10)

    # Operator Dashboard
    DASHBOARD_HOST: str = "127.0.0.1"
    DASHBOARD_PORT: int = 8080
    DASHBOARD_SECRET_KEY: str = Field(default="change-me-in-prod", description="Session signing key")
    DASHBOARD_USERNAME: str = Field(default="admin", description="Operator dashboard username")
    # Deliberately empty, not LEGACY_DEFAULT_DASHBOARD_PASSWORD.
    #
    # The shipped literal was a working admin credential committed to source, so
    # it must never be usable again - not even as a development convenience.
    # Both seeding sites (dashboard/auth.py and
    # services/storage/reset_service.py) already replace an empty or legacy
    # value with a freshly generated random password and log it once, so an
    # unset value still yields a reachable admin account without shipping a
    # known credential. Staging/production/strict mode require an explicit
    # value and refuse to boot otherwise.
    #
    # ROTATE THIS SECRET: if any deployment ever used AdminPass12345!, change
    # it everywhere. Treat it as permanently burned.
    DASHBOARD_PASSWORD: str = Field(
        default="",
        description=(
            "Operator dashboard password, used only to seed the initial admin "
            "account. Empty means 'generate a random password at boot and log "
            "it once'. Staging/production/STRICT_MODE require an explicit, "
            "strong value supplied through the environment."
        ),
    )

    # Video Ingestion & Triple-Tier Authentication (Pillars 1-4)
    YOUTUBE_OAUTH_TOKEN_PATH: Path = Field(
        default=Path(__file__).resolve().parent.parent.parent / "storage" / "youtube_oauth.json",
        description="Path to AES-256-GCM encrypted OAuth2 token vault file",
    )
    YOUTUBE_OAUTH_CLIENT_ID: Optional[str] = Field(
        default=None,
        description="Google OAuth2 Client ID for RFC-8628 device flow",
    )
    YOUTUBE_OAUTH_CLIENT_SECRET: Optional[str] = Field(
        default=None,
        description="Google OAuth2 Client Secret for RFC-8628 device flow",
    )
    INGESTION_ENABLE_OAUTH: bool = Field(
        default=True,
        description="Enable OAuth2 device token authentication tier for YouTube ingestion",
    )
    YOUTUBE_HARVESTER_COOKIES_PATH: Path = Field(
        default=Path(__file__).resolve().parent.parent.parent / "storage" / "harvester_cookies.txt.enc",
        description="Path to fresh Netscape cookies exported by background session harvester (encrypted at rest)",
    )
    YOUTUBE_COOKIES_PATH: Path = Field(
        default=Path(__file__).resolve().parent.parent.parent / "storage" / "cookies.txt",
        description="Path to manual fallback Netscape cookies vault file",
    )
    INGESTION_PROXY_POOL: List[str] = Field(
        default_factory=list,
        description="Pool of HTTP/HTTPS/SOCKS5 proxy URLs for round-robin rotation",
    )
    INGESTION_PROXY_STRICT_MODE: bool = Field(
        default=False,
        description="If True, rejects downloads when no proxy is available. If False, allows direct fallback.",
    )
    INGESTION_RESIDENTIAL_API_KEY: Optional[str] = Field(
        default=None,
        description="API key for commercial residential proxy provider (Webshare/Bright Data)",
    )
    INGESTION_POLICY_MODE: str = Field(
        default="two_tier",
        description="Content policy triage mode: 'two_tier', 'strict_gate', or 'auto_skip'",
    )
    HOTFOLDER_DIR: Path = Field(
        default=Path(__file__).resolve().parent.parent.parent / "storage" / "hotfolder",
        description="Hotfolder directory monitored for automated video drops",
    )
    HOTFOLDER_ENABLED: bool = Field(
        default=True,
        description="Enable automated background hotfolder watcher",
    )
    HOTFOLDER_POLL_INTERVAL_SECONDS: float = Field(
        default=5.0,
        description="Hotfolder poll interval in seconds",
    )
    MAX_INGESTION_DURATION_HOURS: float = Field(
        default=4.0,
        description="Maximum allowed duration in hours for ingested video content",
    )
    MAX_VIDEO_DURATION_SEC: int = Field(
        default=14400,
        ge=60,
        description="Maximum allowed video duration in seconds (default 4 hours = 14400s)",
    )
    MAX_INGESTION_FILE_SIZE_MB: float = Field(
        default=5120.0,
        description="Maximum allowed file size in MB for ingested video content (default 5GB = 5120MB)",
    )
    MAX_VIDEO_FILESIZE_BYTES: int = Field(
        default=5 * 1024 * 1024 * 1024,
        ge=1024 * 1024,
        description="Maximum allowed video file size in bytes (default 5GB)",
    )
    INGESTION_DOWNLOAD_TIMEOUT_SEC: float = Field(
        default=1800.0,
        ge=30.0,
        description="Hard timeout in seconds for video stream downloads (default 30 minutes)",
    )
    INGESTION_RATE_LIMIT_PER_MINUTE: int = Field(
        default=5,
        ge=1,
        description="Rate limit for manual ingestion requests per minute per user",
    )
    FFMPEG_MAX_THREADS: int = Field(
        default=4,
        ge=1,
        le=64,
        description="Hard cap on ffmpeg/ffprobe thread count to prevent CPU starvation",
    )
    FFMPEG_SUBPROCESS_TIMEOUT_SEC: float = Field(
        default=600.0,
        ge=10.0,
        description="Hard timeout in seconds for ffmpeg/ffprobe subprocess execution",
    )


    @model_validator(mode="after")
    def validate_bounds(self) -> "PipelineSettings":
        if self.MIN_SEGMENTS_PER_VIDEO > self.MAX_SEGMENTS_PER_VIDEO:
            raise ValueError(
                f"MIN_SEGMENTS_PER_VIDEO ({self.MIN_SEGMENTS_PER_VIDEO}) cannot exceed "
                f"MAX_SEGMENTS_PER_VIDEO ({self.MAX_SEGMENTS_PER_VIDEO})"
            )
        if self.TARGET_DURATION_MIN_SEC > self.TARGET_DURATION_MAX_SEC:
            raise ValueError(
                f"TARGET_DURATION_MIN_SEC ({self.TARGET_DURATION_MIN_SEC}) cannot exceed "
                f"TARGET_DURATION_MAX_SEC ({self.TARGET_DURATION_MAX_SEC})"
            )
        # Production & Staging security guard: disallow default credentials & enforce minimum entropy
        if self.ENV in ("staging", "production") or self.ENFORCE_SECRET_ENTROPY:
            insecure_passwords = {
                "change-me", "admin", "password", "123456", "12345678", "root",
                "ytpassword_secret_change_me", "",
                LEGACY_DEFAULT_DASHBOARD_PASSWORD.lower(),
            }
            mode = self.ENV if self.ENV in ("staging", "production") else "ENFORCE_SECRET_ENTROPY"
            # Order matters: the most actionable message must win.
            #
            # The field default is now the empty string, so an unconfigured
            # deployment trips the length rule. Reporting that as "Insecure
            # DASHBOARD_PASSWORD" is true but useless - the operator's actual
            # mistake is that they never set one. Ask for the explicit value
            # first, then check whether what they supplied is any good.
            if "DASHBOARD_PASSWORD" not in self.model_fields_set:
                raise ValueError(
                    f"DASHBOARD_PASSWORD must be explicitly provided via the environment "
                    f"for '{mode}'. Falling back to the built-in default is not permitted."
                )
            if self.DASHBOARD_PASSWORD == LEGACY_DEFAULT_DASHBOARD_PASSWORD:
                raise ValueError(
                    f"DASHBOARD_PASSWORD is still the shipped default for '{mode}'. "
                    "ROTATE THIS SECRET: set a unique value through the environment "
                    "(DASHBOARD_PASSWORD=...) before starting."
                )
            if self.DASHBOARD_PASSWORD.lower() in insecure_passwords or len(self.DASHBOARD_PASSWORD) < 12:
                raise ValueError(
                    f"Insecure DASHBOARD_PASSWORD for '{mode}' environment. "
                    "Password must not be a common default and must be at least 12 characters long."
                )
            if self.DASHBOARD_SECRET_KEY in ("change-me-in-prod", "") or len(self.DASHBOARD_SECRET_KEY) < 32:
                raise ValueError(
                    f"Insecure DASHBOARD_SECRET_KEY for '{self.ENV}' environment. "
                    "Secret key must not be a default and must be at least 32 characters long."
                )
            if self.POSTGRES_PASSWORD is not None:
                if self.POSTGRES_PASSWORD.lower() in insecure_passwords or len(self.POSTGRES_PASSWORD) < 12:
                    raise ValueError(
                        f"Insecure POSTGRES_PASSWORD for '{self.ENV}' environment. "
                        "Password must not be a default (e.g. ytpassword_secret_change_me) and must be at least 12 characters long."
                    )
            if "postgresql" in self.DATABASE_URL or "postgres://" in self.DATABASE_URL:
                for bad_pw in ("ytpassword_secret_change_me", "password", "postgres", "admin", "123456"):
                    if f":{bad_pw}@" in self.DATABASE_URL:
                        raise ValueError(
                            f"Insecure password embedded in DATABASE_URL for '{self.ENV}' environment. "
                            f"Default password '{bad_pw}' is strictly prohibited in staging/production."
                        )

        # Secret entropy & placeholder guard (PLAN-P1-08)
        placeholder_keys = {
            "yt_shorts_dev_master_secret_key_32_bytes_min!",
            "yt_shorts_kdf_salt_2026_secure!",
            "change-me-in-prod",
            "change-me",
            "ytpassword_secret_change_me",
            # Shipped default operator password. It is a real credential-shaped
            # default in source, so it must be covered by this guard.
            "AdminPass12345!",
        }
        has_placeholders = (
            (self.MASTER_ENCRYPTION_KEY is not None and self.MASTER_ENCRYPTION_KEY in placeholder_keys)
            or (self.SECRETS_MASTER_KEY is not None and self.SECRETS_MASTER_KEY in placeholder_keys)
            or (self.ENCRYPTION_SALT is not None and self.ENCRYPTION_SALT in placeholder_keys)
            or self.DASHBOARD_SECRET_KEY in placeholder_keys
            or self.DASHBOARD_PASSWORD in placeholder_keys
            or (self.MASTER_ENCRYPTION_KEY is not None and len(self.MASTER_ENCRYPTION_KEY) < 32)
            or (self.SECRETS_MASTER_KEY is not None and len(self.SECRETS_MASTER_KEY) < 32)
            or len(self.DASHBOARD_SECRET_KEY) < 32
        )

        if has_placeholders:
            if self.ENFORCE_SECRET_ENTROPY:
                raise ValueError(
                    "INSECURE STARTUP: Default placeholder keys or insufficient entropy detected in settings! "
                    "Rotate keys before production."
                )
            else:
                logger.warning(
                    "INSECURE STARTUP: Default placeholder keys detected in settings! Rotate keys before production."
                )
        # Ensure directories exist
        for directory in [
            self.STORAGE_DIR,
            self.TEMP_DIR,
            self.OUTPUT_DIR,
            self.DOWNLOADS_DIR,
            self.RAW_DIR,
            self.SFX_DIR,
            self.MODELS_DIR,
            self.STORAGE_DIR / "thumbnails",
            self.STORAGE_DIR / "logs",
            self.STORAGE_DIR / "config",
            self.HOTFOLDER_DIR,
            self.HOTFOLDER_DIR / "processed",
        ]:
            directory.mkdir(parents=True, exist_ok=True)
        return self

    def get_gemini_keys(self) -> List[str]:
        keys: List[str] = []
        sources = list(self.GEMINI_API_KEYS or [])
        if self.GEMINI_API_KEY and self.GEMINI_API_KEY not in sources:
            sources.append(self.GEMINI_API_KEY)
        if not sources:
            env_val = os.getenv("GEMINI_API_KEY")
            if env_val:
                sources.append(env_val)
        for src in sources:
            if src and isinstance(src, str):
                for item in src.replace("\r", "\n").replace(",", "\n").split("\n"):
                    clean = item.strip()
                    if clean and clean not in keys:
                        keys.append(clean)
        return keys

    def get_groq_keys(self) -> List[str]:
        keys: List[str] = []
        sources = list(self.GROQ_API_KEYS or [])
        if self.GROQ_API_KEY and self.GROQ_API_KEY not in sources:
            sources.append(self.GROQ_API_KEY)
        if not sources:
            env_val = os.getenv("GROQ_API_KEY")
            if env_val:
                sources.append(env_val)
        for src in sources:
            if src and isinstance(src, str):
                for item in src.replace("\r", "\n").replace(",", "\n").split("\n"):
                    clean = item.strip()
                    if clean and clean not in keys:
                        keys.append(clean)
        return keys

    def get_openrouter_keys(self) -> List[str]:
        keys: List[str] = []
        sources = list(self.OPENROUTER_API_KEYS or [])
        if self.OPENROUTER_API_KEY and self.OPENROUTER_API_KEY not in sources:
            sources.append(self.OPENROUTER_API_KEY)
        if not sources:
            env_val = os.getenv("OPENROUTER_API_KEY")
            if env_val:
                sources.append(env_val)
        for src in sources:
            if src and isinstance(src, str):
                for item in src.replace("\r", "\n").replace(",", "\n").split("\n"):
                    clean = item.strip()
                    if clean and clean not in keys:
                        keys.append(clean)
        return keys

    def get_openai_keys(self) -> List[str]:
        keys: List[str] = []
        sources = list(self.OPENAI_API_KEYS or [])
        if self.OPENAI_API_KEY and self.OPENAI_API_KEY not in sources:
            sources.append(self.OPENAI_API_KEY)
        if not sources:
            env_val = os.getenv("OPENAI_API_KEY")
            if env_val:
                sources.append(env_val)
        for src in sources:
            if src and isinstance(src, str):
                for item in src.replace("\r", "\n").replace(",", "\n").split("\n"):
                    clean = item.strip()
                    if clean and clean not in keys:
                        keys.append(clean)
        return keys

    def remove_api_key(self, provider: str, key_hash: str) -> Optional[str]:
        """
        Removes a key matching key_hash for provider.
        Returns the removed raw key if found, else None.
        """
        import hashlib
        def _quick_hash(k: str) -> str:
            return hashlib.sha256(k.strip().encode("utf-8")).hexdigest()

        prov = provider.lower().strip()
        keys_attr = f"{prov.upper()}_API_KEYS"
        key_attr = f"{prov.upper()}_API_KEY"

        getter = getattr(self, f"get_{prov}_keys", None)
        if not getter:
            return None

        current_keys = list(getter())
        removed_raw = None
        new_keys = []
        for k in current_keys:
            h = _quick_hash(k)
            if h == key_hash or h[:16] == key_hash or h.startswith(key_hash):
                removed_raw = k
            else:
                new_keys.append(k)

        if removed_raw is not None:
            setattr(self, keys_attr, new_keys)
            setattr(self, key_attr, new_keys[0] if new_keys else None)
            env_var = f"{prov.upper()}_API_KEY"
            if os.getenv(env_var) == removed_raw:
                os.environ[env_var] = ""
        return removed_raw

    def add_api_key(self, provider: str, new_key: str) -> bool:
        """
        Appends a new raw key for provider.
        Returns True if added, False if duplicate or invalid.
        """
        clean = new_key.strip()
        if not clean or clean.startswith("••••"):
            return False

        prov = provider.lower().strip()
        keys_attr = f"{prov.upper()}_API_KEYS"
        key_attr = f"{prov.upper()}_API_KEY"

        getter = getattr(self, f"get_{prov}_keys", None)
        if not getter:
            return False

        current_keys = list(getter())
        if clean in current_keys:
            return False

        current_keys.append(clean)
        setattr(self, keys_attr, current_keys)
        if not getattr(self, key_attr, None):
            setattr(self, key_attr, clean)
        return True

    def get_proxy_pool(self) -> List[str]:
        """Returns clean list of unique configured proxies."""
        proxies: List[str] = []
        for p in (self.INGESTION_PROXY_POOL or []):
            if isinstance(p, str):
                for line in p.replace("\r", "\n").split("\n"):
                    clean = line.strip()
                    if clean and clean not in proxies:
                        proxies.append(clean)
        return proxies

    def add_proxy(self, proxy_url: str) -> bool:
        """Adds a new proxy to INGESTION_PROXY_POOL."""
        clean = proxy_url.strip()
        if not clean or clean in self.INGESTION_PROXY_POOL:
            return False
        self.INGESTION_PROXY_POOL.append(clean)
        return True

    def remove_proxy(self, proxy_url: str) -> bool:
        """Removes a proxy from INGESTION_PROXY_POOL."""
        clean = proxy_url.strip()
        if clean in self.INGESTION_PROXY_POOL:
            self.INGESTION_PROXY_POOL.remove(clean)
            return True
        return False

    def get_llm_pricing(self, provider: str, model: Optional[str] = None) -> Dict[str, float]:
        """
        Returns {'input': float, 'output': float} USD cost per 1M tokens
        for the given provider and model.
        """
        prov_key = (provider or "").lower().strip()
        prov_table = self.LLM_MODEL_PRICING.get(prov_key, {})
        if not prov_table:
            return {"input": 0.05, "output": 0.15}

        model_key = (model or "").lower().strip()
        # Direct match or case-insensitive match
        for m_name, pricing in prov_table.items():
            if m_name.lower() == model_key:
                return pricing

        # Partial matching (e.g. if model has prefixes or suffixes)
        for m_name, pricing in prov_table.items():
            if m_name != "_default" and m_name.lower() in model_key:
                return pricing

        return prov_table.get("_default", {"input": 0.05, "output": 0.15})



# Global singleton settings instance
settings = PipelineSettings()
try:
    settings.FONTS_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass

