from unittest.mock import patch, MagicMock
from shorts_engine.services.ingestion.downloader import IngestionService


def test_get_metadata_attaches_cookies_and_extractor_args(tmp_path):
    """get_metadata attaches discovered cookiefile and player_client extractor args (PLAN-P2-06 / F-21)."""
    service = IngestionService(output_dir=tmp_path)

    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("test_cookie_data")

    with patch.object(service, "get_cookie_path", return_value=str(cookie_file)), \
         patch("yt_dlp.YoutubeDL") as mock_ydl_cls:
        mock_ydl = MagicMock()
        mock_ydl.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.return_value = {"id": "test_id", "title": "Test Title"}
        mock_ydl_cls.return_value = mock_ydl

        meta = service.get_metadata("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

        assert meta["id"] == "test_id"
        assert mock_ydl_cls.called
        opts = mock_ydl_cls.call_args[0][0]
        assert opts.get("cookiefile") == str(cookie_file), "get_metadata must attach discovered cookiefile"
        assert "extractor_args" in opts
        assert opts["extractor_args"]["youtube"]["player_client"] == ["android", "web"]
