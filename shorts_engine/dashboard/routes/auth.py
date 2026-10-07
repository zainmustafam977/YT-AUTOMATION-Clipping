"""
Authentication & user management routes for the operator dashboard.
Includes login, logout, current user inspection, and admin-only user CRUD.
"""

from typing import Optional
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pathlib import Path

from shorts_engine.dashboard.auth import (
    get_db,
    hash_password,
    verify_password,
    create_session_token,
    set_session_cookie,
    clear_session_cookie,
    generate_csrf_token,
    verify_csrf,
    get_current_user,
    require_admin,
    SESSION_COOKIE_NAME,
)
from shorts_engine.db.models import User, UserRole

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


class LoginRequest(BaseModel):
    username: str
    password: str


class CreateUserRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=12, max_length=128)
    role: str = "operator"


@router.get("/login", response_class=HTMLResponse, summary="Login Page")
def login_page(request: Request):
    """Renders the HTML login page."""
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"title": "Operator Login"},
    )


@router.post("/login", summary="User Login")
def login(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    username: Optional[str] = Form(None),
    password: Optional[str] = Form(None),
):
    """
    Authenticates user, creates session token, and attaches HttpOnly, SameSite=Lax cookie.
    Accepts both Form submission and JSON body.
    """
    # Check if request has JSON body if form params are None
    if not username or not password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username and password are required",
        )

    user = db.query(User).filter_by(username=username, is_active=True).first()
    if not user or not verify_password(password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    token = create_session_token(user.id)
    csrf_token = generate_csrf_token(token)

    # Set cookie on response
    resp = JSONResponse(
        content={
            "status": "success",
            "username": user.username,
            "role": user.role.value if hasattr(user.role, "value") else str(user.role),
            "csrf_token": csrf_token,
        }
    )
    set_session_cookie(resp, token)
    return resp


@router.post("/logout", summary="User Logout")
@router.get("/logout", summary="User Logout (GET)")
def logout(request: Request):
    """Clears session cookie and logs user out, redirecting browser requests to /login."""
    accept = request.headers.get("accept", "")
    sec_fetch_dest = request.headers.get("sec-fetch-dest", "")
    is_browser = sec_fetch_dest in ("document", "iframe") or ("text/html" in accept and "application/json" not in accept)

    if is_browser:
        resp = RedirectResponse(
            url="/login?msg=You+have+been+logged+out&type=info",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    else:
        resp = JSONResponse(content={"status": "logged_out"})

    clear_session_cookie(resp)
    return resp


@router.get("/api/me", summary="Get Current Authenticated User")
def get_me(
    request: Request,
    user: User = Depends(get_current_user),
):
    """Returns details and fresh CSRF token for the currently authenticated user."""
    session_token = request.cookies.get(SESSION_COOKIE_NAME, "")
    csrf_token = generate_csrf_token(session_token) if session_token else None
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role.value if hasattr(user.role, "value") else str(user.role),
        "is_active": user.is_active,
        "csrf_token": csrf_token,
    }


# ── Admin-Only User Management ──────────────────────────────────────────

@router.get("/api/users", summary="List All Users (Admin Only)")
def list_users(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Lists all users. Restricted to Admin."""
    users = db.query(User).all()
    return [
        {
            "id": u.id,
            "username": u.username,
            "role": u.role.value if hasattr(u.role, "value") else str(u.role),
            "is_active": u.is_active,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        }
        for u in users
    ]


@router.post("/api/users", summary="Create User (Admin Only)")
async def create_user(
    req: CreateUserRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
    _: bool = Depends(verify_csrf),
):
    """Creates a new user with PBKDF2 hashed password and specified role."""
    # Check if username exists
    existing = db.query(User).filter_by(username=req.username).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"User with username '{req.username}' already exists",
        )

    # Validate role
    role_str = req.role.lower()
    if role_str == "admin":
        role = UserRole.ADMIN
    elif role_str == "operator":
        role = UserRole.OPERATOR
    elif role_str == "viewer":
        role = UserRole.VIEWER
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role '{req.role}'. Must be admin, operator, or viewer.",
        )

    new_user = User(
        username=req.username,
        password_hash=hash_password(req.password),
        role=role,
        is_active=True,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return {
        "id": new_user.id,
        "username": new_user.username,
        "role": new_user.role.value if hasattr(new_user.role, "value") else str(new_user.role),
        "created": True,
    }


@router.delete("/api/users/{user_id}", summary="Delete User (Admin Only)")
async def delete_user(
    user_id: str,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
    _: bool = Depends(verify_csrf),
):
    """Deactivates/deletes a user. Admin cannot delete their own account."""
    if admin.id == user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Admin cannot delete their own account",
        )

    target = db.query(User).filter_by(id=user_id).first()
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    db.delete(target)
    db.commit()
    return {"deleted": True, "user_id": user_id}


# ── Admin-Only User Management HTML UI ──────────────────────────────────

@router.get("/admin/users", response_class=HTMLResponse, summary="User Management (Admin Only)")
def admin_users_page(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """HTML user administration dashboard. Restricted to administrators."""
    users = db.query(User).order_by(User.username.asc()).all()
    session_token = request.cookies.get(SESSION_COOKIE_NAME, "")
    csrf_token = generate_csrf_token(session_token) if session_token else None
    return templates.TemplateResponse(
        request=request,
        name="admin_users.html",
        context={
            "current_user": admin,
            "users": users,
            "csrf_token": csrf_token,
            "msg": request.query_params.get("msg"),
            "msg_type": request.query_params.get("type", "success"),
        },
    )


@router.post("/admin/users", summary="Create User from Admin UI")
def admin_create_user_form(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form("operator"),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
    _: bool = Depends(verify_csrf),
):
    clean_username = username.strip()
    if len(clean_username) < 3 or len(clean_username) > 64:
        return RedirectResponse("/admin/users?msg=Username+must+be+between+3+and+64+characters&type=danger", status_code=303)
    if len(password) < 8:
        return RedirectResponse("/admin/users?msg=Password+must+be+at+least+8+characters&type=danger", status_code=303)

    existing = db.query(User).filter_by(username=clean_username).first()
    if existing:
        return RedirectResponse(f"/admin/users?msg=User+'{clean_username}'+already+exists&type=danger", status_code=303)

    role_str = role.lower()
    if role_str == "admin":
        r = UserRole.ADMIN
    elif role_str == "operator":
        r = UserRole.OPERATOR
    elif role_str == "viewer":
        r = UserRole.VIEWER
    else:
        return RedirectResponse("/admin/users?msg=Invalid+role+selected&type=danger", status_code=303)

    new_user = User(
        username=clean_username,
        password_hash=hash_password(password),
        role=r,
        is_active=True,
    )
    db.add(new_user)
    db.commit()
    return RedirectResponse(f"/admin/users?msg=User+'{clean_username}'+created+successfully&type=success", status_code=303)


@router.post("/admin/users/{user_id}/delete", summary="Delete User from Admin UI")
def admin_delete_user_form(
    user_id: str,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
    _: bool = Depends(verify_csrf),
):
    if admin.id == user_id:
        return RedirectResponse("/admin/users?msg=Cannot+delete+your+own+admin+account&type=danger", status_code=303)
    target = db.query(User).filter_by(id=user_id).first()
    if not target:
        return RedirectResponse("/admin/users?msg=User+not+found&type=danger", status_code=303)
    u_name = target.username
    db.delete(target)
    db.commit()
    return RedirectResponse(f"/admin/users?msg=User+'{u_name}'+deleted+successfully&type=success", status_code=303)

