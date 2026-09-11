import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit
from app.auth import require
from app.db import get_db
from app.models import User, UserRole
from app.schemas import UserCreate, UserOut, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), _: User = Depends(require("users.manage"))) -> list[User]:
    return list(db.scalars(select(User).order_by(User.created_at)))


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    body: UserCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(require("users.manage")),
) -> User:
    if db.scalar(select(User).where(func.lower(User.email) == body.email)):
        raise HTTPException(status.HTTP_409_CONFLICT, f"{body.email} already exists")
    user = User(email=body.email, name=body.name, role=body.role)
    db.add(user)
    db.flush()
    audit.record(db, actor, "users.create", "user", user.id, {"email": user.email, "role": user.role.value})
    db.commit()
    db.refresh(user)
    return user


@router.patch("/{user_id}", response_model=UserOut)
def update_user(
    user_id: uuid.UUID,
    body: UserUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(require("users.manage")),
) -> User:
    # Lock every active admin row so two admins can't demote each other at the same moment.
    active_admins = list(
        db.scalars(select(User).where(User.role == UserRole.admin, User.is_active).with_for_update())
    )
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    changes = body.model_dump(exclude_unset=True)
    loses_admin = user in active_admins and (
        changes.get("role", UserRole.admin) != UserRole.admin or changes.get("is_active") is False
    )
    if loses_admin and len(active_admins) == 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Can't remove the last active admin")

    before = {k: getattr(user, k) for k in changes}
    for key, value in changes.items():
        setattr(user, key, value)
    audit.record(
        db, actor, "users.update", "user", user.id,
        {k: {"from": _plain(before[k]), "to": _plain(v)} for k, v in changes.items()},
    )
    db.commit()
    db.refresh(user)
    return user


def _plain(value):
    return value.value if isinstance(value, UserRole) else value
