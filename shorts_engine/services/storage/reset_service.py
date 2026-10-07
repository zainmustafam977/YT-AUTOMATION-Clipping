"""
Granular Data Reset & Storage Management Service.
Provides fine-grained, safe purge capabilities across storage directories,
database records, custom configurations, and API credentials without compromising
system stability or requiring server restarts.
"""

import os
import shutil
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, Union
from datetime import datetime
from sqlalchemy import text
from sqlalchemy.orm import Session

from shorts_engine.config.settings import settings
from shorts_engine.db.models import (
    SourceVideo,
    Transcript,
    ClipCandidate,
    Clip,
    PublishLog,
    PlatformPublishLog,
    Channel,
    ApiKey,
    User,
    UserRole,
)
from shorts_engine.dashboard.auth import hash_password

logger = logging.getLogger(__name__)


@dataclass
class ResetOptions:
    """Configuration options defining what items to delete or reset."""
    delete_raw_downloads: bool = False
    delete_rendered_outputs: bool = False
    delete_temp_files: bool = False
    delete_publishing_logs: bool = False
    delete_clips_and_candidates: bool = False
    delete_pipeline_history: bool = False
    delete_channels: bool = False
    reset_settings: bool = False
    delete_api_keys: bool = False
    delete_users: bool = False
    preserve_admin: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _format_size(size_bytes: int) -> str:
    """Formats bytes into human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def _get_dir_size_and_count(dir_path: Path) -> Dict[str, Any]:
    """Calculates recursive file count and total size in bytes for a directory, ignoring .gitkeep."""
    if not dir_path.exists():
        return {"count": 0, "size_bytes": 0, "size_human": "0 B"}

    total_bytes = 0
    file_count = 0
    try:
        for entry in os.scandir(dir_path):
            if entry.name == ".gitkeep":
                continue
            if entry.is_file(follow_symlinks=False):
                try:
                    total_bytes += entry.stat().st_size
                    file_count += 1
                except OSError:
                    pass
            elif entry.is_dir(follow_symlinks=False):
                sub = _get_dir_size_and_count(Path(entry.path))
                total_bytes += sub["size_bytes"]
                file_count += sub["count"]
    except OSError as e:
        logger.warning("Error reading directory %s: %s", dir_path, e)

    return {
        "count": file_count,
        "size_bytes": total_bytes,
        "size_human": _format_size(total_bytes),
    }


def _purge_directory_contents(dir_path: Path) -> Tuple[int, int]:
    """
    Deletes all files and subdirectories inside dir_path (preserving .gitkeep),
    then ensures dir_path itself exists. Returns (reclaimed_bytes, files_deleted).
    """
    if not dir_path.exists():
        dir_path.mkdir(parents=True, exist_ok=True)
        return 0, 0

    reclaimed_bytes = 0
    files_deleted = 0
    for item in dir_path.iterdir():
        if item.name == ".gitkeep":
            continue
        try:
            if item.is_file() or item.is_symlink():
                reclaimed_bytes += item.stat().st_size
                item.unlink(missing_ok=True)
                files_deleted += 1
            elif item.is_dir():
                sub_info = _get_dir_size_and_count(item)
                reclaimed_bytes += sub_info["size_bytes"]
                files_deleted += sub_info["count"]
                shutil.rmtree(item, ignore_errors=True)
        except Exception as e:
            logger.warning("Failed to purge item %s in %s: %s", item, dir_path, e)

    dir_path.mkdir(parents=True, exist_ok=True)
    return reclaimed_bytes, files_deleted


class ResetService:
    """
    Central service for granular database and storage resets with 100% stability.
    """

    @staticmethod
    def get_storage_inventory(db: Session) -> Dict[str, Any]:
        """
        Gathers current metrics on all reclaimable storage folders and database entities.
        """
        downloads = _get_dir_size_and_count(settings.DOWNLOADS_DIR)
        outputs = _get_dir_size_and_count(settings.OUTPUT_DIR)
        temp_files = _get_dir_size_and_count(settings.TEMP_DIR)

        downloads["size_mb"] = round(downloads["size_bytes"] / (1024 * 1024), 2)
        outputs["size_mb"] = round(outputs["size_bytes"] / (1024 * 1024), 2)
        temp_files["size_mb"] = round(temp_files["size_bytes"] / (1024 * 1024), 2)
        downloads["file_count"] = downloads["count"]
        outputs["file_count"] = outputs["count"]
        temp_files["file_count"] = temp_files["count"]

        # Database record counts
        try:
            source_videos_count = db.query(SourceVideo).count()
        except Exception:
            source_videos_count = 0

        try:
            transcripts_count = db.query(Transcript).count()
        except Exception:
            transcripts_count = 0

        try:
            clips_count = db.query(Clip).count()
        except Exception:
            clips_count = 0

        try:
            candidates_count = db.query(ClipCandidate).count()
        except Exception:
            candidates_count = 0

        try:
            pub_logs_count = db.query(PublishLog).count() + db.query(PlatformPublishLog).count()
        except Exception:
            pub_logs_count = 0

        try:
            channels_count = db.query(Channel).count()
        except Exception:
            channels_count = 0

        try:
            api_keys_count = db.query(ApiKey).count()
        except Exception:
            api_keys_count = 0

        try:
            users_count = db.query(User).count()
        except Exception:
            users_count = 0

        try:
            res = db.execute(text("SELECT count(*) FROM system_settings")).fetchone()
            settings_count = res[0] if res else 0
        except Exception:
            settings_count = 0

        total_disk_bytes = downloads["size_bytes"] + outputs["size_bytes"] + temp_files["size_bytes"]
        total_storage_mb = round(total_disk_bytes / (1024 * 1024), 2)
        total_records = (
            source_videos_count
            + transcripts_count
            + clips_count
            + candidates_count
            + pub_logs_count
            + channels_count
            + users_count
        )

        return {
            "storage": {
                "downloads": downloads,
                "outputs": outputs,
                "temp": temp_files,
            },
            "database": {
                "source_videos": source_videos_count,
                "transcripts": transcripts_count,
                "clips": clips_count,
                "clip_candidates": candidates_count,
                "candidates": candidates_count,
                "publishing_logs": pub_logs_count,
                "channels": channels_count,
                "custom_settings": settings_count,
                "api_keys": api_keys_count,
                "users": users_count,
                "total_records": total_records,
            },
            "total_storage_mb": total_storage_mb,
            "total_disk_bytes": total_disk_bytes,
            "total_disk_human": _format_size(total_disk_bytes),
        }

    @staticmethod
    def get_config_paths_inventory() -> Dict[str, Dict[str, Any]]:
        """
        Lists all configuration files, storage directories, and system paths with status.
        Returns a dictionary keyed by canonical identifiers.
        """
        sqlite_db_path = getattr(settings, "DB_PATH", settings.STORAGE_DIR / "shorts.db")
        watch_folder_path = getattr(settings, "WATCH_FOLDER_DIR", settings.STORAGE_DIR / "watch_folder")
        fonts_path = getattr(settings, "FONTS_DIR", settings.ROOT_DIR / "assets" / "fonts")
        vault_key_path = settings.STORAGE_DIR / ".vault.key"

        raw_items = [
            (
                "env_example",
                "Default Environment Template (.env.example)",
                "Default reference template showing all configurable environment variables.",
                settings.ROOT_DIR / ".env.example",
                "file",
            ),
            (
                "runtime_settings",
                "Active Dynamic Settings (runtime_settings.json)",
                "Live JSON configuration store for zero-restart dynamic parameters.",
                settings.STORAGE_DIR / "config" / "runtime_settings.json",
                "file",
            ),
            (
                "sqlite_database",
                "Primary SQLite Database (shorts.db)",
                "Main database storing channels, videos, transcripts, clips, and users.",
                sqlite_db_path,
                "file",
            ),
            (
                "encryption_vault_key",
                "Transparent Encryption Vault Key (.vault.key)",
                "Fernet 256-bit symmetric encryption key protecting stored API tokens.",
                vault_key_path,
                "file",
            ),
            (
                "raw_downloads_dir",
                "Raw Ingested Video Downloads",
                "Local workspace for pristine downloaded source video files from YouTube/local.",
                settings.DOWNLOADS_DIR,
                "directory",
            ),
            (
                "rendered_outputs_dir",
                "Master Rendered Short Outputs",
                "Target folder storing finalized 9:16 vertical MP4 shorts with burned subtitles.",
                settings.OUTPUT_DIR,
                "directory",
            ),
            (
                "temp_scratch_dir",
                "Temporary Scratch & VAD Workdir",
                "Transient scratchpad for audio extraction, VAD chunks, and intermediate masks.",
                settings.TEMP_DIR,
                "directory",
            ),
            (
                "watch_folder_dir",
                "Automated Drop & Watch Folder",
                "Monitored folder for drag-and-drop local video ingestion.",
                watch_folder_path,
                "directory",
            ),
            (
                "fonts_dir",
                "Subtitle Typography & Color Emojis",
                "Bundled fonts (Montserrat, Segoe UI Emoji) for high-impact viral subtitles.",
                fonts_path,
                "directory",
            ),
        ]

        result: Dict[str, Dict[str, Any]] = {}
        for key, name, desc, p_obj, item_type in raw_items:
            p = Path(p_obj)
            exists = p.exists()
            is_dir = p.is_dir() if exists else (item_type == "directory")
            size_mb = 0.0
            file_count = 0

            if exists:
                if is_dir:
                    d_info = _get_dir_size_and_count(p)
                    size_mb = round(d_info["size_bytes"] / (1024 * 1024), 2)
                    file_count = d_info["count"]
                else:
                    try:
                        size_mb = round(p.stat().st_size / (1024 * 1024), 2)
                    except OSError:
                        size_mb = 0.0

            result[key] = {
                "key": key,
                "name": name,
                "label": name,
                "description": desc,
                "path": str(p.resolve() if exists else p),
                "exists": exists,
                "type": item_type,
                "is_dir": is_dir,
                "size_mb": size_mb,
                "file_count": file_count,
            }

        return result

    @staticmethod
    def execute_granular_reset(
        options: Union[ResetOptions, Dict[str, Any]],
        db: Session,
        current_user: Optional[User] = None,
    ) -> Dict[str, Any]:
        """
        Executes selected deletion options while strictly preserving application stability.
        Re-creates any purged storage directories and ensures clean baseline records.
        """
        opts: Dict[str, Any] = options.to_dict() if hasattr(options, "to_dict") else dict(options)

        reclaimed_bytes = 0
        total_files_deleted = 0
        total_db_records_deleted = 0
        actions_taken = []
        deleted_components = []
        details: Dict[str, Any] = {}

        # 1. Purge Raw Downloads
        if opts.get("delete_raw_downloads"):
            bytes_freed, files_freed = _purge_directory_contents(settings.DOWNLOADS_DIR)
            reclaimed_bytes += bytes_freed
            total_files_deleted += files_freed
            deleted_components.append("raw_downloads")
            actions_taken.append(f"Purged raw downloads folder ({_format_size(bytes_freed)} freed, {files_freed} files)")

        # 2. Purge Rendered Shorts
        if opts.get("delete_rendered_outputs"):
            bytes_freed, files_freed = _purge_directory_contents(settings.OUTPUT_DIR)
            reclaimed_bytes += bytes_freed
            total_files_deleted += files_freed
            deleted_components.append("rendered_outputs")
            actions_taken.append(f"Purged rendered outputs folder ({_format_size(bytes_freed)} freed, {files_freed} files)")

        # 3. Purge Temporary Files
        if opts.get("delete_temp_files"):
            bytes_freed, files_freed = _purge_directory_contents(settings.TEMP_DIR)
            reclaimed_bytes += bytes_freed
            total_files_deleted += files_freed
            deleted_components.append("temp_files")
            actions_taken.append(f"Purged temporary scratch cache ({_format_size(bytes_freed)} freed, {files_freed} files)")

        # 4. Database Records Deletion (handled in strict foreign-key order)
        if opts.get("delete_publishing_logs"):
            try:
                cnt1 = db.query(PlatformPublishLog).delete()
                cnt2 = db.query(PublishLog).delete()
                db.commit()
                total_db_records_deleted += (cnt1 + cnt2)
                deleted_components.append("publishing_logs")
                actions_taken.append(f"Cleared {cnt1 + cnt2} publishing and platform upload logs")
            except Exception as e:
                db.rollback()
                logger.error("Failed to clear publishing logs: %s", e)

        if opts.get("delete_clips_and_candidates"):
            try:
                cnt1 = db.query(PlatformPublishLog).delete()
                cnt2 = db.query(PublishLog).delete()
                cnt3 = db.query(Clip).delete()
                cnt4 = db.query(ClipCandidate).delete()
                db.commit()
                total_db_records_deleted += (cnt1 + cnt2 + cnt3 + cnt4)
                deleted_components.append("clips_and_candidates")
                actions_taken.append(f"Cleared {cnt3} clips and {cnt4} candidates")
            except Exception as e:
                db.rollback()
                logger.error("Failed to clear clips and candidates: %s", e)

        if opts.get("delete_pipeline_history"):
            try:
                cnt1 = db.query(PlatformPublishLog).delete()
                cnt2 = db.query(PublishLog).delete()
                cnt3 = db.query(Clip).delete()
                cnt4 = db.query(ClipCandidate).delete()
                cnt5 = db.query(Transcript).delete()
                cnt6 = db.query(SourceVideo).delete()
                db.commit()
                total_db_records_deleted += (cnt1 + cnt2 + cnt3 + cnt4 + cnt5 + cnt6)
                deleted_components.append("pipeline_history")
                actions_taken.append(f"Cleared {cnt6} source videos and {cnt5} transcripts")
            except Exception as e:
                db.rollback()
                logger.error("Failed to clear pipeline history: %s", e)

        if opts.get("delete_channels"):
            try:
                cnt1 = db.query(SourceVideo).delete()
                cnt2 = db.query(Channel).delete()
                db.commit()
                total_db_records_deleted += (cnt1 + cnt2)
                deleted_components.append("channels")
                actions_taken.append(f"Cleared {cnt2} monitored channels")
            except Exception as e:
                db.rollback()
                logger.error("Failed to clear channels: %s", e)

        # 5. Reset Custom Settings to Defaults
        if opts.get("reset_settings"):
            try:
                from shorts_engine.config.dynamic_store import DynamicConfigStore
                from shorts_engine.config.logging_config import reload_runtime_settings
                db.execute(text("DELETE FROM system_settings"))
                db.commit()
                DynamicConfigStore.get_instance().refresh(db)
                reload_runtime_settings()
                deleted_components.append("settings")
                actions_taken.append("Reset dynamic configuration to factory defaults")
            except Exception as e:
                db.rollback()
                logger.error("Failed to reset settings: %s", e)

        # 6. Delete API Keys & Vault
        if opts.get("delete_api_keys"):
            try:
                cnt = db.query(ApiKey).delete()
                db.commit()
                total_db_records_deleted += cnt
                deleted_components.append("api_keys")
                actions_taken.append(f"Cleared {cnt} persisted API keys from vault")
            except Exception as e:
                db.rollback()
                logger.error("Failed to clear API keys: %s", e)

        # 7. User Accounts & Login Handling
        if opts.get("delete_users"):
            preserve_admin = bool(opts.get("preserve_admin", True))
            deleted_components.append("users")
            try:
                if preserve_admin and current_user:
                    cnt = db.query(User).filter(User.id != current_user.id).delete()
                    db.commit()
                    total_db_records_deleted += cnt
                    actions_taken.append(f"Purged other user accounts (preserved active admin '{current_user.username}')")
                else:
                    db.query(User).delete()
                    db.commit()
                    default_user = os.getenv("DASHBOARD_USERNAME", "admin")
                    default_pass = os.getenv("DASHBOARD_PASSWORD", "AdminPass12345!")
                    new_admin = User(
                        username=default_user,
                        password_hash=hash_password(default_pass),
                        role=UserRole.ADMIN,
                        is_active=True,
                    )
                    db.add(new_admin)
                    db.commit()
                    details["reseeding_default_admin"] = True
                    actions_taken.append(f"Reset users table and reseeded default administrator ('{default_user}')")
            except Exception as e:
                db.rollback()
                logger.error("Failed to reset user accounts: %s", e)

        # Ensure all core storage directories exist after any purge
        settings.DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
        settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        settings.TEMP_DIR.mkdir(parents=True, exist_ok=True)
        (settings.STORAGE_DIR / "config").mkdir(parents=True, exist_ok=True)

        total_reclaimed_mb = round(reclaimed_bytes / (1024 * 1024), 2)
        logger.info(
            "Granular reset executed. Actions: %s. Reclaimed: %s (%s MB), DB records: %s",
            actions_taken,
            _format_size(reclaimed_bytes),
            total_reclaimed_mb,
            total_db_records_deleted,
        )

        return {
            "status": "success",
            "reclaimed_bytes": reclaimed_bytes,
            "reclaimed_human": _format_size(reclaimed_bytes),
            "total_reclaimed_mb": total_reclaimed_mb,
            "total_files_deleted": total_files_deleted,
            "total_db_records_deleted": total_db_records_deleted,
            "deleted_components": deleted_components,
            "actions": actions_taken,
            "details": details,
            "timestamp": datetime.utcnow().isoformat(),
        }
