"""Sending (PLAN.md §6). Both modes send a real email through Gmail, with the CV attached.

The mode decides only who receives it: in dev every message is redirected to the operator's own
inbox (DEV_REDIRECT_EMAIL, or the sending account) with the intended recipient named in the subject
and a banner in the body, so the whole path can be exercised without reaching a stranger. Prod sends
to the contact's own address. A copy of whatever was sent is archived as an .eml.
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
from app.contacts import service
from app.contacts.service import SENT, ActionError
from app.replies import gmail
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


def dev_redirect_address(db: Session) -> str:
    return get_settings().dev_redirect_email or sender_address(db)


def recipient(db: Session, contact: Contact) -> tuple[str, bool]:
    """(address this message will actually go to, whether it was redirected away from the contact)."""
    if service.app_mode(db) == "prod":
        return contact.email, False
    # Dev: never the contact. Falls back to the sending account, so a missing setting still can't
    # let a message reach a stranger.
    return dev_redirect_address(db), True


def build_message(
    contact: Contact, sender: str, resume: Resume, to_address: str, redirected: bool, rfc_message_id: str
) -> EmailMessage:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = to_address
    subject, body = contact.draft_subject, contact.draft_text
    if redirected:
        subject = f"[DEV → {contact.email}] {subject}"
        body = (
            f"--- DEV MODE: this was addressed to {contact.name} <{contact.email}>, "
            f"redirected to you. They received nothing. ---\n\n{body}"
        )
        message["X-Outreach-Intended-To"] = contact.email
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = rfc_message_id
    message["X-Outreach-Contact-Id"] = str(contact.id)
    message.set_content(body)
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


def send(
    db: Session, contact_id: uuid.UUID, user: User, force: bool = False, expected_mode: str | None = None
) -> Contact:
    contact = db.get(Contact, contact_id, with_for_update=True)
    _check_can_send(contact, force)
    resume = db.get(Resume, contact.cv_used_id)
    if not get_storage().exists(resume.storage_path):
        raise ActionError(400, f"The CV file to attach is missing on the server: {resume.filename}")

    sender = sender_address(db)
    to_address, redirected = recipient(db, contact)
    if "@" not in to_address or to_address.endswith("@localhost"):
        raise ActionError(400, "No sending address is configured — set DEV_REDIRECT_EMAIL, or add the Gmail credential to the vault")
    mode = "dev" if redirected else "prod"
    if expected_mode and expected_mode != mode:
        raise ActionError(409, f"The sending mode is now '{mode}', not '{expected_mode}' — check who this would reach and try again")

    # A previous attempt may have reached Gmail even though the response didn't reach us.
    if _already_sent(db, contact, user, redirected):
        raise ActionError(409, "This email had already been sent — the earlier attempt did reach Gmail")

    rfc_message_id = make_msgid(domain=sender.split("@")[-1] or "localhost")
    contact.send_status, contact.rfc_message_id = SendStatus.queued, rfc_message_id
    db.commit()  # the queued status is what stops an erasure landing mid-send
    try:
        message = build_message(contact, sender, resume, to_address, redirected, rfc_message_id)
        message_id, thread_id = gmail.send_message(db, bytes(message))
        # Archived while still 'queued', so an erasure can't slip in and leave an orphan copy behind.
        _archive(contact_id, message)
    except Exception as exc:  # noqa: BLE001 — never leave a contact stuck in 'queued'
        logger.exception("Send for contact %s failed", contact_id)
        contact.send_status = SendStatus.failed
        audit.record(db, user, "emails.send_failed", "contact", contact.id, {"mode": mode, "error": str(exc)})
        db.commit()
        raise ActionError(502 if isinstance(exc, gmail.GmailError) else 500, f"Could not send the email: {exc}")

    _mark_sent(db, contact, user, redirected, message_id, thread_id)
    # Deliberately no recipient address: erasure can't reach the audit log, and this is a prod address.
    audit.record(db, user, "emails.send", "contact", contact.id, {
        "mode": mode, "gmail_message_id": message_id, "force": force,
    })
    db.commit()
    return contact


def _mark_sent(db: Session, contact: Contact, user: User, redirected: bool, message_id: str, thread_id: str) -> None:
    # sent_dev keeps redirected sends out of reply tracking and out of the real "sent" numbers.
    contact.send_status = SendStatus.sent_dev if redirected else SendStatus.sent
    contact.sent_at, contact.sent_by = _now(), user.id
    contact.mail_service = "gmail_dev" if redirected else "gmail"
    contact.gmail_message_id, contact.gmail_thread_id = message_id, thread_id
    db.add(EmailEvent(
        contact_id=contact.id, direction=EmailDirection.out, gmail_message_id=message_id, thread_id=thread_id,
        snippet=f"{contact.draft_subject}\n\n{contact.draft_text[:300]}",
    ))


def _already_sent(db: Session, contact: Contact, user: User, redirected: bool) -> bool:
    """Did the previous attempt actually go out? A lost response must not become a second email."""
    if contact.send_status != SendStatus.failed or not contact.rfc_message_id:
        return False
    try:
        found = gmail.find_by_rfc_message_id(db, contact.rfc_message_id)
    except gmail.GmailError:
        return False  # can't tell; the operator asked to send, and a stuck contact is worse
    if found is None:
        return False
    logger.warning("Contact %s was already emailed (%s); reconciling instead of sending again", contact.id, found[0])
    _mark_sent(db, contact, user, redirected, *found)
    audit.record(db, user, "emails.send_reconciled", "contact", contact.id, {"gmail_message_id": found[0]})
    db.commit()
    return True


def _archive(contact_id: uuid.UUID, message: EmailMessage) -> None:
    """Keep a copy of exactly what was sent. Best effort: the email has already gone."""
    try:
        stamp = f"{_now().strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"  # two sends can share a second
        get_storage().save(f"{OUTBOX_PREFIX}/{stamp}-{contact_id}.eml", bytes(message), "message/rfc822")
    except Exception:  # noqa: BLE001
        logger.exception("Could not archive the sent email for contact %s", contact_id)


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


def execute_bulk(db: Session, contact_ids: list[uuid.UUID], user_id: uuid.UUID, expected_mode: str | None = None) -> None:
    user = db.get(User, user_id)
    for contact_id in contact_ids:
        if contact_id not in bulk_eligible(db, [contact_id]):
            continue
        try:
            send(db, contact_id, user, expected_mode=expected_mode)
        except ActionError as exc:
            db.rollback()
            if exc.status_code == 409 and expected_mode:
                # The mode changed after the batch was approved: stop rather than mail the rest.
                logger.warning("Bulk send stopped: %s", exc)
                break
            logger.warning("Bulk send for %s skipped: %s", contact_id, exc)
        except Exception as exc:  # noqa: BLE001 — one bad contact must not stop the rest of the batch
            db.rollback()
            logger.warning("Bulk send for %s skipped: %s", contact_id, exc, exc_info=True)


def _run_bulk(contact_ids: list[uuid.UUID], user_id: uuid.UUID, expected_mode: str | None) -> None:
    with SessionLocal() as db:
        execute_bulk(db, contact_ids, user_id, expected_mode)


_pool = WorkerPool("sending", workers=1)


def submit_bulk(contact_ids: list[uuid.UUID], user_id: uuid.UUID, expected_mode: str | None = None) -> None:
    _pool.submit(lambda ids, uid, mode: _run_bulk(ids, uid, mode), contact_ids, user_id, expected_mode)


def fail_interrupted_sends(db: Session) -> int:
    count = db.execute(
        update(Contact).where(Contact.send_status == SendStatus.queued).values(send_status=SendStatus.failed)
    ).rowcount
    db.commit()
    return count
