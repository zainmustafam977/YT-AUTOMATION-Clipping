from unittest.mock import MagicMock, patch
from pathlib import Path
import pytest
import httpx

from shorts_engine.services.publishing.instagram import InstagramReelsPublisher


def test_instagram_rejects_files_over_50mb(tmp_path):
    """Instagram publisher must reject files over 50MB before attempting upload (PLAN-P2-11 / F-28)."""
    publisher = InstagramReelsPublisher(access_token="fake_token", ig_user_id="12345")
    mock_client = MagicMock(spec=httpx.Client)

    oversized_file = tmp_path / "huge_video.mp4"
    oversized_file.write_bytes(b"0" * 100)

    # Mock stat to report 51 MB
    with patch.object(Path, "stat") as mock_stat:
        mock_stat_res = MagicMock()
        mock_stat_res.st_size = 51 * 1024 * 1024  # 51 MB
        mock_stat.return_value = mock_stat_res

        with pytest.raises(ValueError, match="exceeds Instagram Reels limit of 50 MB"):
            publisher._upload_direct_binary(mock_client, "https://upload.example.com", oversized_file)


def test_instagram_streams_file_in_chunks(tmp_path):
    """Instagram upload must stream binary content via chunked generator instead of reading full file into memory."""
    publisher = InstagramReelsPublisher(access_token="fake_token", ig_user_id="12345")
    mock_client = MagicMock(spec=httpx.Client)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_client.post.return_value = mock_resp

    test_file = tmp_path / "normal_video.mp4"
    test_file.write_bytes(b"a" * (2 * 1024 * 1024))  # 2MB file

    publisher._upload_direct_binary(mock_client, "https://upload.example.com", test_file)

    assert mock_client.post.called
    call_kwargs = mock_client.post.call_args[1]
    # content must be a generator/iterable, not full bytes
    content = call_kwargs.get("content")
    assert hasattr(content, "__iter__") and not isinstance(content, (bytes, bytearray, str)), (
        "Content should be a chunked stream generator, not in-memory bytes"
    )
