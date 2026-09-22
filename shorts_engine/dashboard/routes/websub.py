"""
WebSub callback endpoint: receives push notifications from YouTube's PubSubHubbub hub.

GET  /websub/callback  — hub verification challenge (hub.mode=subscribe)
POST /websub/callback  — incoming push notification (new video detected)

This endpoint does NOT require Basic Auth because it must be publicly reachable
by YouTube's hub. Security is enforced via HMAC-SHA1 signature verification instead.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Query, Request, Response, HTTPException, Depends
from sqlalchemy.orm import Session

from shorts_engine.db.session import SessionLocal
from shorts_engine.services.detection.websub import WebSubNotificationParser

logger = logging.getLogger(__name__)

router = APIRouter()
parser = WebSubNotificationParser()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get(
    "/websub/callback",
    summary="WebSub hub challenge verification",
    response_class=Response,
)
def websub_verify(
    hub_mode: str = Query(..., alias="hub.mode"),
    hub_topic: str = Query(..., alias="hub.topic"),
    hub_challenge: str = Query(..., alias="hub.challenge"),
    hub_lease_seconds: Optional[str] = Query(None, alias="hub.lease_seconds"),
):
    """
    YouTube's hub sends a GET request with hub.challenge when verifying a subscription.
    We must echo back hub.challenge as plain text with HTTP 200.
    """
    from shorts_engine.services.detection.websub import WebSubSubscriber
    subscriber = WebSubSubscriber()
    challenge = subscriber.verify_challenge(hub_mode, hub_topic, hub_challenge, hub_lease_seconds)
    if challenge is None:
        raise HTTPException(status_code=400, detail="Invalid WebSub challenge request")

    logger.info("WebSub hub challenge verified for topic: %s", hub_topic)
    return Response(content=challenge, media_type="text/plain")


@router.post(
    "/websub/callback",
    summary="WebSub push notification receiver",
    status_code=200,
)
async def websub_receive(
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Receives a WebSub Atom push notification from YouTube's hub.
    Validates HMAC-SHA1 signature, parses the XML, and enqueues detected videos.
    Always returns HTTP 200 quickly (async) so the hub doesn't retry.
    """
    body = await request.body()
    x_hub_signature = request.headers.get("X-Hub-Signature")

    # 1. Signature verification (timing-safe HMAC-SHA1)
    if not parser.verify_signature(body, x_hub_signature):
        logger.warning("Rejected WebSub notification: invalid X-Hub-Signature.")
        # Return 200 anyway to prevent hub from retrying with the same invalid payload
        return {"status": "rejected", "reason": "invalid_signature"}

    # 2. Parse Atom feed for new video entries
    entries = parser.parse_notification(body)
    if not entries:
        logger.debug("WebSub notification contained no parseable video entries.")
        return {"status": "ok", "detected": 0}

    # 3. Create detection records
    from shorts_engine.services.detection.detector import DetectionService
    detection = DetectionService(db)
    queued = []
    for channel_id, video_id in entries:
        video = detection.on_new_video(channel_id, video_id)
        if video:
            queued.append({"channel_id": channel_id, "video_id": video_id})
            logger.info("WebSub: queued new video %s from channel %s.", video_id, channel_id)

    return {"status": "ok", "detected": len(queued), "queued": queued}
