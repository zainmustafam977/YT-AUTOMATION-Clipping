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
from fastapi import APIRouter, Depends, HTTPException, Request, Form, status, File, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from shorts_engine.config.settings import settings
from shorts_engine.config.logging_config import reload_runtime_settings, save_runtime_settings
from shorts_engine.dashboard.auth import require_operator, require_viewer, verify_csrf, get_csrf_token
from shorts_engine.db.models import User
from shorts_engine.db.security import mask_secret
from shorts_engine.services.llm.quota_tracker import hash_api_key, usage_tracker, mask_api_key

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
                "masked": mask_api_key(k),
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

    from shorts_engine.services.storage.reset_service import ResetService
    from shorts_engine.db.session import SessionLocal

    with SessionLocal() as db:
        storage_inventory = ResetService.get_storage_inventory(db)
        config_paths = ResetService.get_config_paths_inventory()

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
            "storage_inventory": storage_inventory,
            "config_paths": config_paths,
            "ingestion_policy_mode": getattr(settings, "INGESTION_POLICY_MODE", "two_tier"),
            "ingestion_proxy_strict_mode": getattr(settings, "INGESTION_PROXY_STRICT_MODE", False),
            "ingestion_proxy_pool_text": "\n".join(settings.get_proxy_pool()),
            "ingestion_enable_oauth": getattr(settings, "INGESTION_ENABLE_OAUTH", True),
            "hotfolder_enabled": getattr(settings, "HOTFOLDER_ENABLED", True),
            "hotfolder_dir": str(getattr(settings, "HOTFOLDER_DIR", "")),
            "allowed_policy_modes": [
                ("two_tier", "Two-Tier Safety Triage (Recommended)"),
                ("strict_gate", "Strict Pre-Ingestion Gate"),
                ("auto_skip", "Auto-Skip Restricted Content"),
            ],
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
    ingestion_policy_mode: Optional[str] = Form(default="two_tier"),
    ingestion_proxy_strict_mode: Optional[str] = Form(default=None),
    ingestion_proxy_pool: Optional[str] = Form(default=None),
    ingestion_enable_oauth: Optional[str] = Form(default="on"),
    hotfolder_enabled: Optional[str] = Form(default="on"),
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
        "INGESTION_POLICY_MODE": (ingestion_policy_mode or "two_tier").strip().lower(),
        "INGESTION_PROXY_STRICT_MODE": ingestion_proxy_strict_mode is not None and ingestion_proxy_strict_mode.lower() in ("on", "true", "1", "yes"),
        "INGESTION_ENABLE_OAUTH": ingestion_enable_oauth is not None and ingestion_enable_oauth.lower() in ("on", "true", "1", "yes"),
        "HOTFOLDER_ENABLED": hotfolder_enabled is not None and hotfolder_enabled.lower() in ("on", "true", "1", "yes"),
    }

    if ingestion_proxy_pool is not None:
        new_proxies = [line.strip() for line in ingestion_proxy_pool.replace("\r", "\n").split("\n") if line.strip()]
        updates["INGESTION_PROXY_POOL"] = new_proxies

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

        # Synchronize DB ApiKey vault
        try:
            from shorts_engine.db.session import SessionLocal
            from shorts_engine.db.models import ApiKey
            from shorts_engine.db.security import decrypt_secret
            with SessionLocal() as db:
                for k_rec in db.query(ApiKey).filter_by(provider=prov).all():
                    try:
                        raw_val = decrypt_secret(k_rec.api_key_encrypted)
                        if raw_val == removed or hash_api_key(raw_val)[:16] == key_hash or hash_api_key(raw_val) == key_hash:
                            db.delete(k_rec)
                    except Exception:
                        pass
                db.commit()
        except Exception as e:
            logger.warning("Could not synchronize ApiKey DB deletion: %s", e)

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
            msg = "Key could not be added (already configured or invalid)"
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


@router.post("/settings/keys/purge-invalid", summary="Test all keys and purge invalid or expired keys")
@router.post("/api/settings/keys/purge-invalid", summary="API: Test all keys and purge invalid or expired keys")
def purge_invalid_keys(
    request: Request,
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
    """
    Pings every key configured across all providers.
    Automatically prunes any key that returns HTTP 400, 401, or 403 (Invalid/Unauthorized).
    Synchronizes runtime_settings.json, settings singleton, and the DB ApiKey vault.
    """
    from shorts_engine.db.session import SessionLocal
    from shorts_engine.db.models import ApiKey
    from shorts_engine.db.security import decrypt_secret

    purged_keys = []
    healthy_keys = []
    providers = ["groq", "gemini", "openrouter", "openai"]

    for prov in providers:
        getter = getattr(settings, f"get_{prov}_keys", None)
        if not getter:
            continue
        keys = list(getter())
        for raw_k in keys:
            h = hash_api_key(raw_k)[:16]
            status_code = None
            try:
                if prov == "groq":
                    resp = httpx.get("https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {raw_k}"}, timeout=6.0)
                    status_code = resp.status_code
                elif prov == "gemini":
                    resp = httpx.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={raw_k}", timeout=6.0)
                    status_code = resp.status_code
                elif prov == "openrouter":
                    resp = httpx.get("https://openrouter.ai/api/v1/auth/key", headers={"Authorization": f"Bearer {raw_k}"}, timeout=6.0)
                    status_code = resp.status_code
                elif prov == "openai":
                    resp = httpx.get("https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {raw_k}"}, timeout=6.0)
                    status_code = resp.status_code
            except Exception:
                # On transient connection timeouts, do NOT aggressively purge
                status_code = None

            if status_code in (400, 401, 403):
                # Definite invalid/unauthorized key -> purge
                settings.remove_api_key(prov, h)
                usage_tracker.remove_key(prov, h)
                purged_keys.append({"provider": prov, "hash": h, "masked": mask_api_key(raw_k), "status_code": status_code})
            else:
                healthy_keys.append({"provider": prov, "hash": h, "masked": mask_api_key(raw_k)})

        # Update runtime settings for this provider
        remaining = getattr(settings, f"get_{prov}_keys")()
        save_runtime_settings({
            f"{prov.upper()}_API_KEYS": remaining,
            f"{prov.upper()}_API_KEY": remaining[0] if remaining else None,
        })

    # Also clean DB ApiKey records for any purged keys
    if purged_keys:
        purged_hashes = {p["hash"] for p in purged_keys}
        try:
            with SessionLocal() as db:
                for k_rec in db.query(ApiKey).all():
                    try:
                        raw_val = decrypt_secret(k_rec.api_key_encrypted)
                        if hash_api_key(raw_val)[:16] in purged_hashes or hash_api_key(raw_val) in purged_hashes:
                            db.delete(k_rec)
                    except Exception:
                        pass
                db.commit()
        except Exception as e:
            logger.warning("Could not synchronize ApiKey DB purge: %s", e)

    msg = f"Purged {len(purged_keys)} invalid key(s). {len(healthy_keys)} healthy key(s) active."
    logger.info("Operator '%s' purged %d invalid keys: %s", current_user.username, len(purged_keys), purged_keys)

    is_browser = request.headers.get("sec-fetch-dest") == "document" or (
        "text/html" in request.headers.get("accept", "") and "application/json" not in request.headers.get("accept", "")
    )
    if is_browser:
        return RedirectResponse(
            url=f"/settings?msg={urllib.parse.quote_plus(msg)}&type={'success' if purged_keys else 'info'}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    return {
        "status": "success",
        "purged_count": len(purged_keys),
        "purged_keys": purged_keys,
        "healthy_count": len(healthy_keys),
        "healthy_keys": healthy_keys,
        "message": msg,
    }


@router.get("/api/settings/export", summary="Export runtime settings as JSON")
def export_settings_json(
    current_user: User = Depends(require_viewer),
):
    reload_runtime_settings()
    from shorts_engine.config.dynamic_store import DynamicConfigStore
    from shorts_engine.db.session import SessionLocal
    with SessionLocal() as db:
        store = DynamicConfigStore.get_instance()
        store.load_from_db(db)

    return {
        "LLM_PROVIDER": getattr(settings, "LLM_PROVIDER", "auto"),
        "LLM_MODEL_NAME": getattr(settings, "LLM_MODEL_NAME", "gemini-3.8-flash"),
        "GROQ_MODEL": getattr(settings, "GROQ_MODEL_NAME", "openai/gpt-oss-120b"),
        "TARGET_DURATION_MIN_SEC": getattr(settings, "TARGET_DURATION_MIN_SEC", 15),
        "TARGET_DURATION_MAX_SEC": getattr(settings, "TARGET_DURATION_MAX_SEC", 58),
        "MAX_CLIPS_PER_VIDEO": getattr(settings, "MAX_SEGMENTS_PER_VIDEO", 5),
        "MIN_CLIPS_PER_VIDEO": getattr(settings, "MIN_SEGMENTS_PER_VIDEO", 3),
        "VIDEO_CRF": getattr(settings, "VIDEO_ENCODING_CRF", 18),
        "VIDEO_PRESET": getattr(settings, "VIDEO_ENCODING_PRESET", "medium"),
        "VIDEO_SCALE_ALGORITHM": getattr(settings, "VIDEO_SCALE_ALGORITHM", "lanczos"),
        "TARGET_FPS": getattr(settings, "VIDEO_TARGET_FPS", 30),
        "AUDIO_BITRATE": getattr(settings, "AUDIO_BITRATE", "320k"),
        "AUDIO_SAMPLE_RATE": getattr(settings, "AUDIO_SAMPLE_RATE", 48000),
        "DRY_RUN_PUBLISHING": getattr(settings, "DRY_RUN_PUBLISHING", True),
        "DRIP_FEED_ENABLED": getattr(settings, "DRIP_FEED_ENABLED", True),
        "DRIP_FEED_POLL_INTERVAL_SECONDS": getattr(settings, "DRIP_FEED_POLL_INTERVAL_SECONDS", 60),
        "MAX_RETRY_COUNT": getattr(settings, "MAX_RETRY_COUNT", 3),
        "WHISPER_MODEL_SIZE": getattr(settings, "WHISPER_MODEL_SIZE", "base"),
        "WHISPER_BEAM_SIZE": getattr(settings, "WHISPER_BEAM_SIZE", 5),
        "SILERO_VAD_FILTER": getattr(settings, "WHISPER_VAD_FILTER", True),
        "WHISPER_LANGUAGE": getattr(settings, "WHISPER_LANGUAGE", "en"),
        "INGESTION_POLICY_MODE": getattr(settings, "INGESTION_POLICY_MODE", "two_tier"),
        "INGESTION_PROXY_STRICT_MODE": getattr(settings, "INGESTION_PROXY_STRICT_MODE", False),
        "INGESTION_PROXY_POOL": getattr(settings, "INGESTION_PROXY_POOL", []),
        "INGESTION_ENABLE_OAUTH": getattr(settings, "INGESTION_ENABLE_OAUTH", True),
        "HOTFOLDER_ENABLED": getattr(settings, "HOTFOLDER_ENABLED", True),
    }


@router.post("/api/settings/save", summary="Save runtime settings via JSON")
async def save_settings_json(
    request: Request,
    current_user: User = Depends(require_operator),
):
    body = await request.json()
    from shorts_engine.config.dynamic_store import DynamicConfigStore
    from shorts_engine.db.session import SessionLocal

    save_runtime_settings(body)
    with SessionLocal() as db:
        store = DynamicConfigStore.get_instance()
        for k, v in body.items():
            store.set(k, v, db)

    return {"status": "success", "message": "Settings saved and applied dynamically"}


@router.post("/api/settings/import", summary="Restore configuration from JSON backup")
async def import_settings_json(
    request: Request,
    current_user: User = Depends(require_operator),
):
    body = await request.json()
    from shorts_engine.config.dynamic_store import DynamicConfigStore
    from shorts_engine.db.session import SessionLocal

    settings_data = body.get("settings", body)
    if not isinstance(settings_data, dict):
        raise HTTPException(status_code=400, detail="Invalid settings JSON format")

    save_runtime_settings(settings_data)
    with SessionLocal() as db:
        store = DynamicConfigStore.get_instance()
        for k, v in settings_data.items():
            if isinstance(v, (str, int, float, bool)):
                store.set(k, v, db)

    return {"status": "success", "message": "Configuration successfully restored from backup"}


@router.get("/api/settings/storage-inventory", summary="Get storage and database record inventory")
def get_storage_inventory_api(
    current_user: User = Depends(require_viewer),
):
    from shorts_engine.db.session import SessionLocal
    from shorts_engine.services.storage.reset_service import ResetService
    with SessionLocal() as db:
        return ResetService.get_storage_inventory(db)


@router.get("/api/settings/config-paths", summary="Get configuration files inventory")
def get_config_paths_api(
    current_user: User = Depends(require_viewer),
):
    from shorts_engine.services.storage.reset_service import ResetService
    return ResetService.get_config_paths_inventory()


@router.post("/api/settings/reset", summary="Granular data, storage and settings reset via JSON")
async def api_reset_data(
    request: Request,
    current_user: User = Depends(require_operator),
):
    body = await request.json()
    from shorts_engine.services.storage.reset_service import ResetService, ResetOptions
    from shorts_engine.db.session import SessionLocal

    options = ResetOptions(
        delete_raw_downloads=bool(body.get("delete_raw_downloads", False)),
        delete_rendered_outputs=bool(body.get("delete_rendered_outputs", False)),
        delete_temp_files=bool(body.get("delete_temp_files", False)),
        delete_publishing_logs=bool(body.get("delete_publishing_logs", False)),
        delete_clips_and_candidates=bool(body.get("delete_clips_and_candidates", False)),
        delete_pipeline_history=bool(body.get("delete_pipeline_history", False)),
        delete_channels=bool(body.get("delete_channels", False)),
        reset_settings=bool(body.get("reset_settings", False)),
        delete_api_keys=bool(body.get("delete_api_keys", False)),
        delete_users=bool(body.get("delete_users", False)),
        preserve_admin=bool(body.get("preserve_admin", True)),
    )

    is_total_wipe = bool(body.get("is_total_wipe", False))
    confirm_code = str(body.get("confirm_code", "")).strip().upper()
    if is_total_wipe and confirm_code not in ("RESET", "DELETE", "CONFIRM"):
        raise HTTPException(
            status_code=400,
            detail="Safety confirmation code 'RESET' or 'DELETE' required for total reset.",
        )

    with SessionLocal() as db:
        result = ResetService.execute_granular_reset(options, db, current_user)
    return result


@router.post("/settings/reset", summary="Execute granular reset from HTML form")
def form_reset_data(
    request: Request,
    confirm_code: Optional[str] = Form(default=""),
    delete_raw_downloads: Optional[str] = Form(default=None),
    delete_rendered_outputs: Optional[str] = Form(default=None),
    delete_temp_files: Optional[str] = Form(default=None),
    delete_publishing_logs: Optional[str] = Form(default=None),
    delete_clips_and_candidates: Optional[str] = Form(default=None),
    delete_pipeline_history: Optional[str] = Form(default=None),
    delete_channels: Optional[str] = Form(default=None),
    reset_settings: Optional[str] = Form(default=None),
    delete_api_keys: Optional[str] = Form(default=None),
    delete_users: Optional[str] = Form(default=None),
    preserve_admin: Optional[str] = Form(default="on"),
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
    from shorts_engine.services.storage.reset_service import ResetService, ResetOptions
    from shorts_engine.db.session import SessionLocal

    options = ResetOptions(
        delete_raw_downloads=bool(delete_raw_downloads),
        delete_rendered_outputs=bool(delete_rendered_outputs),
        delete_temp_files=bool(delete_temp_files),
        delete_publishing_logs=bool(delete_publishing_logs),
        delete_clips_and_candidates=bool(delete_clips_and_candidates),
        delete_pipeline_history=bool(delete_pipeline_history),
        delete_channels=bool(delete_channels),
        reset_settings=bool(reset_settings),
        delete_api_keys=bool(delete_api_keys),
        delete_users=bool(delete_users),
        preserve_admin=bool(preserve_admin),
    )

    is_total_wipe = (
        options.delete_raw_downloads
        and options.delete_rendered_outputs
        and options.delete_temp_files
        and options.delete_publishing_logs
        and options.delete_clips_and_candidates
        and options.delete_pipeline_history
        and options.delete_channels
        and options.reset_settings
        and options.delete_api_keys
    )

    clean_code = (confirm_code or "").strip().upper()
    if is_total_wipe and clean_code not in ("RESET", "DELETE", "CONFIRM"):
        encoded_err = urllib.parse.quote("Safety confirmation code 'RESET' or 'DELETE' required for total system wipe.")
        return RedirectResponse(
            url=f"/settings?msg={encoded_err}&type=danger",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    with SessionLocal() as db:
        res = ResetService.execute_granular_reset(options, db, current_user)

    msg = f"Reset complete! Cleaned {res.get('total_files_deleted', 0)} files ({res.get('total_reclaimed_mb', 0)} MB) and {res.get('total_db_records_deleted', 0)} DB records."
    encoded_msg = urllib.parse.quote(msg)
    return RedirectResponse(
        url=f"/settings?msg={encoded_msg}&type=success",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# =========================================================================
# API Keys & Full System Snapshot Backup & Restore Endpoints
# =========================================================================

@router.get("/api/settings/keys/export", summary="Export API key vault (plaintext or encrypted)")
def export_api_keys_route(
    mode: str = "plaintext",
    passphrase: Optional[str] = None,
    current_user: User = Depends(require_viewer),
):
    from shorts_engine.services.storage.backup_service import BackupService
    from shorts_engine.db.session import SessionLocal

    try:
        with SessionLocal() as db:
            result = BackupService.export_api_keys(mode=mode, passphrase=passphrase, db=db)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    filename = "api_keys_encrypted_backup.json" if mode.lower() == "encrypted" else "api_keys_backup.json"
    content = json.dumps(result, indent=2)
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/api/settings/keys/import", summary="Restore API keys from backup archive")
async def import_api_keys_api(
    request: Request,
    current_user: User = Depends(require_operator),
):
    from shorts_engine.services.storage.backup_service import BackupService
    from shorts_engine.db.session import SessionLocal

    try:
        body = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")

    payload = body.get("payload", body)
    passphrase = body.get("passphrase")

    try:
        with SessionLocal() as db:
            res = BackupService.import_api_keys(payload, passphrase=passphrase, db=db)
        return res
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Unhandled error restoring API keys: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to restore API keys: {e}")


@router.post("/settings/keys/import", summary="Restore API keys via HTML form upload")
async def import_api_keys_form(
    request: Request,
    backup_file: Optional[UploadFile] = File(None),
    backup_json: Optional[str] = Form(None),
    passphrase: Optional[str] = Form(None),
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
    from shorts_engine.services.storage.backup_service import BackupService
    from shorts_engine.db.session import SessionLocal

    payload = None
    if backup_file and backup_file.filename:
        try:
            content = await backup_file.read()
            payload = json.loads(content.decode("utf-8"))
        except Exception as e:
            err = urllib.parse.quote(f"Could not parse uploaded file: {e}")
            return RedirectResponse(url=f"/settings?msg={err}&type=danger", status_code=status.HTTP_303_SEE_OTHER)
    elif backup_json and backup_json.strip():
        try:
            payload = json.loads(backup_json.strip())
        except Exception as e:
            err = urllib.parse.quote(f"Invalid JSON string: {e}")
            return RedirectResponse(url=f"/settings?msg={err}&type=danger", status_code=status.HTTP_303_SEE_OTHER)

    if not payload:
        err = urllib.parse.quote("No API keys backup file or JSON provided.")
        return RedirectResponse(url=f"/settings?msg={err}&type=danger", status_code=status.HTTP_303_SEE_OTHER)

    try:
        with SessionLocal() as db:
            res = BackupService.import_api_keys(payload, passphrase=passphrase, db=db)
        msg = urllib.parse.quote(
            f"API keys restored successfully! Added: {res['imported']}, Skipped: {res['skipped']}, Providers: {', '.join(res['updated_providers']) or 'None'}."
        )
        return RedirectResponse(url=f"/settings?msg={msg}&type=success", status_code=status.HTTP_303_SEE_OTHER)
    except ValueError as e:
        err = urllib.parse.quote(str(e))
        return RedirectResponse(url=f"/settings?msg={err}&type=danger", status_code=status.HTTP_303_SEE_OTHER)
    except Exception as e:
        err = urllib.parse.quote(f"Key restoration failed: {e}")
        return RedirectResponse(url=f"/settings?msg={err}&type=danger", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/api/settings/snapshot/export", summary="Export full system snapshot archive")
def export_full_snapshot_route(
    mode: str = "plaintext",
    passphrase: Optional[str] = None,
    current_user: User = Depends(require_viewer),
):
    from shorts_engine.services.storage.backup_service import BackupService
    from shorts_engine.db.session import SessionLocal

    try:
        with SessionLocal() as db:
            result = BackupService.export_full_system_snapshot(db, mode=mode, passphrase=passphrase)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    filename = "system_snapshot_encrypted.json" if mode.lower() == "encrypted" else "system_snapshot.json"
    content = json.dumps(result, indent=2)
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/api/settings/snapshot/import", summary="Restore full system snapshot from JSON")
async def import_full_snapshot_api(
    request: Request,
    current_user: User = Depends(require_operator),
):
    from shorts_engine.services.storage.backup_service import BackupService
    from shorts_engine.db.session import SessionLocal

    try:
        body = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")

    payload = body.get("payload", body)
    passphrase = body.get("passphrase")

    try:
        with SessionLocal() as db:
            res = BackupService.import_full_system_snapshot(payload, db=db, passphrase=passphrase)
        return res
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Unhandled error restoring full system snapshot: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to restore system snapshot: {e}")


@router.post("/settings/snapshot/import", summary="Restore full system snapshot via HTML form")
async def import_full_snapshot_form(
    request: Request,
    backup_file: Optional[UploadFile] = File(None),
    backup_json: Optional[str] = Form(None),
    passphrase: Optional[str] = Form(None),
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
    from shorts_engine.services.storage.backup_service import BackupService
    from shorts_engine.db.session import SessionLocal

    payload = None
    if backup_file and backup_file.filename:
        try:
            content = await backup_file.read()
            payload = json.loads(content.decode("utf-8"))
        except Exception as e:
            err = urllib.parse.quote(f"Could not parse uploaded file: {e}")
            return RedirectResponse(url=f"/settings?msg={err}&type=danger", status_code=status.HTTP_303_SEE_OTHER)
    elif backup_json and backup_json.strip():
        try:
            payload = json.loads(backup_json.strip())
        except Exception as e:
            err = urllib.parse.quote(f"Invalid JSON string: {e}")
            return RedirectResponse(url=f"/settings?msg={err}&type=danger", status_code=status.HTTP_303_SEE_OTHER)

    if not payload:
        err = urllib.parse.quote("No system snapshot file or JSON provided.")
        return RedirectResponse(url=f"/settings?msg={err}&type=danger", status_code=status.HTTP_303_SEE_OTHER)

    try:
        with SessionLocal() as db:
            res = BackupService.import_full_system_snapshot(payload, db=db, passphrase=passphrase)
        ch_res = res.get("channels") or {}
        k_res = res.get("keys") or {}
        msg = urllib.parse.quote(
            f"Full system snapshot restored successfully! Settings: updated, Channels: {ch_res.get('imported', 0)} added / {ch_res.get('updated', 0)} updated, Keys: {k_res.get('imported', 0)} added."
        )
        return RedirectResponse(url=f"/settings?msg={msg}&type=success", status_code=status.HTTP_303_SEE_OTHER)
    except ValueError as e:
        err = urllib.parse.quote(str(e))
        return RedirectResponse(url=f"/settings?msg={err}&type=danger", status_code=status.HTTP_303_SEE_OTHER)
    except Exception as e:
        err = urllib.parse.quote(f"System snapshot restoration failed: {e}")
        return RedirectResponse(url=f"/settings?msg={err}&type=danger", status_code=status.HTTP_303_SEE_OTHER)




