"""
Unit tests for reframing, smoothing, and active speaker selection.
"""

from shorts_engine.services.reframing.detector import DetectedFace
from shorts_engine.services.reframing.smoothing import CropSmoother
from shorts_engine.services.reframing.active_speaker import ActiveSpeakerSelector
from shorts_engine.services.reframing.cropper import VideoReframer, CropPlan


def test_crop_smoother_clamping():
    smoother = CropSmoother(alpha=0.8, max_step_ratio=0.05)
    frame_width = 1920
    
    # First frame initializes
    c1 = smoother.update(960.0, frame_width)
    assert c1 == 960.0

    # Sudden jump of 500px should be clamped by max_step_ratio (1920 * 0.05 = 96px)
    c2 = smoother.update(1500.0, frame_width)
    assert c2 < 960.0 + 100.0


def test_active_speaker_selection_with_hysteresis():
    selector = ActiveSpeakerSelector(switch_hysteresis=1.25)
    
    face_a = DetectedFace(x=500, y=200, w=200, h=200, score=0.9)  # area 40,000
    face_b = DetectedFace(x=1200, y=200, w=150, h=150, score=0.8) # area 22,500

    # Face A is larger and higher score -> selected
    selected = selector.select_speaker([face_a, face_b], frame_width=1920)
    assert selected == face_a

    # Even if face B grows slightly, hysteresis prevents erratic camera switching
    face_b_slightly_bigger = DetectedFace(x=1200, y=200, w=205, h=205, score=0.91)
    selected_after = selector.select_speaker([face_a, face_b_slightly_bigger], frame_width=1920)
    assert selected_after == face_a


def test_crop_plan_aspect_ratio():
    reframer = VideoReframer()
    plan = reframer._fallback_center_crop(src_w=1920, src_h=1080)
    
    # Check 9:16 aspect ratio: crop_w / crop_h should be ~ 9/16 = 0.5625
    ratio = plan.crop_w / plan.crop_h
    assert abs(ratio - (9.0 / 16.0)) < 0.01
    assert plan.crop_x >= 0
    assert plan.crop_x + plan.crop_w <= 1920

    # Check FFmpeg filter syntax
    filter_str = plan.to_ffmpeg_filter()
    assert "crop=" in filter_str
    assert "scale=1080:1920" in filter_str
