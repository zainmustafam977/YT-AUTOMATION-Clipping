"""EASE-02 — Saved filters/views HTTP routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from shorts_engine.dashboard.auth import require_operator, require_viewer, verify_csrf
from shorts_engine.db.models import SavedView
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
    name: str = Field(..., min_length=1, max_length=128)
    expression: str = Field("", description="Filter expression (e.g., 'status=ready')")
    owner: str = "system"


@router.get("")
def list_views(owner: str | None = None):
    with SessionLocal() as s:
        q = s.query(SavedView)
        if owner is not None:
            q = q.filter(SavedView.owner == owner)
        rows = q.order_by(SavedView.created_at.desc()).all()
    return {
        "views": [
            {
                "id": v.id, "name": v.name, "expression": v.expression,
                "owner": v.owner, "created_at": str(v.created_at),
            }
            for v in rows
        ]
    }


@router.post("", dependencies=MUTATION_GUARDS)
def create(body: SavedViewIn):
    with SessionLocal() as s:
        # Check for duplicate name+owner.
        existing = s.query(SavedView).filter(
            SavedView.name == body.name, SavedView.owner == body.owner
        ).first()
        if existing is not None:
            raise HTTPException(status_code=409, detail=f"view '{body.name}' already exists for owner '{body.owner}'")
        v = SavedView(name=body.name, expression=body.expression, owner=body.owner)
        s.add(v)
        s.commit()
        s.refresh(v)
        return {
            "id": v.id, "name": v.name, "expression": v.expression,
            "owner": v.owner, "created_at": str(v.created_at),
        }


@router.get("/{view_id}")
def get_view(view_id: str):
    with SessionLocal() as s:
        v = s.query(SavedView).filter(SavedView.id == view_id).first()
        if v is None:
            raise HTTPException(status_code=404, detail=f"view {view_id} not found")
        return {
            "id": v.id, "name": v.name, "expression": v.expression,
            "owner": v.owner, "created_at": str(v.created_at),
        }


@router.put("/{view_id}", dependencies=MUTATION_GUARDS)
def update(view_id: str, body: SavedViewIn):
    with SessionLocal() as s:
        v = s.query(SavedView).filter(SavedView.id == view_id).first()
        if v is None:
            raise HTTPException(status_code=404, detail=f"view {view_id} not found")
        v.name = body.name
        v.expression = body.expression
        v.owner = body.owner
        s.commit()
        s.refresh(v)
        return {
            "id": v.id, "name": v.name, "expression": v.expression,
            "owner": v.owner, "created_at": str(v.created_at),
        }


@router.delete("/{view_id}", dependencies=MUTATION_GUARDS)
def delete(view_id: str):
    with SessionLocal() as s:
        v = s.query(SavedView).filter(SavedView.id == view_id).first()
        if v is None:
            raise HTTPException(status_code=404, detail=f"view {view_id} not found")
        s.delete(v)
        s.commit()
    return {"deleted": view_id}
