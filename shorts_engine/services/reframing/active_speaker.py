"""
Active speaker selection for multi-speaker footage (podcasts, interviews).
Selects the primary subject based on face size, detection confidence, and movement.
"""

from typing import List, Optional
from shorts_engine.services.reframing.detector import DetectedFace


class ActiveSpeakerSelector:
    def __init__(self, switch_hysteresis: float = 1.25):
        """
        switch_hysteresis: A new candidate must be at least 25% higher in score
        to trigger a camera switch, avoiding rapid ping-pong cuts.
        """
        self.switch_hysteresis = switch_hysteresis
        self.current_speaker: Optional[DetectedFace] = None

    def select_speaker(self, faces: List[DetectedFace], frame_width: int) -> Optional[DetectedFace]:
        if not faces:
            return self.current_speaker

        if len(faces) == 1:
            self.current_speaker = faces[0]
            return faces[0]

        # Score faces based on size (closer to camera) and detection score
        scored_faces = []
        for face in faces:
            # Distance from center preference
            norm_center_dist = abs(face.center_x - (frame_width / 2.0)) / (frame_width / 2.0)
            center_bonus = 1.0 - (0.3 * norm_center_dist)
            score = face.area * face.score * center_bonus
            scored_faces.append((score, face))

        scored_faces.sort(key=lambda x: x[0], reverse=True)
        best_score, best_face = scored_faces[0]

        if self.current_speaker is None:
            self.current_speaker = best_face
            return best_face

        # Check if best exceeds current with hysteresis threshold
        curr_norm_dist = abs(self.current_speaker.center_x - (frame_width / 2.0)) / (frame_width / 2.0)
        curr_score = self.current_speaker.area * self.current_speaker.score * (1.0 - (0.3 * curr_norm_dist))

        if best_score > curr_score * self.switch_hysteresis:
            self.current_speaker = best_face

        return self.current_speaker
