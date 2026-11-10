"""EASE-02 — Saved filters/views HTTP routes.

Saved views are personal filter definitions. Ownership is derived from the
authenticated session, never from the request body, and every read and write is
scoped to the calling user: a view belonging to somebody else is reported as
missing rather than forbidden so the endpoint does not confirm its existence.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from shorts_engine.dashboard.auth import require_operator, require_viewer, verify_csrf
from shorts_engine.db.models import SavedView, User
from shorts_engine.db.session import SessionLocal


router = APIRouter(
    prefix="/api/saved-views",
    tags=["saved-views"],
    # Saved views are operator filter definitions, not public read-only data: a
    # signed-in user is required for every endpoint, matching the overlay routes.
    dependencies=[Depends(require_viewer)],
)

# Mutating endpoints additionally require the operator role and a CSRF token.
MUTATION_GUARDS = [Depends(require_operator), Depends(verify_csrf)]


class SavedViewIn(BaseModel):
    """Request body for creating or renaming a saved view.

    Deliberately has no ``owner`` field: the owner always comes from the session.
    An ``owner`` sent by a client is ignored.
    """

    name: str = Field(..., min_length=1, max_length=128)
    expression: str = Field("", description="Filter expression (e.g., 'status=ready')")


def _serialize(view: SavedView) -> dict:
    return {
        "id": view.id,
        "name": view.name,
        "expression": view.expression,
        "owner": view.owner,
        "created_at": str(view.created_at),
    }


def _owned_or_404(session, view_id: str, user: User) -> SavedView:
    """Returns the view only if the caller owns it, otherwise reports 404.

    A 403 would confirm that somebody else's view exists, so ownership failures
    are indistinguishable from a missing id.
    """
    view = session.query(SavedView).filter(SavedView.id == view_id).first()
    if view is None or view.owner != user.username:
        raise HTTPException(status_code=404, detail=f"view {view_id} not found")
    return view


@router.get("")
def list_views(owner: str | None = None, user: User = Depends(require_viewer)):
    """Lists the caller's saved views.

    ``owner`` is still accepted for older callers, but only the authenticated
    identity may be requested; anything else is rejected rather than silently
    returning the caller's own views.
    """
    if owner is not None and owner != user.username:
        raise HTTPException(
            status_code=403,
            detail="Saved views are scoped to the authenticated user",
        )
    with SessionLocal() as s:
        rows = (
            s.query(SavedView)
            .filter(SavedView.owner == user.username)
            .order_by(SavedView.created_at.desc())
            .all()
        )
        return {"views": [_serialize(v) for v in rows]}


@router.post("", dependencies=MUTATION_GUARDS)
def create(body: SavedViewIn, user: User = Depends(require_viewer)):
    with SessionLocal() as s:
        # Names are unique per owner, and the owner is always the caller.
        existing = (
            s.query(SavedView)
            .filter(SavedView.name == body.name, SavedView.owner == user.username)
            .first()
        )
        if existing is not None:
            raise HTTPException(
                status_code=409,
                detail=f"view '{body.name}' already exists for owner '{user.username}'",
            )
        view = SavedView(name=body.name, expression=body.expression, owner=user.username)
        s.add(view)
        s.commit()
        s.refresh(view)
        return _serialize(view)


@router.get("/{view_id}")
def get_view(view_id: str, user: User = Depends(require_viewer)):
    with SessionLocal() as s:
        return _serialize(_owned_or_404(s, view_id, user))


@router.put("/{view_id}", dependencies=MUTATION_GUARDS)
def update(view_id: str, body: SavedViewIn, user: User = Depends(require_viewer)):
    with SessionLocal() as s:
        view = _owned_or_404(s, view_id, user)
        view.name = body.name
        view.expression = body.expression
        s.commit()
        s.refresh(view)
        return _serialize(view)


@router.delete("/{view_id}", dependencies=MUTATION_GUARDS)
def delete(view_id: str, user: User = Depends(require_viewer)):
    with SessionLocal() as s:
        view = _owned_or_404(s, view_id, user)
        s.delete(view)
        s.commit()
    return {"deleted": view_id}
