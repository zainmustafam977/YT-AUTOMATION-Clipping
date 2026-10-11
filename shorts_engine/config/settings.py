"""
Configuration and settings for YouTube Shorts Automation Platform.
Implements unified single source of truth for all pipeline constants.
"""

import os
import logging
from pathlib import Path
from typing import Optional, List
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


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
    GEMINI_API_KEYS: List[str] = Field(default_factory=list, description="Pool of Gemini API keys for load balancing & 429 failover")
    GROQ_API_KEY: Optional[str] = Field(default=None, description="Groq API Key (high-speed LPU inference)")
    GROQ_API_KEYS: List[str] = Field(default_factory=list, description="Pool of Groq API keys for load balancing & 429 failover")
    GROQ_MODEL_NAME: str = Field(
        default="openai/gpt-oss-120b",
        description="Groq model identifier (e.g. openai/gpt-oss-120b, qwen/qwen3.8-27b)",
    )
    OPENROUTER_API_KEY: Optional[str] = Field(default=None, description="OpenRouter API Key for multi-model fallback")
    OPENROUTER_API_KEYS: List[str] = Field(default_factory=list, description="Pool of OpenRouter API keys for load balancing & 429 failover")
    OPENROUTER_MODEL_NAME: str = Field(
        default="meta-llama/llama-3.3-70b-instruct:free",
        description="OpenRouter model identifier (e.g. meta-llama/llama-3.3-70b-instruct:free, google/gemini-2.0-flash-exp:free)",
    )
    OPENAI_API_KEY: Optional[str] = Field(default=None, description="OpenAI API Key for backup / failover")
    OPENAI_API_KEYS: List[str] = Field(default_factory=list, description="Pool of OpenAI API keys for load balancing & 429 failover")
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
    CAPTION_FONT_NAME: str = "Arial Black"
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
    SLACK_WEBHOOK_URL: Optional[str] = Field(default=None, description="Incoming Slack Webhook URL")
    DISCORD_WEBHOOK_URL: Optional[str] = Field(default=None, description="Discord Webhook URL")
    TELEGRAM_BOT_TOKEN: Optional[str] = Field(default=None, description="Telegram Bot Token")
    TELEGRAM_CHAT_ID: Optional[str] = Field(default=None, description="Telegram Chat ID")
    SENTRY_DSN: Optional[str] = Field(default=None, description="Sentry DSN for error telemetry")

    # Publishing & Quotas
    DRY_RUN_PUBLISHING: bool = Field(default=True, description="When true, simulates upload without calling YouTube API")
    DEFAULT_PRIVACY_STATUS: str = Field(default="unlisted", description="unlisted, private, or public")
    DAILY_QUOTA_LIMIT: int = 10000
    QUOTA_WARNING_THRESHOLD: float = 0.80  # Alert when 80% quota is used
    DRIP_FEED_ENABLED: bool = Field(
        default=True,
        description="Enable background automated drip-feed publishing for scheduled clips",
    )
    DRIP_FEED_POLL_INTERVAL_SECONDS: int = Field(
        default=60,
        ge=10,
        le=3600,
        description="Polling interval in seconds for checking scheduled clips to publish",
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

    # Bayesian Multi-Armed Bandit A/B Winner Promotion
    AB_WINNER_CHECK_INTERVAL_SECONDS: int = Field(
        default=120,
        ge=30,
        le=3600,
        description="Interval in seconds between Bayesian bandit A/B winner evaluations",
    )
    AB_MIN_IMPRESSIONS: int = Field(
        default=100,
        ge=10,
        description="Minimum impressions required before considering an A/B variation for winner promotion",
    )
    AB_CONFIDENCE_THRESHOLD: float = Field(
        default=0.95,
        ge=0.80,
        le=0.999,
        description="Posterior probability threshold P(theta_B > theta_A) required to declare a winning title",
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
    DASHBOARD_PASSWORD: str = Field(default="AdminPass12345!", description="Operator dashboard password")

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
        default=Path(__file__).resolve().parent.parent.parent / "storage" / "harvester_cookies.txt",
        description="Path to fresh Netscape cookies exported by background session harvester",
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
        if self.ENV in ("staging", "production"):
            insecure_passwords = {"change-me", "admin", "password", "123456", "12345678", "root", "ytpassword_secret_change_me", ""}
            if self.DASHBOARD_PASSWORD.lower() in insecure_passwords or len(self.DASHBOARD_PASSWORD) < 12:
                raise ValueError(
                    f"Insecure DASHBOARD_PASSWORD for '{self.ENV}' environment. "
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
        }
        has_placeholders = (
            (self.MASTER_ENCRYPTION_KEY is not None and self.MASTER_ENCRYPTION_KEY in placeholder_keys)
            or (self.SECRETS_MASTER_KEY is not None and self.SECRETS_MASTER_KEY in placeholder_keys)
            or (self.ENCRYPTION_SALT is not None and self.ENCRYPTION_SALT in placeholder_keys)
            or self.DASHBOARD_SECRET_KEY in placeholder_keys
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



# Global singleton settings instance
settings = PipelineSettings()
try:
    settings.FONTS_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass

