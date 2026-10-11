"""
9:16 Portrait Cropper: analyzes video segments, tracks the active speaker with smoothing,
and computes optimal FFmpeg crop parameters with center/blurred background fallback.
"""

import logging
from pathlib import Path
from typing import List, Optional
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
        layout_mode: str = "single",
        split_crops: Optional[List[dict]] = None,
        divider_width: int = 4,
    ):
        self.crop_w = crop_w
        self.crop_h = crop_h
        self.crop_x = crop_x
        self.crop_y = crop_y
        self.mode = mode
        self.is_multi_speaker = is_multi_speaker
        self.speaker_count = speaker_count
        self.layout_mode = layout_mode
        self.split_crops = split_crops or []
        self.divider_width = divider_width

    @property
    def is_split_screen(self) -> bool:
        return self.layout_mode == "split_screen" and len(self.split_crops) >= 2

    def to_ffmpeg_filter(self, scale_algo: Optional[str] = None) -> str:
        """Returns the FFmpeg crop filter string with studio-grade Lanczos resampling or override."""
        algo = scale_algo or getattr(settings, "VIDEO_SCALE_ALGORITHM", "lanczos")
        if self.is_split_screen:
            top = self.split_crops[0]
            bot = self.split_crops[1]
            tw, th = settings.TARGET_WIDTH, settings.TARGET_HEIGHT // 2
            div_clause = f",drawbox=y={th - self.divider_width // 2}:h={self.divider_width}:color=black@0.8:t=fill" if self.divider_width > 0 else ""
            return (
                f"split [top_in][bot_in]; "
                f"[top_in] crop={top['crop_w']}:{top['crop_h']}:{top['crop_x']}:{top['crop_y']},"
                f"scale={tw}:{th}:flags={algo}+accurate_rnd [top_out]; "
                f"[bot_in] crop={bot['crop_w']}:{bot['crop_h']}:{bot['crop_x']}:{bot['crop_y']},"
                f"scale={tw}:{th}:flags={algo}+accurate_rnd [bot_out]; "
                f"[top_out][bot_out] vstack{div_clause},setsar=1"
            )
        return f"crop={self.crop_w}:{self.crop_h}:{self.crop_x}:{self.crop_y},scale={settings.TARGET_WIDTH}:{settings.TARGET_HEIGHT}:flags={algo}+accurate_rnd"

    def __repr__(self) -> str:
        return f"<CropPlan mode={self.mode} layout={self.layout_mode} w={self.crop_w} h={self.crop_h} x={self.crop_x} y={self.crop_y} multi={self.is_multi_speaker}>"


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
        layout_preference: str = "auto",
    ) -> CropPlan:
        """
        Samples frames across the segment to compute the optimal smoothed 9:16 crop window
        with multi-speaker clustering, active speaker tracking, and dynamic split-screen layout.
        """
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            if not Path(video_path).exists():
                raise FileNotFoundError(f"Source video file does not exist: {video_path}")
            logger.warning("Could not open video %s. Defaulting to center crop.", video_path.name)
            return self._fallback_center_crop(1920, 1080)

        src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1920
        src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 1080
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

        try:
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
        finally:
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

        # Check for split-screen layout condition (podcast/interview two-speaker wide setup)
        should_split_screen = False
        if layout_preference == "split_screen" and len(speaker_clusters) >= 2:
            should_split_screen = True
        elif layout_preference == "auto" and is_multi_speaker and len(speaker_clusters) >= 2:
            cluster_spread = max(speaker_clusters) - min(speaker_clusters)
            if cluster_spread >= (crop_w * 0.75):
                should_split_screen = True

        # If faces detected, compute median / smoothed center X
        if tracked_centers_x:
            tracked_centers_x.sort()
            median_x = tracked_centers_x[len(tracked_centers_x) // 2]

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

            if should_split_screen:
                # 9:8 portrait half-tiles (1080x960 each)
                tile_h = src_h
                tile_w = int(tile_h * 9 / 8)
                if tile_w > src_w:
                    tile_w = src_w
                    tile_h = int(tile_w * 8 / 9)
                if tile_w % 2 != 0:
                    tile_w -= 1
                if tile_h % 2 != 0:
                    tile_h -= 1

                c1 = speaker_clusters[0]
                c2 = speaker_clusters[-1]
                half_tw = tile_w / 2.0

                x1 = int(max(0, min(src_w - tile_w, c1 - half_tw)))
                if tile_w > 0:
                    rel1 = (c1 - x1) / tile_w
                    if rel1 < 0.20 and x1 > 0:
                        x1 = max(0, int(c1 - 0.25 * tile_w))
                    elif rel1 > 0.80 and x1 < (src_w - tile_w):
                        x1 = min(src_w - tile_w, int(c1 - 0.75 * tile_w))
                if x1 % 2 != 0:
                    x1 -= 1
                x1 = max(0, min(src_w - tile_w, x1))

                x2 = int(max(0, min(src_w - tile_w, c2 - half_tw)))
                if tile_w > 0:
                    rel2 = (c2 - x2) / tile_w
                    if rel2 < 0.20 and x2 > 0:
                        x2 = max(0, int(c2 - 0.25 * tile_w))
                    elif rel2 > 0.80 and x2 < (src_w - tile_w):
                        x2 = min(src_w - tile_w, int(c2 - 0.75 * tile_w))
                if x2 % 2 != 0:
                    x2 -= 1
                x2 = max(0, min(src_w - tile_w, x2))

                return CropPlan(
                    crop_w=crop_w,
                    crop_h=crop_h,
                    crop_x=target_x,
                    crop_y=0,
                    mode="split_screen",
                    is_multi_speaker=True,
                    speaker_count=speaker_count,
                    layout_mode="split_screen",
                    split_crops=[
                        {"crop_w": tile_w, "crop_h": tile_h, "crop_x": x1, "crop_y": 0},
                        {"crop_w": tile_w, "crop_h": tile_h, "crop_x": x2, "crop_y": 0},
                    ],
                )

            # If multi-speaker and speakers can both fit comfortably in 9:16 frame
            if is_multi_speaker and (max(speaker_clusters) - min(speaker_clusters)) < (crop_w * 0.75):
                median_x = (min(speaker_clusters) + max(speaker_clusters)) / 2.0
                mode = "multi_speaker_group"
                target_x = int(max(min_x, min(max_x, median_x - half_w)))
                if target_x % 2 != 0:
                    target_x -= 1
                target_x = max(0, min(max_x, target_x))
            else:
                mode = "multi_speaker_tracking" if is_multi_speaker else "tracking"

            return CropPlan(
                crop_w=crop_w,
                crop_h=crop_h,
                crop_x=target_x,
                crop_y=0,
                mode=mode,
                is_multi_speaker=is_multi_speaker,
                speaker_count=speaker_count,
                layout_mode="single",
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
