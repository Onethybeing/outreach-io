"""Reply tracking (PLAN.md §2B track_replies + classify_reply).

For each contact that was really emailed, find new inbound messages in the sender's Gmail — replies
from the contact, messages in the sent thread, and bounce notices naming the contact's address —
label each one, store it, and update the contact. Dev-mode sends never reach a mailbox, so they're skipped.
"""

import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import evals, llm, prompts, suppression, telemetry, vault
from app.models import Contact, EmailDirection, EmailEvent, ReplyClassification, ReplyStatus, SendStatus
from app.replies import gmail

logger = logging.getLogger(__name__)

TRACK_FOR_DAYS = 60
LABELS = {label.value for label in ReplyClassification}
BOUNCE_SENDERS = ("mailer-daemon@", "postmaster@")
BOUNCE_SUBJECT = re.compile(r"undeliverable|delivery status notification|delivery (has )?failed|returned mail|failure notice", re.I)
# "Delivery Status Notification (Delay)" comes from the same senders but the mail usually arrives later.
# Only explicit delay markers count, and explicit failure wording always wins: quoted subjects and
# SMTP explanations ("temporary failure") appear in real bounces too.
FAILURE_WORDING = re.compile(
    r"\(failure\)|undeliverable|could ?n[o']t be delivered|permanent(ly)?|delivery (has )?failed|failure notice|"
    r"returned mail|address not found|does not exist|user unknown|mailbox unavailable", re.I,
)
DELAY_SUBJECT = re.compile(r"\(delay\)|^\s*delivery delayed|^\s*delayed mail", re.I)
DELAY_BODY = re.compile(r"will (retry|keep trying)|still being retried|has been delayed", re.I)
OOO_SUBJECT = re.compile(r"out of (the )?office|automatic reply|auto(-|\s)?reply|away from (the )?office", re.I)

_poll_lock = threading.Lock()


class PollInProgress(Exception):
    pass


@dataclass
class PollSummary:
    contacts: int = 0
    new_messages: int = 0
    by_label: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def classify(db: Session, message: gmail.GmailMessage, sent_text: str | None) -> tuple[ReplyClassification | None, str]:
    """Cheap rules for unambiguous cases; the classify_reply prompt for everything else.

    Returns (None, note) for delivery-delay notices: recorded, but not a response and not a bounce.
    """
    if message.from_address.startswith(BOUNCE_SENDERS) or BOUNCE_SUBJECT.search(message.subject):
        is_failure = FAILURE_WORDING.search(message.subject) or FAILURE_WORDING.search(message.snippet)
        is_delay = DELAY_SUBJECT.search(message.subject) or DELAY_BODY.search(message.snippet)
        if is_delay and not is_failure:
            return None, "Delivery delayed (not a bounce; the mail may still arrive)"
        return ReplyClassification.bounce, "Delivery failure notice"
    if message.auto_submitted or OOO_SUBJECT.search(message.subject):
        return ReplyClassification.out_of_office, "Automatic reply"
    prompt = prompts.get_active(db, "classify_reply")
    text = prompts.render("classify_reply", prompt.template, {
        "reply_text": message.body_text or message.snippet, "sent_email": sent_text or "",
    })
    result = llm.complete_json(db, prompt.model, text, prompt.temperature, name="classify_reply",
                               metadata={"prompt_version": prompt.version})
    label = str(result.get("label") or "").strip().lower()
    summary = str(result.get("summary") or "").strip() or message.snippet[:200]
    # Anything unrecognised is treated as a human reply: surfacing it beats silently dropping it.
    return ReplyClassification(label) if label in LABELS else ReplyClassification.reply, summary


def trackable_contacts(db: Session) -> list[Contact]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=TRACK_FOR_DAYS)
    return list(db.scalars(
        select(Contact).where(
            Contact.send_status == SendStatus.sent, Contact.email.isnot(None), Contact.sent_at >= cutoff,
        )
    ))


def _candidate_message_ids(db: Session, contact: Contact) -> list[str]:
    after = int(contact.sent_at.timestamp())
    ids = gmail.search(db, f"from:{contact.email} after:{after}")
    ids += gmail.search(db, f'from:(mailer-daemon OR postmaster) "{contact.email}" after:{after}')
    if contact.gmail_thread_id:
        try:
            ids += gmail.thread_message_ids(db, contact.gmail_thread_id)
        except gmail.GmailNotFound:
            pass  # thread deleted in Gmail; the searches above still find replies
    return list(dict.fromkeys(ids))


def _apply(contact: Contact, label: ReplyClassification | None, received_at: datetime) -> None:
    if label is None:
        return
    if label == ReplyClassification.bounce:
        # A human reply proves delivery, so a later/other delivery notice can't override it.
        if contact.reply_status != ReplyStatus.replied:
            contact.reply_status = ReplyStatus.bounced
    elif label in (ReplyClassification.reply, ReplyClassification.unsubscribe):
        contact.reply_status = ReplyStatus.replied
        contact.replied_at = min(contact.replied_at or received_at, received_at)
        if label == ReplyClassification.unsubscribe:
            contact.do_not_contact = True  # permanent: re-verify / update never clears it
    elif contact.reply_status is None:
        contact.reply_status = ReplyStatus.waiting  # out-of-office: still waiting for a real answer


def poll_contact(db: Session, contact: Contact, sender_address: str, summary: PollSummary) -> None:
    known = set(db.scalars(select(EmailEvent.gmail_message_id).where(EmailEvent.contact_id == contact.id)))
    messages = []
    for message_id in _candidate_message_ids(db, contact):
        if message_id in known:
            continue
        try:
            message = gmail.get_message(db, message_id)
        except gmail.GmailNotFound:
            continue  # deleted between search and fetch
        if message.from_address != sender_address.lower():  # skip our own message in the thread
            messages.append(message)

    # Oldest first, so the contact's status reflects the real order of events.
    labels = []
    for message in sorted(messages, key=lambda m: m.received_at):
        label, note = classify(db, message, contact.draft_text)
        db.add(EmailEvent(
            contact_id=contact.id, direction=EmailDirection.in_, gmail_message_id=message.id,
            thread_id=message.thread_id, snippet=f"{message.subject}\n\n{note}"[:2000],
            classification=label, received_at=message.received_at,
        ))
        _apply(contact, label, message.received_at)
        summary.new_messages += 1
        key = label.value if label else "delivery_delayed"
        summary.by_label[key] = summary.by_label.get(key, 0) + 1
        if label is not None:
            labels.append(label)
    if ReplyClassification.unsubscribe in labels:
        # Outlives this contact row, so erasing them later can't undo the unsubscribe.
        suppression.suppress(db, contact.linkedin_url, "unsubscribe")
    db.commit()
    for label in labels:
        evals.safe_signal(evals.record_reply, db, contact, label)


def poll_all(db: Session) -> PollSummary:
    if not _poll_lock.acquire(blocking=False):
        raise PollInProgress("A reply check is already running")
    try:
        summary = PollSummary()
        contacts = trackable_contacts(db)
        if not contacts:
            return summary
        sender = vault.get_credential(db, "gmail")["sender_address"]
        client = telemetry.langfuse_client(db)
        with telemetry.activate(telemetry.RunTelemetry(langfuse=client)):
            for contact in contacts:
                summary.contacts += 1
                try:
                    poll_contact(db, contact, sender, summary)
                except gmail.GmailAuthError as exc:
                    db.rollback()
                    summary.errors.append(str(exc))
                    break  # sign-in problem: every other contact would fail the same way
                except gmail.GmailError as exc:
                    db.rollback()
                    summary.errors.append(f"{contact.name}: {exc}")  # e.g. a rate limit; retry next poll
                except Exception as exc:  # noqa: BLE001 — one contact's odd message mustn't stop the rest
                    db.rollback()
                    logger.exception("Reply check failed for contact %s", contact.id)
                    summary.errors.append(f"{contact.name}: {type(exc).__name__}")
        telemetry.flush(client)
        return summary
    finally:
        _poll_lock.release()
