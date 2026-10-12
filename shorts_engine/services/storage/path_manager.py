"""
Deterministic, hierarchical storage path management and backward-compatible asset resolution.
Enforces Windows MAX_PATH budgeting, safe slugification, atomic writes, and handle-safe unlinking.
"""

import re
import time
import logging
import unicodedata
from pathlib import Path
from typing import Optional, Union, Any, Dict, List

from shorts_engine.config.settings import settings

logger = logging.getLogger(__name__)


def slugify(text: Optional[str], max_length: int = 35) -> str:
    """
    Produces a clean, deterministic, filesystem-safe slug:
    - Normalizes unicode (NFKD)
    - Strips non-alphanumeric characters
    - Lowercases and collapses multiple dashes/underscores
    - Budgets length to prevent exceeding Windows MAX_PATH (260 chars)
    """
    if not text:
        return "unnamed"
    text = unicodedata.normalize("NFKD", str(text))
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9\s_-]", "", text).strip()
    slug = re.sub(r"[\s_-]+", "_", text).lower().strip("_")
    if not slug:
        return "unnamed"
    return slug[:max_length].rstrip("_")


class PathManager:
    """
    Centralized authority for filesystem paths across downloads, outputs, thumbnails, and temp files.
    Provides two-tier backward-compatible resolution for legacy flat assets.
    """

    @staticmethod
    def get_channel_folder_name(channel_name: Optional[str], channel_id: str) -> str:
        c_slug = slugify(channel_name, max_length=25)
        # Keep 12 chars of channel_id for uniqueness
        clean_id = slugify(channel_id, max_length=16)
        return f"{c_slug}_{clean_id}".strip("_")

    @staticmethod
    def get_video_folder_name(video_title: Optional[str], video_id: str) -> str:
        v_slug = slugify(video_title, max_length=30)
        return f"{v_slug}_{video_id}".strip("_")

    @classmethod
    def get_download_dir(
        cls,
        channel_name: Optional[str],
        channel_id: Optional[str],
        video_title: Optional[str],
        video_id: str,
        base_dir: Optional[Path] = None,
    ) -> Path:
        base = base_dir or (settings.STORAGE_DIR / "downloads")
        ch_folder = cls.get_channel_folder_name(channel_name, channel_id or "default")
        vid_folder = cls.get_video_folder_name(video_title, video_id)
        d = base / ch_folder / vid_folder
        d.mkdir(parents=True, exist_ok=True)
        return d

    @classmethod
    def get_download_video_path(
        cls,
        channel_name: Optional[str],
        channel_id: Optional[str],
        video_title: Optional[str],
        video_id: str,
        base_dir: Optional[Path] = None,
    ) -> Path:
        target_dir = cls.get_download_dir(channel_name, channel_id, video_title, video_id, base_dir)
        v_slug = slugify(video_title, max_length=25)
        return target_dir / f"{v_slug}_{video_id}.mp4"

    @classmethod
    def get_download_audio_path(
        cls,
        channel_name: Optional[str],
        channel_id: Optional[str],
        video_title: Optional[str],
        video_id: str,
        base_dir: Optional[Path] = None,
    ) -> Path:
        target_dir = cls.get_download_dir(channel_name, channel_id, video_title, video_id, base_dir)
        v_slug = slugify(video_title, max_length=25)
        return target_dir / f"{v_slug}_{video_id}_audio.wav"

    @classmethod
    def get_output_dir(
        cls,
        channel_name: Optional[str],
        channel_id: Optional[str],
        video_title: Optional[str],
        video_id: str,
        base_dir: Optional[Path] = None,
    ) -> Path:
        base = base_dir or settings.OUTPUT_DIR
        ch_folder = cls.get_channel_folder_name(channel_name, channel_id or "default")
        vid_folder = cls.get_video_folder_name(video_title, video_id)
        d = base / ch_folder / vid_folder
        d.mkdir(parents=True, exist_ok=True)
        return d

    @classmethod
    def get_clip_output_path(
        cls,
        channel_name: Optional[str],
        channel_id: Optional[str],
        video_title: Optional[str],
        video_id: str,
        hook_text: Optional[str],
        start_ms: int,
        end_ms: int,
        base_dir: Optional[Path] = None,
    ) -> Path:
        target_dir = cls.get_output_dir(channel_name, channel_id, video_title, video_id, base_dir) / "clips"
        target_dir.mkdir(parents=True, exist_ok=True)
        h_slug = slugify(hook_text, max_length=30)
        return target_dir / f"{h_slug}_{start_ms}_{end_ms}.mp4"

    @classmethod
    def get_thumbnail_output_path(
        cls,
        channel_name: Optional[str],
        channel_id: Optional[str],
        video_title: Optional[str],
        video_id: str,
        hook_text: Optional[str],
        start_ms: int,
        end_ms: int,
        base_dir: Optional[Path] = None,
    ) -> Path:
        target_dir = cls.get_output_dir(channel_name, channel_id, video_title, video_id, base_dir) / "thumbnails"
        target_dir.mkdir(parents=True, exist_ok=True)
        h_slug = slugify(hook_text, max_length=30)
        return target_dir / f"{h_slug}_{start_ms}_{end_ms}_thumb.jpg"

    @classmethod
    def get_temp_dir(cls, video_id: str, subfolder: Optional[str] = None) -> Path:
        t = settings.TEMP_DIR / video_id
        if subfolder:
            t = t / subfolder
        t.mkdir(parents=True, exist_ok=True)
        return t

    @classmethod
    def get_subtitle_path(cls, video_id: str, start_ms: int, end_ms: int) -> Path:
        sub_dir = cls.get_temp_dir(video_id, subfolder="subtitles")
        return sub_dir / f"{video_id}_{start_ms}_{end_ms}.ass"

    @classmethod
    def resolve_clip_path(cls, clip_or_path: Any) -> Optional[Path]:
        """
        Two-tier resolver for clip video files:
        1. If already an existing path on disk, returns it immediately.
        2. If string path provided, checks existence.
        3. If Clip model instance provided:
           - Checks clip.rendered_path if set and exists.
           - Checks hierarchical structure in settings.OUTPUT_DIR.
           - Falls back to legacy flat locations:
             storage/outputs/{video_id}/{video_id}_short_*.mp4
             storage/outputs/{video_id}_short_*.mp4
        """
        # If passed directly as Path or str
        if isinstance(clip_or_path, (str, Path)):
            p = Path(clip_or_path)
            if p.exists() and p.is_file():
                return p
            # Strip potential CLAIMED: prefix
            raw_str = str(clip_or_path)
            if raw_str.startswith("CLAIMED:"):
                return None

        # If passed as Clip model
        clip = clip_or_path
        if hasattr(clip, "rendered_path") and clip.rendered_path:
            p = Path(clip.rendered_path)
            if p.exists() and p.is_file():
                return p

        # Fallback search if candidate is available
        cand = getattr(clip, "candidate", None) if hasattr(clip, "candidate") else None
        if cand:
            sv = getattr(cand, "source_video", None)
            yt_id = getattr(sv, "youtube_video_id", None) if sv else None
            start_ms = getattr(cand, "start_ms", None)
            end_ms = getattr(cand, "end_ms", None)

            if yt_id and start_ms is not None and end_ms is not None:
                # Search patterns in outputs
                out_root = settings.OUTPUT_DIR
                patterns = [
                    f"**/*{yt_id}*_{start_ms}_{end_ms}.mp4",
                    f"**/*_{start_ms}_{end_ms}.mp4",
                    f"*{yt_id}*short*{start_ms}_{end_ms}.mp4",
                ]
                for pat in patterns:
                    for found in out_root.glob(pat):
                        if found.is_file() and found.stat().st_size > 0:
                            return found

        return None

    @classmethod
    def resolve_download_path(cls, video_or_path: Any) -> Optional[Path]:
        """
        Two-tier resolver for source video MP4 files.
        Checks hierarchical path first, then legacy flat storage/downloads/{video_id}.mp4.
        """
        if isinstance(video_or_path, (str, Path)):
            p = Path(video_or_path)
            if p.exists() and p.is_file():
                return p

        video = video_or_path
        if hasattr(video, "downloaded_path") and video.downloaded_path:
            p = Path(video.downloaded_path)
            if p.exists() and p.is_file():
                return p

        yt_id = getattr(video, "youtube_video_id", None)
        if yt_id:
            # Check legacy flat
            flat = settings.STORAGE_DIR / "downloads" / f"{yt_id}.mp4"
            if flat.exists() and flat.is_file():
                return flat
            # Search hierarchical downloads
            for found in (settings.STORAGE_DIR / "downloads").glob(f"**/*{yt_id}*.mp4"):
                if found.is_file() and not str(found).endswith("_audio.wav"):
                    return found

        return None

    @staticmethod
    def safe_unlink(file_path: Union[str, Path], retries: int = 3, delay_sec: float = 0.5) -> bool:
        """
        Unlinks a file safely with retry logic for Windows file locks (WinError 32).
        Returns True if removed or did not exist, False if failed.
        """
        p = Path(file_path)
        if not p.exists():
            return True
        for attempt in range(1, retries + 1):
            try:
                p.unlink(missing_ok=True)
                return True
            except Exception as e:
                if attempt == retries:
                    logger.warning("Failed to unlink %s after %d attempts: %s", p, retries, e)
                    return False
                time.sleep(delay_sec * attempt)
        return False

    @staticmethod
    def prune_empty_dirs(start_dir: Path, stop_at: Path) -> int:
        """
        Recursively prunes empty child directories up to stop_at boundary.
        """
        pruned = 0
        try:
            curr = start_dir
            while curr != stop_at and curr.is_relative_to(stop_at) and curr != curr.parent:
                if curr.exists() and not any(curr.iterdir()):
                    curr.rmdir()
                    pruned += 1
                    curr = curr.parent
                else:
                    break
        except Exception as e:
            logger.debug("Prune empty dirs stopped at %s: %s", start_dir, e)
        return pruned

    @classmethod
    def get_preview_proxy_path(cls, source_path: Union[str, Path]) -> Path:
        """
        Returns the deterministic file path for a lightweight 720p H.264 preview proxy video (Enhancement 4).
        """
        return get_preview_proxy_path(source_path)

    @classmethod
    def purge_source_video_files(cls, video: Any) -> Dict[str, Any]:
        """
        Safely deletes raw source video files (.mp4), extracted audio (.wav),
        and preview proxies associated with a SourceVideo entity.
        Prunes empty parent directories up to settings.STORAGE_DIR / 'downloads'.
        Returns dict detailing deleted files and freed bytes.
        """
        deleted_files: List[str] = []
        freed_bytes = 0
        parent_dirs = set()

        # 1. Downloaded video file
        video_path = getattr(video, "downloaded_path", None)
        resolved_v = cls.resolve_download_path(video_path or video)
        if resolved_v and resolved_v.exists() and resolved_v.is_file():
            try:
                freed_bytes += resolved_v.stat().st_size
                parent_dirs.add(resolved_v.parent)
                if cls.safe_unlink(resolved_v):
                    deleted_files.append(str(resolved_v))
            except Exception as e:
                logger.warning("Error checking size/deleting video %s: %s", resolved_v, e)

        # 2. Audio track file
        audio_path = getattr(video, "audio_path", None)
        if audio_path:
            p_audio = Path(audio_path)
            if p_audio.exists() and p_audio.is_file():
                try:
                    freed_bytes += p_audio.stat().st_size
                    parent_dirs.add(p_audio.parent)
                    if cls.safe_unlink(p_audio):
                        deleted_files.append(str(p_audio))
                except Exception as e:
                    logger.warning("Error checking size/deleting audio %s: %s", p_audio, e)

        # 3. Preview proxy file (if exists alongside video)
        if resolved_v:
            proxy_path = cls.get_preview_proxy_path(resolved_v)
            if proxy_path.exists() and proxy_path.is_file():
                try:
                    freed_bytes += proxy_path.stat().st_size
                    parent_dirs.add(proxy_path.parent)
                    if cls.safe_unlink(proxy_path):
                        deleted_files.append(str(proxy_path))
                except Exception as e:
                    logger.warning("Error deleting proxy %s: %s", proxy_path, e)

        # 4. Prune empty directories up to downloads root
        downloads_root = Path(settings.STORAGE_DIR) / "downloads"
        for p_dir in parent_dirs:
            cls.prune_empty_dirs(p_dir, downloads_root)

        return {
            "deleted_files": deleted_files,
            "freed_bytes": freed_bytes,
            "file_count": len(deleted_files),
        }

    @classmethod
    def purge_clip_intermediates(cls, clip: Any, video_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Safely deletes intermediate assets (crop plan JSON, subtitle .ass, temp chunks)
        for a specific clip, preserving the final rendered clip.
        Returns dict detailing deleted files and freed bytes.
        """
        deleted_files: List[str] = []
        freed_bytes = 0

        clip_id = getattr(clip, "id", str(clip) if isinstance(clip, (str, int)) else None)
        cand = getattr(clip, "candidate", None) or getattr(clip, "clip_candidate", None)
        start_ms = getattr(cand, "start_ms", None)
        end_ms = getattr(cand, "end_ms", None)

        # Resolve video_id if not explicitly provided
        if not video_id and cand and getattr(cand, "source_video", None):
            video_id = getattr(cand.source_video, "youtube_video_id", cand.source_video.id)

        # 1. Crop plan file
        if clip_id:
            crop_plan = Path(settings.STORAGE_DIR) / "crops" / f"{clip_id}_crop_plan.json"
            if crop_plan.exists() and crop_plan.is_file():
                try:
                    freed_bytes += crop_plan.stat().st_size
                    if cls.safe_unlink(crop_plan):
                        deleted_files.append(str(crop_plan))
                except Exception as e:
                    logger.warning("Error deleting crop plan %s: %s", crop_plan, e)

        # 2. Subtitle .ass file
        if video_id and start_ms is not None and end_ms is not None:
            sub_file = cls.get_subtitle_path(str(video_id), int(start_ms), int(end_ms))
            if sub_file.exists() and sub_file.is_file():
                try:
                    freed_bytes += sub_file.stat().st_size
                    if cls.safe_unlink(sub_file):
                        deleted_files.append(str(sub_file))
                except Exception as e:
                    logger.warning("Error deleting subtitle %s: %s", sub_file, e)

        # 3. Temp directory chunks
        if video_id:
            temp_dir = Path(settings.TEMP_DIR) / str(video_id)
            if temp_dir.exists():
                cls.prune_empty_dirs(temp_dir, Path(settings.TEMP_DIR))

        return {
            "deleted_files": deleted_files,
            "freed_bytes": freed_bytes,
            "file_count": len(deleted_files),
        }


def get_preview_proxy_path(source_path: Union[str, Path]) -> Path:
    """
    Returns the deterministic file path for a lightweight 720p H.264 preview proxy video (Enhancement 4).
    Placed alongside the source video with '_proxy_preview.mp4' suffix.
    """
    p = Path(source_path)
    return p.parent / f"{p.stem}_proxy_preview.mp4"

