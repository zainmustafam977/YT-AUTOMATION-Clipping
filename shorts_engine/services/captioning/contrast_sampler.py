"""
Background luminance sampler: analyzes video frames in the caption bounding area
to dynamically adjust text outline, shadow, and opacity for maximum legibility.
"""

import logging
from typing import Tuple, Optional
import cv2
import numpy as np

logger = logging.getLogger(__name__)


class ContrastSampler:
    """
    Samples luminance in the lower third (caption region) of frames.
    Determines if extra shadow or darker outline is required for ultra-bright footage.
    """

    def sample_background_luminance(
        self,
        frame: np.ndarray,
        caption_box_norm: Tuple[float, float, float, float] = (0.1, 0.65, 0.9, 0.85),
    ) -> float:
        """
        Takes a BGR frame, crops the caption area (x1, y1, x2, y2 as normalized 0.0-1.0),
        converts to grayscale, and returns mean luminance (0.0 to 255.0).
        """
        h, w = frame.shape[:2]
        x1 = int(caption_box_norm[0] * w)
        y1 = int(caption_box_norm[1] * h)
        x2 = int(caption_box_norm[2] * w)
        y2 = int(caption_box_norm[3] * h)

        caption_roi = frame[y1:y2, x1:x2]
        if caption_roi.size == 0:
            return 128.0

        gray = cv2.cvtColor(caption_roi, cv2.COLOR_BGR2GRAY)
        mean_luminance = float(np.mean(gray))
        return mean_luminance

    def compute_style_adjustments(self, mean_luminance: float) -> dict:
        """
        Returns ASS style parameter adjustments based on background brightness.
        """
        # If very bright background (mean > 170), thicken outline & add heavy drop shadow
        if mean_luminance > 170.0:
            return {
                "outline_width": 4.0,
                "shadow_depth": 3.0,
                "outline_color": "&H00000000",  # Pure solid black
                "back_color": "&H80000000",     # 50% semi-transparent black backing box
            }
        # If very dark background (mean < 60)
        elif mean_luminance < 60.0:
            return {
                "outline_width": 2.5,
                "shadow_depth": 1.5,
                "outline_color": "&H00000000",
                "back_color": "&H00000000",
            }
        # Standard balanced background
        return {
            "outline_width": 3.0,
            "shadow_depth": 2.0,
            "outline_color": "&H00000000",
            "back_color": "&H00000000",
        }
