"""
Integration tests for FFmpeg video compositor and render validation.
"""

import subprocess
from pathlib import Path
from shorts_engine.services.rendering.compositor import VideoCompositor, escape_ffmpeg_filter_path
from shorts_engine.services.reframing.cropper import CropPlan
from shorts_engine.services.captioning.ass_generator import ASSSubtitleGenerator
from shorts_engine.services.transcription.formatter import WordSegment


def test_escape_ffmpeg_filter_path():
    path = Path("C:/videos/my_video.ass")
    escaped = escape_ffmpeg_filter_path(path)
    assert "\\:" in escaped
    assert "\\" not in escaped.replace("\\:", "")


def test_render_synthetic_short_and_validate(tmp_path):
    # 1. Create a 2-second 1920x1080 synthetic test video with audio using FFmpeg
    raw_video = tmp_path / "raw_synthetic.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000",
        "-t", "2.0",
        "-c:v", "libx264", "-c:a", "aac",
        str(raw_video)
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert raw_video.exists()

    # 2. Create sample ASS subtitle
    ass_path = tmp_path / "test_subs.ass"
    words = [
        WordSegment("Testing", 0.1, 0.5),
        WordSegment("Shorts", 0.6, 1.2),
    ]
    gen = ASSSubtitleGenerator()
    gen.generate_ass(words, segment_start_ms=0, segment_end_ms=2000, output_path=ass_path)

    # 3. Render 9:16 Short
    compositor = VideoCompositor(output_dir=tmp_path)
    crop_plan = CropPlan(crop_w=607, crop_h=1080, crop_x=656, crop_y=0)

    rendered = compositor.render_short(
        source_video_path=raw_video,
        start_ms=0,
        end_ms=2000,
        crop_plan=crop_plan,
        subtitle_ass_path=ass_path,
        output_filename="synthetic_short.mp4",
        normalize_audio=True,
    )
    assert rendered.exists()
    assert rendered.stat().st_size > 1000

    # 4. Validate output
    val = compositor.validate_render(rendered)
    assert val.is_valid is True
    assert val.width == 1080
    assert val.height == 1920
    assert val.has_audio is True
    assert 1.8 <= val.duration_sec <= 2.2
