"""
Centralized Real-Time Job Progress Tracker.
Tracks live, granular progress (percentage, speed, ETA, and stage details) across all pipeline stages:
- DOWNLOADING: yt-dlp hook (bytes, speed, ETA)
- TRANSCRIBING: Whisper timestamp chunks vs total audio duration
- ANALYZING: LLM viral hook detection and scoring
- REFRAMING: MediaPipe / OpenCV face detection and portrait cropping frame-by-frame
- RENDERING: FFmpeg ASS subtitle burning & compositing across clip candidates
"""

import time
import threading
from typing import Dict, Any, Optional


class JobProgressTracker:
    def __init__(self):
        self._lock = threading.Lock()
        self._progress: Dict[str, Dict[str, Any]] = {}

    def update_progress(
        self,
        video_id: str,
        stage: str,
        percent: float,
        detail: str,
        speed: Optional[str] = None,
        eta_seconds: Optional[float] = None,
    ) -> None:
        """Updates live progress metrics for a video ID."""
        if not video_id:
            return
        clamped_pct = max(0.0, min(100.0, round(float(percent), 1)))
        now = time.time()
        with self._lock:
            self._progress[video_id] = {
                "video_id": video_id,
                "stage": stage.upper(),
                "percent": clamped_pct,
                "detail": detail,
                "speed": speed,
                "eta_seconds": eta_seconds,
                "updated_at": now,
            }

    def get_progress(self, video_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves current progress dictionary for a video ID."""
        if not video_id:
            return None
        with self._lock:
            entry = self._progress.get(video_id)
            if entry:
                return dict(entry)
            return None

    def clear_progress(self, video_id: str) -> None:
        """Cleans up progress tracking entry for completed or failed video."""
        if not video_id:
            return
        with self._lock:
            self._progress.pop(video_id, None)

    def get_all_active_progress(self) -> Dict[str, Dict[str, Any]]:
        """Returns a snapshot of all active progress trackers."""
        with self._lock:
            return {k: dict(v) for k, v in self._progress.items()}


# Global singleton instance
job_progress_tracker = JobProgressTracker()
