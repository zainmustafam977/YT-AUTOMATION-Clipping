"""WebSub signature verification must fail closed when no secret is configured (AUD-007).

If WEBSUB_SECRET is unset, WebSubNotificationParser.verify_signature must
reject every notification, because accepting unsigned pushes would let anyone
enqueue arbitrary videos for ingestion.
"""


from shorts_engine.services.detection.websub import WebSubNotificationParser


def test_websub_rejects_when_no_secret_configured():
    parser = WebSubNotificationParser(secret=None)
    assert parser.verify_signature(b"some-payload", None) is False
    assert parser.verify_signature(b"some-payload", "sha1=deadbeef") is False


def test_websub_rejects_malformed_signature_header_without_secret():
    parser = WebSubNotificationParser(secret="")
    assert parser.verify_signature(b"some-payload", "not-a-signature") is False
