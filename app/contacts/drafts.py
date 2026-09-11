"""Draft generation, editing and approval (PLAN.md §2B generate_draft, §5)."""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, llm, prompts, telemetry, vault
from app.contacts.service import SENT, ActionError
from app.db import SessionLocal
from app.jobs import WorkerPool
from app.models import Contact, DraftStatus, Resume, Startup, User, VerificationStatus

logger = logging.getLogger(__name__)

MAX_SUBJECT = 200
EXPECTED_ERRORS = (llm.LLMError, prompts.PromptError, vault.VaultError)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def clean_subject(subject: str) -> str:
    """One line: email headers can't contain line breaks, so a multi-line subject could never be sent."""
    return " ".join(subject.split())


def _check_can_draft(contact: Contact, force: bool) -> None:
    if contact.do_not_contact:
        raise ActionError(400, "This contact asked not to be contacted")
    if not contact.email:
        raise ActionError(400, "No email yet — find or enter one first")
    # Manual entry is the user vouching for the contact (PLAN.md §15 #7).
    if contact.verification_status != VerificationStatus.verified and contact.email_source != "manual":
        raise ActionError(400, "Employment isn't verified — verify first, or enter the email manually")
    if contact.send_status in SENT and not force:
        raise ActionError(409, "Already emailed — use force to draft again")
    if contact.draft_status != DraftStatus.none and not force:
        raise ActionError(409, f"A draft already exists ({contact.draft_status.value}) — use force to regenerate")


class State(TypedDict, total=False):
    subject: str
    body: str
    prompt_id: str


def _build_graph(db: Session, contact: Contact, startup: Startup, resume: Resume):
    def generate_draft(state: State) -> dict:
        prompt = prompts.get_active(db, "generate_draft")
        profile = resume.parsed_profile or {}
        text = prompts.render("generate_draft", prompt.template, {
            "candidate_profile": json.dumps(profile),
            "contact_name": contact.name,
            "contact_title": contact.verified_title or contact.title or "",
            "startup_name": startup.name,
            "startup_description": startup.description or "",
            "sender_name": profile.get("name") or "",
        })
        raw = llm.complete_json(
            db, prompt.model, text, prompt.temperature,
            name="generate_draft", metadata={"prompt_version": prompt.version, "prompt_id": str(prompt.id)},
        )
        subject = clean_subject(str(raw.get("subject") or ""))
        body = str(raw.get("body") or "").strip()
        if not subject or not body:
            raise llm.LLMError("generate_draft: the model returned an empty subject or body")
        return {"subject": subject[:MAX_SUBJECT], "body": body, "prompt_id": str(prompt.id)}

    graph = StateGraph(State)
    graph.add_node("generate_draft", generate_draft)
    graph.add_edge(START, "generate_draft")
    graph.add_edge("generate_draft", END)
    return graph.compile()


def generate(db: Session, contact_id: uuid.UUID, user: User, force: bool = False) -> Contact:
    contact = db.get(Contact, contact_id, with_for_update=True)
    if contact is None:
        raise ActionError(404, "Contact not found")
    _check_can_draft(contact, force)
    startup = db.get(Startup, contact.startup_id)
    resume = db.get(Resume, contact.cv_used_id)
    if not resume.parsed_profile:
        raise ActionError(400, "The CV used for this contact hasn't been parsed — run discovery on it first")
    db.commit()  # release the lock during the LLM call; the checks are repeated before saving

    client = telemetry.langfuse_client(db)
    tel = telemetry.RunTelemetry(langfuse=client)
    try:
        with telemetry.activate(tel), telemetry.observation(
            "generate_draft_job", as_type="agent",
            trace_context={"trace_id": telemetry.trace_id_for(f"draft:{contact_id}:{_now().isoformat()}")},
            input={"contact": contact.name, "startup": startup.name, "cv": resume.filename},
        ) as root, telemetry.trace_attributes(
            trace_name="generate_draft", session_id=str(contact_id), tags=["contact", "draft"],
        ):
            state = _build_graph(db, contact, startup, resume).invoke({})
            root.update(output={"subject": state["subject"]})
    except EXPECTED_ERRORS as exc:
        db.rollback()
        raise ActionError(502, f"Draft generation failed: {exc}")
    except Exception as exc:  # noqa: BLE001 — surface as a normal action error, never a bare 500 / dead bulk job
        db.rollback()
        logger.exception("Draft generation for contact %s crashed", contact_id)
        raise ActionError(502, f"Draft generation failed unexpectedly ({type(exc).__name__}) — see server logs")
    finally:
        telemetry.flush(client)

    contact = db.get(Contact, contact_id, with_for_update=True)
    _check_can_draft(contact, force)  # state may have changed while the model was writing
    contact.draft_subject, contact.draft_text = state["subject"], state["body"]
    contact.draft_prompt_version_id = uuid.UUID(state["prompt_id"])
    contact.draft_status, contact.draft_generated_at = DraftStatus.generated, _now()
    contact.draft_approved_at = contact.draft_approved_by = None
    contact.draft_edited = False
    audit.record(db, user, "drafts.generate", "contact", contact.id, {"prompt_id": state["prompt_id"], "force": force})
    db.commit()
    return contact


def edit(db: Session, contact_id: uuid.UUID, subject: str, body: str, user: User) -> Contact:
    subject, body = clean_subject(subject), body.strip()
    if not subject or not body:
        raise ActionError(422, "Subject and body can't be empty")
    if len(subject) > MAX_SUBJECT:
        raise ActionError(422, f"Subject is longer than {MAX_SUBJECT} characters")
    contact = db.get(Contact, contact_id, with_for_update=True)
    if contact is None:
        raise ActionError(404, "Contact not found")
    if contact.draft_status == DraftStatus.none:
        raise ActionError(409, "There's no draft to edit — generate one first")
    if contact.send_status in SENT:
        raise ActionError(409, "This draft was already sent")
    changed = (subject, body) != (contact.draft_subject, contact.draft_text)
    if changed:
        contact.draft_subject, contact.draft_text, contact.draft_edited = subject, body, True
        # Approval covered the old text; an edited draft needs a fresh look.
        contact.draft_status, contact.draft_approved_at, contact.draft_approved_by = DraftStatus.generated, None, None
        audit.record(db, user, "drafts.edit", "contact", contact.id)
    db.commit()
    return contact


def approve(db: Session, contact_id: uuid.UUID, user: User) -> Contact:
    contact = db.get(Contact, contact_id, with_for_update=True)
    if contact is None:
        raise ActionError(404, "Contact not found")
    if contact.draft_status != DraftStatus.generated:
        raise ActionError(409, "Only a generated, not-yet-approved draft can be approved")
    if not contact.email or contact.do_not_contact:
        raise ActionError(400, "This contact can't be emailed (no email, or asked not to be contacted)")
    contact.draft_status, contact.draft_approved_by, contact.draft_approved_at = DraftStatus.approved, user.id, _now()
    audit.record(db, user, "drafts.approve", "contact", contact.id)
    db.commit()
    return contact


def bulk_eligible(db: Session, contact_ids: list[uuid.UUID] | None = None) -> list[uuid.UUID]:
    """Contacts with a usable email and no draft yet (the "generate drafts for all with email" button)."""
    query = select(Contact.id).where(
        Contact.email.isnot(None),
        Contact.do_not_contact.is_(False),
        Contact.draft_status == DraftStatus.none,
        Contact.send_status.notin_(SENT),
        (Contact.verification_status == VerificationStatus.verified) | (Contact.email_source == "manual"),
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
            generate(db, contact_id, user)
        except Exception as exc:  # noqa: BLE001 — one bad contact must not stop the rest of the batch
            db.rollback()
            logger.warning("Bulk draft for %s skipped: %s", contact_id, exc, exc_info=not isinstance(exc, ActionError))


def _run_bulk(contact_ids: list[uuid.UUID], user_id: uuid.UUID) -> None:
    with SessionLocal() as db:
        execute_bulk(db, contact_ids, user_id)


_pool = WorkerPool("drafts", workers=1)  # Groq's tokens/minute limit makes parallel drafting pointless


def submit_bulk(contact_ids: list[uuid.UUID], user_id: uuid.UUID) -> None:
    _pool.submit(lambda ids, uid: _run_bulk(ids, uid), contact_ids, user_id)
