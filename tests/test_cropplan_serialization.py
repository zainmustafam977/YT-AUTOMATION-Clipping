import json
import pytest
from shorts_engine.services.reframing.cropper import CropPlan


def test_crop_plan_single_round_trip():
    original = CropPlan(
        crop_w=405,
        crop_h=720,
        crop_x=437,
        crop_y=0,
        mode="tracking",
        is_multi_speaker=False,
        speaker_count=1,
        layout_mode="single",
        split_crops=[],
        divider_width=4,
    )
    d = original.to_dict()
    assert isinstance(d, dict)

    json_str = json.dumps(d)
    loaded_dict = json.loads(json_str)

    restored = CropPlan.from_dict(loaded_dict)
    assert restored.crop_w == 405
    assert restored.crop_h == 720
    assert restored.crop_x == 437
    assert restored.crop_y == 0
    assert restored.mode == "tracking"
    assert restored.is_multi_speaker is False
    assert restored.speaker_count == 1
    assert restored.layout_mode == "single"
    assert restored.split_crops == []
    assert restored.divider_width == 4
    assert not restored.is_split_screen

    # Also check from_dict with existing instance returns itself
    assert CropPlan.from_dict(original) is original


def test_crop_plan_split_screen_round_trip():
    split_crops_data = [
        {"crop_w": 405, "crop_h": 360, "crop_x": 100, "crop_y": 0},
        {"crop_w": 405, "crop_h": 360, "crop_x": 600, "crop_y": 0},
    ]
    original = CropPlan(
        crop_w=405,
        crop_h=720,
        crop_x=100,
        crop_y=0,
        mode="multi_speaker",
        is_multi_speaker=True,
        speaker_count=2,
        layout_mode="split_screen",
        split_crops=split_crops_data,
        divider_width=6,
    )
    d = original.to_dict()
    assert isinstance(d, dict)
    assert len(d["split_crops"]) == 2

    json_str = json.dumps(d)
    loaded_dict = json.loads(json_str)

    restored = CropPlan.from_dict(loaded_dict)
    assert restored.crop_w == original.crop_w
    assert restored.crop_h == original.crop_h
    assert restored.crop_x == original.crop_x
    assert restored.crop_y == original.crop_y
    assert restored.mode == original.mode
    assert restored.is_multi_speaker is True
    assert restored.speaker_count == 2
    assert restored.layout_mode == "split_screen"
    assert restored.divider_width == 6
    assert restored.is_split_screen is True
    assert len(restored.split_crops) == 2
    assert restored.split_crops[0]["crop_w"] == 405
    assert restored.split_crops[1]["crop_x"] == 600
