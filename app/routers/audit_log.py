import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require
from app.db import get_db
from app.models import AuditLog, User
from app.schemas import AuditOut

router = APIRouter(prefix="/audit-log", tags=["audit"])


@router.get("", response_model=list[AuditOut])
def list_audit(
    action: str | None = None,
    user_id: uuid.UUID | None = None,
    before: datetime | None = Query(None, description="Page backwards: pass the last row's created_at"),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(require("audit.view")),
) -> list[AuditOut]:
    query = (
        select(AuditLog, User.email)
        .outerjoin(User, AuditLog.user_id == User.id)
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
    )
    if action:
        query = query.where(AuditLog.action == action)
    if user_id:
        query = query.where(AuditLog.user_id == user_id)
    if before:
        query = query.where(AuditLog.created_at < before)
    return [
        AuditOut(
            id=row.id, user_email=email, action=row.action, target_type=row.target_type,
            target_id=row.target_id, details=row.details, created_at=row.created_at,
        )
        for row, email in db.execute(query)
    ]
