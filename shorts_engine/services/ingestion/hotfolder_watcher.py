"""
Automated Hotfolder Watcher Service for Master Video Ingestion (Pillar 4).
Monitors storage/hotfolder/ for incoming master video drops (.mp4, .mov, .mkv, .webm),
verifies byte stability & Win32 non-exclusive file lock release, and automatically
registers them as SourceVideo records via LocalVideoIngestionService.
"""

import os
import time
import shutil
import asyncio
import logging
import threading
from pathlib import Path
from typing import Optional, Dict, Any, List, Set, Union

from shorts_engine.config.settings import settings
from shorts_engine.db.session import SessionLocal
from shorts_engine.services.ingestion.local_loader import LocalVideoIngestionService
from shorts_engine.services.ingestion.watch_folder import is_file_locked
from shorts_engine.services.events.bus import global_event_bus
from shorts_engine.services.events.models import PlatformEvent

logger = logging.getLogger(__name__)


class HotfolderWatcher:
    """
    Automated hotfolder watcher monitoring storage/hotfolder/ for complete video drops.
    """

    SUPPORTED_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}

    def __init__(
        self,
        folder_path: Optional[Union[str, Path]] = None,
        poll_interval: Optional[float] = None,
        stability_window_sec: float = 2.0,
    ):
        raw_path = folder_path or getattr(settings, "HOTFOLDER_DIR", None) or (settings.STORAGE_DIR / "hotfolder")
        self.folder_path = Path(raw_path).resolve()
        self.processed_dir = self.folder_path / "processed"
        self.folder_path.mkdir(parents=True, exist_ok=True)
        self.processed_dir.mkdir(parents=True, exist_ok=True)

        self.poll_interval = poll_interval or getattr(settings, "HOTFOLDER_POLL_INTERVAL_SECONDS", 5.0)
        self.stability_window = max(1.0, stability_window_sec)

        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._thread: Optional[threading.Thread] = None
        self._processed_files: Set[str] = set()

    def get_pending_files(self) -> List[Path]:
        """Scans folder for candidates with supported video extensions, ignoring processed subdir."""
        if not self.folder_path.exists():
            return []

        candidates = []
        for f in self.folder_path.iterdir():
            if f.is_file() and f.suffix.lower() in self.SUPPORTED_EXTENSIONS:
                if f.name not in self._processed_files:
                    candidates.append(f)
        return sorted(candidates, key=lambda p: p.stat().st_mtime)

    def is_file_ready(self, file_path: Path) -> bool:
        """
        Determines whether file drop has completed writing:
        1. File exists and size > 0.
        2. File is not actively locked by another process (OS lock test).
        3. File size remains completely stable across stability_window.
        """
        if not file_path.exists() or not file_path.is_file():
            return False

        try:
            initial_size = file_path.stat().st_size
            if initial_size <= 0:
                return False

            if is_file_locked(file_path):
                logger.debug("Hotfolder candidate %s is locked by another process.", file_path.name)
                return False

            # Byte stability check
            time.sleep(self.stability_window)

            if not file_path.exists():
                return False

            post_size = file_path.stat().st_size
            if post_size != initial_size:
                logger.debug("Hotfolder candidate %s size still changing (%d -> %d).", file_path.name, initial_size, post_size)
                return False

            return True
        except Exception as e:
            logger.debug("Error checking readiness for %s: %s", file_path.name, e)
            return False

    def ingest_candidate(self, file_path: Path) -> Optional[Any]:
        """
        Registers stabilized file drop into database via LocalVideoIngestionService
        and moves file to processed directory.
        """
        logger.info("Hotfolder detected complete master video drop: %s", file_path.name)
        try:
            with SessionLocal() as db:
                source_video = LocalVideoIngestionService.ingest_local_path(
                    db=db,
                    file_path=file_path,
                    title=file_path.stem.replace("_", " ").title(),
                )

                # Move to processed folder to prevent re-ingesting on restart
                dest_path = self.processed_dir / file_path.name
                if dest_path.exists():
                    dest_path = self.processed_dir / f"{int(time.time())}_{file_path.name}"

                try:
                    shutil.move(str(file_path), str(dest_path))
                    # Update SourceVideo downloaded_path to point to canonical processed path
                    source_video.downloaded_path = str(dest_path)
                    db.commit()
                except Exception as move_err:
                    logger.warning("Could not move hotfolder file to processed dir (%s); recording in-memory.", move_err)

                self._processed_files.add(file_path.name)
                logger.info("Successfully ingested hotfolder video %s as ID %s", dest_path.name, source_video.id)

                # Notify EventBus
                try:
                    event = PlatformEvent(
                        category="ingestion",
                        level="SUCCESS",
                        title=f"Hotfolder Auto-Ingest: {source_video.title}",
                        message=f"Auto-ingested master file '{dest_path.name}' ({source_video.duration_sec}s).",
                        entity_id=source_video.id,
                        entity_type="video",
                    )
                    global_event_bus.publish_sync(event)
                except Exception:
                    pass

                return source_video
        except Exception as e:
            logger.error("Failed to ingest hotfolder video %s: %s", file_path.name, e, exc_info=True)
            return None

    def scan_once(self) -> List[Any]:
        """
        Runs a single synchronous scan iteration over the hotfolder.
        Useful for scheduled crons, API triggers, and unit testing.
        """
        ingested = []
        pending = self.get_pending_files()
        for cand in pending:
            if self.is_file_ready(cand):
                res = self.ingest_candidate(cand)
                if res:
                    ingested.append(res)
        return ingested

    async def start(self) -> None:
        """Starts the async background watch loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("HotfolderWatcher started monitoring %s (interval: %.1fs).", self.folder_path, self.poll_interval)

    async def stop(self) -> None:
        """Stops the async background watch loop."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("HotfolderWatcher stopped.")

    async def _run_loop(self) -> None:
        while self._running:
            try:
                # Run synchronous scan in worker thread to prevent blocking event loop during stability sleep
                await asyncio.to_thread(self.scan_once)
            except Exception as e:
                logger.error("Error in hotfolder scan loop: %s", e)
            await asyncio.sleep(self.poll_interval)

    def get_status(self) -> Dict[str, Any]:
        """Returns diagnostic overview of hotfolder status."""
        enabled = getattr(settings, "HOTFOLDER_ENABLED", True)
        pending_count = len(self.get_pending_files())
        processed_count = len(list(self.processed_dir.glob("*.*"))) if self.processed_dir.exists() else 0

        return {
            "enabled": enabled,
            "path": str(self.folder_path),
            "processed_path": str(self.processed_dir),
            "pending_files_count": pending_count,
            "processed_files_count": processed_count,
            "poll_interval_sec": self.poll_interval,
            "supported_extensions": sorted(self.SUPPORTED_EXTENSIONS),
        }


# Global singleton instance
hotfolder_watcher = HotfolderWatcher()
