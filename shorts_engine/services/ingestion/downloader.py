"""
Ingestion Service: Downloads video & audio using yt-dlp and extracts metadata.
Implements exponential backoff retry and robust file management.
"""

import os
import gc
import time
import random
import logging
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional, List
import yt_dlp
from shorts_engine.config.settings import settings
from shorts_engine.services.execution.watchdog import SubprocessWatchdog

logger = logging.getLogger(__name__)


class VideoDownloadResult:
    def __init__(
        self,
        video_id: str,
        title: str,
        duration_sec: int,
        video_path: Path,
        audio_path: Path,
        metadata: Dict[str, Any],
    ):
        self.video_id = video_id
        self.title = title
        self.duration_sec = duration_sec
        self.video_path = video_path
        self.audio_path = audio_path
        self.metadata = metadata

    def __repr__(self) -> str:
        return (
            f"<VideoDownloadResult id={self.video_id} title='{self.title[:30]}' "
            f"duration={self.duration_sec}s>"
        )


class IngestionService:
    def __init__(self, output_dir: Optional[Path] = None, max_retries: int = 3):
        self.output_dir = output_dir or settings.STORAGE_DIR / "downloads"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.max_retries = max_retries
        try:
            self.purge_stale_fragments(max_age_hours=2.0)
            self.purge_stale_temp_files(max_age_hours=24.0)
        except Exception as e:
            logger.debug("Initial stale fragment purge skipped: %s", e)

    @staticmethod
    def purge_stale_temp_files(max_age_hours: float = 24.0) -> int:
        """
        Scans settings.TEMP_DIR for orphaned *.ass and temporary files older than max_age_hours
        and deletes them to prevent uncontrolled disk accumulation.
        """
        purged = 0
        now = time.time()
        max_age_sec = max_age_hours * 3600.0
        temp_dir = settings.TEMP_DIR
        if temp_dir.exists():
            for pattern in ("*.ass", "*.tmp", "*.temp*", "temp_*"):
                for f in temp_dir.glob(pattern):
                    try:
                        if f.is_file() and (now - f.stat().st_mtime) >= max_age_sec:
                            f.unlink(missing_ok=True)
                            purged += 1
                    except Exception as e:
                        logger.debug("Could not purge temp file %s: %s", f, e)
        if purged > 0:
            logger.info("Purged %d stale temp files from %s", purged, temp_dir)
        return purged

    def purge_stale_fragments(self, max_age_hours: float = 2.0) -> int:
        """
        Scans output_dir for orphaned *.part* and *.temp* download files older than max_age_hours
        and deletes them to prevent YouTube CDN token expiry / HTTP 416 range errors.
        Returns the number of purged files.
        """
        purged = 0
        now = time.time()
        max_age_sec = max_age_hours * 3600.0
        for pattern in ("*.part*", "*.temp*", "*.ytdl"):
            for f in self.output_dir.glob(pattern):
                try:
                    if f.is_file() and (now - f.stat().st_mtime) >= max_age_sec:
                        f.unlink(missing_ok=True)
                        purged += 1
                except Exception as e:
                    logger.debug("Could not purge stale fragment %s: %s", f, e)
        if purged > 0:
            logger.info("Purged %d stale download fragments from %s", purged, self.output_dir)
        return purged

    def get_video_url(self, video_id_or_url: str) -> str:
        if video_id_or_url.startswith("http://") or video_id_or_url.startswith("https://"):
            return video_id_or_url
        return f"https://www.youtube.com/watch?v={video_id_or_url}"

    def extract_video_id(self, video_id_or_url: str) -> str:
        if "v=" in video_id_or_url:
            return video_id_or_url.split("v=")[1].split("&")[0]
        if "youtu.be/" in video_id_or_url:
            return video_id_or_url.split("youtu.be/")[1].split("?")[0]
        return video_id_or_url

    def extract_audio(self, video_path: Path, audio_path: Path) -> Path:
        """Extracts 16kHz mono WAV from video file using FFmpeg for optimal ASR."""
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ffmpeg",
            "-y",
            "-i", str(video_path),
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", "16000",
            "-ac", "1",
            str(audio_path),
        ]
        result = SubprocessWatchdog.run(
            cmd,
            timeout_sec=120.0,
            check=False,
            artifacts_to_cleanup=[audio_path],
        )
        if result.exit_code != 0:
            logger.error("FFmpeg audio extraction failed: %s", result.stderr)
            raise RuntimeError(f"FFmpeg audio extraction failed: {result.stderr}")
        return audio_path

    def probe_duration_sec(self, file_path: Path) -> int:
        """Probes video or audio file duration using ffprobe with timeout protection."""
        try:
            cmd = [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(file_path),
            ]
            res = SubprocessWatchdog.run(cmd, timeout_sec=15.0, check=True)
            return int(float(res.stdout.strip()))
        except Exception:
            return 0

    def download_video(self, video_id_or_url: str) -> VideoDownloadResult:
        """
        Downloads the video using yt-dlp with retries and extracts a 16kHz WAV track.
        """
        url = self.get_video_url(video_id_or_url)
        video_id = self.extract_video_id(video_id_or_url)
        target_video = self.output_dir / f"{video_id}.mp4"
        target_audio = self.output_dir / f"{video_id}_audio.wav"

        # Resolve cached video using PathManager two-tier resolution
        from shorts_engine.services.storage.path_manager import PathManager
        cached_video = PathManager.resolve_download_path(video_id)
        if cached_video and cached_video.exists() and cached_video.stat().st_size > 0:
            target_audio = cached_video.with_name(f"{cached_video.stem}_audio.wav")
            if target_audio.exists():
                logger.info("Video %s already downloaded at %s", video_id, cached_video)
                meta = {}
                try:
                    meta = self.get_metadata(url)
                except Exception as meta_err:
                    logger.warning("Could not fetch remote metadata for cached video %s: %s. Using local probe fallback.", video_id, meta_err)

                duration_sec = int(meta.get("duration", 0))
                if duration_sec <= 0:
                    duration_sec = self.probe_duration_sec(cached_video)

                return VideoDownloadResult(
                    video_id=video_id,
                    title=meta.get("title", video_id),
                    duration_sec=duration_sec,
                    video_path=cached_video,
                    audio_path=target_audio,
                    metadata=meta,
                )

        # Build download target folder (using temp fragments for download safety)
        temp_fragments_dir = PathManager.get_temp_dir(video_id, subfolder="fragments")
        target_video_raw = temp_fragments_dir / f"{video_id}.%(ext)s"

        from shorts_engine.services.telemetry.progress_tracker import job_progress_tracker

        def _progress_hook(d):
            if d.get("status") == "downloading":
                downloaded = d.get("downloaded_bytes", 0)
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                speed = d.get("speed") or 0
                eta = d.get("eta") or 0
                pct = (downloaded / total * 100.0) if total > 0 else 0.0
                speed_mb = f"{speed / (1024*1024):.1f} MB/s" if speed else "—"
                total_mb = f"{total / (1024*1024):.1f} MB" if total else "—"
                downloaded_mb = f"{downloaded / (1024*1024):.1f} MB"
                detail = f"{downloaded_mb} / {total_mb} ({speed_mb}, ETA: {int(eta)}s)" if eta else f"{downloaded_mb} / {total_mb} ({speed_mb})"
                job_progress_tracker.update_progress(
                    video_id=video_id,
                    stage="DOWNLOADING",
                    percent=pct,
                    detail=f"Downloading: {detail}",
                    speed=speed_mb,
                    eta_seconds=float(eta) if eta else None,
                )
            elif d.get("status") == "finished":
                job_progress_tracker.update_progress(
                    video_id=video_id,
                    stage="DOWNLOADING",
                    percent=100.0,
                    detail="Download complete, extracting audio...",
                )

        # Dynamic client rotation strategies to bypass bot challenges
        client_strategies = [
            ["android", "web"],
            ["ios", "mweb"],
            ["android_creator", "web_creator"],
        ]

        cookie_path = self.get_cookie_path()

        last_err = None
        force_fresh_start = False

        for attempt in range(1, self.max_retries + 1):
            strategy_idx = (attempt - 1) % len(client_strategies)
            chosen_clients = client_strategies[strategy_idx]

            ydl_opts = {
                "format": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                "outtmpl": str(target_video_raw),
                "merge_output_format": "mp4",
                "quiet": True,
                "no_warnings": True,
                "js_runtimes": {"node": {}},
                "extractor_args": {"youtube": {"player_client": chosen_clients}},
                "progress_hooks": [_progress_hook],
                "windowsfilenames": True,
            }
            if cookie_path:
                ydl_opts["cookiefile"] = cookie_path
            if force_fresh_start:
                ydl_opts["continuedl"] = False
                ydl_opts["nopart"] = True

            try:
                logger.info(
                    "Downloading %s (attempt %d/%d, client_strategy=%s)...",
                    url,
                    attempt,
                    self.max_retries,
                    chosen_clients,
                )
                job_progress_tracker.update_progress(
                    video_id=video_id,
                    stage="DOWNLOADING",
                    percent=5.0,
                    detail=f"Initiating stream download (attempt {attempt}/{self.max_retries})...",
                )
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    actual_downloaded = Path(ydl.prepare_filename(info))
                    if not actual_downloaded.exists():
                        # Look for merged mp4 in temp_fragments_dir
                        for f in temp_fragments_dir.glob(f"{video_id}*.mp4"):
                            if f.is_file() and f.stat().st_size > 0:
                                actual_downloaded = f
                                break

                    ch_name = info.get("uploader") or info.get("channel") or "Unknown_Channel"
                    ch_id = info.get("channel_id") or "default"
                    v_title = info.get("title") or video_id

                    # Organize into clean hierarchical storage
                    dest_video_path = PathManager.get_download_video_path(ch_name, ch_id, v_title, video_id, self.output_dir)
                    dest_audio_path = PathManager.get_download_audio_path(ch_name, ch_id, v_title, video_id, self.output_dir)

                    dest_video_path.parent.mkdir(parents=True, exist_ok=True)
                    if actual_downloaded.exists() and actual_downloaded != dest_video_path:
                        import shutil
                        # Robust Windows move with retry
                        for move_attempt in range(3):
                            try:
                                shutil.move(str(actual_downloaded), str(dest_video_path))
                                break
                            except PermissionError:
                                gc.collect()
                                time.sleep(0.5 * (move_attempt + 1))
                    elif not dest_video_path.exists():
                        dest_video_path = actual_downloaded

                    # Extract local thumbnail from downloaded video
                    try:
                        from shorts_engine.services.rendering.thumbnail_generator import ThumbnailGenerator
                        thumb_dir = Path(settings.STORAGE_DIR) / "thumbnails"
                        thumb_dir.mkdir(parents=True, exist_ok=True)
                        flat_thumb = thumb_dir / f"{video_id}.jpg"
                        if not flat_thumb.exists():
                            ThumbnailGenerator.extract_frame_ffmpeg(dest_video_path, flat_thumb, timestamp_sec=2.0)
                        hierarchical_thumb = dest_video_path.parent / "thumbnail.jpg"
                        if not hierarchical_thumb.exists():
                            ThumbnailGenerator.extract_frame_ffmpeg(dest_video_path, hierarchical_thumb, timestamp_sec=2.0)
                    except Exception as thumb_err:
                        logger.warning("Could not extract local thumbnail for %s: %s", video_id, thumb_err)

                    # Extract WAV audio for transcription
                    job_progress_tracker.update_progress(
                        video_id=video_id,
                        stage="DOWNLOADING",
                        percent=95.0,
                        detail="Extracting 16kHz audio for speech recognition...",
                    )
                    self.extract_audio(dest_video_path, dest_audio_path)

                    # Clean up temp fragments
                    shutil.rmtree(temp_fragments_dir, ignore_errors=True)

                    job_progress_tracker.update_progress(
                        video_id=video_id,
                        stage="DOWNLOADING",
                        percent=100.0,
                        detail="Download and audio extraction completed.",
                    )

                    return VideoDownloadResult(
                        video_id=video_id,
                        title=v_title,
                        duration_sec=int(info.get("duration", 0)),
                        video_path=dest_video_path,
                        audio_path=dest_audio_path,
                        metadata=info,
                    )
            except Exception as e:
                last_err = e
                err_msg = str(e)
                logger.warning("Attempt %d failed to download %s: %s", attempt, url, err_msg)

                # 1. HTTP 416 (Requested range not satisfiable) recovery:
                # Expired CDN byte offset -> purge all partials across temp & download dirs and reset byte range
                if "416" in err_msg or "Requested range not satisfiable" in err_msg:
                    logger.info("Purging all stale partial download files for video %s to reset byte range...", video_id)
                    force_fresh_start = True
                    for scan_dir in (temp_fragments_dir, self.output_dir):
                        if scan_dir.exists():
                            for pattern in (f"*{video_id}*.part*", f"*{video_id}*.temp*", f"*{video_id}*.ytdl*"):
                                for partial_file in scan_dir.glob(pattern):
                                    try:
                                        partial_file.unlink(missing_ok=True)
                                    except Exception:
                                        pass

                # 2. Windows WinError 32 file-locking recovery
                if "WinError 32" in err_msg or "used by another process" in err_msg:
                    gc.collect()
                    lock_wait = 2.0 + random.uniform(0.5, 2.0)
                    logger.info("Windows file handle lock on fragment for %s. GC run, waiting %.1fs for handle release...", video_id, lock_wait)
                    time.sleep(lock_wait)

                # 3. YouTube bot detection alert & mitigation
                if "confirm you’re not a bot" in err_msg or "Sign in to confirm" in err_msg:
                    logger.warning("YouTube anti-bot challenge detected for %s. Cycling player client and jittering wait...", video_id)
                    force_fresh_start = True

                # Jittered exponential backoff
                backoff_wait = min(12.0, (2.0 ** attempt) + random.uniform(0.2, 1.2))
                time.sleep(backoff_wait)

        raise RuntimeError(f"Failed to download video {video_id} after {self.max_retries} attempts: {last_err}")

    def get_cookie_path(self) -> Optional[str]:
        """Automatically discovers YouTube bot-bypass cookies across settings and standard paths."""
        candidate_cookies = [
            getattr(settings, "YOUTUBE_COOKIE_PATH", None),
            os.getenv("YOUTUBE_COOKIE_PATH"),
            settings.STORAGE_DIR / "cookies.txt",
            settings.ROOT_DIR / "cookies.txt",
        ]
        for cand in candidate_cookies:
            if cand and Path(cand).exists() and Path(cand).stat().st_size > 0:
                return str(Path(cand).resolve())
        return None

    def get_metadata(self, video_id_or_url: str) -> Dict[str, Any]:
        """Extracts metadata without downloading media, attaching anti-bot cookies and extractor options."""
        url = self.get_video_url(video_id_or_url)
        opts: Dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
        }
        cookie_path = self.get_cookie_path()
        if cookie_path:
            opts["cookiefile"] = cookie_path
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)
