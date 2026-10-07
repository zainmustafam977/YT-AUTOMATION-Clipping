"""
Multi-Provider LLM Router with Multi-Key Pool & Intelligent Tiered Failover.
Supports:
  1. Groq (Ultra-fast LPU inference: openai/gpt-oss-120b, qwen/qwen3.8-27b) - Tier 1 Free
  2. Google Gemini (via google-genai: gemini-3.8-flash, gemini-3.6-flash) - Tier 2 Free
  3. OpenRouter (Multi-model aggregator: liquid/lfm-2.5-2.6b:free, meta-llama/llama-3.3-70b-instruct:free) - Tier 3 Free
  4. OpenAI (via REST client: gpt-4o-mini) - Tier 4 Paid fallback
  5. Algorithmic Heuristic Fallback - Tier 5 Offline guaranteed continuity

Supports multiple API keys per provider with round-robin load-balancing,
intra-provider rate-limit failover with automatic 60s cooldowns,
and real-time per-key quota telemetry via APIUsageTracker.
"""

import re
import json
import time
import random
import logging
import threading
from typing import Type, TypeVar, Optional, Tuple, Dict, Any, List, Set
from pydantic import BaseModel
import httpx

from shorts_engine.config.settings import settings
from shorts_engine.config.logging_config import reload_runtime_settings
from shorts_engine.services.llm.quota_tracker import usage_tracker, mask_api_key

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class LLMRouter:
    _rr_counters: Dict[str, int] = {
        "groq": 0,
        "gemini": 0,
        "openrouter": 0,
        "openai": 0,
    }
    _rr_lock = threading.Lock()
    _gemini_last_call_time: Dict[str, float] = {}
    _gemini_pacing_lock = threading.Lock()
    _decommissioned_models: Set[str] = set()

    def __init__(
        self,
        gemini_api_key: Optional[str] = None,
        groq_api_key: Optional[str] = None,
        openrouter_api_key: Optional[str] = None,
        openai_api_key: Optional[str] = None,
        gemini_api_keys: Optional[List[str]] = None,
        groq_api_keys: Optional[List[str]] = None,
        openrouter_api_keys: Optional[List[str]] = None,
        openai_api_keys: Optional[List[str]] = None,
        gemini_model: Optional[str] = None,
        groq_model: Optional[str] = None,
        openrouter_model: Optional[str] = None,
        openai_model: Optional[str] = None,
        provider: Optional[str] = None,
        tracker: Optional[Any] = None,
    ):
        reload_runtime_settings()
        self.tracker = tracker or usage_tracker
        self._rr_counters: Dict[str, int] = {
            "groq": 0,
            "gemini": 0,
            "openrouter": 0,
            "openai": 0,
        }

        # Load key pools
        if groq_api_keys is not None:
            self.groq_api_keys = [k.strip() for k in groq_api_keys if k and k.strip()]
        elif groq_api_key is not None:
            self.groq_api_keys = [groq_api_key.strip()] if groq_api_key.strip() else []
        else:
            self.groq_api_keys = settings.get_groq_keys()

        if gemini_api_keys is not None:
            self.gemini_api_keys = [k.strip() for k in gemini_api_keys if k and k.strip()]
        elif gemini_api_key is not None:
            self.gemini_api_keys = [gemini_api_key.strip()] if gemini_api_key.strip() else []
        else:
            self.gemini_api_keys = settings.get_gemini_keys()

        if openrouter_api_keys is not None:
            self.openrouter_api_keys = [k.strip() for k in openrouter_api_keys if k and k.strip()]
        elif openrouter_api_key is not None:
            self.openrouter_api_keys = [openrouter_api_key.strip()] if openrouter_api_key.strip() else []
        else:
            self.openrouter_api_keys = settings.get_openrouter_keys()

        if openai_api_keys is not None:
            self.openai_api_keys = [k.strip() for k in openai_api_keys if k and k.strip()]
        elif openai_api_key is not None:
            self.openai_api_keys = [openai_api_key.strip()] if openai_api_key.strip() else []
        else:
            self.openai_api_keys = settings.get_openai_keys()

        # Primary single keys for backwards compatibility
        self.groq_api_key = self.groq_api_keys[0] if self.groq_api_keys else None
        self.gemini_api_key = self.gemini_api_keys[0] if self.gemini_api_keys else None
        self.openrouter_api_key = self.openrouter_api_keys[0] if self.openrouter_api_keys else None
        self.openai_api_key = self.openai_api_keys[0] if self.openai_api_keys else None

        self.gemini_model = gemini_model or settings.LLM_MODEL_NAME
        self.groq_model = groq_model or settings.GROQ_MODEL_NAME
        self.openrouter_model = openrouter_model or settings.OPENROUTER_MODEL_NAME
        self.openai_model = openai_model or settings.OPENAI_MODEL_NAME
        self.provider = (provider or settings.LLM_PROVIDER or "auto").lower()

        self._gemini_client = None

    def _get_ordered_keys(self, provider: str, keys: List[str]) -> List[str]:
        """Returns keys ordered by round-robin with cooling-down keys deprioritized."""
        if not keys:
            return []
        prov = provider.lower()
        with self._rr_lock:
            cur_idx = self._rr_counters.get(prov, 0)
            self._rr_counters[prov] = cur_idx + 1
        n = len(keys)
        start = cur_idx % n
        rotated = keys[start:] + keys[:start]
        active = [k for k in rotated if not self.tracker.is_cooling_down(prov, k)]
        cooling = [k for k in rotated if self.tracker.is_cooling_down(prov, k)]
        return active if active else cooling

    @property
    def gemini_client(self):
        if self._gemini_client is None and self.gemini_api_key:
            try:
                from google import genai
                from google.genai import types
                timeout_ms = int(float(getattr(settings, "GEMINI_TIMEOUT_SEC", 25.0)) * 1000)
                self._gemini_client = genai.Client(
                    api_key=self.gemini_api_key,
                    http_options=types.HttpOptions(timeout=timeout_ms),
                )
            except Exception as e:
                logger.warning("Could not initialize google.genai client: %s", str(e))
        return self._gemini_client

    def _get_gemini_client(self, active_key: Optional[str] = None):
        """Returns client, honoring class/instance property mocks or creating per-key client."""
        cls_prop = getattr(type(self), "gemini_client", None)
        if not isinstance(cls_prop, property):
            return getattr(self, "gemini_client", None)
        if self._gemini_client is not None and (active_key is None or active_key == self.gemini_api_key):
            return self._gemini_client
        target_key = active_key or self.gemini_api_key
        if not target_key:
            return None
        try:
            from google import genai
            from google.genai import types
            timeout_ms = int(float(getattr(settings, "GEMINI_TIMEOUT_SEC", 25.0)) * 1000)
            client = genai.Client(
                api_key=target_key,
                http_options=types.HttpOptions(timeout=timeout_ms),
            )
            if target_key == self.gemini_api_key:
                self._gemini_client = client
            return client
        except Exception as e:
            logger.warning("Could not create google.genai client for key %s: %s", mask_api_key(target_key), e)
            return None

    @property
    def is_groq_available(self) -> bool:
        return bool(self.groq_api_keys)

    @property
    def is_gemini_available(self) -> bool:
        return bool(self.gemini_api_keys)

    @property
    def is_openrouter_available(self) -> bool:
        return bool(self.openrouter_api_keys)

    @property
    def is_openai_available(self) -> bool:
        return bool(self.openai_api_keys)

    def _is_rate_limit_error(self, exc: Exception) -> bool:
        """Determines if an exception corresponds to an HTTP 429 or quota limit."""
        err_str = str(exc).lower()
        if "429" in err_str or "resource_exhausted" in err_str or "quota" in err_str or "rate limit" in err_str:
            return True
        code = getattr(exc, "code", None)
        status_code = getattr(exc, "status_code", None)
        return code == 429 or status_code == 429

    def _classify_error(self, status_code: Optional[int], err_text: str) -> str:
        """Classifies an error as PERMANENT (fast skip without retry) or TRANSIENT."""
        if status_code in (400, 401, 403, 404):
            return "PERMANENT"
        err_lower = err_text.lower()
        if any(w in err_lower for w in ["model_not_found", "does not exist", "deprecated", "decommissioned", "invalid_api_key", "bad_request"]):
            return "PERMANENT"
        return "TRANSIENT"

    def _extract_retry_delay(
        self,
        err_text: str,
        headers: Optional[Dict[str, str]] = None,
        default_delay: Optional[float] = None,
    ) -> Optional[float]:
        """Extracts suggested retry delay in seconds from error text or HTTP headers."""
        if headers:
            for h in ("retry-after", "Retry-After"):
                if h in headers:
                    try:
                        return float(headers[h])
                    except (ValueError, TypeError):
                        pass
        m = re.search(r"retry\s+in\s+([0-9]+(?:\.[0-9]+)?)\s*s?", err_text, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
        m = re.search(r"retry\s+after\s+([0-9]+(?:\.[0-9]+)?)\s*(?:s|seconds)?", err_text, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
        m = re.search(r"retryDelay[\"']?\s*:\s*[\"']?([0-9]+(?:\.[0-9]+)?)s?", err_text, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
        return default_delay

    def generate_structured(
        self,
        prompt: str,
        schema: Type[T],
        temperature: float = 0.2,
        video_id: Optional[str] = None,
    ) -> Tuple[Optional[T], Dict[str, Any]]:
        reload_runtime_settings()
        provider_mode = self.provider

        # Explicit single provider mode
        if provider_mode == "groq":
            res, meta = self._call_groq(prompt, schema, temperature=temperature, video_id=video_id)
            if res is not None:
                return res, meta
            if not settings.LLM_FALLBACK_ON_RATE_LIMIT:
                return None, meta
        elif provider_mode == "gemini":
            res, meta = self._call_gemini(prompt, schema, temperature=temperature, video_id=video_id)
            if res is not None:
                return res, meta
            if not settings.LLM_FALLBACK_ON_RATE_LIMIT:
                return None, meta
        elif provider_mode == "openrouter":
            res, meta = self._call_openrouter(prompt, schema, temperature=temperature, video_id=video_id)
            if res is not None:
                return res, meta
            if not settings.LLM_FALLBACK_ON_RATE_LIMIT:
                return None, meta
        elif provider_mode == "openai":
            res, meta = self._call_openai(prompt, schema, temperature=temperature, video_id=video_id)
            if res is not None:
                return res, meta
            if not settings.LLM_FALLBACK_ON_RATE_LIMIT:
                return None, meta

        # Token estimation pre-flight:
        estimated_tokens = int(len(prompt.split()) * 1.33)
        is_large_payload = estimated_tokens >= getattr(settings, "GROQ_TOKEN_THRESHOLD", 5500)

        # Intelligent Dynamic Quality-Ranked Failover Chain:
        # Prioritize providers whose keys are ready (not cooling down).
        # For large payloads (>= 5,500 tokens), prioritize Gemini (1M context) to avoid Groq 8,000 TPM limit (HTTP 413).
        groq_base_rank = 10 if is_large_payload else 1
        gemini_base_rank = 1 if is_large_payload else 2
        openrouter_base_rank = 3
        openai_base_rank = 4

        available_providers = []
        if self.is_groq_available:
            available_providers.append(("groq", self._call_groq, groq_base_rank))
        if self.is_gemini_available:
            available_providers.append(("gemini", self._call_gemini, gemini_base_rank))
        if self.is_openrouter_available:
            available_providers.append(("openrouter", self._call_openrouter, openrouter_base_rank))
        if self.is_openai_available:
            available_providers.append(("openai", self._call_openai, openai_base_rank))

        def _provider_sort_key(item):
            p_name, _, base_rank = item
            keys = getattr(self, f"{p_name}_api_keys", [])
            all_cooling = bool(keys) and all(self.tracker.is_cooling_down(p_name, k) for k in keys)
            return (1 if all_cooling else 0, base_rank)

        sorted_tiers = sorted(available_providers, key=_provider_sort_key)

        failover_trail = []
        tried = []
        for idx, (p_name, call_fn, base_rank) in enumerate(sorted_tiers):
            is_failover = idx > 0
            logger.info("Executing LLM Tier %d provider: %s (failover=%s)...", idx + 1, p_name, is_failover)
            res, meta = call_fn(prompt, schema, temperature=temperature, failover_triggered=is_failover, video_id=video_id)
            if res is not None:
                failover_trail.append({
                    "provider": p_name,
                    "model": meta.get("model"),
                    "status": "SUCCESS",
                    "latency_ms": meta.get("latency_ms", 0),
                })
                meta["failover_trail"] = failover_trail
                meta["is_failover"] = is_failover
                return res, meta

            # Failed attempt
            failover_trail.append({
                "provider": p_name,
                "model": meta.get("model") or "unknown",
                "status": "FAILED",
                "error": meta.get("error", "failed")[:120],
                "latency_ms": meta.get("latency_ms", 0),
            })
            tried.append(f"{p_name}: {meta.get('error', 'failed')}")

            # Record live failover alert if there is a next provider
            if idx < len(sorted_tiers) - 1:
                next_prov = sorted_tiers[idx + 1][0]
                self.tracker.record_failover_event(
                    video_id=video_id,
                    from_provider=p_name,
                    to_provider=next_prov,
                    reason=meta.get("error", "error")[:80],
                )

        logger.warning(
            "All configured LLM providers failed or no keys configured: %s. Tripping Tier 5 Heuristic Circuit Breaker.",
            ", ".join(tried) if tried else "no keys",
        )
        self.tracker.record_failover_event(
            video_id=video_id,
            from_provider=sorted_tiers[-1][0] if sorted_tiers else "none",
            to_provider="heuristic",
            reason="All cloud AI tiers exhausted; circuit breaker triggered",
        )
        err_msg = "No LLM API keys configured" if not sorted_tiers else f"All LLM providers unavailable ({', '.join(tried)})"
        return None, {
            "provider": "heuristic",
            "model": "algorithmic-fallback",
            "badge": "⚙️ Algorithmic Heuristic Fallback",
            "failover": True,
            "is_failover": True,
            "is_circuit_breaker": True,
            "failover_trail": failover_trail,
            "error": err_msg,
        }

    def _call_groq(
        self,
        prompt: str,
        schema: Type[T],
        temperature: float = 0.2,
        failover_triggered: bool = False,
        video_id: Optional[str] = None,
    ) -> Tuple[Optional[T], Dict[str, Any]]:
        """Calls Groq Chat Completions API with multi-key round-robin, permanent 404 fast-skip & retry delay."""
        if not self.groq_api_keys:
            return None, {"error": "GROQ_API_KEY not configured", "provider": "groq"}

        # Pre-check: bypass Groq if prompt exceeds Groq TPM safe limit and other tiers are available
        est_tokens = int(len(prompt.split()) * 1.33)
        groq_limit = getattr(settings, "GROQ_TOKEN_THRESHOLD", 5500)
        if est_tokens >= groq_limit:
            if self.is_gemini_available or self.is_openrouter_available or self.is_openai_available:
                logger.info("Prompt size (~%d tokens) exceeds Groq safe limit (%d TPM). Fast-routing to large-context tier.", est_tokens, groq_limit)
                return None, {
                    "error": f"Payload (~{est_tokens} tokens) exceeds Groq TPM threshold ({groq_limit})",
                    "provider": "groq",
                    "failover": failover_triggered,
                    "is_oversized": True,
                }
            else:
                # Other tiers unavailable: auto-budget window prompt to protect against Groq 413 TPM overflow
                logger.info("Prompt size (~%d tokens) exceeds Groq TPM ceiling (%d). Auto-windowing prompt for Groq compatibility.", est_tokens, groq_limit)
                words = prompt.split()
                safe_word_count = int(groq_limit / 1.4)
                prompt = " ".join(words[:safe_word_count]) + "\n\n[Transcript truncated for token budget compliance]"
                est_tokens = int(len(prompt.split()) * 1.33)

        models_to_try = [self.groq_model]
        for alt in [
            "openai/gpt-oss-120b",
            "openai/gpt-oss-20b",
            "qwen/qwen3.8-27b",
            "groq/compound-mini",
            "groq/compound",
        ]:
            if alt not in models_to_try:
                models_to_try.append(alt)
        models_to_try = [m for m in models_to_try if m not in self._decommissioned_models]

        schema_json = json.dumps(schema.model_json_schema()) if hasattr(schema, "model_json_schema") else "{}"
        system_msg = (
            "You are an expert viral YouTube Shorts producer and AI content analyst. "
            "You MUST respond ONLY with a valid JSON object strictly complying with this JSON Schema:\n"
            f"{schema_json}\n"
            "Do not include markdown code fences or conversational text outside the JSON."
        )

        keys_to_try = self._get_ordered_keys("groq", self.groq_api_keys)

        last_error = None
        for key_idx, active_key in enumerate(keys_to_try):
            # Proactive rate-limit budgeting: check if recorded tokens in 1m window are insufficient
            rem_tokens = self.tracker.get_groq_remaining_tokens(active_key)
            if rem_tokens is not None and rem_tokens < (est_tokens + 200):
                logger.info("Groq key %s has only %d remaining tokens (< %d needed). Fast-skipping to next key or tier.", mask_api_key(active_key), rem_tokens, est_tokens)
                continue

            headers = {
                "Authorization": f"Bearer {active_key}",
                "Content-Type": "application/json",
                "Groq-Beta": "inference-metrics",
            }
            is_key_failover = key_idx > 0

            for target_model in models_to_try:
                payload = {
                    "model": target_model,
                    "messages": [
                        {"role": "system", "content": system_msg},
                        {"role": "user", "content": prompt},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": temperature,
                }
                # Protect models with strict OTPM ceiling (e.g. qwen3.8-27b OTPM limit 1000)
                if "qwen" in target_model.lower():
                    payload["max_tokens"] = 800

                t0 = time.perf_counter()
                self.tracker.set_active_call(
                    provider="groq",
                    model=target_model,
                    video_id=video_id,
                    status_text=f"⚡ Groq executing {target_model}...",
                    key=active_key,
                )
                try:
                    logger.info("Calling Groq API (%s) using key %s...", target_model, mask_api_key(active_key))
                    with httpx.Client(timeout=45.0) as client:
                        res = client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload)
                        latency_ms = int((time.perf_counter() - t0) * 1000)
                        resp_headers = dict(res.headers)

                        if res.status_code == 404:
                            self._decommissioned_models.add(target_model)
                            self.tracker.record_request("groq", active_key, status="ERROR", latency_ms=latency_ms, model=target_model, error=f"404: {res.text}", headers=resp_headers)
                            logger.warning("Groq model %s returned 404 (model deprecated). Added to blacklist, fast-skipping...", target_model)
                            last_error = f"HTTP 404: Model {target_model} decommissioned"
                            continue

                        if res.status_code == 429:
                            delay = self._extract_retry_delay(res.text, resp_headers)
                            if delay and delay <= 15.0:
                                logger.info("Groq 429 delay is %.1fs. Waiting and retrying once...", delay)
                                time.sleep(delay)
                                res = client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload)
                                latency_ms = int((time.perf_counter() - t0) * 1000)
                                resp_headers = dict(res.headers)
                                if res.status_code == 200:
                                    pass  # proceed to parse
                            if res.status_code == 429:
                                cd = delay if (delay and delay > 0) else 60.0
                                self.tracker.record_request("groq", active_key, status="RATE_LIMITED", latency_ms=latency_ms, model=target_model, error=res.text, cooldown_seconds=cd, headers=resp_headers)
                                logger.warning("Groq key %s hit 429 rate limit (cooldown %ds). Trying next key...", mask_api_key(active_key), int(cd))
                                last_error = f"HTTP 429: {res.text}"
                                break

                        if res.status_code == 413:
                            self.tracker.record_request("groq", active_key, status="ERROR", latency_ms=latency_ms, model=target_model, error=f"413: {res.text[:120]}", headers=resp_headers)
                            logger.warning("Groq API returned HTTP 413 (Payload Too Large): %s. Fast-skipping Groq tier for this prompt.", res.text[:120])
                            return None, {
                                "error": f"HTTP 413: {res.text[:120]}",
                                "provider": "groq",
                                "failover": failover_triggered,
                                "is_oversized": True,
                            }

                        if res.status_code != 200:
                            self.tracker.record_request("groq", active_key, status="ERROR", latency_ms=latency_ms, model=target_model, error=res.text, headers=resp_headers)
                            logger.warning("Groq API model %s returned status %s: %s", target_model, res.status_code, res.text)
                            last_error = f"HTTP {res.status_code}: {res.text}"
                            continue

                        data = res.json()
                        choice = data.get("choices", [{}])[0]
                        message = choice.get("message", {})
                        content = message.get("content")
                        if not content and "reasoning" in message:
                            content = message["reasoning"]
                        if not content:
                            last_error = "Empty completion content"
                            continue

                        clean_content = content.strip()
                        if clean_content.startswith("```"):
                            lines = clean_content.splitlines()
                            if lines[0].startswith("```"):
                                lines = lines[1:]
                            if lines and lines[-1].startswith("```"):
                                lines = lines[:-1]
                            clean_content = "\n".join(lines).strip()

                        parsed = json.loads(clean_content)
                        validated = schema.model_validate(parsed) if hasattr(schema, "model_validate") else parsed

                        # Extract inference metrics from Groq-Beta header response
                        metadata = data.get("metadata")
                        tokens_per_sec = None
                        if metadata and isinstance(metadata, dict):
                            comp_time = metadata.get("completion_time")
                            usage = data.get("usage", {})
                            comp_tok = usage.get("completion_tokens", 0)
                            if comp_time and comp_tok:
                                try:
                                    tokens_per_sec = round(float(comp_tok) / float(comp_time), 1)
                                except (ValueError, TypeError, ZeroDivisionError):
                                    pass

                        self.tracker.record_request(
                            "groq",
                            active_key,
                            status="SUCCESS",
                            latency_ms=latency_ms,
                            model=target_model,
                            headers=resp_headers,
                            inference_metrics=metadata,
                        )

                        return validated, {
                            "provider": "groq",
                            "model": target_model,
                            "badge": f"⚡ Groq ({target_model})",
                            "key_masked": mask_api_key(active_key),
                            "failover": failover_triggered or is_key_failover,
                            "latency_ms": latency_ms,
                            "tokens_per_sec": tokens_per_sec,
                        }
                except Exception as e:
                    latency_ms = int((time.perf_counter() - t0) * 1000)
                    last_error = str(e)
                    if self._is_rate_limit_error(e):
                        delay = self._extract_retry_delay(str(e))
                        cd = delay if (delay and delay > 0) else 60.0
                        self.tracker.record_request("groq", active_key, status="RATE_LIMITED", latency_ms=latency_ms, model=target_model, error=str(e), cooldown_seconds=cd)
                        break
                    else:
                        self.tracker.record_request("groq", active_key, status="ERROR", latency_ms=latency_ms, model=target_model, error=str(e))
                        logger.warning("Groq call for model %s failed: %s", target_model, str(e))
                finally:
                    self.tracker.clear_active_call("groq", active_key)

        return None, {
            "error": str(last_error),
            "provider": "groq",
            "failover": failover_triggered,
        }

    def _call_gemini(
        self,
        prompt: str,
        schema: Type[T],
        temperature: float = 0.2,
        failover_triggered: bool = False,
        video_id: Optional[str] = None,
    ) -> Tuple[Optional[T], Dict[str, Any]]:
        """Calls Google Gemini with client-side rate pacing (3.0s/key), retry delay parsing, and multi-key failover."""
        if not self.gemini_api_keys:
            return None, {"error": "GEMINI_API_KEY not configured", "provider": "gemini"}

        models_to_try = [self.gemini_model]
        for alt in ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-flash-latest"]:
            if alt not in models_to_try:
                models_to_try.append(alt)

        keys_to_try = self._get_ordered_keys("gemini", self.gemini_api_keys)

        last_error = None
        for key_idx, active_key in enumerate(keys_to_try):
            is_key_failover = key_idx > 0
            client = self._get_gemini_client(active_key)
            if client is None:
                continue

            for target_model in models_to_try:
                t0 = time.perf_counter()
                # Client-side pacing: enforce minimum pacing interval between requests per key to respect 15 RPM ceiling
                pacing_interval = float(getattr(settings, "GEMINI_PACING_SEC", 4.0))
                with self._gemini_pacing_lock:
                    last_call = self._gemini_last_call_time.get(active_key, 0.0)
                    elapsed = time.time() - last_call
                    wait_time = max(0.0, pacing_interval - elapsed)
                    if wait_time > 0:
                        time.sleep(wait_time)
                    self._gemini_last_call_time[active_key] = time.time()

                self.tracker.set_active_call(
                    provider="gemini",
                    model=target_model,
                    video_id=video_id,
                    status_text=f"🤖 Gemini executing {target_model}...",
                    key=active_key,
                )
                try:
                    logger.info("Calling Gemini (%s) using key %s...", target_model, mask_api_key(active_key))
                    raw_text = None
                    used_api = "generate_content"
                    if hasattr(client, "interactions") and hasattr(client.interactions, "create"):
                        try:
                            schema_def = schema.model_json_schema() if hasattr(schema, "model_json_schema") else {}
                            resp_fmt = [{"type": "text", "mime_type": "application/json"}]
                            if schema_def and "$defs" not in schema_def:
                                resp_fmt[0]["schema"] = schema_def
                            interaction = client.interactions.create(
                                model=target_model,
                                input=prompt,
                                response_format=resp_fmt,
                                temperature=temperature,
                            )
                            out_candidate = getattr(interaction, "output_text", None)
                            if isinstance(out_candidate, str) and out_candidate.strip():
                                raw_text = out_candidate
                                used_api = "interactions"
                        except Exception as inter_err:
                            logger.debug("Gemini Interactions API call fell back to generate_content: %s", inter_err)
                            raw_text = None

                    if raw_text is None:
                        from google.genai import types
                        gen_config = dict(
                            response_mime_type="application/json",
                            temperature=temperature,
                            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                        )
                        if hasattr(schema, "model_json_schema") or hasattr(schema, "__fields__"):
                            gen_config["response_schema"] = schema
                        response = client.models.generate_content(
                            model=target_model,
                            contents=prompt,
                            config=gen_config,
                        )
                        resp_text = getattr(response, "text", None)
                        if isinstance(resp_text, str):
                            raw_text = resp_text
                        elif isinstance(resp_text, dict):
                            raw_text = json.dumps(resp_text)
                        else:
                            raw_text = str(resp_text)

                    latency_ms = int((time.perf_counter() - t0) * 1000)
                    parsed = json.loads(raw_text) if isinstance(raw_text, str) else raw_text
                    if hasattr(schema, "model_validate"):
                        validated = schema.model_validate(parsed)
                    else:
                        validated = parsed

                    self.tracker.record_request("gemini", active_key, status="SUCCESS", latency_ms=latency_ms, model=target_model)

                    return validated, {
                        "provider": "gemini",
                        "model": target_model,
                        "badge": f"🤖 Gemini ({target_model})",
                        "key_masked": mask_api_key(active_key),
                        "failover": failover_triggered or is_key_failover,
                        "latency_ms": latency_ms,
                        "api": used_api,
                    }
                except Exception as e:
                    latency_ms = int((time.perf_counter() - t0) * 1000)
                    last_error = e
                    if self._is_rate_limit_error(e):
                        delay = self._extract_retry_delay(str(e))
                        if delay and delay <= 15.0:
                            logger.info("Gemini 429 suggests retry in %.1fs (<= 15s). Pacing wait and retrying once...", delay)
                            time.sleep(delay)
                            try:
                                raw_text = None
                                used_api = "generate_content"
                                if hasattr(client, "interactions") and hasattr(client.interactions, "create"):
                                    try:
                                        schema_def = schema.model_json_schema() if hasattr(schema, "model_json_schema") else {}
                                        resp_fmt = [{"type": "text", "mime_type": "application/json"}]
                                        if schema_def and "$defs" not in schema_def:
                                            resp_fmt[0]["schema"] = schema_def
                                        interaction = client.interactions.create(
                                            model=target_model,
                                            input=prompt,
                                            response_format=resp_fmt,
                                            temperature=temperature,
                                        )
                                        out_candidate = getattr(interaction, "output_text", None)
                                        if isinstance(out_candidate, str) and out_candidate.strip():
                                            raw_text = out_candidate
                                            used_api = "interactions"
                                    except Exception:
                                        raw_text = None
                                if raw_text is None:
                                    gen_config = dict(
                                        response_mime_type="application/json",
                                        temperature=temperature,
                                    )
                                    if hasattr(schema, "model_json_schema") or hasattr(schema, "__fields__"):
                                        gen_config["response_schema"] = schema
                                    response = client.models.generate_content(
                                        model=target_model,
                                        contents=prompt,
                                        config=gen_config,
                                    )
                                    resp_text = getattr(response, "text", None)
                                    if isinstance(resp_text, str):
                                        raw_text = resp_text
                                    elif isinstance(resp_text, dict):
                                        raw_text = json.dumps(resp_text)
                                    else:
                                        raw_text = str(resp_text)
                                latency_ms = int((time.perf_counter() - t0) * 1000)
                                parsed = json.loads(raw_text) if isinstance(raw_text, str) else raw_text
                                if hasattr(schema, "model_validate"):
                                    validated = schema.model_validate(parsed)
                                else:
                                    validated = parsed
                                self.tracker.record_request("gemini", active_key, status="SUCCESS", latency_ms=latency_ms, model=target_model)
                                return validated, {
                                    "provider": "gemini",
                                    "model": target_model,
                                    "badge": f"🤖 Gemini ({target_model})",
                                    "key_masked": mask_api_key(active_key),
                                    "failover": failover_triggered or is_key_failover,
                                    "latency_ms": latency_ms,
                                    "api": used_api,
                                }
                            except Exception as retry_err:
                                last_error = retry_err
                        cd = delay if (delay and delay > 0) else 60.0
                        self.tracker.record_request("gemini", active_key, status="RATE_LIMITED", latency_ms=latency_ms, model=target_model, error=str(last_error), cooldown_seconds=cd)
                        logger.warning("Gemini key %s hit 429 (cooldown %ds). Trying next key if available...", mask_api_key(active_key), int(cd))
                        break  # Try next key!
                    else:
                        err_str = str(e).lower()
                        if "503" in err_str or "unavailable" in err_str or "504" in err_str or "deadline" in err_str:
                            wait_time = min(10.0, 2.5 + random.uniform(0.5, 2.0))
                            logger.info("Gemini transient issue (%s). Waiting %.1fs with jitter before retry...", "504 deadline" if "504" in err_str or "deadline" in err_str else "503 demand spike", wait_time)
                            time.sleep(wait_time)
                            try:
                                raw_text = None
                                used_api = "generate_content"
                                if hasattr(client, "interactions") and hasattr(client.interactions, "create"):
                                    try:
                                        schema_def = schema.model_json_schema() if hasattr(schema, "model_json_schema") else {}
                                        resp_fmt = [{"type": "text", "mime_type": "application/json"}]
                                        if schema_def:
                                            resp_fmt[0]["schema"] = schema_def
                                        interaction = client.interactions.create(
                                            model=target_model,
                                            input=prompt,
                                            response_format=resp_fmt,
                                            temperature=temperature,
                                        )
                                        out_candidate = getattr(interaction, "output_text", None)
                                        if isinstance(out_candidate, str) and out_candidate.strip():
                                            raw_text = out_candidate
                                            used_api = "interactions"
                                    except Exception:
                                        raw_text = None
                                if raw_text is None:
                                    gen_config = dict(
                                        response_mime_type="application/json",
                                        temperature=temperature,
                                    )
                                    if hasattr(schema, "model_json_schema") or hasattr(schema, "__fields__"):
                                        gen_config["response_schema"] = schema
                                    response = client.models.generate_content(
                                        model=target_model,
                                        contents=prompt,
                                        config=gen_config,
                                    )
                                    resp_text = getattr(response, "text", None)
                                    if isinstance(resp_text, str):
                                        raw_text = resp_text
                                    elif isinstance(resp_text, dict):
                                        raw_text = json.dumps(resp_text)
                                    else:
                                        raw_text = str(resp_text)
                                latency_ms = int((time.perf_counter() - t0) * 1000)
                                parsed = json.loads(raw_text) if isinstance(raw_text, str) else raw_text
                                if hasattr(schema, "model_validate"):
                                    validated = schema.model_validate(parsed)
                                else:
                                    validated = parsed
                                self.tracker.record_request("gemini", active_key, status="SUCCESS", latency_ms=latency_ms, model=target_model)
                                return validated, {
                                    "provider": "gemini",
                                    "model": target_model,
                                    "badge": f"🤖 Gemini ({target_model})",
                                    "key_masked": mask_api_key(active_key),
                                    "failover": failover_triggered or is_key_failover,
                                    "latency_ms": latency_ms,
                                    "api": used_api,
                                }
                            except Exception as retry_503_err:
                                last_error = retry_503_err
                        self.tracker.record_request("gemini", active_key, status="ERROR", latency_ms=latency_ms, model=target_model, error=str(e))
                        logger.warning("Gemini model %s failed: %s", target_model, str(e))
                finally:
                    self.tracker.clear_active_call("gemini", active_key)

        return None, {
            "error": str(last_error),
            "provider": "gemini",
            "failover": failover_triggered,
        }

    def _call_openrouter(
        self,
        prompt: str,
        schema: Type[T],
        temperature: float = 0.2,
        failover_triggered: bool = False,
        video_id: Optional[str] = None,
    ) -> Tuple[Optional[T], Dict[str, Any]]:
        """Calls OpenRouter Chat Completions API with multi-key round-robin, backup free slug failover, and retry delay."""
        if not self.openrouter_api_keys:
            return None, {"error": "OPENROUTER_API_KEY not configured", "provider": "openrouter"}

        models_to_try = [self.openrouter_model]
        for alt in [
            "google/gemini-2.5-flash-thinking-preview:free",
            "deepseek/deepseek-r1:free",
            "meta-llama/llama-3.2-3b-instruct:free",
            "liquid/lfm-7b:free",
            "mistralai/mistral-small-3.2:free",
        ]:
            if alt not in models_to_try:
                models_to_try.append(alt)
        models_to_try = [m for m in models_to_try if m not in self._decommissioned_models]

        schema_json = json.dumps(schema.model_json_schema())
        system_msg = (
            "You are an expert viral YouTube Shorts producer and AI content analyst. "
            "You MUST respond ONLY with a valid JSON object strictly complying with this JSON Schema:\n"
            f"{schema_json}\n"
            "Do not include markdown code fences or conversational text outside the JSON."
        )

        keys_to_try = self._get_ordered_keys("openrouter", self.openrouter_api_keys)

        last_error = None
        for key_idx, active_key in enumerate(keys_to_try):
            is_key_failover = key_idx > 0
            headers = {
                "Authorization": f"Bearer {active_key}",
                "HTTP-Referer": "http://127.0.0.1:8000",
                "X-Title": "YouTube Shorts Engine",
                "Content-Type": "application/json",
            }

            for target_model in models_to_try:
                payload = {
                    "model": target_model,
                    "messages": [
                        {"role": "system", "content": system_msg},
                        {"role": "user", "content": prompt},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": temperature,
                }
                t0 = time.perf_counter()
                self.tracker.set_active_call(
                    provider="openrouter",
                    model=target_model,
                    video_id=video_id,
                    status_text=f"🌐 OpenRouter executing {target_model}...",
                    key=active_key,
                )
                try:
                    logger.info("Calling OpenRouter API (%s) using key %s...", target_model, mask_api_key(active_key))
                    with httpx.Client(timeout=45.0) as client:
                        res = client.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload)
                        latency_ms = int((time.perf_counter() - t0) * 1000)

                        if res.status_code == 404:
                            self._decommissioned_models.add(target_model)
                            self.tracker.record_request("openrouter", active_key, status="ERROR", latency_ms=latency_ms, model=target_model, error=f"404: {res.text}")
                            logger.warning("OpenRouter model %s returned 404 (free slug decommissioned). Added to blacklist, fast-skipping...", target_model)
                            last_error = f"HTTP 404: Model {target_model} decommissioned"
                            continue

                        if res.status_code == 429:
                            delay = self._extract_retry_delay(res.text, dict(res.headers))
                            cd = delay if (delay and delay > 0) else 60.0
                            self.tracker.record_request("openrouter", active_key, status="RATE_LIMITED", latency_ms=latency_ms, model=target_model, error=res.text, cooldown_seconds=cd)
                            logger.warning("OpenRouter key %s hit 429 (cooldown %ds). Trying next key...", mask_api_key(active_key), int(cd))
                            last_error = f"HTTP 429: {res.text}"
                            break

                        if res.status_code != 200:
                            self.tracker.record_request("openrouter", active_key, status="ERROR", latency_ms=latency_ms, model=target_model, error=res.text)
                            logger.warning("OpenRouter API model %s returned status %s: %s", target_model, res.status_code, res.text)
                            last_error = f"HTTP {res.status_code}: {res.text}"
                            continue

                        data = res.json()
                        choice = data.get("choices", [{}])[0]
                        message = choice.get("message", {})
                        content = message.get("content")
                        if not content and "reasoning" in message:
                            content = message["reasoning"]
                        if not content:
                            last_error = "Empty completion content from model"
                            continue

                        clean_content = content.strip()
                        if clean_content.startswith("```"):
                            lines = clean_content.splitlines()
                            if lines[0].startswith("```"):
                                lines = lines[1:]
                            if lines and lines[-1].startswith("```"):
                                lines = lines[:-1]
                            clean_content = "\n".join(lines).strip()

                        parsed = json.loads(clean_content)
                        validated = schema.model_validate(parsed)
                        self.tracker.record_request("openrouter", active_key, status="SUCCESS", latency_ms=latency_ms, model=target_model)

                        return validated, {
                            "provider": "openrouter",
                            "model": target_model,
                            "badge": f"🌐 OpenRouter ({target_model})",
                            "key_masked": mask_api_key(active_key),
                            "failover": failover_triggered or is_key_failover,
                            "latency_ms": latency_ms,
                        }
                except Exception as e:
                    latency_ms = int((time.perf_counter() - t0) * 1000)
                    last_error = str(e)
                    if self._is_rate_limit_error(e):
                        delay = self._extract_retry_delay(str(e))
                        cd = delay if (delay and delay > 0) else 60.0
                        self.tracker.record_request("openrouter", active_key, status="RATE_LIMITED", latency_ms=latency_ms, model=target_model, error=str(e), cooldown_seconds=cd)
                        break
                    else:
                        self.tracker.record_request("openrouter", active_key, status="ERROR", latency_ms=latency_ms, model=target_model, error=str(e))
                        logger.warning("OpenRouter call for model %s failed: %s", target_model, str(e))
                finally:
                    self.tracker.clear_active_call("openrouter", active_key)

        return None, {
            "error": str(last_error),
            "provider": "openrouter",
            "failover": failover_triggered,
        }

    def _call_openai(
        self,
        prompt: str,
        schema: Type[T],
        temperature: float = 0.2,
        failover_triggered: bool = False,
        video_id: Optional[str] = None,
    ) -> Tuple[Optional[T], Dict[str, Any]]:
        """Calls OpenAI Chat Completions API with multi-key round-robin & retry delay."""
        if not self.openai_api_keys:
            return None, {"error": "OPENAI_API_KEY not configured", "provider": "openai"}

        schema_json = json.dumps(schema.model_json_schema())
        system_msg = (
            "You are an expert viral YouTube Shorts producer and AI content analyst. "
            "You MUST respond ONLY with a valid JSON object strictly complying with this JSON Schema:\n"
            f"{schema_json}\n"
            "Do not include markdown code fences or conversational text outside the JSON."
        )

        keys_to_try = self._get_ordered_keys("openai", self.openai_api_keys)

        last_error = None
        for key_idx, active_key in enumerate(keys_to_try):
            is_key_failover = key_idx > 0
            headers = {
                "Authorization": f"Bearer {active_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": self.openai_model,
                "messages": [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt},
                ],
                "response_format": {"type": "json_object"},
                "temperature": temperature,
            }
            t0 = time.perf_counter()
            self.tracker.set_active_call(
                provider="openai",
                model=self.openai_model,
                video_id=video_id,
                status_text=f"🟢 OpenAI executing {self.openai_model}...",
                key=active_key,
            )
            try:
                logger.info("Calling OpenAI API (%s) using key %s...", self.openai_model, mask_api_key(active_key))
                with httpx.Client(timeout=45.0) as client:
                    res = client.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload)
                    latency_ms = int((time.perf_counter() - t0) * 1000)

                    if res.status_code == 429:
                        delay = self._extract_retry_delay(res.text, dict(res.headers))
                        cd = delay if (delay and delay > 0) else 60.0
                        self.tracker.record_request("openai", active_key, status="RATE_LIMITED", latency_ms=latency_ms, model=self.openai_model, error=res.text, cooldown_seconds=cd)
                        logger.warning("OpenAI key %s hit 429 (cooldown %ds). Trying next key if available...", mask_api_key(active_key), int(cd))
                        last_error = f"HTTP 429: {res.text}"
                        continue

                    if res.status_code != 200:
                        self.tracker.record_request("openai", active_key, status="ERROR", latency_ms=latency_ms, model=self.openai_model, error=res.text)
                        logger.warning("OpenAI API returned status %s: %s", res.status_code, res.text)
                        last_error = f"HTTP {res.status_code}: {res.text}"
                        continue

                    data = res.json()
                    choice = data.get("choices", [{}])[0]
                    content = choice.get("message", {}).get("content", "")
                    clean_content = content.strip()
                    if clean_content.startswith("```"):
                        lines = clean_content.splitlines()
                        if lines[0].startswith("```"):
                            lines = lines[1:]
                        if lines and lines[-1].startswith("```"):
                            lines = lines[:-1]
                        clean_content = "\n".join(lines).strip()

                    parsed = json.loads(clean_content)
                    validated = schema.model_validate(parsed)
                    self.tracker.record_request("openai", active_key, status="SUCCESS", latency_ms=latency_ms, model=self.openai_model)

                    return validated, {
                        "provider": "openai",
                        "model": self.openai_model,
                        "badge": f"🟢 OpenAI ({self.openai_model})",
                        "key_masked": mask_api_key(active_key),
                        "failover": failover_triggered or is_key_failover,
                        "latency_ms": latency_ms,
                    }
            except Exception as e:
                latency_ms = int((time.perf_counter() - t0) * 1000)
                last_error = str(e)
                if self._is_rate_limit_error(e):
                    delay = self._extract_retry_delay(str(e))
                    cd = delay if (delay and delay > 0) else 60.0
                    self.tracker.record_request("openai", active_key, status="RATE_LIMITED", latency_ms=latency_ms, model=self.openai_model, error=str(e), cooldown_seconds=cd)
                else:
                    self.tracker.record_request("openai", active_key, status="ERROR", latency_ms=latency_ms, model=self.openai_model, error=str(e))
                logger.warning("OpenAI call failed: %s", str(e))
            finally:
                self.tracker.clear_active_call("openai", active_key)

        return None, {
            "error": str(last_error),
            "provider": "openai",
            "failover": failover_triggered,
        }
