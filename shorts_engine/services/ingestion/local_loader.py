"""
Universal Local Video Ingestion Gateway Service.
Provides zero-copy local disk path ingestion and chunk-streamed multipart file uploads
with ffprobe/OpenCV metadata probing and EventBus notification integration.
"""

import os
import re
import json
import shutil
import logging
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any, Union
from sqlalchemy.orm import Session

from shorts_engine.config.settings import settings
from shorts_engine.db.models import (
    SourceVideo,
    Channel,
    SourceVideoStatus,
    generate_uuid,
)
from shorts_engine.services.events.bus import global_event_bus
from shorts_engine.services.events.models import PlatformEvent
from shorts_engine.services.storage.path_manager import get_preview_proxy_path

logger = logging.getLogger(__name__)

INCOMPATIBLE_PREVIEW_CODECS = {"hevc", "h265", "prores", "vp9", "av1"}
INCOMPATIBLE_PREVIEW_EXTENSIONS = {".mkv"}


def is_preview_proxy_needed(video_path: Union[str, Path], codec: Optional[str] = None) -> bool:
    """
    Checks if a video container or codec is incompatible with native HTML5 browser playback (Enhancement 4).
    Targets: HEVC, ProRes, VP9, AV1, or extension .mkv.
    """
    p = Path(video_path)
    if p.suffix.lower() in INCOMPATIBLE_PREVIEW_EXTENSIONS:
        return True
    if codec:
        return str(codec).lower().strip() in INCOMPATIBLE_PREVIEW_CODECS
    if p.exists() and p.is_file():
        try:
            meta = LocalVideoIngestionService.probe_video_metadata(p)
            c = str(meta.get("codec") or "").lower().strip()
            if c in INCOMPATIBLE_PREVIEW_CODECS:
                return True
        except Exception:
            pass
    return False


def generate_preview_proxy(
    source_path: Union[str, Path],
    output_proxy_path: Optional[Path] = None,
    force: bool = False,
    timeout_sec: float = 120.0,
) -> Optional[Path]:
    """
    Generates an ultra-fast, lightweight 720p H.264 preview proxy (720p H.264 ultrafast, 128k aac)
    for footage encoded in formats incompatible with browser HTML5 playback (Enhancement 4).
    """
    p = Path(source_path)
    if not p.exists() or p.stat().st_size == 0:
        return None

    proxy_target = output_proxy_path or get_preview_proxy_path(p)
    if not force and proxy_target.exists() and proxy_target.stat().st_size > 0:
        return proxy_target

    proxy_target.parent.mkdir(parents=True, exist_ok=True)
    temp_proxy = proxy_target.parent / f"{proxy_target.stem}_tmp.mp4"

    # 720p H.264 ultrafast, 128k aac
    vf = "scale='min(1280,iw)':'min(720,ih)':force_original_aspect_ratio=decrease,scale=trunc(iw/2)*2:trunc(ih/2)*2"
    cmd = [
        "ffmpeg", "-y",
        "-i", str(p),
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(temp_proxy),
    ]
    try:
        from shorts_engine.services.execution.watchdog import SubprocessWatchdog
        from shorts_engine.utils.fs import atomic_replace
        res = SubprocessWatchdog.run(cmd, timeout_sec=timeout_sec, check=False)
        if res.returncode == 0 and temp_proxy.exists() and temp_proxy.stat().st_size > 0:
            atomic_replace(temp_proxy, proxy_target)
            logger.info("Generated lightweight preview proxy for %s at %s", p.name, proxy_target)
            return proxy_target
        else:
            if temp_proxy.exists():
                temp_proxy.unlink(missing_ok=True)
            logger.warning("Preview proxy generation failed for %s: %s", p, getattr(res, "stderr", ""))
            return None
    except Exception as e:
        if temp_proxy.exists():
            temp_proxy.unlink(missing_ok=True)
        logger.warning("Failed to generate preview proxy for %s: %s", p, e)
        return None


class LocalVideoIngestionService:
    """
    High-performance local video ingestion engine:
    1. Zero-copy filesystem mapping (avoids multi-GB disk duplication).
    2. Stream-chunked HTTP uploads with atomic .part file assembly.
    3. Resilient media metadata probing (ffprobe + OpenCV fallback).
    4. Auto-binding to Local Media channels.
    """

    SUPPORTED_EXTENSIONS = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".flv"}

    @classmethod
    def validate_local_file(cls, file_path: Union[str, Path]) -> Path:
        """
        Validates that a target local file exists, is non-empty, readable,
        and has a supported video container extension.
        Returns the resolved canonical Windows/POSIX Path.
        """
        if not file_path:
            raise ValueError("File path cannot be empty")

        raw_str = str(file_path).strip().strip('"').strip("'")
        if not raw_str:
            raise ValueError("File path cannot be empty")

        p = Path(raw_str).resolve()

        if not p.exists():
            raise FileNotFoundError(f"Local file does not exist: {p}")

        if not p.is_file():
            raise ValueError(f"Path is not a regular file: {p}")

        ext = p.suffix.lower()
        if ext not in cls.SUPPORTED_EXTENSIONS:
            raise ValueError(
                f"Unsupported video extension '{ext}'. Supported formats: {sorted(cls.SUPPORTED_EXTENSIONS)}"
            )

        try:
            size_b = p.stat().st_size
            if size_b <= 0:
                raise ValueError(f"Target video file is empty (0 bytes): {p}")
            max_bytes = getattr(settings, "MAX_VIDEO_FILESIZE_BYTES", int(settings.MAX_INGESTION_FILE_SIZE_MB * 1024 * 1024))
            if size_b > max_bytes:
                from shorts_engine.services.ingestion.security import VideoTooLargeError
                size_mb = size_b / (1024 * 1024)
                max_mb = max_bytes / (1024 * 1024)
                raise VideoTooLargeError(
                    f"File size ({size_mb:.1f} MB) exceeds maximum allowed limit ({max_mb:.1f} MB)."
                )
        except OSError as e:
            raise OSError(f"Cannot read file attributes for {p}: {e}") from e

        return p

    @classmethod
    def probe_video_metadata(cls, file_path: Path) -> Dict[str, Any]:
        """
        Probes video duration, dimensions, codecs, and audio presence.
        Uses ffprobe primary engine with OpenCV fallback.
        """
        metadata: Dict[str, Any] = {
            "duration_sec": 0.0,
            "width": 0,
            "height": 0,
            "fps": 30.0,
            "codec": "unknown",
            "has_audio": False,
            "audio_codec": None,
            "size_bytes": 0,
        }

        try:
            metadata["size_bytes"] = file_path.stat().st_size
        except Exception:
            pass

        # Primary Probe: ffprobe JSON output
        norm_path = os.path.normpath(str(file_path))
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration,size,bit_rate:stream=index,codec_type,codec_name,width,height,r_frame_rate",
            "-of", "json",
            norm_path,
        ]

        ffprobe_success = False
        try:
            from shorts_engine.services.execution.watchdog import SubprocessWatchdog
            res = SubprocessWatchdog.run(cmd, timeout_sec=12.0, check=False)
            if res.exit_code == 0 and res.stdout:
                data = json.loads(res.stdout)
                fmt = data.get("format", {})
                if "duration" in fmt:
                    try:
                        metadata["duration_sec"] = max(0.0, float(fmt["duration"]))
                    except (ValueError, TypeError):
                        pass

                streams = data.get("streams", [])
                for st in streams:
                    ctype = st.get("codec_type")
                    if ctype == "video" and metadata["width"] == 0:
                        metadata["width"] = int(st.get("width") or 0)
                        metadata["height"] = int(st.get("height") or 0)
                        metadata["codec"] = str(st.get("codec_name") or "h264")
                        r_fps = st.get("r_frame_rate", "30/1")
                        try:
                            if "/" in r_fps:
                                num, den = r_fps.split("/")
                                metadata["fps"] = float(num) / float(den) if float(den) > 0 else 30.0
                            else:
                                metadata["fps"] = float(r_fps)
                        except Exception:
                            metadata["fps"] = 30.0
                    elif ctype == "audio":
                        metadata["has_audio"] = True
                        metadata["audio_codec"] = str(st.get("codec_name") or "aac")

                if metadata["duration_sec"] > 0.0:
                    ffprobe_success = True
        except Exception as e:
            logger.debug("ffprobe execution failed on %s: %s. Attempting OpenCV fallback.", file_path.name, e)

        # Fallback Probe: OpenCV VideoCapture
        if not ffprobe_success or metadata["duration_sec"] <= 0.0:
            try:
                import cv2
                cap = cv2.VideoCapture(norm_path)
                if cap.isOpened():
                    frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
                    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
                    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
                    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
                    cap.release()

                    if frames > 0 and fps > 0:
                        metadata["duration_sec"] = round(frames / fps, 2)
                        metadata["fps"] = round(fps, 2)
                    if w > 0 and h > 0:
                        metadata["width"] = w
                        metadata["height"] = h
                    metadata["codec"] = metadata.get("codec") or "h264"
            except Exception as cv_err:
                logger.debug("OpenCV probe fallback failed on %s: %s", file_path.name, cv_err)

        return metadata

    @classmethod
    def get_or_create_local_channel(
        cls,
        db: Session,
        channel_id: Optional[str] = None,
        channel_name: str = "Local Media",
    ) -> Channel:
        """
        Resolves or creates a dedicated Channel entity for local file ingestion.
        """
        if channel_id:
            ch = db.query(Channel).filter(Channel.id == channel_id).first()
            if not ch:
                raise ValueError(f"Target Channel not found with ID: {channel_id}")
            return ch

        # Lookup canonical local channel
        local_ch = db.query(Channel).filter(Channel.youtube_channel_id == "local_media").first()
        if local_ch:
            return local_ch

        # Create new default local media channel
        new_ch = Channel(
            id=generate_uuid(),
            youtube_channel_id="local_media",
            name=channel_name,
            watch_enabled=False,
            human_review_required=True,
        )
        db.add(new_ch)
        db.commit()
        db.refresh(new_ch)
        logger.info("Initialized default Local Media channel: %s", new_ch.id)
        return new_ch

    @classmethod
    def ingest_local_path(
        cls,
        db: Session,
        file_path: Union[str, Path],
        channel_id: Optional[str] = None,
        title: Optional[str] = None,
        runtime_overrides: Optional[Dict[str, Any]] = None,
    ) -> SourceVideo:
        """
        Performs zero-copy local file ingestion:
        - Validates file path and ensures media container integrity.
        - Probes metadata (duration, resolution, audio tracks).
        - Creates SourceVideo record pointing directly to existing disk path.
        - Emits real-time EventBus notification.
        """
        canonical_path = cls.validate_local_file(file_path)
        meta = cls.probe_video_metadata(canonical_path)
        channel = cls.get_or_create_local_channel(db, channel_id)

        norm_disk_path = os.path.normpath(str(canonical_path))
        existing_video = db.query(SourceVideo).filter(SourceVideo.downloaded_path == norm_disk_path).first()
        if existing_video:
            logger.info("Local file '%s' already registered as SourceVideo %s", canonical_path.name, existing_video.youtube_video_id)
            return existing_video

        # Deterministic video ID for local videos based on file path hash
        import hashlib
        clean_title = (title or canonical_path.stem).strip()
        path_hash = hashlib.md5(norm_disk_path.encode()).hexdigest()[:12]
        local_vid = f"loc_{path_hash}"

        duration_sec = int(round(meta.get("duration_sec", 0.0)))
        max_dur = getattr(settings, "MAX_VIDEO_DURATION_SEC", int(settings.MAX_INGESTION_DURATION_HOURS * 3600))
        if duration_sec > max_dur:
            from shorts_engine.services.ingestion.security import VideoTooLongError
            raise VideoTooLongError(
                f"Local video duration ({duration_sec}s / {duration_sec/3600:.2f}h) exceeds maximum allowed duration ({max_dur}s)."
            )

        source_video = SourceVideo(
            id=generate_uuid(),
            channel_id=channel.id,
            youtube_video_id=local_vid,
            title=clean_title,
            duration_sec=duration_sec,
            status=SourceVideoStatus.DETECTED,
            downloaded_path=norm_disk_path,
            runtime_overrides_json=json.dumps(runtime_overrides) if runtime_overrides else None,
            retry_count=0,
        )
        db.add(source_video)
        db.commit()
        db.refresh(source_video)

        logger.info("Successfully ingested local file '%s' as video %s", canonical_path.name, local_vid)

        # Generate lightweight 720p H.264 preview proxy if codec/container is incompatible with web browsers (Enhancement 4)
        if is_preview_proxy_needed(canonical_path, codec=meta.get("codec")):
            try:
                generate_preview_proxy(canonical_path)
            except Exception as e:
                logger.debug("Preview proxy generation skipped during local ingestion: %s", e)


        try:
            event = PlatformEvent(
                category="ingestion",
                level="SUCCESS",
                title=f"Local Video Ingested: {clean_title}",
                message=f"Mapped local video '{canonical_path.name}' ({duration_sec}s) with zero-copy reference.",
                entity_id=source_video.id,
                entity_type="video",
                action_url=f"/?video={source_video.youtube_video_id}",
                details={
                    "path": norm_disk_path,
                    "duration_sec": duration_sec,
                    "resolution": f"{meta['width']}x{meta['height']}",
                    "has_audio": meta["has_audio"],
                },
            )
            global_event_bus.publish_sync(event)
        except Exception as e:
            logger.debug("Failed to emit local ingestion event: %s", e)

        return source_video

    @classmethod
    async def save_chunked_upload(
        cls,
        upload_file: Any,  # fastapi.UploadFile
        destination_dir: Optional[Path] = None,
        chunk_size: int = 1024 * 1024,  # 1MB buffer
    ) -> Path:
        """
        Asynchronously streams uploaded file chunks to disk using atomic .part staging,
        preventing RAM exhaustion on multi-gigabyte video uploads.
        """
        dest_dir = destination_dir or Path(settings.RAW_DIR)
        dest_dir.mkdir(parents=True, exist_ok=True)

        original_filename = getattr(upload_file, "filename", "uploaded_video.mp4")
        # Sanitize filename
        safe_basename = re.sub(r"[^A-Za-z0-9_.-]", "_", Path(original_filename).name)
        if not safe_basename:
            safe_basename = "upload.mp4"

        final_name = f"{generate_uuid()[:8]}_{safe_basename}"
        final_path = dest_dir / final_name
        part_path = dest_dir / f"{final_name}.part"

        total_bytes = 0
        max_bytes = getattr(settings, "MAX_VIDEO_FILESIZE_BYTES", int(settings.MAX_INGESTION_FILE_SIZE_MB * 1024 * 1024))

        try:
            with open(part_path, "wb") as f_out:
                while True:
                    chunk = await upload_file.read(chunk_size)
                    if not chunk:
                        break
                    total_bytes += len(chunk)
                    if total_bytes > max_bytes:
                        from shorts_engine.services.ingestion.security import VideoTooLargeError
                        raise VideoTooLargeError(
                            f"Uploaded file exceeds maximum allowed file size ({max_bytes / (1024*1024):.1f} MB)."
                        )
                    f_out.write(chunk)

            # Atomic move from .part to final
            shutil.move(str(part_path), str(final_path))
            return final_path
        except Exception as e:
            if part_path.exists():
                try:
                    part_path.unlink()
                except Exception:
                    pass
            from shorts_engine.services.ingestion.security import VideoTooLargeError
            if isinstance(e, VideoTooLargeError):
                raise e
            if isinstance(e, (IOError, OSError)):
                raise e
            raise IOError(f"Failed to stream and save uploaded file: {e}") from e

    @classmethod
    async def ingest_uploaded_file(
        cls,
        db: Session,
        upload_file: Any,  # fastapi.UploadFile
        channel_id: Optional[str] = None,
        title: Optional[str] = None,
        runtime_overrides: Optional[Dict[str, Any]] = None,
    ) -> SourceVideo:
        """
        Handles multipart chunked video upload and creates the database SourceVideo record.
        """
        saved_path = await cls.save_chunked_upload(upload_file)
        display_title = title or Path(getattr(upload_file, "filename", "Upload")).stem
        try:
            return cls.ingest_local_path(
                db=db,
                file_path=saved_path,
                channel_id=channel_id,
                title=display_title,
                runtime_overrides=runtime_overrides,
            )
        except Exception:
            if saved_path.exists():
                try:
                    saved_path.unlink()
                except Exception:
                    pass
            raise
