"""
Dashboard Settings Route: view and update safe runtime configuration parameters
without requiring .env edits or process/container restarts.
"""

import logging
import time
import urllib.parse
from pathlib import Path
import json
from typing import Optional
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Form, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from shorts_engine.config.settings import settings
from shorts_engine.config.logging_config import reload_runtime_settings, save_runtime_settings
from shorts_engine.dashboard.auth import require_operator, require_viewer, verify_csrf, get_csrf_token
from shorts_engine.db.models import User
from shorts_engine.db.security import mask_secret
from shorts_engine.services.llm.quota_tracker import hash_api_key, usage_tracker

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))

ALLOWED_MODELS = [
    "gemini-3.8-flash",
    "gemini-2.5-flash",
    "gemini-3.6-flash",
    "gemini-flash-latest",
]
ALLOWED_PROVIDERS = [
    "auto",
    "groq",
    "gemini",
    "openrouter",
    "openai",
]
ALLOWED_GROQ_MODELS = [
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "qwen/qwen3.8-27b",
    "groq/compound-mini",
]
ALLOWED_OPENROUTER_MODELS = [
    "meta-llama/llama-3.3-70b-instruct:free",
    "google/gemini-2.0-flash-exp:free",
    "qwen/qwen-2.5-72b-instruct:free",
    "mistralai/mistral-7b-instruct:free",
]
ALLOWED_OPENAI_MODELS = [
    "gpt-4o-mini",
    "gpt-4o",
]
ALLOWED_WHISPER_MODELS = [
    "tiny",
    "base",
    "small",
    "medium",
    "large-v3",
]
ALLOWED_PRIVACY_STATUSES = [
    "unlisted",
    "private",
    "public",
]
ALLOWED_SCALE_ALGORITHMS = [
    "lanczos",
    "bicubic",
    "bilinear",
    "neighbor",
]
ALLOWED_ENCODING_PRESETS = [
    "veryslow",
    "slow",
    "medium",
    "fast",
    "veryfast",
]
ALLOWED_CAPTION_PRESETS = [
    ("hormozi", "Alex Hormozi Viral"),
    ("mrbeast", "MrBeast Impact"),
    ("neon", "Cyberpunk Neon"),
    ("minimalist", "Minimalist Editorial"),
    ("high_contrast_box", "High Contrast Box"),
]


@router.get("/settings", response_class=HTMLResponse, summary="View runtime configuration")
def view_settings(
    request: Request,
    current_user: User = Depends(require_viewer),
):
    reload_runtime_settings()
    groq_keys = settings.get_groq_keys()
    gemini_keys = settings.get_gemini_keys()
    openrouter_keys = settings.get_openrouter_keys()
    openai_keys = settings.get_openai_keys()

    all_metrics = {m["key_hash"][:16]: m for m in usage_tracker.get_per_key_metrics()}

    def _build_key_items(keys_list: list):
        items = []
        for idx, k in enumerate(keys_list, 1):
            h = hash_api_key(k)[:16]
            metric = all_metrics.get(h)
            items.append({
                "index": idx,
                "hash": h,
                "masked": mask_secret(k),
                "metric": metric,
                "calls": metric.get("total_requests", 0) if metric else 0,
                "status": metric.get("status", "READY") if metric else "READY",
                "is_cooling": metric.get("is_cooling_down", False) if metric else False,
            })
        return items

    groq_key_items = _build_key_items(groq_keys)
    gemini_key_items = _build_key_items(gemini_keys)
    openrouter_key_items = _build_key_items(openrouter_keys)
    openai_key_items = _build_key_items(openai_keys)

    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "settings": settings,
            "current_user": current_user,
            "csrf_token": get_csrf_token(request),
            "allowed_models": ALLOWED_MODELS,
            "allowed_providers": ALLOWED_PROVIDERS,
            "allowed_groq_models": ALLOWED_GROQ_MODELS,
            "allowed_openrouter_models": ALLOWED_OPENROUTER_MODELS,
            "allowed_openai_models": ALLOWED_OPENAI_MODELS,
            "allowed_whisper_models": ALLOWED_WHISPER_MODELS,
            "allowed_privacy_statuses": ALLOWED_PRIVACY_STATUSES,
            "allowed_scale_algorithms": ALLOWED_SCALE_ALGORITHMS,
            "allowed_encoding_presets": ALLOWED_ENCODING_PRESETS,
            "allowed_caption_presets": ALLOWED_CAPTION_PRESETS,
            "groq_keys": groq_keys,
            "gemini_keys": gemini_keys,
            "openrouter_keys": openrouter_keys,
            "openai_keys": openai_keys,
            "groq_key_items": groq_key_items,
            "gemini_key_items": gemini_key_items,
            "openrouter_key_items": openrouter_key_items,
            "openai_key_items": openai_key_items,
            "masked_groq_keys": [mask_secret(k) for k in groq_keys],
            "masked_gemini_keys": [mask_secret(k) for k in gemini_keys],
            "masked_openrouter_keys": [mask_secret(k) for k in openrouter_keys],
            "masked_openai_keys": [mask_secret(k) for k in openai_keys],
            "masked_groq_key": mask_secret(groq_keys[0]) if groq_keys else "",
            "masked_openrouter_key": mask_secret(openrouter_keys[0]) if openrouter_keys else "",
            "masked_openai_key": mask_secret(openai_keys[0]) if openai_keys else "",
            "masked_gemini_key": mask_secret(gemini_keys[0]) if gemini_keys else "",
            "msg": request.query_params.get("msg"),
            "msg_type": request.query_params.get("type", "success"),
        },
    )


@router.post("/settings", summary="Update runtime configuration")
def update_settings(
    request: Request,
    target_duration_min_sec: int = Form(...),
    target_duration_max_sec: int = Form(...),
    min_segments_per_video: int = Form(...),
    max_segments_per_video: int = Form(...),
    caption_margin_vertical: int = Form(...),
    enable_sfx: Optional[str] = Form(default=None),
    max_retry_count: int = Form(...),
    quota_warning_threshold: float = Form(...),
    llm_model_name: str = Form(...),
    gemini_pacing_sec: Optional[float] = Form(default=3.0),
    llm_provider: Optional[str] = Form(default="auto"),
    groq_api_key: Optional[str] = Form(default=None),
    groq_api_keys: Optional[str] = Form(default=None),
    groq_model_name: Optional[str] = Form(default="openai/gpt-oss-120b"),
    gemini_api_key: Optional[str] = Form(default=None),
    gemini_api_keys: Optional[str] = Form(default=None),
    openrouter_api_key: Optional[str] = Form(default=None),
    openrouter_api_keys: Optional[str] = Form(default=None),
    openrouter_model_name: Optional[str] = Form(default="liquid/lfm-2.5-2.6b:free"),
    openai_api_key: Optional[str] = Form(default=None),
    openai_api_keys: Optional[str] = Form(default=None),
    openai_model_name: Optional[str] = Form(default="gpt-4o-mini"),
    whisper_model_size: Optional[str] = Form(default="base"),
    face_tracking_confidence: Optional[float] = Form(default=0.6),
    llm_temperature: Optional[float] = Form(default=0.2),
    default_privacy_status: Optional[str] = Form(default="unlisted"),
    llm_fallback_on_rate_limit: Optional[str] = Form(default="on"),
    use_groq_whisper: Optional[str] = Form(default=None),
    video_scale_algorithm: Optional[str] = Form(default="lanczos"),
    video_encoding_crf: Optional[int] = Form(default=18),
    video_encoding_preset: Optional[str] = Form(default="slow"),
    video_target_fps: Optional[int] = Form(default=30),
    audio_bitrate: Optional[str] = Form(default="320k"),
    audio_sample_rate: Optional[int] = Form(default=48000),
    default_caption_preset: Optional[str] = Form(default="hormozi"),
    whisper_beam_size: Optional[int] = Form(default=5),
    whisper_vad_filter: Optional[str] = Form(default="on"),
    whisper_language: Optional[str] = Form(default=None),
    whisper_temperature: Optional[float] = Form(default=0.0),
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
    is_browser = request.headers.get("sec-fetch-dest") == "document" or (
        "text/html" in request.headers.get("accept", "") and "application/json" not in request.headers.get("accept", "")
    )

    err = None
    if target_duration_min_sec < 5 or target_duration_min_sec > target_duration_max_sec:
        err = "TARGET_DURATION_MIN_SEC must be >= 5 and <= TARGET_DURATION_MAX_SEC"
    elif target_duration_max_sec > 180:
        err = "TARGET_DURATION_MAX_SEC cannot exceed 180 seconds"
    elif min_segments_per_video < 1 or min_segments_per_video > max_segments_per_video:
        err = "MIN_SEGMENTS_PER_VIDEO must be >= 1 and <= MAX_SEGMENTS_PER_VIDEO"
    elif max_segments_per_video > 20:
        err = "MAX_SEGMENTS_PER_VIDEO cannot exceed 20"
    elif caption_margin_vertical < 200 or caption_margin_vertical > 600:
        err = "CAPTION_MARGIN_VERTICAL must be between 200 and 600 px"
    elif max_retry_count < 1 or max_retry_count > 10:
        err = "MAX_RETRY_COUNT must be between 1 and 10"
    elif quota_warning_threshold <= 0.0 or quota_warning_threshold > 1.0:
        err = "QUOTA_WARNING_THRESHOLD must be between 0.1 and 1.0"
    elif float(gemini_pacing_sec if gemini_pacing_sec is not None else 3.0) < 1.0 or float(gemini_pacing_sec if gemini_pacing_sec is not None else 3.0) > 10.0:
        err = "GEMINI_PACING_SEC must be between 1.0 and 10.0 seconds"
    elif llm_model_name.strip() not in ALLOWED_MODELS:
        err = f"LLM_MODEL_NAME must be one of: {ALLOWED_MODELS}"
    elif (llm_provider or "auto").strip().lower() not in ALLOWED_PROVIDERS:
        err = f"LLM_PROVIDER must be one of: {ALLOWED_PROVIDERS}"
    elif (openai_model_name or "gpt-4o-mini").strip() not in ALLOWED_OPENAI_MODELS:
        err = f"OPENAI_MODEL_NAME must be one of: {ALLOWED_OPENAI_MODELS}"
    elif (whisper_model_size or "base").strip().lower() not in ALLOWED_WHISPER_MODELS:
        err = f"WHISPER_MODEL_SIZE must be one of: {ALLOWED_WHISPER_MODELS}"
    elif (default_privacy_status or "unlisted").strip().lower() not in ALLOWED_PRIVACY_STATUSES:
        err = f"DEFAULT_PRIVACY_STATUS must be one of: {ALLOWED_PRIVACY_STATUSES}"
    elif float(llm_temperature if llm_temperature is not None else 0.2) < 0.0 or float(llm_temperature if llm_temperature is not None else 0.2) > 1.0:
        err = "LLM_TEMPERATURE must be between 0.0 and 1.0"
    elif float(face_tracking_confidence if face_tracking_confidence is not None else 0.6) < 0.1 or float(face_tracking_confidence if face_tracking_confidence is not None else 0.6) > 1.0:
        err = "FACE_TRACKING_CONFIDENCE must be between 0.1 and 1.0"
    elif (video_scale_algorithm or "lanczos").strip().lower() not in ALLOWED_SCALE_ALGORITHMS:
        err = f"VIDEO_SCALE_ALGORITHM must be one of: {ALLOWED_SCALE_ALGORITHMS}"
    elif video_encoding_crf is not None and (int(video_encoding_crf) < 0 or int(video_encoding_crf) > 51):
        err = "VIDEO_ENCODING_CRF must be between 0 and 51"
    elif (video_encoding_preset or "slow").strip().lower() not in ALLOWED_ENCODING_PRESETS:
        err = f"VIDEO_ENCODING_PRESET must be one of: {ALLOWED_ENCODING_PRESETS}"
    elif video_target_fps is not None and (int(video_target_fps) < 15 or int(video_target_fps) > 60):
        err = "VIDEO_TARGET_FPS must be between 15 and 60"
    elif (default_caption_preset or "hormozi").strip().lower() not in [p[0] for p in ALLOWED_CAPTION_PRESETS]:
        err = f"DEFAULT_CAPTION_PRESET must be one of: {[p[0] for p in ALLOWED_CAPTION_PRESETS]}"

    if err:
        if request.headers.get("hx-request") == "true":
            return Response(
                content=f'<div class="alert alert-danger py-2 mb-3">❌ {err}</div>',
                headers={"HX-Trigger": json.dumps({"showToast": {"message": err, "type": "danger"}})},
                status_code=200,
            )
        if is_browser:
            return RedirectResponse(
                url=f"/settings?msg={urllib.parse.quote_plus(err)}&type=danger",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        raise HTTPException(status_code=422, detail=err)

    pacing_clean = round(float(gemini_pacing_sec if gemini_pacing_sec is not None else 3.0), 1)
    model_clean = llm_model_name.strip()
    provider_clean = (llm_provider or "auto").strip().lower()
    openai_model_clean = (openai_model_name or "gpt-4o-mini").strip()
    whisper_clean = (whisper_model_size or "base").strip().lower()
    privacy_clean = (default_privacy_status or "unlisted").strip().lower()
    temp_clean = float(llm_temperature if llm_temperature is not None else 0.2)
    face_clean = float(face_tracking_confidence if face_tracking_confidence is not None else 0.6)

    is_sfx_enabled = enable_sfx is not None and enable_sfx.lower() in ("on", "true", "1", "yes")
    is_fallback_enabled = llm_fallback_on_rate_limit is not None and llm_fallback_on_rate_limit.lower() in ("on", "true", "1", "yes")
    is_groq_whisper_enabled = use_groq_whisper is not None and use_groq_whisper.lower() in ("on", "true", "1", "yes")
    is_vad_enabled = whisper_vad_filter is not None and whisper_vad_filter.lower() in ("on", "true", "1", "yes")

    updates = {
        "TARGET_DURATION_MIN_SEC": int(target_duration_min_sec),
        "TARGET_DURATION_MAX_SEC": int(target_duration_max_sec),
        "MIN_SEGMENTS_PER_VIDEO": int(min_segments_per_video),
        "MAX_SEGMENTS_PER_VIDEO": int(max_segments_per_video),
        "CAPTION_MARGIN_VERTICAL": int(caption_margin_vertical),
        "ENABLE_SFX": is_sfx_enabled,
        "MAX_RETRY_COUNT": int(max_retry_count),
        "QUOTA_WARNING_THRESHOLD": float(quota_warning_threshold),
        "GEMINI_PACING_SEC": pacing_clean,
        "LLM_MODEL_NAME": model_clean,
        "LLM_PROVIDER": provider_clean,
        "GROQ_MODEL_NAME": (groq_model_name or "openai/gpt-oss-120b").strip(),
        "OPENROUTER_MODEL_NAME": (openrouter_model_name or "meta-llama/llama-3.3-70b-instruct:free").strip(),
        "OPENAI_MODEL_NAME": openai_model_clean,
        "WHISPER_MODEL_SIZE": whisper_clean,
        "USE_GROQ_WHISPER": is_groq_whisper_enabled,
        "FACE_TRACKING_CONFIDENCE": face_clean,
        "LLM_TEMPERATURE": temp_clean,
        "DEFAULT_PRIVACY_STATUS": privacy_clean,
        "LLM_FALLBACK_ON_RATE_LIMIT": is_fallback_enabled,
        "VIDEO_SCALE_ALGORITHM": (video_scale_algorithm or "lanczos").strip(),
        "VIDEO_ENCODING_CRF": int(video_encoding_crf if video_encoding_crf is not None else 18),
        "VIDEO_ENCODING_PRESET": (video_encoding_preset or "slow").strip(),
        "VIDEO_TARGET_FPS": int(video_target_fps if video_target_fps is not None else 30),
        "AUDIO_BITRATE": (audio_bitrate or "320k").strip(),
        "AUDIO_SAMPLE_RATE": int(audio_sample_rate if audio_sample_rate is not None else 48000),
        "DEFAULT_CAPTION_PRESET": (default_caption_preset or "hormozi").strip(),
        "WHISPER_BEAM_SIZE": int(whisper_beam_size if whisper_beam_size is not None else 5),
        "WHISPER_VAD_FILTER": is_vad_enabled,
        "WHISPER_LANGUAGE": whisper_language.strip() if whisper_language and whisper_language.strip() else None,
        "WHISPER_TEMPERATURE": float(whisper_temperature if whisper_temperature is not None else 0.0),
    }

    def _parse_keys_input(raw_text: Optional[str], fallback_single: Optional[str], current_keys: list) -> list:
        candidates = []
        if raw_text:
            for line in raw_text.replace("\r", "\n").replace(",", "\n").split("\n"):
                clean = line.strip()
                if clean and not clean.startswith("••••") and clean not in candidates:
                    candidates.append(clean)
        if fallback_single:
            clean = fallback_single.strip()
            if clean and not clean.startswith("••••") and clean not in candidates:
                candidates.append(clean)
        return candidates if candidates else current_keys

    parsed_groq = _parse_keys_input(groq_api_keys, groq_api_key, settings.get_groq_keys())
    if parsed_groq:
        updates["GROQ_API_KEYS"] = parsed_groq
        updates["GROQ_API_KEY"] = parsed_groq[0]

    parsed_gemini = _parse_keys_input(gemini_api_keys, gemini_api_key, settings.get_gemini_keys())
    if parsed_gemini:
        updates["GEMINI_API_KEYS"] = parsed_gemini
        updates["GEMINI_API_KEY"] = parsed_gemini[0]

    parsed_openrouter = _parse_keys_input(openrouter_api_keys, openrouter_api_key, settings.get_openrouter_keys())
    if parsed_openrouter:
        updates["OPENROUTER_API_KEYS"] = parsed_openrouter
        updates["OPENROUTER_API_KEY"] = parsed_openrouter[0]

    parsed_openai = _parse_keys_input(openai_api_keys, openai_api_key, settings.get_openai_keys())
    if parsed_openai:
        updates["OPENAI_API_KEYS"] = parsed_openai
        updates["OPENAI_API_KEY"] = parsed_openai[0]

    save_runtime_settings(updates)
    logger.info("Operator '%s' updated runtime settings: %s", current_user.username, updates)

    if request.headers.get("hx-request") == "true":
        return Response(
            content='<div class="alert alert-success py-2 mb-3">✅ Settings saved and applied dynamically!</div>',
            headers={"HX-Trigger": json.dumps({"showToast": {"message": "Settings saved and applied dynamically!", "type": "success"}})}
        )

    if request.headers.get("sec-fetch-dest") == "document" or ("text/html" in request.headers.get("accept", "") and "application/json" not in request.headers.get("accept", "")):
        return RedirectResponse(
            url="/settings?msg=Settings+saved+and+dynamically+applied+successfully&type=success",
            status_code=303,
        )

    return {"status": "success", "updated_settings": updates}


@router.post("/settings/keys/delete", summary="Remove an individual API key")
def delete_api_key(
    request: Request,
    provider: str = Form(...),
    key_hash: str = Form(...),
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
    prov = provider.strip().lower()
    if prov not in ALLOWED_PROVIDERS or prov == "auto":
        raise HTTPException(status_code=400, detail="Invalid provider")

    removed = settings.remove_api_key(prov, key_hash)
    if not removed:
        msg = f"Key not found for {prov}"
        msg_type = "warning"
    else:
        usage_tracker.remove_key(prov, key_hash)
        remaining_keys = getattr(settings, f"get_{prov}_keys")()
        updates = {
            f"{prov.upper()}_API_KEYS": remaining_keys,
            f"{prov.upper()}_API_KEY": remaining_keys[0] if remaining_keys else None,
        }
        save_runtime_settings(updates)
        logger.info("Operator '%s' removed %s API key [%s]", current_user.username, prov, key_hash)
        msg = f"{prov.title()} API key removed successfully"
        msg_type = "success"

    is_browser = request.headers.get("sec-fetch-dest") == "document" or (
        "text/html" in request.headers.get("accept", "") and "application/json" not in request.headers.get("accept", "")
    )
    if is_browser:
        return RedirectResponse(
            url=f"/settings?msg={urllib.parse.quote_plus(msg)}&type={msg_type}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return {"status": "success" if removed else "not_found", "message": msg}


@router.post("/settings/keys/add", summary="Add a new API key")
def add_api_key(
    request: Request,
    provider: str = Form(...),
    new_key: str = Form(...),
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
    prov = provider.strip().lower()
    if prov not in ALLOWED_PROVIDERS or prov == "auto":
        raise HTTPException(status_code=400, detail="Invalid provider")

    clean_key = new_key.strip()
    if not clean_key or clean_key.startswith("••••"):
        msg = "Invalid key string"
        msg_type = "danger"
        added = False
    else:
        added = settings.add_api_key(prov, clean_key)
        if not added:
            msg = f"Key could not be added (already configured or invalid)"
            msg_type = "warning"
        else:
            updated_keys = getattr(settings, f"get_{prov}_keys")()
            updates = {
                f"{prov.upper()}_API_KEYS": updated_keys,
                f"{prov.upper()}_API_KEY": updated_keys[0] if updated_keys else None,
            }
            save_runtime_settings(updates)
            logger.info("Operator '%s' added new %s API key", current_user.username, prov)
            msg = f"New {prov.title()} API key added and registered successfully"
            msg_type = "success"

    is_browser = request.headers.get("sec-fetch-dest") == "document" or (
        "text/html" in request.headers.get("accept", "") and "application/json" not in request.headers.get("accept", "")
    )
    if is_browser:
        return RedirectResponse(
            url=f"/settings?msg={urllib.parse.quote_plus(msg)}&type={msg_type}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return {"status": "success" if added else "error", "message": msg}


@router.post("/settings/keys/test", summary="Live test/ping an API key")
def test_api_key(
    request: Request,
    provider: str = Form(...),
    key_hash: str = Form(...),
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
    prov = provider.strip().lower()
    getter = getattr(settings, f"get_{prov}_keys", None)
    if not getter:
        return {"status": "error", "active": False, "message": f"Unsupported provider {prov}"}

    keys = getter()
    target_key = next((k for k in keys if hash_api_key(k)[:16] == key_hash or hash_api_key(k) == key_hash), None)
    if not target_key:
        return {"status": "error", "active": False, "message": f"Key not found in {prov} key pool"}

    t0 = time.time()
    try:
        if prov == "groq":
            resp = httpx.get(
                "https://api.groq.com/openai/v1/models",
                headers={"Authorization": f"Bearer {target_key}"},
                timeout=6.0,
            )
        elif prov == "gemini":
            resp = httpx.get(
                f"https://generativelanguage.googleapis.com/v1beta/models?key={target_key}",
                timeout=6.0,
            )
        elif prov == "openrouter":
            resp = httpx.get(
                "https://openrouter.ai/api/v1/auth/key",
                headers={"Authorization": f"Bearer {target_key}"},
                timeout=6.0,
            )
        elif prov == "openai":
            resp = httpx.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {target_key}"},
                timeout=6.0,
            )
        else:
            return {"status": "error", "active": False, "message": f"Unsupported provider {prov}"}

        latency_ms = int((time.time() - t0) * 1000)
        if resp.status_code == 200:
            return {
                "status": "success",
                "active": True,
                "latency_ms": latency_ms,
                "message": f"Key verified active ({latency_ms}ms)",
            }
        else:
            return {
                "status": "error",
                "active": False,
                "latency_ms": latency_ms,
                "message": f"HTTP {resp.status_code}: {resp.text[:80]}",
            }
    except Exception as e:
        latency_ms = int((time.time() - t0) * 1000)
        return {
            "status": "error",
            "active": False,
            "latency_ms": latency_ms,
            "message": f"Connection error: {str(e)[:80]}",
        }

