"""Sending (PLAN.md §6). Dev mode writes a real .eml (with the CV attached) instead of sending.

Real sending is deliberately not wired in yet: the user asked for no real email until they approve it.
"""

import logging
import mimetypes
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import audit, vault
from app.config import get_settings
from app.contacts.service import SENT, ActionError
from app.db import SessionLocal
from app.jobs import WorkerPool
from app.models import Contact, DraftStatus, EmailDirection, EmailEvent, Resume, SendStatus, User
from app.storage import OUTBOX_PREFIX, get_storage

logger = logging.getLogger(__name__)

READY = (SendStatus.none, SendStatus.failed)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def sender_address(db: Session) -> str:
    try:
        return vault.get_credential(db, "gmail")["sender_address"]
    except (vault.VaultError, RuntimeError, KeyError):
        return get_settings().gmail_sender_address or "outreach@localhost"


def build_message(contact: Contact, sender: str, resume: Resume) -> EmailMessage:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = contact.email
    message["Subject"] = contact.draft_subject
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain=sender.split("@")[-1] or "localhost")
    message["X-Outreach-Contact-Id"] = str(contact.id)
    message.set_content(contact.draft_text)
    data = get_storage().read(resume.storage_path)
    maintype, _, subtype = (mimetypes.guess_type(resume.filename)[0] or "application/octet-stream").partition("/")
    message.add_attachment(data, maintype=maintype, subtype=subtype, filename=resume.filename)
    return message


def _check_can_send(contact: Contact | None, force: bool) -> None:
    if contact is None:
        raise ActionError(404, "Contact not found")
    if contact.do_not_contact:
        raise ActionError(400, "This contact asked not to be contacted")
    if not contact.email:
        raise ActionError(400, "No email address")
    if contact.draft_status != DraftStatus.approved:
        raise ActionError(409, "Approve the draft before sending")
    if contact.send_status == SendStatus.queued:
        raise ActionError(409, "This email is already being sent")
    if contact.send_status in SENT and not force:
        raise ActionError(409, "Already sent — use force to send again")


def send(db: Session, contact_id: uuid.UUID, user: User, force: bool = False) -> Contact:
    contact = db.get(Contact, contact_id, with_for_update=True)
    _check_can_send(contact, force)
    if get_settings().app_mode != "dev":
        raise ActionError(409, "Real sending is off until you explicitly approve it; only dev mode (.eml files) is available")
    resume = db.get(Resume, contact.cv_used_id)
    if not get_storage().exists(resume.storage_path):
        raise ActionError(400, f"The CV file to attach is missing on the server: {resume.filename}")

    contact.send_status = SendStatus.queued
    db.commit()
    try:
        message = build_message(contact, sender_address(db), resume)
        name = f"{_now().strftime('%Y%m%dT%H%M%S')}-{contact.id}.eml"
        get_storage().save(f"{OUTBOX_PREFIX}/{name}", bytes(message), "message/rfc822")
    except Exception as exc:  # noqa: BLE001 — never leave a contact stuck in 'queued'
        logger.exception("Dev send for contact %s failed", contact_id)
        contact.send_status = SendStatus.failed
        audit.record(db, user, "emails.send_failed", "contact", contact.id, {"mode": "dev", "error": str(exc)})
        db.commit()
        raise ActionError(500, f"Could not write the dev email: {exc}")

    contact.send_status, contact.sent_at, contact.sent_by = SendStatus.sent_dev, _now(), user.id
    contact.mail_service, contact.gmail_message_id = "dev_eml", message["Message-ID"]
    db.add(EmailEvent(
        contact_id=contact.id, direction=EmailDirection.out, gmail_message_id=message["Message-ID"],
        snippet=f"{contact.draft_subject}\n\n{contact.draft_text[:300]}",
    ))
    audit.record(db, user, "emails.send", "contact", contact.id, {"mode": "dev", "file": name, "force": force})
    db.commit()
    return contact


def bulk_eligible(db: Session, contact_ids: list[uuid.UUID] | None = None) -> list[uuid.UUID]:
    query = select(Contact.id).where(
        Contact.draft_status == DraftStatus.approved,
        Contact.email.isnot(None),
        Contact.do_not_contact.is_(False),
        Contact.send_status.in_(READY),
    )
    if contact_ids is not None:
        if not contact_ids:
            return []
        query = query.where(Contact.id.in_(contact_ids))
    return list(db.scalars(query))


def execute_bulk(db: Session, contact_ids: list[uuid.UUID], user_id: uuid.UUID) -> None:
    user = db.get(User, user_id)
    for contact_id in contact_ids:
        if contact_id not in bulk_eligible(db, [contact_id]):
            continue
        try:
            send(db, contact_id, user)
        except Exception as exc:  # noqa: BLE001 — one bad contact must not stop the rest of the batch
            db.rollback()
            logger.warning("Bulk send for %s skipped: %s", contact_id, exc, exc_info=not isinstance(exc, ActionError))


def _run_bulk(contact_ids: list[uuid.UUID], user_id: uuid.UUID) -> None:
    with SessionLocal() as db:
        execute_bulk(db, contact_ids, user_id)


_pool = WorkerPool("sending", workers=1)


def submit_bulk(contact_ids: list[uuid.UUID], user_id: uuid.UUID) -> None:
    _pool.submit(lambda ids, uid: _run_bulk(ids, uid), contact_ids, user_id)


def fail_interrupted_sends(db: Session) -> int:
    count = db.execute(
        update(Contact).where(Contact.send_status == SendStatus.queued).values(send_status=SendStatus.failed)
    ).rowcount
    db.commit()
    return count
