"""Human-gated contact actions (PLAN.md §5): candidate decisions, email lookup, manual email."""

import logging
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import and_, select, true
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import audit
from app.contacts import apollo
from app.db import SessionLocal
from app.jobs import WorkerPool
from app.models import (
    AppSetting, Candidate, CandidateStatus, Contact, DraftStatus, EmailLookupStatus, Run, RunStatus,
    SendStatus, Startup, User, VerificationStatus,
)

logger = logging.getLogger(__name__)

EMAIL_PROVIDERS = {"apollo": apollo.find_email}
DEFAULT_EMAIL_PROVIDER = "apollo"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)
SENT = (SendStatus.sent, SendStatus.sent_dev)


class ActionError(Exception):
    def __init__(self, status_code: int, message: str, provider_unavailable: bool = False) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.provider_unavailable = provider_unavailable  # a bulk job should stop, not try every contact


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- candidate decisions -----------------------------------------------------------

def _pending_candidate(db: Session, candidate_id: uuid.UUID) -> tuple[Candidate, Run]:
    candidate = db.get(Candidate, candidate_id, with_for_update=True)
    if candidate is None:
        raise ActionError(404, "Candidate not found")
    run = db.get(Run, candidate.run_id)
    if run.status != RunStatus.completed:
        raise ActionError(409, "This candidate's run hasn't completed")
    if candidate.status != CandidateStatus.pending:
        raise ActionError(409, f"Candidate already {candidate.status.value}")
    return candidate, run


def _existing_contact(db: Session, candidate: Candidate) -> Contact | None:
    # Re-check now: someone may have approved the same person from another run since discovery.
    # Row lock: the status checks callers make on this contact must not interleave with a lookup
    # or verification claiming it (those lock the same row).
    contact = db.scalar(select(Contact).where(Contact.linkedin_url == candidate.linkedin_url).with_for_update())
    candidate.existing_contact_id = contact.id if contact else None
    return contact


def _decide(candidate: Candidate, status: CandidateStatus, user: User, contact: Contact | None) -> None:
    candidate.status, candidate.decided_by, candidate.decided_at = status, user.id, _now()
    candidate.contact_id = contact.id if contact else None


def approve(db: Session, candidate_id: uuid.UUID, user: User) -> Contact:
    candidate, run = _pending_candidate(db, candidate_id)
    if _existing_contact(db, candidate) is not None:
        db.commit()
        raise ActionError(409, "This person is already a contact — choose Reuse or Update instead")
    contact = Contact(
        startup_id=candidate.startup_id, run_id=run.id, resume_id=run.resume_id, cv_used_id=run.resume_id,
        name=candidate.name, title=candidate.title, linkedin_url=candidate.linkedin_url,
        verification_status=VerificationStatus.queued, approved_by=user.id,
    )
    db.add(contact)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ActionError(409, "This person was just added as a contact — refresh and choose Reuse or Update")
    _decide(candidate, CandidateStatus.approved, user, contact)
    audit.record(db, user, "candidates.approve", "candidate", candidate.id, {"contact_id": str(contact.id)})
    db.commit()
    return contact


def reject(db: Session, candidate_id: uuid.UUID, user: User) -> Candidate:
    candidate, _ = _pending_candidate(db, candidate_id)
    _decide(candidate, CandidateStatus.rejected, user, None)
    audit.record(db, user, "candidates.reject", "candidate", candidate.id)
    db.commit()
    return candidate


def reuse(db: Session, candidate_id: uuid.UUID, user: User) -> Contact:
    candidate, _ = _pending_candidate(db, candidate_id)
    contact = _existing_contact(db, candidate)
    if contact is None:
        raise ActionError(409, "No existing contact for this person — approve instead")
    _decide(candidate, CandidateStatus.reused, user, contact)
    audit.record(db, user, "candidates.reuse", "candidate", candidate.id, {"contact_id": str(contact.id)})
    db.commit()
    return contact


def update_existing(db: Session, candidate_id: uuid.UUID, user: User) -> Contact:
    """The person was found again (maybe at a new company): point the contact at this run and re-verify.

    Email and draft are reset because they depended on the old company. Send history, reply status
    and do_not_contact are kept, so an earlier send still blocks an accidental re-send.
    """
    candidate, run = _pending_candidate(db, candidate_id)
    contact = _existing_contact(db, candidate)
    if contact is None:
        raise ActionError(409, "No existing contact for this person — approve instead")
    if contact.verification_status in (VerificationStatus.queued, VerificationStatus.running):
        raise ActionError(409, "This contact is being verified right now — try again when it finishes")
    if contact.email_lookup_status == EmailLookupStatus.running:
        # Otherwise the lookup would finish after the reset and restore the old company's email.
        raise ActionError(409, "An email lookup is running for this contact — try again when it finishes")
    contact.startup_id, contact.run_id = candidate.startup_id, run.id
    contact.resume_id = contact.cv_used_id = run.resume_id
    contact.name, contact.title = candidate.name, candidate.title
    _reset_verification(contact, VerificationStatus.queued)
    contact.email = contact.email_source = contact.email_lookup_note = contact.email_looked_up_at = None
    contact.email_lookup_status = EmailLookupStatus.not_run
    contact.draft_status, contact.draft_text, contact.draft_prompt_version_id = DraftStatus.none, None, None
    _decide(candidate, CandidateStatus.updated, user, contact)
    audit.record(db, user, "candidates.update_contact", "candidate", candidate.id, {"contact_id": str(contact.id)})
    db.commit()
    return contact


def _reset_verification(contact: Contact, status: VerificationStatus) -> None:
    contact.verification_status = status
    contact.verification_note = contact.verification_source = contact.verified_at = None
    contact.company_at_scrape = contact.verified_title = contact.verified_company_url = None
    contact.employment_verified = None


def request_verification(db: Session, contact_id: uuid.UUID, user: User) -> Contact:
    contact = _contact(db, contact_id, lock=True)
    if contact.verification_status in (VerificationStatus.queued, VerificationStatus.running):
        raise ActionError(409, "Verification is already in progress")
    _reset_verification(contact, VerificationStatus.queued)
    audit.record(db, user, "contacts.verify", "contact", contact.id)
    db.commit()
    return contact


# --- email ---------------------------------------------------------------------------

def _contact(db: Session, contact_id: uuid.UUID, lock: bool = False) -> Contact:
    contact = db.get(Contact, contact_id, with_for_update=lock)
    if contact is None:
        raise ActionError(404, "Contact not found")
    return contact


def email_provider(db: Session) -> str:
    setting = db.get(AppSetting, "email_provider")
    return setting.value if setting and setting.value in EMAIL_PROVIDERS else DEFAULT_EMAIL_PROVIDER


def set_email_provider(db: Session, provider: str, user: User) -> str:
    if provider not in EMAIL_PROVIDERS:
        raise ActionError(400, f"Unknown email provider '{provider}'. Available: {', '.join(EMAIL_PROVIDERS)}")
    setting = db.get(AppSetting, "email_provider")
    if setting is None:
        db.add(AppSetting(key="email_provider", value=provider))
    else:
        setting.value = provider
    audit.record(db, user, "settings.email_provider", "setting", "email_provider", {"value": provider})
    db.commit()
    return provider


def lookup_email(db: Session, contact_id: uuid.UUID, user: User, force: bool = False) -> Contact:
    """Synchronous single lookup. Rules keep credits from being spent twice or on the wrong person."""
    contact = _contact(db, contact_id, lock=True)
    if contact.do_not_contact:
        raise ActionError(400, "This contact asked not to be contacted")
    if contact.email_lookup_status == EmailLookupStatus.running:
        raise ActionError(409, "An email lookup is already running for this contact")
    if not force and contact.email_lookup_status in (EmailLookupStatus.found, EmailLookupStatus.not_found):
        raise ActionError(409, f"Already looked up ({contact.email_lookup_status.value}) — use force to spend another lookup")
    if not force and contact.verification_status != VerificationStatus.verified:
        raise ActionError(400, "Employment isn't verified yet — verify first, or use force")

    provider = email_provider(db)
    contact.email_lookup_status = EmailLookupStatus.running
    db.commit()
    startup = db.get(Startup, contact.startup_id)
    try:
        result = EMAIL_PROVIDERS[provider](db, contact.name, contact.linkedin_url, startup.website)
    except Exception as exc:  # noqa: BLE001 — whatever happens, never leave the status stuck on 'running'
        db.rollback()
        unavailable = isinstance(exc, apollo.ProviderUnavailable)
        if isinstance(exc, (apollo.ProviderUnavailable, apollo.EmailLookupError)):
            message = str(exc)
        else:
            logger.exception("Email lookup for contact %s crashed", contact_id)
            message = f"Email lookup failed unexpectedly ({type(exc).__name__}) — see server logs"
        contact = _contact(db, contact_id, lock=True)
        contact.email_lookup_status, contact.email_lookup_note = EmailLookupStatus.failed, message
        contact.email_looked_up_at = _now()
        audit.record(db, user, "contacts.email_lookup", "contact", contact.id, {"provider": provider, "error": message})
        db.commit()
        raise ActionError(400 if unavailable else 502, message, provider_unavailable=unavailable)

    contact.email_looked_up_at, contact.email_lookup_note = _now(), result.note
    if result.found:
        contact.email, contact.email_source = result.email, provider
        contact.email_lookup_status = EmailLookupStatus.found
    else:
        contact.email_lookup_status = EmailLookupStatus.not_found
    audit.record(db, user, "contacts.email_lookup", "contact", contact.id, {"provider": provider, "found": result.found})
    db.commit()
    return contact


def set_manual_email(db: Session, contact_id: uuid.UUID, email: str, user: User) -> Contact:
    email = email.strip().lower()
    if not EMAIL_RE.match(email):
        raise ActionError(422, "That doesn't look like an email address")
    contact = _contact(db, contact_id, lock=True)
    if contact.email_lookup_status == EmailLookupStatus.running:
        raise ActionError(409, "An email lookup is running for this contact — wait for it to finish")
    contact.email, contact.email_source = email, "manual"
    contact.email_lookup_status, contact.email_lookup_note = EmailLookupStatus.found, "Entered manually"
    audit.record(db, user, "contacts.manual_email", "contact", contact.id)
    db.commit()
    return contact


def bulk_lookup_eligible(db: Session, contact_ids: list[uuid.UUID] | None = None) -> list[uuid.UUID]:
    """Verified, no email yet, never successfully looked up (not_run / failed), not do-not-contact."""
    query = select(Contact.id).where(
        Contact.verification_status == VerificationStatus.verified,
        Contact.email.is_(None),
        Contact.email_lookup_status.in_([EmailLookupStatus.not_run, EmailLookupStatus.failed]),
        Contact.do_not_contact.is_(False),
    )
    if contact_ids is not None:
        if not contact_ids:
            return []  # an empty selection means nothing, never "everyone"
        query = query.where(Contact.id.in_(contact_ids))
    return list(db.scalars(query))


def run_bulk_lookup(contact_ids: list[uuid.UUID], user_id: uuid.UUID) -> None:
    with SessionLocal() as db:
        execute_bulk_lookup(db, contact_ids, user_id)


def execute_bulk_lookup(db: Session, contact_ids: list[uuid.UUID], user_id: uuid.UUID) -> None:
    user = db.get(User, user_id)
    for contact_id in contact_ids:
        if contact_id not in bulk_lookup_eligible(db, [contact_id]):
            continue  # changed since the request (manual email, lookup done elsewhere)
        try:
            lookup_email(db, contact_id, user)
        except ActionError as exc:
            if exc.provider_unavailable:
                break  # the provider can't answer anyone; leave the rest untouched
            continue


_bulk_pool = WorkerPool("email-lookup", workers=1)


def submit_bulk_lookup(contact_ids: list[uuid.UUID], user_id: uuid.UUID) -> None:
    _bulk_pool.submit(lambda ids, uid: run_bulk_lookup(ids, uid), contact_ids, user_id)


# --- listing ---------------------------------------------------------------------------

VIEWS = ("active", "sent", "no_email", "all")


def view_condition(view: str):
    no_email = and_(
        Contact.email.is_(None),
        Contact.email_lookup_status.in_([EmailLookupStatus.not_found, EmailLookupStatus.failed]),
        Contact.send_status.notin_(SENT),
    )
    if view == "sent":
        return Contact.send_status.in_(SENT)
    if view == "no_email":
        return no_email
    if view == "active":
        return and_(Contact.send_status.notin_(SENT), ~no_email)
    return true()
