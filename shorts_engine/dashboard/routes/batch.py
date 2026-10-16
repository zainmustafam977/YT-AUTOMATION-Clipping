"""E-001..E-010 + Round 3 batch operations HTTP API.

* GET  /api/batch/operations
* POST /api/batch/preview
* POST /api/batch/run
* POST /api/batch/{id}/undo
* GET  /api/batch/{id}
* GET  /api/batch
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from shorts_engine.db.session import get_db
from shorts_engine.dashboard.auth import require_operator
from shorts_engine.db.models import BatchJob, User
from shorts_engine.services.batch import BatchEngine, BatchError


router = APIRouter(prefix="/api/batch", tags=["Batch (E-001)"])


class BatchRequest(BaseModel):
    operation: str = Field(..., min_length=1, max_length=64)
    item_ids: List[str] = Field(..., min_length=1)
    params: Optional[Dict[str, Any]] = None
    actor: Optional[str] = None


class UndoRequest(BaseModel):
    item_ids: List[str] = Field(..., min_length=1)


@router.get("/operations", dependencies=[Depends(require_operator)])
def list_operations(db: Session = Depends(get_db)) -> Dict[str, Any]:
    return {"operations": BatchEngine(db).list_operations()}


@router.post("/preview", dependencies=[Depends(require_operator)])
def preview(req: BatchRequest, db: Session = Depends(get_db)) -> Dict[str, Any]:
    try:
        result = BatchEngine(db).preview(req.operation, req.item_ids, req.params)
    except BatchError as e:
        raise HTTPException(status_code=400, detail={"code": e.code, "message": str(e)})
    return result.to_dict()


@router.post("/run", dependencies=[Depends(require_operator)])
def run(
    req: BatchRequest,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    actor = req.actor or user.username
    try:
        result = BatchEngine(db).run(req.operation, req.item_ids, req.params, actor=actor)
    except BatchError as e:
        raise HTTPException(status_code=400, detail={"code": e.code, "message": str(e)})
    return result.to_dict()


@router.post("/{batch_id}/undo", dependencies=[Depends(require_operator)])
def undo(
    batch_id: str,
    req: UndoRequest,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    try:
        result = BatchEngine(db).undo_with_items(
            batch_id, req.item_ids, actor=user.username
        )
    except BatchError as e:
        raise HTTPException(status_code=400, detail={"code": e.code, "message": str(e)})
    return result.to_dict()


@router.get("/{batch_id}", dependencies=[Depends(require_operator)])
def get_batch(batch_id: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    job = db.query(BatchJob).filter_by(id=batch_id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="batch not found")
    return {
        "id": job.id,
        "operation": job.operation,
        "total_items": job.total_items,
        "ok_count": job.ok_count,
        "failed_count": job.failed_count,
        "skipped_count": job.skipped_count,
        "actor": job.actor,
        "state": job.state.value if hasattr(job.state, "value") else str(job.state),
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


@router.get("", dependencies=[Depends(require_operator)])
def list_batches(
    limit: int = 50, db: Session = Depends(get_db)
) -> Dict[str, Any]:
    limit = min(limit, 200)
    rows = db.query(BatchJob).order_by(BatchJob.created_at.desc()).limit(limit).all()
    return {
        "batches": [
            {
                "id": r.id,
                "operation": r.operation,
                "total_items": r.total_items,
                "ok_count": r.ok_count,
                "failed_count": r.failed_count,
                "actor": r.actor,
                "state": r.state.value if hasattr(r.state, "value") else str(r.state),
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    }
