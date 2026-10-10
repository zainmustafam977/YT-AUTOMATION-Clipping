from unittest.mock import MagicMock, patch
import pytest

from shorts_engine.services.quality_gate.render_inspector import RenderInspector
from shorts_engine.services.reframing.cropper import VideoReframer


def test_render_inspector_releases_cap_on_exception(tmp_path):
    """RenderInspector releases VideoCapture in finally block even when frame processing raises (PLAN-P2-08 / F-32)."""
    dummy_video = tmp_path / "test.mp4"
    dummy_video.write_bytes(b"data")

    inspector = RenderInspector()

    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = True
    mock_cap.get.return_value = 100
    mock_frame = MagicMock()
    mock_frame.shape = (1080, 1920, 3)
    mock_cap.read.return_value = (True, mock_frame)

    # Simulate an error inside frame inspection loop
    with patch("cv2.VideoCapture", return_value=mock_cap), \
         patch.object(inspector.detector, "detect_faces", side_effect=RuntimeError("CV Error")):
        with pytest.raises(RuntimeError, match="CV Error"):
            inspector.inspect_render(dummy_video)

        # Verify cap.release() was called despite the exception
        assert mock_cap.release.called, "cv2.VideoCapture must be released even when an exception occurs"


def test_cropper_releases_cap_on_exception(tmp_path):
    """VideoReframer releases VideoCapture in finally block even when face detection raises (PLAN-P2-08 / F-34)."""
    dummy_video = tmp_path / "source.mp4"
    dummy_video.write_bytes(b"data")

    reframer = VideoReframer()

    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = True
    mock_cap.get.side_effect = lambda prop: 1920 if prop == 3 else (1080 if prop == 4 else 30.0)
    mock_cap.read.return_value = (True, MagicMock())

    with patch("cv2.VideoCapture", return_value=mock_cap), \
         patch.object(reframer.detector, "detect_faces", side_effect=ValueError("Detection Failure")):
        with pytest.raises(ValueError, match="Detection Failure"):
            reframer.compute_crop_plan(dummy_video, start_ms=0, end_ms=5000)

        assert mock_cap.release.called, "cv2.VideoCapture must be released in finally block"
