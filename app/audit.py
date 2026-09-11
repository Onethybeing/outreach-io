import uuid

from sqlalchemy.orm import Session

from app.models import AuditLog, User


def record(
    db: Session,
    user: User | None,
    action: str,
    target_type: str | None = None,
    target_id: str | uuid.UUID | None = None,
    details: dict | None = None,
) -> None:
    """Add an audit row to the caller's transaction; it commits (or rolls back) with the action."""
    db.add(
        AuditLog(
            user_id=user.id if user else None,
            action=action,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            details=details,
        )
    )
