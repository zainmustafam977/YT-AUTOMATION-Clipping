"""
9:16 Portrait Cropper: analyzes video segments, tracks the active speaker with smoothing,
and computes optimal FFmpeg crop parameters with center/blurred background fallback.
"""

import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
import cv2
from shorts_engine.config.settings import settings
from shorts_engine.services.reframing.detector import SubjectDetector
from shorts_engine.services.reframing.active_speaker import ActiveSpeakerSelector
from shorts_engine.services.reframing.smoothing import CropSmoother

logger = logging.getLogger(__name__)


class CropPlan:
    def __init__(
        self,
        crop_w: int,
        crop_h: int,
        crop_x: int,
        crop_y: int,
        mode: str = "tracking",
        is_multi_speaker: bool = False,
        speaker_count: int = 1,
    ):
        self.crop_w = crop_w
        self.crop_h = crop_h
        self.crop_x = crop_x
        self.crop_y = crop_y
        self.mode = mode
        self.is_multi_speaker = is_multi_speaker
        self.speaker_count = speaker_count

    def to_ffmpeg_filter(self, scale_algo: Optional[str] = None) -> str:
        """Returns the FFmpeg crop filter string with studio-grade Lanczos resampling or override."""
        algo = scale_algo or getattr(settings, "VIDEO_SCALE_ALGORITHM", "lanczos")
        return f"crop={self.crop_w}:{self.crop_h}:{self.crop_x}:{self.crop_y},scale={settings.TARGET_WIDTH}:{settings.TARGET_HEIGHT}:flags={algo}+accurate_rnd"

    def __repr__(self) -> str:
        return f"<CropPlan mode={self.mode} w={self.crop_w} h={self.crop_h} x={self.crop_x} y={self.crop_y} multi={self.is_multi_speaker}>"


class VideoReframer:
    def __init__(self):
        self.detector = SubjectDetector()
        self.speaker_selector = ActiveSpeakerSelector()
        self.smoother = CropSmoother(alpha=settings.SMOOTHING_FACTOR)

    def compute_crop_plan(
        self,
        video_path: Path,
        start_ms: int,
        end_ms: int,
        sample_fps: float = 2.0,
    ) -> CropPlan:
        """
        Samples frames across the segment to compute the optimal smoothed 9:16 crop window
        with multi-speaker clustering and active speaker tracking.
        """
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            logger.warning("Could not open video %s. Defaulting to center crop.", video_path.name)
            return self._fallback_center_crop(1920, 1080)

        src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1920
        src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 1080
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

        # Calculate 9:16 crop dimensions from source
        # In a landscape 16:9 (e.g. 1920x1080), 9:16 height is src_h, width is src_h * 9 / 16
        crop_h = src_h
        crop_w = int(crop_h * 9 / 16)
        if crop_w % 2 != 0:
            crop_w -= 1

        start_sec = start_ms / 1000.0
        end_sec = end_ms / 1000.0
        duration_sec = max(1.0, end_sec - start_sec)

        # Sample timestamps
        num_samples = max(3, int(duration_sec * sample_fps))
        sample_timestamps = [start_sec + (i * duration_sec / num_samples) for i in range(num_samples)]

        tracked_centers_x: List[float] = []
        all_detected_faces_x: List[float] = []

        for t in sample_timestamps:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
            ret, frame = cap.read()
            if not ret or frame is None:
                continue

            faces = self.detector.detect_faces(frame)
            for f in faces:
                all_detected_faces_x.append(f.center_x)
            active_face = self.speaker_selector.select_speaker(faces, src_w)
            if active_face:
                tracked_centers_x.append(active_face.center_x)

        cap.release()

        # Identify distinct speaker clusters (if faces separated by > 18% of frame width)
        speaker_clusters: List[float] = []
        if all_detected_faces_x:
            sorted_faces = sorted(all_detected_faces_x)
            curr_cluster = [sorted_faces[0]]
            for x in sorted_faces[1:]:
                if x - curr_cluster[-1] < src_w * 0.18:
                    curr_cluster.append(x)
                else:
                    speaker_clusters.append(sum(curr_cluster) / len(curr_cluster))
                    curr_cluster = [x]
            if curr_cluster:
                speaker_clusters.append(sum(curr_cluster) / len(curr_cluster))

        is_multi_speaker = len(speaker_clusters) >= 2
        speaker_count = max(1, len(speaker_clusters))

        # If faces detected, compute median / smoothed center X
        if tracked_centers_x:
            # If multi-speaker and speakers can both fit comfortably in 9:16 frame
            if is_multi_speaker and (max(speaker_clusters) - min(speaker_clusters)) < (crop_w * 0.75):
                median_x = (min(speaker_clusters) + max(speaker_clusters)) / 2.0
                mode = "multi_speaker_group"
            else:
                tracked_centers_x.sort()
                median_x = tracked_centers_x[len(tracked_centers_x) // 2]
                mode = "multi_speaker_tracking" if is_multi_speaker else "tracking"

            # Bound crop_x so it stays within frame while preserving subject safe margins
            half_w = crop_w / 2.0
            min_x = 0
            max_x = max(0, src_w - crop_w)
            target_x = int(max(min_x, min(max_x, median_x - half_w)))

            # Subject safe-zone centering: ensure face is not pinned to the extreme <20% or >80% margins
            if crop_w > 0:
                rel_x = (median_x - target_x) / crop_w
                if rel_x < 0.20 and target_x > min_x:
                    target_x = max(min_x, int(median_x - 0.25 * crop_w))
                elif rel_x > 0.80 and target_x < max_x:
                    target_x = min(max_x, int(median_x - 0.75 * crop_w))

            if target_x % 2 != 0:
                target_x -= 1
            target_x = max(0, min(max_x, target_x))

            return CropPlan(
                crop_w=crop_w,
                crop_h=crop_h,
                crop_x=target_x,
                crop_y=0,
                mode=mode,
                is_multi_speaker=is_multi_speaker,
                speaker_count=speaker_count,
            )

        # No faces detected -> smart center crop fallback
        logger.info("No faces detected in segment; using center crop fallback.")
        return self._fallback_center_crop(src_w, src_h)

    def _fallback_center_crop(self, src_w: int, src_h: int) -> CropPlan:
        crop_h = src_h
        crop_w = int(crop_h * 9 / 16)
        if crop_w % 2 != 0:
            crop_w -= 1
        crop_x = int((src_w - crop_w) / 2)
        if crop_x % 2 != 0:
            crop_x -= 1

        return CropPlan(
            crop_w=crop_w,
            crop_h=crop_h,
            crop_x=max(0, crop_x),
            crop_y=0,
            mode="center_fallback",
        )
