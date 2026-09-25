from unittest.mock import MagicMock, patch
import pytest
from googleapiclient.errors import HttpError
import httplib2

from shorts_engine.services.publishing.client import _upload_video
from shorts_engine.services.publishing.metadata import ShortsMetadata


def _make_http_error(status: int, reason: str = "Error"):
    resp = httplib2.Response({"status": str(status)})
    return HttpError(resp, content=reason.encode("utf-8"))


def test_retry_transient_5xx_and_succeed(tmp_path):
    """Resumable upload retries transient 5xx errors and succeeds when next_chunk recovers."""
    dummy_video = tmp_path / "vid.mp4"
    dummy_video.write_bytes(b"content")

    mock_client = MagicMock()
    mock_request = MagicMock()

    # First call to next_chunk raises 503 Service Unavailable; second returns response
    mock_request.next_chunk.side_effect = [
        _make_http_error(503, "Backend Error"),
        (None, {"id": "yt_success_id"}),
    ]
    mock_client.videos.return_value.insert.return_value = mock_request

    meta = ShortsMetadata(title="Test Short", description="Desc", tags=["#shorts"])

    with patch("googleapiclient.http.MediaFileUpload"):
        with patch("time.sleep"):  # don't wait in test
            yt_id = _upload_video(mock_client, dummy_video, meta)

    assert yt_id == "yt_success_id"
    assert mock_request.next_chunk.call_count == 2


def test_fail_fast_on_quota_exceeded_or_403(tmp_path):
    """Resumable upload fails fast immediately on quotaExceeded or 403 without retrying."""
    dummy_video = tmp_path / "vid.mp4"
    dummy_video.write_bytes(b"content")

    mock_client = MagicMock()
    mock_request = MagicMock()

    mock_request.next_chunk.side_effect = _make_http_error(403, "The request cannot be completed because you have exceeded your quotaExceeded")
    mock_client.videos.return_value.insert.return_value = mock_request

    meta = ShortsMetadata(title="Test Short", description="Desc", tags=["#shorts"])

    with patch("googleapiclient.http.MediaFileUpload"):
        with pytest.raises(HttpError):
            _upload_video(mock_client, dummy_video, meta)

    # Must have failed fast on the very first attempt (no retries)
    assert mock_request.next_chunk.call_count == 1


def test_persistent_5xx_exceeds_retries_and_raises(tmp_path):
    """Resumable upload raises after exhausting 3 retries on persistent 5xx errors."""
    dummy_video = tmp_path / "vid.mp4"
    dummy_video.write_bytes(b"content")

    mock_client = MagicMock()
    mock_request = MagicMock()

    mock_request.next_chunk.side_effect = _make_http_error(500, "Internal Server Error")
    mock_client.videos.return_value.insert.return_value = mock_request

    meta = ShortsMetadata(title="Test Short", description="Desc", tags=["#shorts"])

    with patch("googleapiclient.http.MediaFileUpload"):
        with patch("time.sleep"):
            with pytest.raises(HttpError):
                _upload_video(mock_client, dummy_video, meta)

    # 1 initial attempt + 3 retries = 4 total calls
    assert mock_request.next_chunk.call_count == 4

