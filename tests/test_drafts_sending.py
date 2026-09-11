import email
import email.policy
import uuid

import pytest
from sqlalchemy import select

from app import llm, prompts, telemetry
from app.config import get_settings
from app.contacts import sending
from app.models import (
    AuditLog, Contact, DraftStatus, EmailEvent, EmailLookupStatus, Resume, Run, RunStatus, SendStatus,
    Startup, UserRole, VerificationStatus,
)


@pytest.fixture(autouse=True)
def no_tracing(monkeypatch):
    monkeypatch.setattr(telemetry, "langfuse_client", lambda db: None)


@pytest.fixture
def outbox(tmp_path, monkeypatch):
    monkeypatch.setattr(sending, "OUTBOX_DIR", tmp_path / "outbox")
    monkeypatch.setattr(sending, "sender_address", lambda db: "priya@example.com")
    return tmp_path / "outbox"


@pytest.fixture
def llm_answer(monkeypatch):
    state = {"answer": {"subject": "RAG engineer for Acme", "body": "Hi Jane,\n\nI build RAG pipelines...\n\nPriya"}, "calls": []}

    def fake(db, model, prompt, temperature, max_tokens=4096, **kwargs):
        state["calls"].append({"name": kwargs["name"], "prompt": prompt})
        return state["answer"]

    monkeypatch.setattr(llm, "complete_json", fake)
    return state


@pytest.fixture
def contact(db, tmp_path):
    prompts.seed_defaults(db)
    cv = tmp_path / "priya_cv.pdf"
    cv.write_bytes(b"%PDF-1.4 fake cv bytes")
    resume = Resume(filename="priya_cv.pdf", storage_path=str(cv),
                    parsed_profile={"name": "Priya Sharma", "current_role": "ML Engineer", "skills": ["RAG"]})
    db.add(resume)
    db.flush()
    run = Run(resume_id=resume.id, status=RunStatus.completed)
    db.add(run)
    db.flush()
    startup = Startup(run_id=run.id, name="Acme Vector", description="Retrieval infra")
    db.add(startup)
    db.flush()
    c = Contact(
        startup_id=startup.id, run_id=run.id, resume_id=resume.id, cv_used_id=resume.id, name="Jane Doe",
        title="CTO", linkedin_url=f"https://www.linkedin.com/in/jane-{uuid.uuid4().hex[:8]}",
        email="jane@acmevector.example", email_source="apollo", email_lookup_status=EmailLookupStatus.found,
        verification_status=VerificationStatus.verified, verified_title="Co-founder & CTO",
    )
    db.add(c)
    db.commit()
    return c


@pytest.fixture
def operator(make_user, login):
    return login(make_user(UserRole.operator))


def _generate(client, contact, **params):
    return client.post(f"/contacts/{contact.id}/draft/generate", params=params)


# --- drafts ----------------------------------------------------------------------------

def test_generate_draft_uses_profile_contact_and_active_prompt(db, contact, operator, llm_answer):
    body = _generate(operator, contact).json()
    assert body["draft_status"] == "generated" and body["draft_subject"] == "RAG engineer for Acme"
    prompt = llm_answer["calls"][0]["prompt"]
    assert "Jane Doe" in prompt and "Co-founder & CTO" in prompt and "Acme Vector" in prompt and "Priya Sharma" in prompt
    db.refresh(contact)
    assert contact.draft_prompt_version_id == prompts.get_active(db, "generate_draft").id


def test_regenerate_needs_force_and_resets_approval(db, contact, operator, llm_answer):
    _generate(operator, contact)
    operator.post(f"/contacts/{contact.id}/draft/approve")
    assert _generate(operator, contact).status_code == 409
    llm_answer["answer"] = {"subject": "Take two", "body": "Second version"}
    again = _generate(operator, contact, force="true").json()
    assert again["draft_subject"] == "Take two" and again["draft_status"] == "generated" and again["draft_approved_at"] is None


@pytest.mark.parametrize("change, status, message", [
    ({"email": None}, 400, "No email"),
    ({"verification_status": VerificationStatus.mismatch}, 400, "verified"),
    ({"do_not_contact": True}, 400, "not to be contacted"),
])
def test_draft_preconditions(db, contact, operator, llm_answer, change, status, message):
    for key, value in change.items():
        setattr(contact, key, value)
    db.commit()
    response = _generate(operator, contact)
    assert response.status_code == status and message in response.json()["detail"]
    assert llm_answer["calls"] == []  # nothing spent


def test_manual_email_counts_as_vouched_for(db, contact, operator, llm_answer):
    contact.verification_status, contact.email_source = VerificationStatus.unconfirmed, "manual"
    db.commit()
    assert _generate(operator, contact).status_code == 200


def test_empty_model_output_is_an_error_and_saves_nothing(db, contact, operator, llm_answer):
    llm_answer["answer"] = {"subject": "", "body": "x"}
    response = _generate(operator, contact)
    assert response.status_code == 502
    db.refresh(contact)
    assert contact.draft_status == DraftStatus.none


def test_edit_marks_edited_and_needs_reapproval(db, contact, operator, llm_answer):
    _generate(operator, contact)
    operator.post(f"/contacts/{contact.id}/draft/approve")
    same = operator.put(f"/contacts/{contact.id}/draft", json={"subject": "RAG engineer for Acme", "body": llm_answer["answer"]["body"]}).json()
    assert same["draft_status"] == "approved" and same["draft_edited"] is False  # no change, approval kept

    edited = operator.put(f"/contacts/{contact.id}/draft", json={"subject": "Better subject", "body": "Shorter body"}).json()
    assert edited["draft_edited"] is True and edited["draft_status"] == "generated"
    assert operator.put(f"/contacts/{contact.id}/draft", json={"subject": " ", "body": "x"}).status_code == 422


def test_viewer_cannot_draft_or_send(contact, make_user, login, llm_answer):
    viewer = login(make_user(UserRole.viewer))
    assert _generate(viewer, contact).status_code == 403
    assert viewer.post(f"/contacts/{contact.id}/send").status_code == 403


# --- sending -----------------------------------------------------------------------------

def _approved(db, operator, contact):
    _generate(operator, contact)
    assert operator.post(f"/contacts/{contact.id}/draft/approve").status_code == 200


def test_dev_send_writes_eml_with_cv_attached(db, contact, operator, llm_answer, outbox):
    _approved(db, operator, contact)
    body = operator.post(f"/contacts/{contact.id}/send").json()
    assert body["send_status"] == "sent_dev" and body["mail_service"] == "dev_eml" and body["sent_at"]

    files = list(outbox.glob("*.eml"))
    assert len(files) == 1
    message = email.message_from_bytes(files[0].read_bytes(), policy=email.policy.default)
    assert message["To"] == "jane@acmevector.example" and message["From"] == "priya@example.com"
    assert message["Subject"] == "RAG engineer for Acme"
    assert "I build RAG pipelines" in message.get_body(("plain",)).get_content()
    attachments = list(message.iter_attachments())
    assert [a.get_filename() for a in attachments] == ["priya_cv.pdf"]
    assert attachments[0].get_content() == b"%PDF-1.4 fake cv bytes"

    event = db.scalar(select(EmailEvent).where(EmailEvent.contact_id == contact.id))
    assert event.gmail_message_id == message["Message-ID"]
    assert db.scalar(select(AuditLog).where(AuditLog.action == "emails.send", AuditLog.target_id == str(contact.id))) is not None
    assert str(contact.id) in {c["id"] for c in operator.get("/contacts?view=sent").json()}
    assert len(operator.get(f"/contacts/{contact.id}/emails").json()) == 1


def test_resend_needs_force(db, contact, operator, llm_answer, outbox):
    _approved(db, operator, contact)
    operator.post(f"/contacts/{contact.id}/send")
    again = operator.post(f"/contacts/{contact.id}/send")
    assert again.status_code == 409 and "force" in again.json()["detail"]
    assert operator.post(f"/contacts/{contact.id}/send?force=true").status_code == 200
    assert len(list(outbox.glob("*.eml"))) == 2


def test_send_requires_approved_draft(db, contact, operator, llm_answer, outbox):
    _generate(operator, contact)
    response = operator.post(f"/contacts/{contact.id}/send")
    assert response.status_code == 409 and "Approve" in response.json()["detail"]
    assert not outbox.exists()


def test_prod_mode_never_sends(db, contact, operator, llm_answer, outbox, monkeypatch):
    _approved(db, operator, contact)
    monkeypatch.setattr(get_settings(), "app_mode", "prod")
    response = operator.post(f"/contacts/{contact.id}/send")
    assert response.status_code == 409 and "Real sending is off" in response.json()["detail"]
    db.refresh(contact)
    assert contact.send_status == SendStatus.none and not outbox.exists()


def test_missing_cv_file_blocks_send_without_side_effects(db, contact, operator, llm_answer, outbox):
    _approved(db, operator, contact)
    db.get(Resume, contact.cv_used_id).storage_path = "storage/gone.pdf"
    db.commit()
    response = operator.post(f"/contacts/{contact.id}/send")
    assert response.status_code == 400 and "missing" in response.json()["detail"]
    db.refresh(contact)
    assert contact.send_status == SendStatus.none


def test_do_not_contact_blocks_send_even_with_force(db, contact, operator, llm_answer, outbox):
    _approved(db, operator, contact)
    contact.do_not_contact = True
    db.commit()
    assert operator.post(f"/contacts/{contact.id}/send?force=true").status_code == 400


def test_bulk_draft_and_send_counts_and_execution(db, contact, operator, llm_answer, outbox, monkeypatch, make_user):
    from app.contacts import drafts

    worker_user = make_user(UserRole.operator).id
    queued = {}
    monkeypatch.setattr(drafts, "submit_bulk", lambda ids, uid: queued.setdefault("drafts", ids))
    monkeypatch.setattr(sending, "submit_bulk", lambda ids, uid: queued.setdefault("send", ids))

    assert operator.post("/contacts/drafts/generate-bulk", json={"contact_ids": []}).json() == {"eligible": 0, "queued": False}
    scoped = operator.post("/contacts/drafts/generate-bulk", json={"dry_run": False, "contact_ids": [str(contact.id)]}).json()
    assert scoped == {"eligible": 1, "queued": True} and queued["drafts"] == [contact.id]

    drafts.execute_bulk(db, [contact.id], worker_user)
    db.refresh(contact)
    assert contact.draft_status == DraftStatus.generated
    operator.post(f"/contacts/{contact.id}/draft/approve")

    dry = operator.post("/contacts/send-approved", json={"contact_ids": [str(contact.id)]}).json()
    assert dry == {"eligible": 1, "queued": False} and "send" not in queued
    sending.execute_bulk(db, [contact.id], worker_user)
    db.refresh(contact)
    assert contact.send_status == SendStatus.sent_dev


def test_interrupted_send_is_failed_at_startup(db, contact):
    contact.send_status = SendStatus.queued
    db.commit()
    assert sending.fail_interrupted_sends(db) >= 1
    db.refresh(contact)
    assert contact.send_status == SendStatus.failed
