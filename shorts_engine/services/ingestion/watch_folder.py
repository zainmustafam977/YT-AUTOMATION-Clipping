"""
Watched Folder Daemon for Automated Video Ingestion.
Monitors storage/watch_folder/ for incoming video drops with Win32 non-exclusive
file-lock detection, multi-second byte-stability verification, and real-time EventBus alerts.
"""

import os
import sys
import time
import logging
import threading
from pathlib import Path
from typing import Optional, Callable, Dict, Any, Set, Union, List

from shorts_engine.config.settings import settings
from shorts_engine.db.session import SessionLocal
from shorts_engine.services.events.bus import global_event_bus
from shorts_engine.services.events.models import PlatformEvent
from shorts_engine.services.ingestion.local_loader import LocalVideoIngestionService

logger = logging.getLogger(__name__)

# Try importing watchdog, fall back to pure polling if absent
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    HAS_WATCHDOG = True
except ImportError:
    Observer = None
    FileSystemEventHandler = object
    HAS_WATCHDOG = False


def is_file_locked(file_path: Union[str, Path]) -> bool:
    """
    Checks whether a file on disk is currently locked by another process
    (such as an active copy, download, or write handle).
    Uses native Win32 CreateFileW on Windows, falling back to exclusive open on POSIX.
    Properly handles 64-bit Win32 handles and closed read-only files.
    """
    p = Path(file_path)
    if not p.exists() or not p.is_file():
        return True

    # Win32 Native Check
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes
            kernel32 = ctypes.windll.kernel32

            kernel32.CreateFileW.argtypes = [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.LPVOID,
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.HANDLE,
            ]
            kernel32.CreateFileW.restype = wintypes.HANDLE
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL

            GENERIC_READ = 0x80000000
            GENERIC_WRITE = 0x40000000
            FILE_SHARE_READ = 0x00000001
            FILE_SHARE_WRITE = 0x00000002
            OPEN_EXISTING = 3
            FILE_ATTRIBUTE_NORMAL = 0x80
            INVALID_HANDLE = wintypes.HANDLE(-1).value
            INVALID_HANDLES = (INVALID_HANDLE, -1, 0, None)

            norm_path = os.path.normpath(str(p.resolve()))

            # Primary: probe write access to detect active copy/download processes
            handle = kernel32.CreateFileW(
                norm_path,
                GENERIC_READ | GENERIC_WRITE,
                FILE_SHARE_READ,
                None,
                OPEN_EXISTING,
                FILE_ATTRIBUTE_NORMAL,
                None,
            )

            if handle not in INVALID_HANDLES:
                kernel32.CloseHandle(handle)
                return False

            err = kernel32.GetLastError()
            # 32 = ERROR_SHARING_VIOLATION, 33 = ERROR_LOCK_VIOLATION
            if err in (32, 33):
                return True

            # If ERROR_ACCESS_DENIED (5), the file may be read-only on disk rather than actively written
            if err == 5:
                # Probe exclusive read: if another process has exclusive lock or write lock, this fails with sharing violation
                ro_handle = kernel32.CreateFileW(
                    norm_path,
                    GENERIC_READ,
                    0,  # Exclusive read check
                    None,
                    OPEN_EXISTING,
                    FILE_ATTRIBUTE_NORMAL,
                    None,
                )
                if ro_handle not in INVALID_HANDLES:
                    kernel32.CloseHandle(ro_handle)
                    return False

                ro_err = kernel32.GetLastError()
                if ro_err in (32, 33):
                    return True

                # Secondary read check with shared read/write
                shared_ro = kernel32.CreateFileW(
                    norm_path,
                    GENERIC_READ,
                    FILE_SHARE_READ | FILE_SHARE_WRITE,
                    None,
                    OPEN_EXISTING,
                    FILE_ATTRIBUTE_NORMAL,
                    None,
                )
                if shared_ro not in INVALID_HANDLES:
                    kernel32.CloseHandle(shared_ro)
                    return False

            return True
        except Exception as e:
            logger.debug("Win32 lock check fallback on %s: %s", p.name, e)

    # Standard POSIX / General Fallback
    try:
        with open(p, "r+b"):
            return False
    except PermissionError:
        # Check if file is read-only but closed
        try:
            with open(p, "rb"):
                return False
        except (PermissionError, OSError):
            return True
    except OSError:
        return True


class _WatchFolderEventHandler(FileSystemEventHandler):
    """Event handler forwarding filesystem creations to parent daemon's pending registry."""

    def __init__(self, daemon: "WatchFolderDaemon"):
        super().__init__()
        self.daemon = daemon

    def on_created(self, event):
        if not event.is_directory:
            self.daemon.register_candidate(event.src_path)

    def on_modified(self, event):
        if not event.is_directory:
            self.daemon.register_candidate(event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            self.daemon.register_candidate(event.dest_path)


class WatchFolderDaemon:
    """
    Watches storage/watch_folder/ directory for video drops.
    Guarantees stability by ensuring:
    1. File is non-empty (>0 bytes).
    2. File is not held by another process via Win32 lock check.
    3. File size remains constant for at least `stable_time_sec`.
    """

    SUPPORTED_EXTENSIONS = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v"}

    def __init__(
        self,
        watch_dir: Optional[Union[str, Path]] = None,
        callback: Optional[Callable[[Path], Any]] = None,
        stable_time_sec: float = 3.0,
        poll_interval_sec: float = 1.0,
        auto_ingest_db: bool = True,
        session_factory: Optional[Callable[[], Any]] = None,
    ):
        self.watch_dir = Path(watch_dir or Path(settings.STORAGE_DIR) / "watch_folder").resolve()
        self.watch_dir.mkdir(parents=True, exist_ok=True)
        self.callback = callback
        self.stable_time_sec = max(0.001, float(stable_time_sec))
        self.poll_interval_sec = max(0.001, float(poll_interval_sec))
        self.auto_ingest_db = auto_ingest_db
        self.session_factory = session_factory

        self._pending_files: Dict[str, Dict[str, Any]] = {}
        self._processed_files: Set[str] = set()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._poller_thread: Optional[threading.Thread] = None
        self._observer: Optional[Any] = None

    @property
    def is_running(self) -> bool:
        return not self._stop_event.is_set() and (
            (self._poller_thread is not None and self._poller_thread.is_alive())
            or (self._observer is not None and getattr(self._observer, "is_alive", lambda: False)())
        )

    def register_candidate(self, path_str: str) -> None:
        """Registers a candidate file path into the pending stabilization registry."""
        p = Path(path_str)
        if p.suffix.lower() not in self.SUPPORTED_EXTENSIONS:
            return

        with self._lock:
            canonical = str(p.resolve())
            if canonical in self._processed_files:
                return
            if canonical not in self._pending_files:
                self._pending_files[canonical] = {
                    "first_seen": time.monotonic(),
                    "last_size": -1,
                    "last_checked": time.monotonic(),
                }

    def scan_directory_candidates(self) -> None:
        """Discovers existing video files in watch_dir that may not have triggered an event."""
        if not self.watch_dir.exists():
            return

        try:
            for item in self.watch_dir.iterdir():
                if item.is_file() and item.suffix.lower() in self.SUPPORTED_EXTENSIONS:
                    self.register_candidate(str(item))
        except Exception as e:
            logger.debug("Failed scanning watch folder: %s", e)

    def poll_and_process_stable_files(self) -> List[Path]:
        """
        Evaluates pending files. If unlocked and byte size has remained constant
        for at least `stable_time_sec`, dispatches to processing callback or auto-ingests.
        Returns the list of processed Path objects.
        """
        now = time.monotonic()
        ready_paths: List[Path] = []

        # Prune deleted files from _processed_files so re-dropping files works cleanly
        with self._lock:
            missing_processed = [f for f in self._processed_files if not Path(f).exists()]
            for m in missing_processed:
                self._processed_files.remove(m)
            pending_keys = list(self._pending_files.keys())

        for path_str in pending_keys:
            p = Path(path_str)
            if not p.exists() or not p.is_file():
                with self._lock:
                    self._pending_files.pop(path_str, None)
                continue

            try:
                curr_size = p.stat().st_size
            except OSError:
                continue

            if curr_size <= 0:
                # File is empty (still creating or truncated)
                continue

            # Win32 file-lock check
            if is_file_locked(p):
                # Another process holds an active write lock: keep resetting stabilization window
                with self._lock:
                    if path_str in self._pending_files:
                        self._pending_files[path_str]["last_size"] = curr_size
                        self._pending_files[path_str]["first_seen"] = now
                        self._pending_files[path_str]["last_checked"] = now
                continue

            with self._lock:
                info = self._pending_files.get(path_str)
                if not info:
                    continue

                if curr_size == info["last_size"]:
                    elapsed = now - info["first_seen"]
                    if elapsed >= self.stable_time_sec:
                        ready_paths.append(p)
                        self._pending_files.pop(path_str, None)
                        self._processed_files.add(path_str)
                else:
                    # Size changed, reset stabilization window
                    info["last_size"] = curr_size
                    info["first_seen"] = now
                    info["last_checked"] = now

        # Dispatch ready files
        for r_path in ready_paths:
            self._handle_ready_file(r_path)

        return ready_paths

    def _handle_ready_file(self, file_path: Path) -> None:
        """Dispatches an unlocked, stable file to callback and database ingestion."""
        logger.info("Watch folder stabilized file ready: %s", file_path.name)

        try:
            event = PlatformEvent(
                category="ingestion",
                level="INFO",
                title=f"Watch Folder Video Detected: {file_path.name}",
                message=f"File '{file_path.name}' is fully written and unlocked. Initiating ingestion.",
            )
            global_event_bus.publish_sync(event)
        except Exception:
            pass

        # 1. Custom callback
        if self.callback:
            try:
                self.callback(file_path)
            except Exception as e:
                logger.error("Callback failed for watch folder file %s: %s", file_path.name, e, exc_info=True)

        # 2. Database Ingestion & Queue Producer
        if self.auto_ingest_db and not self.callback:
            try:
                session_maker = self.session_factory or SessionLocal
                with session_maker() as db:
                    video = LocalVideoIngestionService.ingest_local_path(
                        db=db,
                        file_path=file_path,
                        title=file_path.stem,
                    )
                    from shorts_engine.services.queue.durable_queue import DurableJobQueue
                    queue = DurableJobQueue(session_factory=session_maker)
                    queue.enqueue(
                        job_type="ingest",
                        entity_id=video.youtube_video_id,
                        entity_type="source_video",
                        stage="ingest",
                        payload={"file_path": str(file_path)},
                        db=db,
                    )
            except Exception as e:
                logger.error("Auto-ingestion failed for watch folder file %s: %s", file_path.name, e, exc_info=True)

    def _poller_loop(self) -> None:
        """Continuous polling background thread loop."""
        while not self._stop_event.is_set():
            try:
                self.scan_directory_candidates()
                self.poll_and_process_stable_files()
            except Exception as e:
                logger.debug("Error in watch folder poller loop: %s", e)
            self._stop_event.wait(timeout=self.poll_interval_sec)

    def start(self) -> None:
        """Starts the watch folder observer and polling daemon."""
        self._stop_event.clear()

        # Start watchdog observer if available
        if HAS_WATCHDOG and Observer is not None:
            try:
                self._observer = Observer()
                handler = _WatchFolderEventHandler(self)
                self._observer.schedule(handler, str(self.watch_dir), recursive=False)
                self._observer.start()
                logger.info("Watchdog filesystem observer started on %s", self.watch_dir)
            except Exception as e:
                logger.warning("Failed to start watchdog observer on %s: %s. Relying on polling thread.", self.watch_dir, e)
                self._observer = None

        # Start polling stability worker thread
        self._poller_thread = threading.Thread(
            target=self._poller_loop,
            name="WatchFolderDaemonThread",
            daemon=True,
        )
        self._poller_thread.start()
        logger.info("WatchFolderDaemon background thread started.")

    def stop(self, timeout: float = 2.0) -> None:
        """Stops the watch folder daemon and waits for threads to terminate."""
        self._stop_event.set()

        if self._observer:
            try:
                self._observer.stop()
                self._observer.join(timeout=timeout)
            except Exception:
                pass
            self._observer = None

        if self._poller_thread and self._poller_thread.is_alive():
            self._poller_thread.join(timeout=timeout)
            self._poller_thread = None

        logger.info("WatchFolderDaemon stopped cleanly.")

    def __enter__(self) -> "WatchFolderDaemon":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()
