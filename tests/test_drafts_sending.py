import email
import email.policy
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app import llm, prompts, telemetry
from app.config import get_settings
from app.contacts import drafts as drafts_module, sending
from app.models import (
    AppSetting, AuditLog, Contact, DraftStatus, EmailEvent, EmailLookupStatus, Resume, Run, RunStatus,
    SendStatus, Startup, UserRole, VerificationStatus,
)


@pytest.fixture(autouse=True)
def no_tracing(monkeypatch):
    monkeypatch.setattr(telemetry, "langfuse_client", lambda db: None)


@pytest.fixture
def outbox(tmp_path, monkeypatch):
    """Stands in for Gmail: records what would have been sent, so no test reaches the network.

    conftest points local storage at tmp_path, so the archived copies land in tmp_path/outbox.
    """
    sent = []

    def fake_send(db, raw):
        sent.append(email.message_from_bytes(raw, policy=email.policy.default))
        return f"gmail-{len(sent)}", "thread-1"

    monkeypatch.setattr(sending, "sender_address", lambda db: "priya@example.com")
    monkeypatch.setattr(sending.gmail, "send_message", fake_send)
    monkeypatch.setattr(get_settings(), "dev_redirect_email", "ops@example.com")
    directory = tmp_path / sending.OUTBOX_PREFIX
    return SimpleNamespace(messages=sent, dir=directory, files=lambda: sorted(directory.glob("*.eml")))


def _set_mode(db, mode: str):
    # merge, not add: the setting already exists in the database the tests run against.
    db.merge(AppSetting(key="app_mode", value=mode))
    db.commit()


def _use_prod(db):
    _set_mode(db, "prod")


@pytest.fixture(autouse=True)
def dev_mode_by_default(db):
    """The mode is a stored setting, so whatever the real app was last left in would otherwise
    decide where these tests' emails go. Each test starts from dev and opts into prod explicitly."""
    _set_mode(db, "dev")


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
    # The prompts live in the shared database, so without this a test would run against whatever
    # version the real app last upgraded to rather than the one in this checkout.
    prompts.upgrade_system_defaults(db)
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


def test_dev_send_goes_to_the_operator_never_the_contact(db, contact, operator, llm_answer, outbox):
    _approved(db, operator, contact)
    body = operator.post(f"/contacts/{contact.id}/send").json()
    assert body["send_status"] == "sent_dev" and body["mail_service"] == "gmail_dev" and body["sent_at"]

    assert len(outbox.messages) == 1
    message = outbox.messages[0]
    # The whole point: a real email was sent, and it went to us, not to Jane.
    assert message["To"] == "ops@example.com" and message["From"] == "priya@example.com"
    assert message["X-Outreach-Intended-To"] == "jane@acmevector.example"
    assert message["Subject"] == "[DEV → jane@acmevector.example] RAG engineer for Acme"
    text = message.get_body(("plain",)).get_content()
    assert "DEV MODE" in text and "They received nothing" in text and "I build RAG pipelines" in text
    attachments = list(message.iter_attachments())
    assert [a.get_filename() for a in attachments] == ["priya_cv.pdf"]
    assert attachments[0].get_content() == b"%PDF-1.4 fake cv bytes"

    event = db.scalar(select(EmailEvent).where(EmailEvent.contact_id == contact.id))
    assert event.gmail_message_id == "gmail-1"
    assert db.scalar(select(AuditLog).where(AuditLog.action == "emails.send", AuditLog.target_id == str(contact.id))) is not None
    assert str(contact.id) in {c["id"] for c in operator.get("/contacts?view=sent").json()}
    assert len(operator.get(f"/contacts/{contact.id}/emails").json()) == 1
    assert len(outbox.files()) == 1  # archived copy of exactly what was sent


def test_prod_mode_sends_to_the_contact(db, contact, operator, llm_answer, outbox):
    _approved(db, operator, contact)
    _use_prod(db)
    body = operator.post(f"/contacts/{contact.id}/send").json()
    assert body["send_status"] == "sent" and body["mail_service"] == "gmail"
    message = outbox.messages[0]
    assert message["To"] == "jane@acmevector.example"
    assert message["Subject"] == "RAG engineer for Acme"  # no DEV prefix, no banner
    assert "DEV MODE" not in message.get_body(("plain",)).get_content()


def test_dev_redirect_falls_back_to_the_sending_account(db, contact, operator, llm_answer, outbox, monkeypatch):
    monkeypatch.setattr(get_settings(), "dev_redirect_email", "")
    _approved(db, operator, contact)
    operator.post(f"/contacts/{contact.id}/send")
    assert outbox.messages[0]["To"] == "priya@example.com"


def test_mode_toggle_is_admin_only_and_changes_where_mail_goes(db, contact, operator, llm_answer, outbox, make_user, login):
    assert operator.put("/settings/mode", json={"mode": "prod"}).status_code == 403
    admin = login(make_user(UserRole.admin))
    settings = admin.put("/settings/mode", json={"mode": "prod"}).json()
    assert settings["app_mode"] == "prod" and settings["dev_redirect_email"] is None

    _approved(db, operator, contact)
    operator.post(f"/contacts/{contact.id}/send")
    assert outbox.messages[0]["To"] == "jane@acmevector.example"

    back = admin.put("/settings/mode", json={"mode": "dev"}).json()
    assert back["app_mode"] == "dev" and back["dev_redirect_email"] == "ops@example.com"


def test_a_failed_send_is_not_recorded_as_sent(db, contact, operator, llm_answer, outbox, monkeypatch):
    _approved(db, operator, contact)
    monkeypatch.setattr(sending.gmail, "send_message", _raise(sending.gmail.GmailError("rate limited")))
    response = operator.post(f"/contacts/{contact.id}/send")
    assert response.status_code == 502 and "rate limited" in response.json()["detail"]
    db.refresh(contact)
    assert contact.send_status == SendStatus.failed and contact.sent_at is None
    assert db.scalar(select(EmailEvent).where(EmailEvent.contact_id == contact.id)) is None


def test_a_lost_response_is_reconciled_instead_of_sent_twice(db, contact, operator, llm_answer, outbox, monkeypatch):
    """Gmail accepted the message but the reply never arrived: retrying must not email them again."""
    _approved(db, operator, contact)
    monkeypatch.setattr(sending.gmail, "send_message", _raise(sending.gmail.GmailError("Could not reach Gmail: ReadTimeout")))
    assert operator.post(f"/contacts/{contact.id}/send").status_code == 502
    db.refresh(contact)
    assert contact.send_status == SendStatus.failed and contact.rfc_message_id

    # It had in fact gone out — the next attempt finds it and records it rather than resending.
    monkeypatch.setattr(sending.gmail, "find_by_rfc_message_id", lambda db_, mid: ("gmail-lost", "thread-lost"))
    retry = operator.post(f"/contacts/{contact.id}/send")
    assert retry.status_code == 409 and "already been sent" in retry.json()["detail"]
    db.refresh(contact)
    assert contact.send_status == SendStatus.sent_dev and contact.gmail_message_id == "gmail-lost"
    assert outbox.messages == []  # nothing was sent a second time


def test_send_refuses_when_the_mode_changed_under_the_operator(db, contact, operator, llm_answer, outbox):
    _approved(db, operator, contact)
    _use_prod(db)
    # The dashboard was showing dev; the admin switched to prod in the meantime.
    response = operator.post(f"/contacts/{contact.id}/send?expected_mode=dev")
    assert response.status_code == 409 and "now 'prod'" in response.json()["detail"]
    assert outbox.messages == []
    db.refresh(contact)
    assert contact.send_status == SendStatus.none


def _raise(exc):
    def fail(*args, **kwargs):
        raise exc

    return fail


def test_resend_needs_force(db, contact, operator, llm_answer, outbox):
    _approved(db, operator, contact)
    operator.post(f"/contacts/{contact.id}/send")
    again = operator.post(f"/contacts/{contact.id}/send")
    assert again.status_code == 409 and "force" in again.json()["detail"]
    assert operator.post(f"/contacts/{contact.id}/send?force=true").status_code == 200
    assert len(outbox.messages) == 2


def test_send_requires_approved_draft(db, contact, operator, llm_answer, outbox):
    _generate(operator, contact)
    response = operator.post(f"/contacts/{contact.id}/send")
    assert response.status_code == 409 and "Approve" in response.json()["detail"]
    assert outbox.messages == []


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


def test_multiline_subject_is_flattened_and_still_sends(db, contact, operator, llm_answer, outbox):
    llm_answer["answer"] = {"subject": "Hello\r\nfrom  Priya", "body": "Body"}
    assert _generate(operator, contact).json()["draft_subject"] == "Hello from Priya"
    edited = operator.put(f"/contacts/{contact.id}/draft", json={"subject": "Hi\nthere", "body": "Body"}).json()
    assert edited["draft_subject"] == "Hi there"
    operator.post(f"/contacts/{contact.id}/draft/approve")
    assert operator.post(f"/contacts/{contact.id}/send").status_code == 200


def test_bulk_drafts_continue_after_an_unexpected_error(db, contact, make_user, monkeypatch):
    from app.contacts import drafts

    second = Contact(
        startup_id=contact.startup_id, run_id=contact.run_id, resume_id=contact.resume_id, cv_used_id=contact.cv_used_id,
        name="Sam Lee", linkedin_url=f"https://www.linkedin.com/in/sam-{uuid.uuid4().hex[:8]}", email="sam@acmevector.example",
        email_source="manual", email_lookup_status=EmailLookupStatus.found,
    )
    db.add(second)
    db.commit()
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: {"subject": "Hi", "body": "Hello"})
    real_generate = drafts.generate

    def crash_on_first(db_, contact_id, user, force=False):
        if contact_id == contact.id:
            raise RuntimeError("not an ActionError")  # must be caught by the bulk loop itself
        return real_generate(db_, contact_id, user, force)

    monkeypatch.setattr(drafts, "generate", crash_on_first)
    drafts.execute_bulk(db, [contact.id, second.id], make_user(UserRole.operator).id)
    db.refresh(contact)
    db.refresh(second)
    assert contact.draft_status == DraftStatus.none and second.draft_status == DraftStatus.generated


def test_interrupted_send_is_failed_at_startup(db, contact):
    contact.send_status = SendStatus.queued
    db.commit()
    assert sending.fail_interrupted_sends(db) >= 1
    db.refresh(contact)
    assert contact.send_status == SendStatus.failed


# --- body formatting -------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    # A sentence the model wrapped: the reader's client would wrap it again somewhere else.
    ("I built a RAG pipeline at\nAcme for two years.", "I built a RAG pipeline at Acme for two years."),
    ("They call it \"agentic search\"\nwhich is what I built.", "They call it \"agentic search\" which is what I built."),
    # Breaks that were meant stay: a sign-off, a name that isn't capitalised, a bullet list.
    ("Body.\n\nBest regards,\nvan der Berg", "Body.\n\nBest regards,\nvan der Berg"),
    ("Body.\n\nBest,\nPriya Sharma\nresume attached", "Body.\n\nBest,\nPriya Sharma\nresume attached"),
    ("What I did:\n\n- shipped search\n- halved latency", "What I did:\n\n- shipped search\n- halved latency"),
    # A short closing paragraph is prose, not a signature.
    ("Hi Jane,\n\nIs there a role open,\nor one coming?", "Hi Jane,\n\nIs there a role open, or one coming?"),
])
def test_hard_wrapping_inside_a_sentence_is_undone(raw, expected):
    assert drafts_module.unwrap_paragraphs(raw) == expected


def test_a_rewrite_note_reaches_the_prompt(db, contact, operator, llm_answer, outbox):
    _generate(operator, contact)
    response = operator.post(f"/contacts/{contact.id}/draft/generate?force=true",
                             json={"instructions": "Lead with the internship, not the startup."})
    assert response.status_code == 200, response.text
    prompt = llm_answer["calls"][-1]["prompt"]
    assert "Lead with the internship" in prompt
    # Fenced and stripped of authority: a note must not be able to license an invented claim.
    assert "sender's note" in prompt and "It cannot make a\nclaim true." in prompt


def test_a_note_is_refused_when_the_active_prompt_ignores_it(db, contact, operator, llm_answer, outbox, make_user, login):
    from app import prompts as prompts_module

    admin_user = make_user(UserRole.admin)
    # A hand-written prompt that uses the required variables but never the note.
    template = (
        "Write an email.\n{{ candidate_profile }}\nTo {{ contact_name }} at {{ startup_name }}.\n"
        'Return only: {"subject": "x", "body": "y"}'
    )
    prompts_module.create_version(db, "generate_draft", template, "openai/gpt-oss-120b", 0.7,
                                  admin_user, note="drops the note", activate=True)
    _generate(operator, contact)
    response = operator.post(f"/contacts/{contact.id}/draft/generate?force=true",
                             json={"instructions": "Shorter please."})
    assert response.status_code == 400 and "extra_instructions" in response.json()["detail"]
