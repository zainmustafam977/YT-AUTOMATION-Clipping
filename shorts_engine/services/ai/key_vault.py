"""
Multi-Provider Dynamic API Key Vault with Fernet Encryption,
Leaky-Bucket Rate Limiting, Exponential Cooldown Backoff,
and Real-Time EventBus Notifications.
"""

import os
import sys
import time
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Union, Dict

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import nulls_first
from sqlalchemy.orm import Session

from shorts_engine.config.settings import settings
from shorts_engine.db.models import ApiKey, generate_uuid
from shorts_engine.services.events.bus import global_event_bus
from shorts_engine.services.events.models import PlatformEvent

logger = logging.getLogger(__name__)


class LeakyBucketTracker:
    """
    Thread-safe leaky-bucket rate limiter for an API key.
    Enforces dual leaky-bucket replenishment for both Requests Per Minute (RPM)
    and Tokens Per Minute (TPM).
    """

    def __init__(self, rpm_limit: int, tpm_limit: int, initial_time: Optional[float] = None):
        self.rpm_limit = max(1, rpm_limit)
        self.tpm_limit = max(1, tpm_limit)
        self.request_water: float = 0.0
        self.token_water: float = 0.0
        self.last_leak_time: float = initial_time if initial_time is not None else time.monotonic()
        self._lock = threading.Lock()

    def update_limits(self, rpm_limit: int, tpm_limit: int) -> None:
        with self._lock:
            self.rpm_limit = max(1, rpm_limit)
            self.tpm_limit = max(1, tpm_limit)

    def _calculate_levels(self, now: float) -> tuple[float, float]:
        if now <= self.last_leak_time:
            return self.request_water, self.token_water

        elapsed = now - self.last_leak_time
        req_leak_rate = self.rpm_limit / 60.0
        tok_leak_rate = self.tpm_limit / 60.0

        new_req_water = max(0.0, self.request_water - (elapsed * req_leak_rate))
        new_tok_water = max(0.0, self.token_water - (elapsed * tok_leak_rate))
        return new_req_water, new_tok_water

    def leak(self, now: Optional[float] = None) -> None:
        with self._lock:
            curr = now if now is not None else time.monotonic()
            if curr < self.last_leak_time:
                self.last_leak_time = curr
                return
            new_req, new_tok = self._calculate_levels(curr)
            self.request_water = new_req
            self.token_water = new_tok
            self.last_leak_time = curr

    def can_consume(self, estimated_tokens: int = 1, now: Optional[float] = None) -> bool:
        tok_val = max(1, int(estimated_tokens)) if estimated_tokens is not None else 1
        with self._lock:
            curr = now if now is not None else time.monotonic()
            if curr < self.last_leak_time:
                self.last_leak_time = curr
            new_req, new_tok = self._calculate_levels(curr)
            return (
                (new_req + 1.0) <= float(self.rpm_limit)
                and (new_tok + float(tok_val)) <= float(self.tpm_limit)
            )

    def consume(self, estimated_tokens: int = 1, now: Optional[float] = None) -> bool:
        tok_val = max(1, int(estimated_tokens)) if estimated_tokens is not None else 1
        with self._lock:
            curr = now if now is not None else time.monotonic()
            if curr < self.last_leak_time:
                self.last_leak_time = curr
            new_req, new_tok = self._calculate_levels(curr)
            if (
                (new_req + 1.0) <= float(self.rpm_limit)
                and (new_tok + float(tok_val)) <= float(self.tpm_limit)
            ):
                self.request_water = new_req + 1.0
                self.token_water = new_tok + float(tok_val)
                self.last_leak_time = curr
                return True
            return False

    def reset(self) -> None:
        with self._lock:
            self.request_water = 0.0
            self.token_water = 0.0
            self.last_leak_time = time.monotonic()


class ApiKeyVault:
    """
    Centralized API key management vault with Fernet encryption at rest,
    per-provider leaky-bucket pacing, cooldown failover, and EventBus telemetry.
    """

    _bucket_cache: Dict[str, LeakyBucketTracker] = {}
    _cache_lock = threading.Lock()

    def __init__(
        self,
        db: Session,
        vault_key_path: Optional[Path] = None,
        fernet_key: Optional[Union[bytes, str]] = None,
    ):
        self.db = db
        self.vault_key_path = vault_key_path or Path(
            os.environ.get("SHORTS_VAULT_KEY_PATH", Path(settings.STORAGE_DIR) / ".vault.key")
        )
        self._fernet = self._init_fernet(fernet_key)

    def _init_fernet(self, explicit_key: Optional[Union[bytes, str]] = None) -> Fernet:
        """Initializes or loads the 256-bit Fernet key from parameter, disk, or secretbox."""
        if explicit_key:
            key_bytes = explicit_key.encode("utf-8") if isinstance(explicit_key, str) else explicit_key
            return Fernet(key_bytes)

        # Check existing key file on disk if explicitly present
        if self.vault_key_path and self.vault_key_path.exists() and self.vault_key_path.stat().st_size > 0:
            try:
                with open(self.vault_key_path, "rb") as f:
                    key_bytes = f.read().strip()
                return Fernet(key_bytes)
            except Exception as e:
                logger.warning("Failed to read vault key file from %s: %s. Using secretbox.", self.vault_key_path, e)

        # If a custom non-default path was specified (e.g. in test fixtures), generate into it
        default_path = Path(settings.STORAGE_DIR) / ".vault.key"
        if self.vault_key_path and self.vault_key_path != default_path:
            key_bytes = Fernet.generate_key()
            try:
                self.vault_key_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.vault_key_path, "wb") as f:
                    f.write(key_bytes)
                return Fernet(key_bytes)
            except Exception as e:
                logger.error("Could not persist custom vault key to %s: %s", self.vault_key_path, e)

        # Standard centralized Fernet key from SECRETS_MASTER_KEY
        from shorts_engine.security.secretbox import get_fernet
        return get_fernet()

    def encrypt_key(self, raw_key: str) -> str:
        """Encrypts a plaintext API key string using Fernet."""
        if not raw_key:
            raise ValueError("Cannot encrypt an empty API key")
        token = self._fernet.encrypt(raw_key.strip().encode("utf-8"))
        return token.decode("utf-8")

    def decrypt_key(self, encrypted_key: str) -> str:
        """Decrypts a Fernet ciphertext string back to plaintext API key."""
        if not encrypted_key:
            return ""
        try:
            plain_bytes = self._fernet.decrypt(encrypted_key.encode("utf-8"))
            return plain_bytes.decode("utf-8")
        except InvalidToken as e:
            logger.error("Failed to decrypt API key: Invalid Fernet token or incorrect vault key")
            raise ValueError("Invalid API key ciphertext or vault key mismatch") from e

    def _get_bucket(self, key: ApiKey) -> LeakyBucketTracker:
        with self._cache_lock:
            bucket = self._bucket_cache.get(key.id)
            if bucket is None:
                bucket = LeakyBucketTracker(key.rpm_limit, key.tpm_limit)
                self._bucket_cache[key.id] = bucket
            else:
                bucket.update_limits(key.rpm_limit, key.tpm_limit)
            return bucket

    def _check_and_reset_daily(self, key: ApiKey, now_dt: datetime) -> None:
        """Resets daily counts if the date has changed since last usage."""
        if key.last_used_at is not None:
            if key.last_used_at.date() < now_dt.date():
                key.daily_request_count = 0
                key.daily_token_count = 0

    def add_key(
        self,
        provider: str,
        raw_key: str,
        nickname: str,
        rpm_limit: int = 30,
        tpm_limit: int = 60000,
        rpd_limit: int = 14400,
    ) -> ApiKey:
        """
        Securely encrypts and persists a new API key into the vault.
        """
        provider_clean = provider.strip().lower()
        encrypted = self.encrypt_key(raw_key)

        api_key = ApiKey(
            id=generate_uuid(),
            provider=provider_clean,
            api_key_encrypted=encrypted,
            nickname=nickname.strip(),
            status="ACTIVE",
            rpm_limit=rpm_limit,
            tpm_limit=tpm_limit,
            rpd_limit=rpd_limit,
            daily_request_count=0,
            daily_token_count=0,
            consecutive_errors=0,
            cooldown_until=None,
            last_used_at=None,
        )
        self.db.add(api_key)
        self.db.commit()
        self.db.refresh(api_key)

        logger.info("Added API key '%s' for provider '%s' to vault", nickname, provider_clean)
        return api_key

    def get_key(self, key_id: str) -> Optional[ApiKey]:
        """Fetches an ApiKey record by its ID."""
        return self.db.query(ApiKey).filter(ApiKey.id == key_id).first()

    def get_decrypted_key(self, key_or_id: Union[ApiKey, str]) -> str:
        """Returns the decrypted plaintext API key string."""
        if isinstance(key_or_id, ApiKey):
            return self.decrypt_key(key_or_id.api_key_encrypted)
        key = self.get_key(key_or_id)
        if not key:
            raise ValueError(f"API key not found with ID: {key_or_id}")
        return self.decrypt_key(key.api_key_encrypted)

    def list_keys(self, provider: Optional[str] = None) -> List[ApiKey]:
        """Lists API keys with optional provider filter."""
        query = self.db.query(ApiKey)
        if provider:
            query = query.filter(ApiKey.provider == provider.strip().lower())
        return query.order_by(ApiKey.created_at.asc()).all()

    def disable_key(self, key_id: str) -> Optional[ApiKey]:
        """Disables an API key manually."""
        key = self.get_key(key_id)
        if key:
            key.status = "DISABLED"
            self.db.commit()
            self.db.refresh(key)
        return key

    def enable_key(self, key_id: str) -> Optional[ApiKey]:
        """Enables a disabled or exhausted API key."""
        key = self.get_key(key_id)
        if key:
            key.status = "ACTIVE"
            key.consecutive_errors = 0
            key.cooldown_until = None
            self.db.commit()
            self.db.refresh(key)
        return key

    def delete_key(self, key_id: str) -> bool:
        """Permanently removes an API key from the vault."""
        key = self.get_key(key_id)
        if key:
            with self._cache_lock:
                self._bucket_cache.pop(key.id, None)
            self.db.delete(key)
            self.db.commit()
            return True
        return False

    def reset_daily_quotas(self) -> int:
        """Resets daily request and token counters for all stored keys."""
        updated = self.db.query(ApiKey).update({
            "daily_request_count": 0,
            "daily_token_count": 0,
        })
        self.db.commit()
        return updated

    def acquire_key(
        self,
        provider: str,
        estimated_tokens: int = 2000,
        max_wait_sec: float = 0.0,
    ) -> ApiKey:
        """
        Selects the least-loaded, active API key for the requested provider.
        Enforces RPM and TPM leaky-bucket limits, RPD quotas, and recovers expired cooldowns.
        Raises RuntimeError if all keys are cooling down, rate-limited, or exhausted.
        """
        provider_clean = provider.strip().lower()
        now = datetime.utcnow()
        tokens_to_acquire = max(1, int(estimated_tokens)) if estimated_tokens is not None else 1

        # 1. Recover expired cooldowns
        expired_cooldowns = self.db.query(ApiKey).filter(
            ApiKey.provider == provider_clean,
            ApiKey.status == "COOLING_DOWN",
            ApiKey.cooldown_until <= now,
        ).all()

        for exp_key in expired_cooldowns:
            exp_key.status = "ACTIVE"
            exp_key.cooldown_until = None
            # Do NOT reset consecutive_errors to 0 here; consecutive_errors must persist
            # so that subsequent failures continue exponential backoff progression.
            # Only report_success resets consecutive_errors to 0.
            logger.info("API key '%s' cooldown expired; restored to ACTIVE", exp_key.nickname)

        if expired_cooldowns:
            self.db.commit()

        # 2. Query available active keys with cross-DB consistent nulls_first ordering
        active_keys = (
            self.db.query(ApiKey)
            .filter(
                ApiKey.provider == provider_clean,
                ApiKey.status == "ACTIVE",
            )
            .order_by(ApiKey.daily_token_count.asc(), nulls_first(ApiKey.last_used_at.asc()))
            .all()
        )

        if not active_keys:
            raise RuntimeError(f"All API keys for provider '{provider_clean}' are cooling down or exhausted.")

        # 3. Check daily resets and evaluate leaky-bucket capacity
        start_wait = time.monotonic()
        while True:
            for candidate in active_keys:
                self._check_and_reset_daily(candidate, now)

                # Check daily RPD limit
                if candidate.rpd_limit > 0 and (candidate.daily_request_count + 1) > candidate.rpd_limit:
                    continue

                bucket = self._get_bucket(candidate)
                if bucket.consume(tokens_to_acquire):
                    candidate.daily_request_count += 1
                    candidate.daily_token_count += tokens_to_acquire
                    candidate.last_used_at = now
                    self.db.commit()
                    self.db.refresh(candidate)
                    return candidate

            # If max_wait_sec provided, wait brief tick and retry leaky-bucket
            if max_wait_sec > 0.0 and (time.monotonic() - start_wait) < max_wait_sec:
                time.sleep(0.1)
                now = datetime.utcnow()
                continue
            break

        raise RuntimeError(
            f"All API keys for provider '{provider_clean}' are currently rate-limited by leaky-bucket limits."
        )

    def report_error(self, key_id: str, http_status: int = 0, error_msg: str = "") -> None:
        """
        Records an API call failure.
        Applies exponential cooldown backoff for 429/quota errors and emits EventBus alerts.
        Permanently exhausts keys on authentication or invalid credential errors.
        """
        key = self.get_key(key_id)
        if not key:
            return

        key.consecutive_errors += 1
        msg_lower = error_msg.lower() if error_msg else ""

        is_auth_error = (
            http_status == 401
            or ("invalid" in msg_lower and any(k in msg_lower for k in ("key", "token", "credential", "auth")))
            or "unauthorized" in msg_lower
            or "api_key_invalid" in msg_lower
        )

        is_rate_limit = (
            not is_auth_error
            and (
                http_status in (429, 503)
                or (http_status == 403 and any(s in msg_lower for s in ("quota", "rate", "limit", "exhausted", "capacity")))
                or any(s in msg_lower for s in ("quota", "rate limit", "resource_exhausted", "too many requests", "tokens per minute"))
            )
        )

        if is_auth_error:
            key.status = "EXHAUSTED"
            self.db.commit()

            try:
                event = PlatformEvent(
                    category="quota",
                    level="ERROR",
                    title=f"API Key Invalid: {key.nickname}",
                    message=f"{key.provider.upper()} key rejected due to invalid authentication: {error_msg[:100]}",
                    entity_id=key.id,
                    entity_type="key",
                    details={
                        "provider": key.provider,
                        "consecutive_errors": key.consecutive_errors,
                        "http_status": http_status,
                        "error": error_msg[:200] if error_msg else "Authentication credentials rejected",
                    },
                )
                global_event_bus.publish_sync(event)
            except Exception as e:
                logger.debug("Failed to broadcast API invalid event: %s", e)

        elif is_rate_limit:
            # Exponential cooldown: 30s, 60s, 120s, 240s... up to 3600s
            backoff_sec = min(3600, 30 * (2 ** max(0, key.consecutive_errors - 1)))
            key.status = "COOLING_DOWN"
            key.cooldown_until = datetime.utcnow() + timedelta(seconds=backoff_sec)
            self.db.commit()

            # Emit live notification bubble via EventBus
            try:
                event = PlatformEvent(
                    category="quota",
                    level="WARNING",
                    title=f"API Key Rate Limited: {key.nickname}",
                    message=f"{key.provider.upper()} key throttled. Cooling down for {backoff_sec}s. Auto-switching to spare key.",
                    entity_id=key.id,
                    entity_type="key",
                    details={
                        "provider": key.provider,
                        "consecutive_errors": key.consecutive_errors,
                        "backoff_sec": backoff_sec,
                        "http_status": http_status,
                        "error": error_msg[:200] if error_msg else "Rate limit / quota exceeded",
                    },
                )
                global_event_bus.publish_sync(event)
            except Exception as e:
                logger.debug("Failed to broadcast API rate limit event: %s", e)

        elif key.consecutive_errors >= 5:
            key.status = "EXHAUSTED"
            self.db.commit()

            try:
                event = PlatformEvent(
                    category="quota",
                    level="ERROR",
                    title=f"API Key Disabled: {key.nickname}",
                    message=f"{key.provider.upper()} key disabled after 5 consecutive failures: {error_msg[:100]}",
                    entity_id=key.id,
                    entity_type="key",
                    details={
                        "provider": key.provider,
                        "consecutive_errors": key.consecutive_errors,
                        "http_status": http_status,
                        "error": error_msg[:200] if error_msg else "Consecutive failures threshold exceeded",
                    },
                )
                global_event_bus.publish_sync(event)
            except Exception as e:
                logger.debug("Failed to broadcast API exhaustion event: %s", e)
        else:
            self.db.commit()

    def report_success(self, key_id: str, actual_tokens: Optional[int] = None) -> None:
        """
        Records a successful API call, resetting error counters.
        """
        key = self.get_key(key_id)
        if not key:
            return

        key.consecutive_errors = 0
        if key.status == "RATE_LIMITED":
            key.status = "ACTIVE"

        self.db.commit()
