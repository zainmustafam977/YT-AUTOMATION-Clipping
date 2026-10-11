"""
Thread-safe AI Token & Cost Tracking Service.
Tracks input/output token volume and estimated USD inference expenditure per clip,
per provider (Groq, Gemini, OpenRouter, OpenAI), and per day.
Persists state atomically to storage/ai_token_costs.json.
"""

import json
import logging
import threading
from datetime import datetime, date
from pathlib import Path
from typing import Dict, Any, Optional, List

from shorts_engine.config.settings import settings
from shorts_engine.utils.fs import atomic_write_json

logger = logging.getLogger(__name__)

# Standard Token Pricing Matrix (USD per 1,000,000 tokens)
# Source: Official provider developer pricing tiers (2025-2026 baselines)
DEFAULT_TOKEN_PRICING: Dict[str, Dict[str, float]] = {
    # Groq: Generous free tier default; LPU commercial rate fallback
    "groq": {
        "input_per_million": 0.05,
        "output_per_million": 0.08,
        "free_tier": True,
    },
    # Google Gemini: Gemini 1.5 / 2.0 / 2.5 Flash
    "gemini": {
        "input_per_million": 0.075,
        "output_per_million": 0.30,
        "free_tier": False,
    },
    # OpenRouter: Community free tier default
    "openrouter": {
        "input_per_million": 0.00,
        "output_per_million": 0.00,
        "free_tier": True,
    },
    # OpenAI: GPT-4o-mini
    "openai": {
        "input_per_million": 0.15,
        "output_per_million": 0.60,
        "free_tier": False,
    },
}


class AITokenCostTracker:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            # Allow isolated instances for unit tests if state_file is explicitly passed
            if kwargs.get("state_file") or (args and args[0]):
                instance = super(AITokenCostTracker, cls).__new__(cls)
                instance._initialized = False
                return instance
            if cls._instance is None:
                cls._instance = super(AITokenCostTracker, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self, state_file: Optional[Path] = None):
        target_path = Path(state_file or (Path(settings.STORAGE_DIR) / "ai_token_costs.json"))
        if getattr(self, "_initialized", False) and getattr(self, "state_file", None) == target_path:
            return
        self.state_file = target_path
        self._mutex = threading.RLock()
        self._state: Dict[str, Any] = self._load_state()
        self._initialized = True

    def _load_state(self) -> Dict[str, Any]:
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception as e:
                logger.warning("Failed to load ai_token_costs.json, creating fresh: %s", e)

        return {
            "version": 1,
            "totals": {
                "total_tokens": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "estimated_cost_usd": 0.0,
                "total_calls": 0,
            },
            "providers": {},
            "clips": {},
            "daily": {},
        }

    def _save_state(self) -> None:
        try:
            atomic_write_json(self.state_file, self._state, indent=2)
        except Exception as e:
            logger.warning("Failed to persist ai_token_costs.json: %s", e)

    def calculate_cost(
        self,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> float:
        """
        Calculates estimated cost in USD for the provided token usage.
        """
        prov_key = provider.lower()
        pricing = DEFAULT_TOKEN_PRICING.get(prov_key, {
            "input_per_million": 0.10,
            "output_per_million": 0.30,
            "free_tier": False,
        })

        input_rate = pricing.get("input_per_million", 0.0) / 1_000_000.0
        output_rate = pricing.get("output_per_million", 0.0) / 1_000_000.0

        cost = (input_tokens * input_rate) + (output_tokens * output_rate)
        return round(cost, 6)

    def record_usage(
        self,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        clip_id: Optional[str] = None,
        video_id: Optional[str] = None,
        stage: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Records an LLM execution's token consumption and updates cost ledgers.
        Returns dictionary with token counts and calculated USD cost.
        """
        inp = max(0, int(input_tokens or 0))
        out = max(0, int(output_tokens or 0))
        tot = inp + out
        cost = self.calculate_cost(provider, model, inp, out)
        today_str = date.today().isoformat()
        now_iso = datetime.utcnow().isoformat()
        prov_key = provider.lower()

        with self._mutex:
            # 1. Update global totals
            totals = self._state.setdefault("totals", {
                "total_tokens": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "estimated_cost_usd": 0.0,
                "total_calls": 0,
            })
            totals["total_tokens"] += tot
            totals["input_tokens"] += inp
            totals["output_tokens"] += out
            totals["estimated_cost_usd"] = round(totals.get("estimated_cost_usd", 0.0) + cost, 6)
            totals["total_calls"] += 1

            # 2. Update provider totals
            providers = self._state.setdefault("providers", {})
            prov_data = providers.setdefault(prov_key, {
                "total_tokens": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "estimated_cost_usd": 0.0,
                "total_calls": 0,
                "models": {},
            })
            prov_data["total_tokens"] += tot
            prov_data["input_tokens"] += inp
            prov_data["output_tokens"] += out
            prov_data["estimated_cost_usd"] = round(prov_data.get("estimated_cost_usd", 0.0) + cost, 6)
            prov_data["total_calls"] += 1

            # Model breakdown under provider
            model_name = model or "default"
            mod_data = prov_data["models"].setdefault(model_name, {
                "total_tokens": 0,
                "estimated_cost_usd": 0.0,
                "calls": 0,
            })
            mod_data["total_tokens"] += tot
            mod_data["estimated_cost_usd"] = round(mod_data.get("estimated_cost_usd", 0.0) + cost, 6)
            mod_data["calls"] += 1

            # 3. Update daily ledger
            daily = self._state.setdefault("daily", {})
            day_data = daily.setdefault(today_str, {
                "total_tokens": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "estimated_cost_usd": 0.0,
                "total_calls": 0,
            })
            day_data["total_tokens"] += tot
            day_data["input_tokens"] += inp
            day_data["output_tokens"] += out
            day_data["estimated_cost_usd"] = round(day_data.get("estimated_cost_usd", 0.0) + cost, 6)
            day_data["total_calls"] += 1

            # 4. Update per-clip ledger if clip_id is provided
            if clip_id:
                clips = self._state.setdefault("clips", {})
                clip_data = clips.setdefault(str(clip_id), {
                    "clip_id": str(clip_id),
                    "video_id": str(video_id) if video_id else None,
                    "total_tokens": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "estimated_cost_usd": 0.0,
                    "total_calls": 0,
                    "calls": [],
                })
                clip_data["total_tokens"] += tot
                clip_data["input_tokens"] += inp
                clip_data["output_tokens"] += out
                clip_data["estimated_cost_usd"] = round(clip_data.get("estimated_cost_usd", 0.0) + cost, 6)
                clip_data["total_calls"] += 1
                clip_data["calls"].append({
                    "provider": prov_key,
                    "model": model,
                    "input_tokens": inp,
                    "output_tokens": out,
                    "total_tokens": tot,
                    "cost_usd": cost,
                    "stage": stage or "inference",
                    "timestamp": now_iso,
                })

            self._save_state()

        return {
            "provider": prov_key,
            "model": model,
            "input_tokens": inp,
            "output_tokens": out,
            "total_tokens": tot,
            "cost_usd": cost,
            "clip_id": clip_id,
        }

    def get_clip_cost(self, clip_id: str) -> Dict[str, Any]:
        """
        Retrieves the aggregated token usage and USD cost for a specific clip.
        """
        with self._mutex:
            clips = self._state.get("clips", {})
            clip_info = clips.get(str(clip_id))
            if clip_info:
                return dict(clip_info)
            return {
                "clip_id": str(clip_id),
                "total_tokens": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "estimated_cost_usd": 0.0,
                "total_calls": 0,
                "calls": [],
            }

    def get_summary(self) -> Dict[str, Any]:
        """
        Returns full observability snapshot across totals, providers, daily, and clip counts.
        """
        with self._mutex:
            today_str = date.today().isoformat()
            daily_today = self._state.get("daily", {}).get(today_str, {
                "total_tokens": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "estimated_cost_usd": 0.0,
                "total_calls": 0,
            })
            return {
                "totals": dict(self._state.get("totals", {})),
                "today": dict(daily_today),
                "providers": dict(self._state.get("providers", {})),
                "total_tracked_clips": len(self._state.get("clips", {})),
                "pricing_tiers": DEFAULT_TOKEN_PRICING,
            }

    def reset_for_testing(self) -> None:
        """Clears memory and persisted file state for clean test isolation."""
        with self._mutex:
            self._state = {
                "version": 1,
                "totals": {
                    "total_tokens": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "estimated_cost_usd": 0.0,
                    "total_calls": 0,
                },
                "providers": {},
                "clips": {},
                "daily": {},
            }
            self._save_state()


# Module-level default singleton instance
token_tracker = AITokenCostTracker()

__all__ = [
    "AITokenCostTracker",
    "token_tracker",
    "DEFAULT_TOKEN_PRICING",
]
