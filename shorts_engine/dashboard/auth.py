"""
Multi-user session-based authentication, RBAC, and CSRF protection.
HTTP Basic Auth and shared legacy credentials have been completely removed.
All authentication requires an active User entity with assigned role (admin, operator, viewer).
"""

import hashlib
import hmac
import logging
import secrets
import time
from typing import Optional, List, Union
from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from shorts_engine.config.settings import settings, LEGACY_DEFAULT_DASHBOARD_PASSWORD
from shorts_engine.db.session import SessionLocal
from shorts_engine.db.models import User, UserRole

logger = logging.getLogger(__name__)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_default_admin(db: Session) -> None:
    """
    Ensures at least one active administrator exists with valid credentials.
    If 'admin' exists with legacy 'change-me' password, automatically upgrades it to settings.DASHBOARD_PASSWORD.
    """
    try:
        admin_user = db.query(User).filter_by(username="admin").first()
        default_user = settings.DASHBOARD_USERNAME or "admin"
        # Never fall back to a hardcoded credential. If the operator has not
        # configured one, generate a random password and log it once so the
        # account is still reachable without shipping a known default.
        default_pass = settings.DASHBOARD_PASSWORD
        if not default_pass or default_pass == LEGACY_DEFAULT_DASHBOARD_PASSWORD:
            default_pass = secrets.token_urlsafe(24)
            logger.warning(
                "DASHBOARD_PASSWORD is unset or still the shipped default; "
                "generated a random administrator password for this boot. "
                "Set DASHBOARD_PASSWORD in the environment to make it durable."
            )

        if not admin_user:
            admin_user = User(
                username=default_user,
                password_hash=hash_password(default_pass),
                role=UserRole.ADMIN,
                is_active=True,
            )
            db.add(admin_user)
            db.commit()
            logger.info("Created default administrator '%s'", default_user)
        else:
            if verify_password("change-me", admin_user.password_hash):
                admin_user.password_hash = hash_password(default_pass)
                admin_user.is_active = True
                db.commit()
                logger.info("Migrated administrator '%s' from legacy default password", default_user)
    except Exception as e:
        logger.warning("ensure_default_admin check failed: %s", e)


# ── Password Hashing (PBKDF2-HMAC-SHA256, 600,000 iterations) ─────────────

def hash_password(password: str) -> str:
    """Hashes a password using PBKDF2-HMAC-SHA256 with 600,000 iterations and a 16-byte random salt."""
    salt = secrets.token_hex(16)
    iterations = 600_000
    hash_bytes = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    )
    return f"pbkdf2_sha256${iterations}${salt}${hash_bytes.hex()}"


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifies a plain password against a PBKDF2-HMAC-SHA256 hash using constant-time comparison."""
    try:
        algorithm, iterations_str, salt, target_hash = hashed_password.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_str)
        derived = hashlib.pbkdf2_hmac(
            "sha256",
            plain_password.encode("utf-8"),
            salt.encode("utf-8"),
            iterations,
        )
        return secrets.compare_digest(derived.hex(), target_hash)
    except Exception:
        return False


# ── Session Tokens & Cookie Signing ──────────────────────────────────────

SESSION_COOKIE_NAME = "session_id"


def create_session_token(user_id: str, secret_key: str = None, timestamp: Optional[int] = None) -> str:
    """Creates a cryptographically signed session token: <user_id>.<timestamp>.<hmac_sha256>."""
    key = secret_key or settings.DASHBOARD_SECRET_KEY
    ts = timestamp if timestamp is not None else int(time.time())
    payload = f"{user_id}.{ts}"
    signature = hmac.new(
        key.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload}.{signature}"


def verify_session_token(token: str, secret_key: str = None, max_age_secs: int = 86400 * 7) -> Optional[str]:
    """Verifies a signed session token. Returns user_id if valid and unexpired; None otherwise."""
    if not token:
        return None
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        user_id, ts_str, signature = parts
        ts = int(ts_str)
        key = secret_key or settings.DASHBOARD_SECRET_KEY
        expected_payload = f"{user_id}.{ts}"
        expected_sig = hmac.new(
            key.encode("utf-8"),
            expected_payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not secrets.compare_digest(signature, expected_sig):
            return None
        if int(time.time()) - ts > max_age_secs:
            return None
        return user_id
    except Exception:
        return None


def set_session_cookie(response: Response, token: str, max_age: int = 86400 * 7):
    """Sets a secure, HttpOnly, SameSite=Lax session cookie on the response."""
    is_prod = settings.ENV.lower() in ("production", "staging")
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=is_prod,
    )


def clear_session_cookie(response: Response):
    """Clears the session cookie."""
    response.delete_cookie(key=SESSION_COOKIE_NAME, httponly=True, samesite="lax")


# ── CSRF Protection ───────────────────────────────────────────────────────

def generate_csrf_token(session_token: str, secret_key: str = None) -> str:
    """Generates a cryptographic CSRF token deterministically bound to the active session."""
    key = secret_key or settings.DASHBOARD_SECRET_KEY
    return hmac.new(
        key.encode("utf-8"),
        f"csrf:{session_token}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_csrf_token(session_token: str, csrf_token: str, secret_key: str = None) -> bool:
    """Verifies that a CSRF token matches the active session."""
    if not session_token or not csrf_token:
        return False
    expected = generate_csrf_token(session_token, secret_key)
    return secrets.compare_digest(expected, csrf_token)


def get_csrf_token(request: Request) -> str:
    """Extracts session token from request cookies and generates the bound CSRF token."""
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    if session_token:
        return generate_csrf_token(session_token)
    return ""


async def verify_csrf(request: Request) -> bool:
    """
    FastAPI dependency validating CSRF tokens on state-changing methods (POST, PUT, DELETE, PATCH).
    Checks header 'X-CSRF-Token' and form field 'csrf_token'.
    Enforced for browser sessions authenticated via session cookie.
    Non-cookie requests (e.g. Bearer token API scripts) are exempt since browsers do not auto-attach Bearer headers.
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return True

    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_token:
        # Requests using Bearer tokens (or unauthenticated requests that will fail at user level) are exempt from CSRF
        return True

    csrf_token = request.headers.get("X-CSRF-Token")
    if not csrf_token:
        csrf_token = request.query_params.get("csrf_token")
    if not csrf_token:
        content_type = request.headers.get("Content-Type", "")
        if "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
            try:
                form = await request.form()
                csrf_token = form.get("csrf_token")
            except Exception:
                pass

    if not csrf_token or not verify_csrf_token(session_token, str(csrf_token)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing CSRF token",
        )
    return True


# ── Current User & RBAC Dependencies ──────────────────────────────────────

def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    """
    Authenticates current user exclusively via:
    1. Signed session cookie ('session_id')
    2. Bearer session token ('Authorization: Bearer <session_token>') for non-browser scripts

    HTTP Basic Auth and shared legacy credentials (DASHBOARD_USERNAME/PASSWORD) are fully disabled.
    Every request MUST map to an active User row with a validated RBAC role.
    Raises HTTP 401 if unauthenticated.
    """
    session_token = None

    # 1. Check session cookie (browser UI)
    if SESSION_COOKIE_NAME in request.cookies:
        session_token = request.cookies.get(SESSION_COOKIE_NAME)

    # 2. Check Bearer token header (scripts/API)
    elif "Authorization" in request.headers:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            session_token = auth_header[7:].strip()

    if session_token:
        user_id = verify_session_token(session_token)
        if user_id:
            user = db.query(User).filter_by(id=user_id, is_active=True).first()
            if user:
                return user

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
    )


def require_role(allowed_roles: List[Union[UserRole, str]]):
    """
    Dependency factory checking if current user has one of the allowed roles.
    Raises HTTP 403 if unauthorized.
    """
    normalized_roles = [
        r.value if isinstance(r, UserRole) else str(r).lower()
        for r in allowed_roles
    ]

    def role_checker(user: User = Depends(get_current_user)) -> User:
        user_role_str = user.role.value if isinstance(user.role, UserRole) else str(user.role).lower()
        if user_role_str not in normalized_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Operation not permitted for role '{user_role_str}'",
            )
        return user

    return role_checker


require_admin = require_role([UserRole.ADMIN])
require_operator = require_role([UserRole.ADMIN, UserRole.OPERATOR])
require_viewer = require_role([UserRole.ADMIN, UserRole.OPERATOR, UserRole.VIEWER])
