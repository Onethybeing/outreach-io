import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require
from app.config import get_settings
from app.contacts import drafts, sending, service, verification
from app.db import get_db
from app.models import Contact, EmailDirection, EmailEvent, Resume, Startup, User
from app.schemas import (
    BulkActionIn, BulkActionOut, BulkDecisionIn, BulkDecisionResult, BulkLookupIn, BulkLookupOut,
    CandidateDecisionOut, ContactOut, DoNotContactIn, DraftEditIn, EmailEventOut, EmailProviderIn,
    ManualEmailIn, SettingsOut,
)

candidates_router = APIRouter(prefix="/candidates", tags=["candidates"])
contacts_router = APIRouter(prefix="/contacts", tags=["contacts"])
settings_router = APIRouter(prefix="/settings", tags=["settings"])


def _contact_rows(db: Session, *conditions) -> list[ContactOut]:
    rows = db.execute(
        select(Contact, Startup.name, Startup.website, Resume.filename)
        .join(Startup, Contact.startup_id == Startup.id)
        .join(Resume, Contact.cv_used_id == Resume.id)
        .where(*conditions)
        .order_by(Contact.updated_at.desc())
    )
    return [
        ContactOut(
            id=c.id, name=c.name, title=c.title, linkedin_url=c.linkedin_url, startup_id=c.startup_id,
            startup_name=startup_name, startup_website=website, run_id=c.run_id, cv_used_id=c.cv_used_id,
            cv_filename=cv, verification_status=c.verification_status.value, verification_note=c.verification_note,
            employment_verified=c.employment_verified, company_at_scrape=c.company_at_scrape,
            verified_title=c.verified_title, verified_company_url=c.verified_company_url, verified_at=c.verified_at,
            email=c.email, email_source=c.email_source, email_lookup_status=c.email_lookup_status.value,
            email_lookup_note=c.email_lookup_note, draft_status=c.draft_status.value,
            draft_subject=c.draft_subject, draft_text=c.draft_text, draft_generated_at=c.draft_generated_at,
            draft_approved_at=c.draft_approved_at, draft_edited=c.draft_edited,
            send_status=c.send_status.value, sent_at=c.sent_at, mail_service=c.mail_service,
            reply_status=c.reply_status.value if c.reply_status else None, do_not_contact=c.do_not_contact,
            created_at=c.created_at, updated_at=c.updated_at,
        )
        for c, startup_name, website, cv in rows
    ]


def contact_out(db: Session, contact_id: uuid.UUID) -> ContactOut:
    return _contact_rows(db, Contact.id == contact_id)[0]


# --- candidate decisions -------------------------------------------------------------

@candidates_router.post("/{candidate_id}/approve", response_model=CandidateDecisionOut)
def approve(candidate_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require("contacts.act"))):
    contact = service.approve(db, candidate_id, user)
    verification.submit_verification(contact.id)
    return CandidateDecisionOut(candidate_id=candidate_id, status="approved", contact=contact_out(db, contact.id))


@candidates_router.post("/{candidate_id}/reject", response_model=CandidateDecisionOut)
def reject(candidate_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require("contacts.act"))):
    service.reject(db, candidate_id, user)
    return CandidateDecisionOut(candidate_id=candidate_id, status="rejected")


@candidates_router.post("/{candidate_id}/reuse", response_model=CandidateDecisionOut)
def reuse(candidate_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require("contacts.act"))):
    contact = service.reuse(db, candidate_id, user)
    return CandidateDecisionOut(candidate_id=candidate_id, status="reused", contact=contact_out(db, contact.id))


@candidates_router.post("/{candidate_id}/update-contact", response_model=CandidateDecisionOut)
def update_contact(candidate_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require("contacts.act"))):
    contact = service.update_existing(db, candidate_id, user)
    verification.submit_verification(contact.id)
    return CandidateDecisionOut(candidate_id=candidate_id, status="updated", contact=contact_out(db, contact.id))


@candidates_router.post("/bulk", response_model=list[BulkDecisionResult])
def bulk_decide(body: BulkDecisionIn, db: Session = Depends(get_db), user: User = Depends(require("contacts.act"))):
    results = []
    for candidate_id in dict.fromkeys(body.candidate_ids):  # de-duplicate, keep order
        try:
            if body.action == "approve":
                contact = service.approve(db, candidate_id, user)
                verification.submit_verification(contact.id)
            else:
                service.reject(db, candidate_id, user)
            results.append(BulkDecisionResult(
                candidate_id=candidate_id, ok=True, status={"approve": "approved", "reject": "rejected"}[body.action],
            ))
        except service.ActionError as exc:
            db.rollback()
            results.append(BulkDecisionResult(candidate_id=candidate_id, ok=False, error=str(exc)))
    return results


# --- contacts ------------------------------------------------------------------------

@contacts_router.get("", response_model=list[ContactOut])
def list_contacts(
    view: str = Query("active", pattern="^(active|sent|no_email|all)$"),
    resume_id: uuid.UUID | None = None,
    startup_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(require("dashboard.view")),
):
    conditions = [service.view_condition(view)]
    if resume_id:
        conditions.append(Contact.cv_used_id == resume_id)
    if startup_id:
        conditions.append(Contact.startup_id == startup_id)
    return _contact_rows(db, *conditions)


@contacts_router.get("/{contact_id}", response_model=ContactOut)
def get_contact(contact_id: uuid.UUID, db: Session = Depends(get_db), _: User = Depends(require("dashboard.view"))):
    rows = _contact_rows(db, Contact.id == contact_id)
    if not rows:
        raise service.ActionError(404, "Contact not found")
    return rows[0]


@contacts_router.post("/{contact_id}/verify", response_model=ContactOut, status_code=status.HTTP_202_ACCEPTED)
def verify(contact_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require("contacts.act"))):
    service.request_verification(db, contact_id, user)
    verification.submit_verification(contact_id)
    return contact_out(db, contact_id)


@contacts_router.post("/{contact_id}/email/lookup", response_model=ContactOut)
def lookup_email(
    contact_id: uuid.UUID,
    force: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(require("contacts.act")),
):
    service.lookup_email(db, contact_id, user, force=force)
    return contact_out(db, contact_id)


@contacts_router.put("/{contact_id}/email", response_model=ContactOut)
def set_email(
    contact_id: uuid.UUID, body: ManualEmailIn,
    db: Session = Depends(get_db), user: User = Depends(require("contacts.act")),
):
    service.set_manual_email(db, contact_id, body.email, user)
    return contact_out(db, contact_id)


@contacts_router.put("/{contact_id}/do-not-contact", response_model=ContactOut)
def set_do_not_contact(
    contact_id: uuid.UUID, body: DoNotContactIn,
    db: Session = Depends(get_db), user: User = Depends(require("contacts.act")),
):
    """Honour (or undo) a request not to be emailed. Sending and lookups refuse while it is set."""
    service.set_do_not_contact(db, contact_id, body.do_not_contact, user)
    return contact_out(db, contact_id)


@contacts_router.delete("/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
def erase_contact(contact_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require("contacts.delete"))):
    """Erase a person's record and email history, for a deletion request. Not undoable."""
    service.erase_contact(db, contact_id, user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@contacts_router.post("/email/lookup-bulk", response_model=BulkLookupOut)
def lookup_bulk(body: BulkLookupIn, db: Session = Depends(get_db), user: User = Depends(require("contacts.act"))):
    """dry_run=true (default) only counts what would be spent; send dry_run=false to start."""
    eligible = service.bulk_lookup_eligible(db, body.contact_ids)
    provider = service.email_provider(db)
    if not body.dry_run and eligible:
        service.submit_bulk_lookup(eligible, user.id)
    return BulkLookupOut(provider=provider, eligible=len(eligible), queued=not body.dry_run and bool(eligible))


# --- drafts & sending -----------------------------------------------------------------

@contacts_router.post("/{contact_id}/draft/generate", response_model=ContactOut)
def generate_draft(
    contact_id: uuid.UUID, force: bool = False,
    db: Session = Depends(get_db), user: User = Depends(require("drafts.act")),
):
    drafts.generate(db, contact_id, user, force=force)
    return contact_out(db, contact_id)


@contacts_router.put("/{contact_id}/draft", response_model=ContactOut)
def edit_draft(
    contact_id: uuid.UUID, body: DraftEditIn,
    db: Session = Depends(get_db), user: User = Depends(require("drafts.act")),
):
    drafts.edit(db, contact_id, body.subject, body.body, user)
    return contact_out(db, contact_id)


@contacts_router.post("/{contact_id}/draft/approve", response_model=ContactOut)
def approve_draft(contact_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require("drafts.act"))):
    drafts.approve(db, contact_id, user)
    return contact_out(db, contact_id)


@contacts_router.post("/drafts/generate-bulk", response_model=BulkActionOut)
def generate_drafts_bulk(body: BulkActionIn, db: Session = Depends(get_db), user: User = Depends(require("drafts.act"))):
    """Contacts with an email and no draft. dry_run=true (default) only counts."""
    eligible = drafts.bulk_eligible(db, body.contact_ids)
    if not body.dry_run and eligible:
        drafts.submit_bulk(eligible, user.id)
    return BulkActionOut(eligible=len(eligible), queued=not body.dry_run and bool(eligible))


@contacts_router.post("/{contact_id}/send", response_model=ContactOut)
def send_email(
    contact_id: uuid.UUID, force: bool = False,
    db: Session = Depends(get_db), user: User = Depends(require("emails.send")),
):
    sending.send(db, contact_id, user, force=force)
    return contact_out(db, contact_id)


@contacts_router.post("/send-approved", response_model=BulkActionOut)
def send_approved_bulk(body: BulkActionIn, db: Session = Depends(get_db), user: User = Depends(require("emails.send"))):
    """Every contact with an approved draft that hasn't been sent. dry_run=true (default) only counts."""
    eligible = sending.bulk_eligible(db, body.contact_ids)
    if not body.dry_run and eligible:
        sending.submit_bulk(eligible, user.id)
    return BulkActionOut(eligible=len(eligible), queued=not body.dry_run and bool(eligible))


@contacts_router.get("/{contact_id}/emails", response_model=list[EmailEventOut])
def list_emails(contact_id: uuid.UUID, db: Session = Depends(get_db), _: User = Depends(require("dashboard.view"))):
    events = db.scalars(select(EmailEvent).where(EmailEvent.contact_id == contact_id).order_by(EmailEvent.received_at))
    return [
        EmailEventOut(
            id=e.id, direction="out" if e.direction == EmailDirection.out else "in",
            gmail_message_id=e.gmail_message_id, snippet=e.snippet,
            classification=e.classification.value if e.classification else None, received_at=e.received_at,
        )
        for e in events
    ]


# --- settings --------------------------------------------------------------------------

@settings_router.get("", response_model=SettingsOut)
def read_settings(db: Session = Depends(get_db), _: User = Depends(require("dashboard.view"))):
    return SettingsOut(
        app_mode=get_settings().app_mode,
        email_provider=service.email_provider(db),
        email_providers=sorted(service.EMAIL_PROVIDERS),
    )


@settings_router.put("/email-provider", response_model=SettingsOut)
def change_email_provider(body: EmailProviderIn, db: Session = Depends(get_db), user: User = Depends(require("settings.manage"))):
    service.set_email_provider(db, body.provider, user)
    return read_settings(db, user)
