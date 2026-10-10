"""
OAuth 2.0 Route Handlers for YouTube Channel Authorization.
Supports both 1-click Google OAuth redirect (/oauth2callback) and manual code paste exchange.
"""

import logging
import urllib.parse
from typing import Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from shorts_engine.dashboard.auth import require_operator, require_viewer
from shorts_engine.dashboard.routes.queue import get_db
from shorts_engine.db.models import Channel, User
from shorts_engine.services.publishing.oauth_manager import ChannelOAuthManager

logger = logging.getLogger(__name__)

router = APIRouter()
oauth_manager = ChannelOAuthManager()


class TokenExchangePayload(BaseModel):
    code: Optional[str] = None
    callback_url: Optional[str] = None
    redirect_uri: Optional[str] = None


@router.get("/oauth2callback", summary="Google OAuth 2.0 redirect handler")
def oauth2_callback(
    request: Request,
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """
    Receives authorization code from Google OAuth redirect, exchanges it for refresh/access tokens,
    verifies identity with channels.list(mine=true), enforces presence of refresh token,
    and isolates any account mismatch into PendingOAuthMismatch.
    """
    if error:
        logger.error("OAuth error returned from Google: %s", error)
        msg = urllib.parse.quote_plus(f"Google authorization failed: {error}")
        return RedirectResponse(url=f"/queue?studio=open&msg={msg}&type=danger&oauth_status=error", status_code=303)

    if not code:
        logger.error("OAuth callback reached without authorization code")
        msg = urllib.parse.quote_plus("Missing authorization code from Google")
        return RedirectResponse(url=f"/queue?studio=open&msg={msg}&type=danger&oauth_status=error", status_code=303)

    redirect_uri = str(request.url).split("?")[0]

    try:
        res = oauth_manager.handle_callback(
            code=code,
            state=state or "",
            db=db,
            redirect_uri=redirect_uri,
        )
        if res.get("status") == "error":
            err_msg = res.get("message", "Authorization failed")
            msg = urllib.parse.quote_plus(err_msg)
            return RedirectResponse(url=f"/queue?studio=open&msg={msg}&type=danger&oauth_status=error", status_code=303)

        if res.get("status") == "mismatch":
            mismatch_id = res.get("mismatch_id")
            msg = urllib.parse.quote_plus("Channel mismatch detected between selected channel and authorized Google account.")
            return RedirectResponse(
                url=f"/channels?oauth_status=mismatch&mismatch_id={mismatch_id}&msg={msg}&type=warning",
                status_code=303,
            )

        # Success
        ch_title = res.get("title") or res.get("channel_id", "Channel")
        msg = urllib.parse.quote_plus(f"Channel '{ch_title}' successfully connected to YouTube!")
        return RedirectResponse(
            url=f"/queue?studio=open&msg={msg}&type=success&oauth_status=success&channel_id={res.get('channel_id')}",
            status_code=303,
        )
    except Exception as e:
        logger.exception("Failed to exchange OAuth code: %s", e)
        msg = urllib.parse.quote_plus(f"Failed to connect channel: {str(e)}")
        return RedirectResponse(url=f"/queue?studio=open&msg={msg}&type=danger&oauth_status=error", status_code=303)


@router.get("/api/channels/{channel_id}/oauth/start", summary="Generate Google OAuth authorization URL")
def get_channel_oauth_url(
    channel_id: str,
    request: Request,
    redirect_uri: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
):
    """Generates the Google OAuth 2.0 authorization URL for a specific channel."""
    channel = db.query(Channel).filter_by(id=channel_id).first()
    if not channel:
        channel = db.query(Channel).filter_by(youtube_channel_id=channel_id).first()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    eff_redirect = redirect_uri or f"{str(request.base_url).rstrip('/')}/oauth2callback"
    try:
        auth_url = oauth_manager.get_authorization_url(channel_id=channel.id, redirect_uri=eff_redirect)
        return {
            "channel_id": channel.id,
            "channel_name": channel.name,
            "authorization_url": auth_url,
            "redirect_uri": eff_redirect,
        }
    except Exception as e:
        logger.exception("Failed to generate OAuth URL for channel %s: %s", channel_id, e)
        raise HTTPException(status_code=500, detail=f"Failed to generate authorization URL: {str(e)}")


@router.post("/api/channels/{channel_id}/oauth/exchange", summary="Exchange authorization code or callback URL for tokens")
def exchange_channel_oauth_code(
    channel_id: str,
    payload: TokenExchangePayload,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
):
    """
    Accepts manually pasted authorization code or callback URL, exchanges it for refresh/access tokens,
    and stores them encrypted for the channel.
    """
    channel = db.query(Channel).filter_by(id=channel_id).first()
    if not channel:
        channel = db.query(Channel).filter_by(youtube_channel_id=channel_id).first()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    raw_input = payload.code or payload.callback_url
    if not raw_input or not raw_input.strip():
        raise HTTPException(status_code=400, detail="Missing authorization code or callback URL")

    redirect_uri = payload.redirect_uri or f"{str(request.base_url).rstrip('/')}/oauth2callback"

    try:
        tokens = oauth_manager.exchange_code_for_tokens(code=raw_input, redirect_uri=redirect_uri)
        oauth_manager.bind_token_to_channel(channel.id, tokens, db)
        return {
            "status": "success",
            "channel_id": channel.id,
            "channel_name": channel.name,
            "message": f"Channel '{channel.name}' connected successfully!",
        }
    except Exception as e:
        logger.exception("Failed to exchange code for channel %s: %s", channel_id, e)
        raise HTTPException(status_code=400, detail=f"Failed to exchange authorization code: {str(e)}")


@router.get("/api/channels/{channel_id}/oauth/status", summary="Check channel live OAuth connection and stats")
def get_channel_oauth_status(
    channel_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_viewer),
):
    """Returns live connection health and channel statistics via YouTube Data API."""
    channel = db.query(Channel).filter_by(id=channel_id).first()
    if not channel:
        channel = db.query(Channel).filter_by(youtube_channel_id=channel_id).first()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    return oauth_manager.verify_channel_token(channel)


@router.post("/api/channels/{channel_id}/oauth/revoke", summary="Revoke and clear channel OAuth tokens")
def revoke_channel_oauth(
    channel_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
):
    """Clears stored OAuth credentials for the specified channel."""
    channel = db.query(Channel).filter_by(id=channel_id).first()
    if not channel:
        channel = db.query(Channel).filter_by(youtube_channel_id=channel_id).first()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    oauth_manager.revoke_channel_token(channel, db)
    return {
        "status": "revoked",
        "channel_id": channel.id,
        "channel_name": channel.name,
        "message": f"OAuth credentials cleared for channel '{channel.name}'",
    }
