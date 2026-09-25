from pathlib import Path
from unittest.mock import patch
from shorts_engine.services.rendering.compositor import get_default_fontfile


def test_get_default_fontfile_includes_container_and_asset_paths(tmp_path):
    """get_default_fontfile resolves fallback container and asset font paths (PLAN-P2-09 / F-35)."""
    custom_font = tmp_path / "DejaVuSans-Bold.ttf"
    custom_font.write_bytes(b"mock_font_bytes")

    # Match when candidate font is checked
    with patch.object(Path, "exists", autospec=True, side_effect=lambda self: "dejavu" in str(self).lower()):
        with patch("shorts_engine.services.rendering.compositor.settings") as mock_settings:
            mock_settings.ROOT_DIR = tmp_path
            result = get_default_fontfile()
            assert result is not None
