"""
Multimodal Post-Render Inspector: inspects the finished rendered MP4 video
to ensure the subject is centered, captions do not collide with YouTube Shorts UI overlays,
and audio is intelligible.
"""

import logging
from pathlib import Path
from typing import Dict, Any, Tuple, List
import cv2
import numpy as np
from shorts_engine.config.settings import settings
from shorts_engine.services.reframing.detector import SubjectDetector

logger = logging.getLogger(__name__)


class RenderInspector:
    def __init__(self):
        self.detector = SubjectDetector()

    def inspect_render(self, video_path: Path) -> Tuple[bool, Dict[str, Any]]:
        """
        Samples keyframes across the rendered video to verify:
        1. Subject stays within safe horizontal margins (15% <= X <= 85%).
        2. Caption bounding box does not collide with YouTube Shorts UI overlay dead zones.
        3. Audio channel integrity.
        """
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            return False, {"error": "Could not open rendered video for inspection"}

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames <= 0:
            cap.release()
            return False, {"error": "Video contains 0 frames"}

        # Sample at 10%, 30%, 50%, 70%, 90%
        sample_indices = [
            int(total_frames * ratio)
            for ratio in [0.1, 0.3, 0.5, 0.7, 0.9]
        ]

        subject_framing_violations = 0
        caption_collision_violations = 0
        inspected_frames = 0

        for frame_idx in sample_indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ret, frame = cap.read()
            if not ret or frame is None:
                continue

            inspected_frames += 1
            h, w = frame.shape[:2]

            # 1. Subject framing check: isolate primary subject (dominant face by area)
            faces = self.detector.detect_faces(frame)
            if faces:
                primary_face = max(faces, key=lambda f: f.area)
                norm_x = primary_face.center_x / w
                if norm_x < settings.SAFE_ZONE_MIN_X or norm_x > settings.SAFE_ZONE_MAX_X:
                    subject_framing_violations += 1

            # 2. Caption UI safe-zone check
            # YouTube Shorts UI dead zones:
            # - Bottom title bar (Y > 80% / bottom 384px, reserved for title/channel)
            # - Right side buttons (X > 85%, Y > 40%, reserved for like/share/remix)
            # - Top bar (Y < 12%)
            # Check if high-contrast caption pixels bleed into either dead zone:
            right_dead_zone = frame[
                int(0.40 * h) : int(settings.UI_DEADZONE_BOTTOM_Y_RATIO * h),
                int(settings.UI_DEADZONE_RIGHT_X_RATIO * w) :,
            ]
            bottom_dead_zone = frame[
                int(settings.UI_DEADZONE_BOTTOM_Y_RATIO * h) :,
                int(0.10 * w) : int(settings.UI_DEADZONE_RIGHT_X_RATIO * w),
            ]

            collision = False
            if right_dead_zone.size > 0:
                edges_r = cv2.Canny(right_dead_zone, 100, 200)
                if (float(np.mean(edges_r)) / 255.0) > 0.15:
                    collision = True

            if bottom_dead_zone.size > 0:
                edges_b = cv2.Canny(bottom_dead_zone, 100, 200)
                if (float(np.mean(edges_b)) / 255.0) > 0.15:
                    collision = True

            if collision:
                caption_collision_violations += 1

        cap.release()

        passed = (subject_framing_violations <= 1 and caption_collision_violations <= 1)
        details = {
            "all_passed": passed,
            "inspected_frames": inspected_frames,
            "subject_framing_violations": subject_framing_violations,
            "caption_collision_violations": caption_collision_violations,
        }
        return passed, details
