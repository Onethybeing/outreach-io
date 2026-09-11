import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require
from app.config import get_settings
from app.db import get_db
from app.models import Contact, EmailDirection, EmailEvent, ReplyClassification, Startup, User
from app.replies import tracker

router = APIRouter(prefix="/replies", tags=["replies"])
internal_router = APIRouter(prefix="/internal", tags=["internal"], include_in_schema=False)


class PollOut(BaseModel):
    contacts: int
    new_messages: int
    by_label: dict[str, int]
    errors: list[str]


class ReplyOut(BaseModel):
    event_id: str
    contact_id: str
    contact_name: str
    contact_email: str | None
    startup_name: str
    classification: str | None
    snippet: str | None
    received_at: datetime
    reply_status: str | None
    do_not_contact: bool


def _poll(db: Session) -> PollOut:
    try:
        summary = tracker.poll_all(db)
    except tracker.PollInProgress as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    return PollOut(**summary.__dict__)


@router.post("/poll", response_model=PollOut)
def poll_now(db: Session = Depends(get_db), _: User = Depends(require("contacts.act"))) -> PollOut:
    return _poll(db)


@router.get("", response_model=list[ReplyOut])
def list_replies(
    classification: ReplyClassification | None = None,
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(require("dashboard.view")),
) -> list[ReplyOut]:
    query = (
        select(EmailEvent, Contact, Startup.name)
        .join(Contact, EmailEvent.contact_id == Contact.id)
        .join(Startup, Contact.startup_id == Startup.id)
        .where(EmailEvent.direction == EmailDirection.in_)
        .order_by(EmailEvent.received_at.desc())
        .limit(limit)
    )
    if classification:
        query = query.where(EmailEvent.classification == classification)
    return [
        ReplyOut(
            event_id=str(event.id), contact_id=str(contact.id), contact_name=contact.name, contact_email=contact.email,
            startup_name=startup_name, classification=event.classification.value if event.classification else None,
            snippet=event.snippet, received_at=event.received_at,
            reply_status=contact.reply_status.value if contact.reply_status else None, do_not_contact=contact.do_not_contact,
        )
        for event, contact, startup_name in db.execute(query)
    ]


@internal_router.post("/poll-replies", response_model=PollOut)
def scheduled_poll(x_internal_token: str | None = Header(None), db: Session = Depends(get_db)) -> PollOut:
    """For Cloud Scheduler. Disabled unless INTERNAL_TASK_TOKEN is set."""
    expected = get_settings().internal_task_token
    if not expected:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    if not x_internal_token or not secrets.compare_digest(x_internal_token, expected):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid internal token")
    return _poll(db)
