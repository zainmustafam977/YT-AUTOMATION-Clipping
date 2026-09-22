"""
Thread-safe, persistent API Quota & Usage Tracker.
Tracks daily and all-time request volume, HTTP 429 rate-limit events,
response latency, and temporary cooldown states for each individual configured API key.
Persists state to storage/api_key_usage.json.
"""

import os
import json
import time
import hashlib
import logging
import threading
from datetime import datetime, date
from pathlib import Path
from typing import Dict, Any, List, Optional

from shorts_engine.config.settings import settings

logger = logging.getLogger(__name__)

# Daily estimated free-tier request limits per provider
ESTIMATED_DAILY_LIMITS = {
    "groq": 14400,       # Groq generous free tier (~30 RPM / 14.4k RPD)
    "gemini": 1500,      # Google AI Studio Gemini free tier (~15 RPM / 1.5k RPD)
    "openrouter": 200,   # OpenRouter free models tier (~20 RPM / 200 RPD)
    "openai": 1000,      # OpenAI backup usage baseline
    "youtube": 10000,    # YouTube Data API units
}


def mask_api_key(key: Optional[str]) -> str:
    """Safely masks an API key showing only prefix and suffix."""
    if not key or not isinstance(key, str):
        return "—"
    clean = key.strip()
    if len(clean) <= 8:
        return "••••••••"
    return f"{clean[:8]}...{clean[-4:]}"


def hash_api_key(key: Optional[str]) -> str:
    """Returns a deterministic SHA256 hex digest for key matching."""
    if not key:
        return "none"
    return hashlib.sha256(key.strip().encode("utf-8")).hexdigest()[:16]


class APIUsageTracker:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            # If explicit path is provided for testing, return fresh instance
            if kwargs.get("storage_path") or kwargs.get("state_file") or (args and args[0]):
                instance = super(APIUsageTracker, cls).__new__(cls)
                instance._initialized = False
                return instance
            if cls._instance is None:
                cls._instance = super(APIUsageTracker, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self, state_file: Optional[Path] = None, storage_path: Optional[Path] = None):
        target_path = Path(storage_path or state_file or (Path(settings.STORAGE_DIR) / "api_key_usage.json"))
        if getattr(self, "_initialized", False) and getattr(self, "state_file", None) == target_path:
            return
        self.state_file = target_path
        self._mutex = threading.RLock()
        self._state: Dict[str, Any] = self._load_state()
        self._active_call: Optional[Dict[str, Any]] = None
        self._active_keys: set = set()
        self._recent_failovers: List[Dict[str, Any]] = []
        self._initialized = True

    def _load_state(self) -> Dict[str, Any]:
        today_str = date.today().isoformat()
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                if data.get("date") != today_str:
                    # New day: carry over total_requests but reset today counters
                    keys_state = data.get("keys", {})
                    for k_id, k_data in keys_state.items():
                        k_data["requests_today"] = 0
                        k_data["rate_limit_hits_today"] = 0
                        k_data["cooldown_until"] = 0.0
                    data["date"] = today_str
                    data["keys"] = keys_state
                    self._save_state(data)
                return data
            except Exception as e:
                logger.warning("Could not read api_key_usage.json, initializing fresh: %s", e)

        initial = {"date": today_str, "keys": {}}
        return initial

    def _save_state(self, state: Dict[str, Any]) -> None:
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to persist api_key_usage.json: %s", e)

    def _get_key_identifier(self, provider: str, key: str) -> str:
        k_hash = hash_api_key(key)
        return f"{provider.lower()}:{k_hash}"

    def is_cooling_down(self, provider: str, key: str) -> bool:
        """Returns True if this key is temporarily cooling down from an HTTP 429."""
        with self._mutex:
            k_id = self._get_key_identifier(provider, key)
            k_info = self._state.get("keys", {}).get(k_id)
            if not k_info:
                return False
            cooldown_until = k_info.get("cooldown_until", 0.0)
            return time.time() < cooldown_until

    def is_key_cooling_down(self, provider: str, key: str) -> bool:
        """Alias for is_cooling_down."""
        return self.is_cooling_down(provider, key)

    def record_rate_limit(self, provider: str, key: str, cooldown_seconds: int = 60) -> None:
        """Alias for set_cooldown."""
        self.set_cooldown(provider, key, duration_sec=cooldown_seconds)

    def set_active_call(
        self,
        provider: str,
        model: str,
        video_id: Optional[str] = None,
        status_text: Optional[str] = None,
        key: Optional[str] = None,
    ) -> str:
        """Sets real-time in-flight state for an active model execution and returns call_id."""
        with self._mutex:
            k_id = self._get_key_identifier(provider, key) if key else None
            if k_id:
                self._active_keys.add(k_id)
            call_id = f"{provider}:{time.time()}"
            self._active_call = {
                "call_id": call_id,
                "provider": provider.lower(),
                "model": model,
                "video_id": video_id,
                "status_text": status_text or f"Executing {provider.upper()} ({model})...",
                "key_masked": mask_api_key(key) if key else None,
                "started_at": time.time(),
            }
            return call_id

    def clear_active_call(self, provider: Optional[str] = None, key: Optional[str] = None) -> None:
        """Clears current in-flight model activity."""
        with self._mutex:
            if provider and key:
                k_id = self._get_key_identifier(provider, key)
                self._active_keys.discard(k_id)
            else:
                self._active_keys.clear()
            self._active_call = None

    def record_failover_event(
        self,
        from_provider: str = "",
        to_provider: str = "",
        reason: str = "",
        video_id: Optional[str] = None,
        **kwargs,
    ) -> None:
        """Logs a real-time failover event for live telemetry and alerting."""
        with self._mutex:
            # Handle potential positional mismatch or legacy kwargs
            f_prov = from_provider or kwargs.get("from_provider", "")
            t_prov = to_provider or kwargs.get("to_provider", "")
            r_text = reason or kwargs.get("reason", "")
            v_id = video_id or kwargs.get("video_id", "—")

            # Check if first arg was passed as video_id positionally
            if not t_prov and "to" in kwargs:
                t_prov = kwargs["to"]

            event = {
                "from": str(f_prov).lower(),
                "to": str(t_prov).lower(),
                "from_provider": str(f_prov).lower(),
                "to_provider": str(t_prov).lower(),
                "reason": str(r_text),
                "video_id": str(v_id),
                "time": datetime.utcnow().strftime("%H:%M:%S"),
                "timestamp": datetime.utcnow().strftime("%H:%M:%S"),
            }
            self._recent_failovers.insert(0, event)
            if len(self._recent_failovers) > 20:
                self._recent_failovers.pop()

    def get_live_telemetry(self) -> Dict[str, Any]:
        """Returns instantaneous snapshot of active LLM calls, cooldowns, and failovers."""
        with self._mutex:
            now_ts = time.time()
            active_info = None
            if self._active_call:
                active_info = dict(self._active_call)
                active_info["elapsed_sec"] = round(now_ts - self._active_call.get("started_at", now_ts), 1)

            cooling_summary = {}
            for k_id, k_data in self._state.get("keys", {}).items():
                cd_until = k_data.get("cooldown_until", 0.0)
                if now_ts < cd_until:
                    cooling_summary[k_id] = max(0, int(cd_until - now_ts))

            in_flight_count = 1 if active_info else 0
            active_calls = [active_info] if active_info else []

            return {
                "active_call": active_info,
                "has_active_call": active_info is not None,
                "in_flight_count": in_flight_count,
                "active_calls": active_calls,
                "is_cooldown_active": len(cooling_summary) > 0,
                "cooling_keys_count": len(cooling_summary),
                "cooling_keys": cooling_summary,
                "recent_failovers": list(self._recent_failovers[:5]),
            }

    def set_cooldown(self, provider: str, key: str, duration_sec: float = 60.0) -> None:
        """Puts a specific key into temporary cooldown after receiving HTTP 429."""
        with self._mutex:
            self._ensure_today()
            k_id = self._get_key_identifier(provider, key)
            keys_dict = self._state.setdefault("keys", {})
            k_info = keys_dict.setdefault(k_id, {
                "provider": provider.lower(),
                "key_masked": mask_api_key(key),
                "key_hash": hash_api_key(key),
                "requests_today": 0,
                "total_requests": 0,
                "rate_limit_hits_today": 0,
                "last_latency_ms": 0,
                "last_model_used": "—",
                "last_used_at": None,
                "cooldown_until": 0.0,
                "daily_limit_est": ESTIMATED_DAILY_LIMITS.get(provider.lower(), 1000),
            })
            k_info["cooldown_until"] = time.time() + float(duration_sec)
            k_info["rate_limit_hits_today"] = k_info.get("rate_limit_hits_today", 0) + 1
            self._save_state(self._state)
            logger.warning(
                "API key %s (%s) cooling down for %ds due to rate-limit / 429.",
                k_info["key_masked"], provider, int(duration_sec)
            )

    def record_request(
        self,
        provider: str,
        key: str,
        status: str = "SUCCESS",
        latency_ms: int = 0,
        model: str = "",
        error: Optional[str] = None,
        cooldown_seconds: Optional[float] = None,
        headers: Optional[Dict[str, str]] = None,
        inference_metrics: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Records an API request invocation against a specific key."""
        with self._mutex:
            self._ensure_today()
            prov = provider.lower()
            k_id = self._get_key_identifier(prov, key)
            keys_dict = self._state.setdefault("keys", {})

            k_info = keys_dict.setdefault(k_id, {
                "provider": prov,
                "key_masked": mask_api_key(key),
                "key_hash": hash_api_key(key),
                "requests_today": 0,
                "total_requests": 0,
                "rate_limit_hits_today": 0,
                "last_latency_ms": latency_ms,
                "last_model_used": model or "—",
                "last_used_at": None,
                "cooldown_until": 0.0,
                "daily_limit_est": ESTIMATED_DAILY_LIMITS.get(prov, 1000),
            })

            k_info["requests_today"] = k_info.get("requests_today", 0) + 1
            k_info["total_requests"] = k_info.get("total_requests", 0) + 1
            k_info["last_latency_ms"] = latency_ms
            k_info["last_model_used"] = model or k_info.get("last_model_used", "—")
            k_info["last_used_at"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            k_info["last_status"] = status
            if error:
                k_info["last_error"] = error[:200]

            # Parse Groq / provider rate-limit response headers
            if headers:
                norm_headers = {k.lower(): v for k, v in headers.items()}
                if "x-ratelimit-remaining-tokens" in norm_headers:
                    try:
                        k_info["remaining_tokens_minute"] = int(norm_headers["x-ratelimit-remaining-tokens"])
                        k_info["tokens_updated_at"] = time.time()
                    except (ValueError, TypeError):
                        pass
                if "x-ratelimit-reset-tokens" in norm_headers:
                    val = str(norm_headers["x-ratelimit-reset-tokens"]).strip()
                    try:
                        # Handles formats like "7.66s" or "7.66"
                        val_clean = val.rstrip("s")
                        k_info["reset_tokens_sec"] = float(val_clean)
                    except (ValueError, TypeError):
                        k_info["reset_tokens_sec"] = 60.0
                if "x-ratelimit-remaining-requests" in norm_headers:
                    try:
                        k_info["remaining_requests_day"] = int(norm_headers["x-ratelimit-remaining-requests"])
                    except (ValueError, TypeError):
                        pass

            # Parse inference metrics (from Groq-Beta: inference-metrics)
            if inference_metrics:
                k_info["last_inference_metrics"] = inference_metrics
                tps = inference_metrics.get("tokens_per_sec")
                if tps is not None:
                    try:
                        k_info["last_tokens_per_sec"] = float(tps)
                    except (ValueError, TypeError):
                        pass
                comp_time = inference_metrics.get("completion_time")
                if comp_time is not None:
                    try:
                        k_info["last_completion_time_sec"] = float(comp_time)
                    except (ValueError, TypeError):
                        pass

            if status == "RATE_LIMITED":
                cd = cooldown_seconds if (cooldown_seconds and cooldown_seconds > 0) else 60.0
                k_info["rate_limit_hits_today"] = k_info.get("rate_limit_hits_today", 0) + 1
                k_info["cooldown_until"] = time.time() + float(cd)

            self._save_state(self._state)
            logger.debug(
                "Recorded API call for %s (%s): status=%s, reqs_today=%d",
                prov, k_info["key_masked"], status, k_info["requests_today"]
            )

    def get_groq_remaining_tokens(self, key: str) -> Optional[int]:
        """Returns live remaining tokens in current 1m window if recently tracked, else None."""
        with self._mutex:
            k_id = self._get_key_identifier("groq", key)
            k_info = self._state.get("keys", {}).get(k_id)
            if not k_info:
                return None
            updated_at = k_info.get("tokens_updated_at", 0.0)
            reset_sec = k_info.get("reset_tokens_sec", 60.0)
            if time.time() - updated_at > reset_sec:
                return None  # window has expired and reset
            return k_info.get("remaining_tokens_minute")

    def _ensure_today(self) -> None:
        today_str = date.today().isoformat()
        if self._state.get("date") != today_str:
            keys_state = self._state.get("keys", {})
            for k_data in keys_state.values():
                k_data["requests_today"] = 0
                k_data["rate_limit_hits_today"] = 0
                k_data["cooldown_until"] = 0.0
            self._state["date"] = today_str
            self._save_state(self._state)

    def remove_key(self, provider: str, key_hash: str) -> bool:
        """Removes a key from persistent tracker state by its hash."""
        with self._mutex:
            target_id = None
            for k_id, k_info in self._state.get("keys", {}).items():
                if k_info.get("provider") == provider.lower() and k_info.get("key_hash") == key_hash:
                    target_id = k_id
                    break
            if target_id and target_id in self._state.get("keys", {}):
                del self._state["keys"][target_id]
                self._save_state(self._state)
                return True
            return False

    def get_per_key_metrics(self) -> List[Dict[str, Any]]:
        """
        Returns structured metrics for all configured keys across all providers.
        Also discovers keys configured in settings that haven't been invoked yet,
        and automatically prunes unconfigured or stale test keys from state.
        """
        with self._mutex:
            self._ensure_today()
            now_ts = time.time()
            result: List[Dict[str, Any]] = []

            configured_keys = {
                "groq": settings.get_groq_keys(),
                "gemini": settings.get_gemini_keys(),
                "openrouter": settings.get_openrouter_keys(),
                "openai": settings.get_openai_keys(),
            }

            provider_badges = {
                "groq": "⚡ Groq LPU",
                "gemini": "🤖 Google Gemini",
                "openrouter": "🌐 OpenRouter",
                "openai": "🟢 OpenAI",
                "youtube": "📹 YouTube Data API",
            }

            valid_key_ids = set()
            for prov, keys in configured_keys.items():
                for k in keys:
                    k_id = self._get_key_identifier(prov, k)
                    valid_key_ids.add(k_id)
                    if k_id not in self._state.get("keys", {}):
                        self._state.setdefault("keys", {})[k_id] = {
                            "provider": prov,
                            "key_masked": mask_api_key(k),
                            "key_hash": hash_api_key(k),
                            "requests_today": 0,
                            "total_requests": 0,
                            "rate_limit_hits_today": 0,
                            "last_latency_ms": 0,
                            "last_model_used": getattr(settings, f"{prov.upper()}_MODEL_NAME", "—") if hasattr(settings, f"{prov.upper()}_MODEL_NAME") else "—",
                            "last_used_at": None,
                            "cooldown_until": 0.0,
                            "daily_limit_est": ESTIMATED_DAILY_LIMITS.get(prov, 1000),
                        }

            # Prune any stale or unconfigured keys from state for persistent storage
            prod_path = Path(settings.STORAGE_DIR) / "api_key_usage.json"
            if self.state_file.resolve() == prod_path.resolve():
                current_keys_in_state = list(self._state.get("keys", {}).keys())
                state_changed = False
                for k_id in current_keys_in_state:
                    if k_id not in valid_key_ids:
                        del self._state["keys"][k_id]
                        state_changed = True
                if state_changed:
                    self._save_state(self._state)

            for k_id, data in self._state.get("keys", {}).items():
                prov = data.get("provider", "unknown")
                daily_limit = data.get("daily_limit_est", ESTIMATED_DAILY_LIMITS.get(prov, 1000))
                reqs_today = data.get("requests_today", 0)
                cooldown_until = data.get("cooldown_until", 0.0)
                is_cooling = now_ts < cooldown_until
                cooldown_remaining_sec = max(0, int(cooldown_until - now_ts)) if is_cooling else 0
                is_in_flight = k_id in self._active_keys

                pct = round((reqs_today / daily_limit) * 100, 1) if daily_limit > 0 else 0.0
                pct = min(100.0, pct)

                last_error = data.get("last_error")
                last_status = data.get("last_status")

                if is_in_flight:
                    status_badge = "⚡ INVOKING (in-flight)"
                    status_class = "primary"
                    status_val = "INVOKING"
                elif is_cooling:
                    status_badge = f"🟡 RATE LIMITED ({cooldown_remaining_sec}s left)"
                    status_class = "warning"
                    status_val = "COOLDOWN"
                elif last_status == "ERROR" and reqs_today == 0:
                    status_badge = "🔴 ERROR (Check logs)"
                    status_class = "danger"
                    status_val = "ERROR"
                elif reqs_today > 0:
                    status_badge = "🟢 ACTIVE / HEALTHY"
                    status_class = "success"
                    status_val = "ACTIVE"
                else:
                    status_badge = "⚪ IDLE / READY"
                    status_class = "secondary"
                    status_val = "IDLE"

                result.append({
                    "id": k_id,
                    "provider": prov,
                    "provider_label": provider_badges.get(prov, prov.title()),
                    "key_masked": data.get("key_masked", "••••"),
                    "key_hash": data.get("key_hash", ""),
                    "requests_today": reqs_today,
                    "total_requests": data.get("total_requests", 0),
                    "all_time_requests": data.get("total_requests", 0),
                    "daily_limit": daily_limit,
                    "pct": pct,
                    "rate_limit_hits": data.get("rate_limit_hits_today", 0),
                    "rate_limit_hits_today": data.get("rate_limit_hits_today", 0),
                    "last_latency_ms": data.get("last_latency_ms", 0),
                    "avg_latency_ms": float(data.get("last_latency_ms", 0)),
                    "last_model_used": data.get("last_model_used", "—"),
                    "last_used_at": data.get("last_used_at") or "Not used today",
                    "status": status_val,
                    "status_badge": status_badge,
                    "status_class": status_class,
                    "is_cooling_down": is_cooling,
                    "cooldown_remaining_sec": cooldown_remaining_sec,
                    "in_flight": is_in_flight,
                    "last_error": last_error,
                    "remaining_tokens_minute": data.get("remaining_tokens_minute"),
                    "reset_tokens_sec": data.get("reset_tokens_sec"),
                    "remaining_requests_day": data.get("remaining_requests_day"),
                    "last_tokens_per_sec": data.get("last_tokens_per_sec"),
                    "last_completion_time_sec": data.get("last_completion_time_sec"),
                })

            result.sort(key=lambda x: (x["requests_today"], x["provider"]), reverse=True)
            return result

    def remove_key(self, provider: str, key_hash: str) -> bool:
        """Explicitly removes a key matching key_hash from state and disk."""
        with self._mutex:
            found = False
            target_prefix = f"{provider.lower()}:"
            for k_id in list(self._state.get("keys", {}).keys()):
                k_data = self._state["keys"][k_id]
                if k_id.startswith(target_prefix) and (
                    k_data.get("key_hash") == key_hash
                    or k_data.get("key_hash", "")[:16] == key_hash
                    or k_id.endswith(f":{key_hash}")
                ):
                    del self._state["keys"][k_id]
                    found = True
            if found:
                self._save_state(self._state)
            return found


    def get_summary(self, youtube_used: int = 0, youtube_limit: int = 10000) -> Dict[str, Any]:
        """Returns aggregated summary metrics for top API Quota card."""
        with self._mutex:
            self._ensure_today()
            all_keys = self.get_per_key_metrics()
            total_ai_requests = sum(k["requests_today"] for k in all_keys if k["provider"] != "youtube")
            total_ai_limit = sum(k["daily_limit"] for k in all_keys if k["provider"] != "youtube") or 1500

            ai_pct = round((total_ai_requests / total_ai_limit) * 100, 1) if total_ai_limit > 0 else 0.0
            yt_pct = round((youtube_used / youtube_limit) * 100, 1) if youtube_limit > 0 else 0.0
            combined_pct = max(ai_pct, yt_pct)

            groq_reqs = sum(k["requests_today"] for k in all_keys if k["provider"] == "groq")
            gemini_reqs = sum(k["requests_today"] for k in all_keys if k["provider"] == "gemini")
            openrouter_reqs = sum(k["requests_today"] for k in all_keys if k["provider"] == "openrouter")

            subtext = f"⚡ Groq: {groq_reqs} | 🤖 Gemini: {gemini_reqs} | 🌐 OpenRouter: {openrouter_reqs} | 📹 YT: {youtube_used}u"

            return {
                "used_units": total_ai_requests,
                "limit_units": total_ai_limit,
                "pct": combined_pct,
                "total_requests_today": total_ai_requests,
                "ai_requests_today": total_ai_requests,
                "youtube_used": youtube_used,
                "youtube_limit": youtube_limit,
                "subtext": subtext,
                "warning": combined_pct >= (settings.QUOTA_WARNING_THRESHOLD * 100),
                "date": self._state.get("date"),
            }


# Global singleton instance
usage_tracker = APIUsageTracker()
