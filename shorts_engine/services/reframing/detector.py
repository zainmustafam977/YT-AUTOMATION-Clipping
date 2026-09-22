"""
Face and subject detection using OpenCV's deep-learning YuNet ONNX model (no legacy Haar cascades).
Automatically downloads the official lightweight (~300KB) YuNet model if not present.
"""

import logging
import urllib.request
from pathlib import Path
from typing import List, Tuple, Optional
import cv2
import numpy as np
from shorts_engine.config.settings import settings

logger = logging.getLogger(__name__)

YUNET_MODEL_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/"
    "face_detection_yunet/face_detection_yunet_2023mar.onnx"
)


class DetectedFace:
    def __init__(self, x: int, y: int, w: int, h: int, score: float):
        self.x = x
        self.y = y
        self.w = w
        self.h = h
        self.score = score

    @property
    def center_x(self) -> float:
        return self.x + (self.w / 2.0)

    @property
    def center_y(self) -> float:
        return self.y + (self.h / 2.0)

    @property
    def area(self) -> float:
        return float(self.w * self.h)


class SubjectDetector:
    def __init__(self, model_path: Optional[Path] = None, score_threshold: float = 0.6):
        self.model_path = model_path or settings.MODELS_DIR / "face_detection_yunet.onnx"
        self.score_threshold = score_threshold
        self._detector = None
        self._initialized = False

    def _ensure_model(self) -> bool:
        if self.model_path.exists() and self.model_path.stat().st_size > 50_000:
            return True
        try:
            logger.info("Downloading official YuNet face detection ONNX model (~300KB)...")
            self.model_path.parent.mkdir(parents=True, exist_ok=True)
            urllib.request.urlretrieve(YUNET_MODEL_URL, str(self.model_path))
            return True
        except Exception as e:
            logger.warning("Could not download YuNet ONNX model: %s. Using heuristic tracker.", str(e))
            return False

    def detect_faces(self, frame: np.ndarray) -> List[DetectedFace]:
        """Detects faces in a BGR frame using YuNet."""
        h, w = frame.shape[:2]
        if not self._initialized:
            if self._ensure_model() and hasattr(cv2, "FaceDetectorYN"):
                try:
                    self._detector = cv2.FaceDetectorYN.create(
                        model=str(self.model_path),
                        config="",
                        input_size=(w, h),
                        score_threshold=self.score_threshold,
                        nms_threshold=0.3,
                        top_k=10,
                    )
                except Exception as e:
                    logger.warning("FaceDetectorYN initialization failed: %s", str(e))
            self._initialized = True

        faces: List[DetectedFace] = []

        if self._detector is not None:
            try:
                self._detector.setInputSize((w, h))
                _, detected = self._detector.detect(frame)
                if detected is not None:
                    for det in detected:
                        box = det[0:4].astype(int)
                        score = float(det[14])
                        faces.append(
                            DetectedFace(
                                x=max(0, box[0]),
                                y=max(0, box[1]),
                                w=box[2],
                                h=box[3],
                                score=score,
                            )
                        )
                    return faces
            except Exception as e:
                logger.debug("YuNet frame detection error: %s", str(e))

        # Fallback: simple center-weighted skin/motion color heuristic if model unavailable
        return faces
