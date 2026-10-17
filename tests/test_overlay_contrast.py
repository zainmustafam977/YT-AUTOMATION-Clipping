"""Tests for E-011 contrast module (WCAG ratio + best-contrast picker)."""
import pytest

from shorts_engine.services.overlay.contrast import (
    rel_luminance,
    wcag_contrast_ratio,
    best_contrast_color,
)


def test_rel_luminance_black():
    assert rel_luminance("#000000") == pytest.approx(0.0, abs=1e-6)


def test_rel_luminance_white():
    assert rel_luminance("#FFFFFF") == pytest.approx(1.0, abs=1e-3)


def test_wcag_white_on_black():
    r = wcag_contrast_ratio("#FFFFFF", "#000000")
    assert r == pytest.approx(21.0, abs=0.01)


def test_wcag_white_on_white():
    r = wcag_contrast_ratio("#FFFFFF", "#FFFFFF")
    assert r == pytest.approx(1.0, abs=0.01)


def test_wcag_symmetric():
    a = wcag_contrast_ratio("#FF0000", "#00FF00")
    b = wcag_contrast_ratio("#00FF00", "#FF0000")
    assert a == pytest.approx(b)


def test_wcag_short_form():
    # #FFF should equal #FFFFFF
    a = wcag_contrast_ratio("#FFF", "#000")
    b = wcag_contrast_ratio("#FFFFFF", "#000000")
    assert a == pytest.approx(b)


def test_best_contrast_picks_white_on_dark():
    color, ratio = best_contrast_color("#000000", candidates=("#FFFFFF", "#000000"))
    assert color == "#FFFFFF"
    assert ratio > 10.0


def test_best_contrast_picks_black_on_light():
    color, ratio = best_contrast_color("#FFFFFF", candidates=("#FFFFFF", "#000000"))
    assert color == "#000000"
    assert ratio > 10.0


def test_rel_luminance_rejects_invalid():
    with pytest.raises(ValueError):
        rel_luminance("not-a-color")
    with pytest.raises(ValueError):
        rel_luminance("#FFFAA")  # bad length


def test_wcag_min_max_bounds():
    # Test that the ratio stays in the valid WCAG range [1, 21].
    for fg in ("#000000", "#FFFFFF", "#7F7F7F", "#FF0000", "#00FF00", "#0000FF"):
        for bg in ("#000000", "#FFFFFF", "#7F7F7F", "#FF0000", "#00FF00", "#0000FF"):
            r = wcag_contrast_ratio(fg, bg)
            assert 1.0 <= r <= 21.0
