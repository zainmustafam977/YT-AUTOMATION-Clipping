"""
Smoothing filter for 9:16 crop window panning (Kalman / Exponential Moving Average).
Prevents jittery crop shifts while transitioning smoothly when subjects move.
"""

from typing import List, Optional


class CropSmoother:
    def __init__(self, alpha: float = 0.85, max_step_ratio: float = 0.05):
        """
        alpha: weight given to previous smoothed value (0.80 - 0.90 gives cinematic pan).
        max_step_ratio: maximum horizontal jump per frame as fraction of frame width.
        """
        self.alpha = alpha
        self.max_step_ratio = max_step_ratio
        self.current_center_x: Optional[float] = None

    def update(self, target_center_x: float, frame_width: int) -> float:
        """Updates and returns smoothed center_x position."""
        if self.current_center_x is None:
            self.current_center_x = target_center_x
            return self.current_center_x

        max_step = frame_width * self.max_step_ratio
        diff = target_center_x - self.current_center_x
        # Clamp maximum jump
        if abs(diff) > max_step:
            target_center_x = self.current_center_x + (max_step if diff > 0 else -max_step)

        # Exponential moving average
        self.current_center_x = (self.alpha * self.current_center_x) + ((1.0 - self.alpha) * target_center_x)
        return self.current_center_x

    def smooth_trajectory(self, trajectory: List[float], frame_width: int) -> List[float]:
        """Smooths a pre-computed sequence of target X centers."""
        if not trajectory:
            return []
        smoothed = []
        self.current_center_x = None
        for val in trajectory:
            smoothed.append(self.update(val, frame_width))
        return smoothed
