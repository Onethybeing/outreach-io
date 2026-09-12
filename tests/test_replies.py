import base64
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app import llm, prompts, suppression, telemetry, vault
from app.config import get_settings
from app.models import (
    Contact, EmailEvent, ReplyClassification, ReplyStatus, Resume, Run, RunStatus, SendStatus, Startup, UserRole,
)
from app.replies import gmail, tracker

SENDER = "priya@example.com"


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def _msg(id_, from_, subject="Re: hello", body="Thanks, let's talk", auto=False, thread="t1", minutes_after=5):
    return gmail.GmailMessage(
        id=id_, thread_id=thread, from_address=from_, subject=subject,
        received_at=datetime.now(timezone.utc) + timedelta(minutes=minutes_after), snippet=body[:50],
        body_text=body, auto_submitted=auto,
    )


@pytest.fixture(autouse=True)
def no_tracing(monkeypatch):
    monkeypatch.setattr(telemetry, "langfuse_client", lambda db: None)


@pytest.fixture
def sent_contact(db):
    prompts.seed_defaults(db)
    resume = Resume(filename="cv.pdf", storage_path="x.pdf")
    db.add(resume)
    db.flush()
    run = Run(resume_id=resume.id, status=RunStatus.completed)
    db.add(run)
    db.flush()
    startup = Startup(run_id=run.id, name="Acme")
    db.add(startup)
    db.flush()
    contact = Contact(
        startup_id=startup.id, run_id=run.id, resume_id=resume.id, cv_used_id=resume.id, name="Jane",
        linkedin_url=f"https://www.linkedin.com/in/jane-{uuid.uuid4().hex[:8]}", email="jane@acme.example",
        send_status=SendStatus.sent, sent_at=datetime.now(timezone.utc) - timedelta(hours=1),
        gmail_thread_id="t1", draft_text="Hi Jane, resume attached.",
    )
    db.add(contact)
    db.commit()
    return contact


@pytest.fixture
def inbox(monkeypatch):
    """A fake mailbox: messages by id, plus what each search/thread returns."""
    state = {"messages": {}, "search": {}, "thread": [], "llm": {"label": "reply", "summary": "Wants a call"}, "llm_calls": 0}

    def search(db, query, max_results=20):
        return [mid for key, ids in state["search"].items() if key in query for mid in ids]

    def fake_llm(db, model, prompt, temperature, max_tokens=4096, **kwargs):
        state["llm_calls"] += 1
        return state["llm"]

    monkeypatch.setattr(gmail, "search", search)
    monkeypatch.setattr(gmail, "thread_message_ids", lambda db, tid: state["thread"])
    monkeypatch.setattr(gmail, "get_message", lambda db, mid: state["messages"][mid])
    monkeypatch.setattr(llm, "complete_json", fake_llm)
    monkeypatch.setattr(vault, "get_credential", lambda db, provider: {"sender_address": SENDER})
    return state


def _events(db, contact):
    return list(db.scalars(select(EmailEvent).where(EmailEvent.contact_id == contact.id)))


# --- classification --------------------------------------------------------------------

@pytest.mark.parametrize("message, expected, uses_llm", [
    (_msg("1", "mailer-daemon@googlemail.com", "Delivery Status Notification (Failure)"), ReplyClassification.bounce, False),
    (_msg("2", "postmaster@acme.example", "Hello"), ReplyClassification.bounce, False),
    (_msg("3", "jane@acme.example", "Undeliverable: hi"), ReplyClassification.bounce, False),
    (_msg("4", "jane@acme.example", "Hi", auto=True), ReplyClassification.out_of_office, False),
    (_msg("5", "jane@acme.example", "Automatic reply: Hi"), ReplyClassification.out_of_office, False),
    (_msg("6", "jane@acme.example", "Re: Hi"), ReplyClassification.reply, True),
])
def test_classification_rules_before_llm(db, inbox, message, expected, uses_llm):
    prompts.seed_defaults(db)
    label, _ = tracker.classify(db, message, "sent text")
    assert label == expected and (inbox["llm_calls"] == 1) is uses_llm


@pytest.mark.parametrize("subject, snippet, expected", [
    ("Delivery Status Notification (Delay)", "Delivery incomplete. Gmail will retry for 46 more hours.", None),
    ("Delayed Mail (still being retried)", "Your message has been delayed", None),
    ("Undeliverable: Temporary staffing for your launch", "Your message couldn't be delivered", ReplyClassification.bounce),
    ("Undeliverable: Sorry for the delayed follow-up", "", ReplyClassification.bounce),
    ("Delivery Status Notification (Failure)", "451 4.3.0 temporary failure; retries exhausted", ReplyClassification.bounce),
    ("Mail delivery failed: returning message to sender", "The mailbox is temporarily unavailable, will retry", ReplyClassification.bounce),
])
def test_delay_vs_failure_wording(db, inbox, subject, snippet, expected):
    message = gmail.GmailMessage("x", "t", "mailer-daemon@googlemail.com", subject,
                                 datetime.now(timezone.utc), snippet, snippet, False)
    assert tracker.classify(db, message, None)[0] == expected


def test_unknown_llm_label_is_treated_as_reply(db, inbox):
    prompts.seed_defaults(db)
    inbox["llm"] = {"label": "spam?", "summary": "odd"}
    assert tracker.classify(db, _msg("7", "jane@acme.example"), None)[0] == ReplyClassification.reply


# --- polling ------------------------------------------------------------------------------

def test_reply_is_stored_once_and_marks_replied(db, sent_contact, inbox):
    inbox["messages"] = {"m1": _msg("m1", "jane@acme.example"), "own": _msg("own", SENDER, "Hi Jane")}
    inbox["search"] = {"from:jane@acme.example": ["m1"]}
    inbox["thread"] = ["own", "m1"]

    summary = tracker.poll_all(db)
    assert summary.new_messages == 1 and summary.by_label == {"reply": 1}
    db.refresh(sent_contact)
    assert sent_contact.reply_status == ReplyStatus.replied and sent_contact.replied_at is not None
    events = _events(db, sent_contact)
    assert [e.gmail_message_id for e in events] == ["m1"] and events[0].classification == ReplyClassification.reply

    again = tracker.poll_all(db)  # idempotent
    assert again.new_messages == 0 and len(_events(db, sent_contact)) == 1


def test_bounce_found_by_address_search(db, sent_contact, inbox):
    inbox["messages"] = {"b1": _msg("b1", "mailer-daemon@googlemail.com", "Delivery Status Notification (Failure)")}
    inbox["search"] = {"mailer-daemon": ["b1"]}
    tracker.poll_all(db)
    db.refresh(sent_contact)
    assert sent_contact.reply_status == ReplyStatus.bounced and inbox["llm_calls"] == 0


def test_unsubscribe_sets_do_not_contact(db, sent_contact, inbox):
    inbox["messages"] = {"u1": _msg("u1", "jane@acme.example", body="Please remove me from your list")}
    inbox["search"] = {"from:jane@acme.example": ["u1"]}
    inbox["llm"] = {"label": "unsubscribe", "summary": "Asked to be removed"}
    tracker.poll_all(db)
    db.refresh(sent_contact)
    assert sent_contact.do_not_contact is True and sent_contact.reply_status == ReplyStatus.replied
    # Recorded separately too, so erasing the contact later can't undo the unsubscribe.
    assert suppression.is_suppressed(db, sent_contact.linkedin_url)


def test_out_of_office_keeps_waiting(db, sent_contact, inbox):
    inbox["messages"] = {"o1": _msg("o1", "jane@acme.example", "Out of Office: back Monday")}
    inbox["search"] = {"from:jane@acme.example": ["o1"]}
    tracker.poll_all(db)
    db.refresh(sent_contact)
    assert sent_contact.reply_status == ReplyStatus.waiting and sent_contact.replied_at is None


def test_dev_and_old_sends_are_not_tracked(db, sent_contact, inbox):
    sent_contact.send_status = SendStatus.sent_dev
    db.commit()
    assert sent_contact not in tracker.trackable_contacts(db)
    sent_contact.send_status = SendStatus.sent
    sent_contact.sent_at = datetime.now(timezone.utc) - timedelta(days=tracker.TRACK_FOR_DAYS + 1)
    db.commit()
    assert sent_contact not in tracker.trackable_contacts(db)


def test_gmail_signin_problem_stops_poll_and_is_reported(db, sent_contact, inbox, monkeypatch):
    def expired(db_, query, max_results=20):
        raise gmail.GmailAuthError("Gmail sign-in expired or was revoked")

    monkeypatch.setattr(gmail, "search", expired)
    summary = tracker.poll_all(db)
    assert summary.errors == ["Gmail sign-in expired or was revoked"] and summary.new_messages == 0


def test_delay_notice_is_recorded_but_not_a_bounce(db, sent_contact, inbox):
    inbox["messages"] = {"d1": _msg("d1", "mailer-daemon@googlemail.com", "Delivery Status Notification (Delay)",
                                    body="Delivery incomplete. Gmail will retry for 46 more hours.")}
    inbox["search"] = {"mailer-daemon": ["d1"]}
    summary = tracker.poll_all(db)
    db.refresh(sent_contact)
    assert sent_contact.reply_status is None and summary.by_label == {"delivery_delayed": 1}
    assert _events(db, sent_contact)[0].classification is None
    assert tracker.poll_all(db).new_messages == 0  # stored, so not re-fetched


def test_reply_wins_over_a_delivery_notice_in_the_same_poll(db, sent_contact, inbox):
    inbox["messages"] = {
        "r1": _msg("r1", "jane@acme.example", "Re: hello", minutes_after=5),
        "b1": _msg("b1", "mailer-daemon@googlemail.com", "Undeliverable: hello", minutes_after=10),
    }
    inbox["search"] = {"from:jane@acme.example": ["r1"], "mailer-daemon": ["b1"]}
    tracker.poll_all(db)
    db.refresh(sent_contact)
    assert sent_contact.reply_status == ReplyStatus.replied


def test_deleted_thread_or_message_and_per_contact_errors_dont_stop_the_poll(db, sent_contact, inbox, monkeypatch):
    # Second contact whose lookups hit a transient Gmail error.
    other = Contact(
        startup_id=sent_contact.startup_id, run_id=sent_contact.run_id, resume_id=sent_contact.resume_id,
        cv_used_id=sent_contact.cv_used_id, name="Rate Limited", linkedin_url=f"https://www.linkedin.com/in/rl-{uuid.uuid4().hex[:8]}",
        email="rl@acme.example", send_status=SendStatus.sent, sent_at=sent_contact.sent_at,
    )
    db.add(other)
    db.commit()
    inbox["messages"] = {"m1": _msg("m1", "jane@acme.example")}
    inbox["search"] = {"from:jane@acme.example": ["m1", "gone"]}
    real_search = gmail.search

    def search(db_, query, max_results=20):
        if "rl@acme.example" in query:
            raise gmail.GmailError("Gmail error (HTTP 429)")
        return real_search(db_, query, max_results)

    def thread(db_, tid):
        raise gmail.GmailNotFound("thread deleted")

    def get_message(db_, mid):
        if mid == "gone":
            raise gmail.GmailNotFound("message deleted")
        return inbox["messages"][mid]

    monkeypatch.setattr(gmail, "search", search)
    monkeypatch.setattr(gmail, "thread_message_ids", thread)
    monkeypatch.setattr(gmail, "get_message", get_message)
    summary = tracker.poll_all(db)
    db.refresh(sent_contact)
    assert sent_contact.reply_status == ReplyStatus.replied  # deleted thread/message skipped, reply still found
    assert summary.contacts >= 2 and any("429" in e for e in summary.errors)


def test_overlapping_polls_are_refused(db, sent_contact, inbox, make_user, login):
    tracker._poll_lock.acquire()
    try:
        response = login(make_user(UserRole.operator)).post("/replies/poll")
        assert response.status_code == 409
    finally:
        tracker._poll_lock.release()


# --- API ------------------------------------------------------------------------------

def test_replies_list_and_permissions(db, sent_contact, inbox, make_user, login):
    inbox["messages"] = {"m1": _msg("m1", "jane@acme.example")}
    inbox["search"] = {"from:jane@acme.example": ["m1"]}
    assert login(make_user(UserRole.viewer)).post("/replies/poll").status_code == 403

    operator = login(make_user(UserRole.operator))
    assert operator.post("/replies/poll").json()["new_messages"] == 1
    listed = [r for r in operator.get("/replies?classification=reply").json() if r["contact_id"] == str(sent_contact.id)]
    assert len(listed) == 1 and listed[0]["reply_status"] == "replied"
    assert operator.get("/replies?classification=bogus").status_code == 422


def test_internal_poll_requires_configured_token(db, client, monkeypatch, inbox):
    settings = get_settings()
    monkeypatch.setattr(settings, "internal_task_token", "")
    assert client.post("/internal/poll-replies").status_code == 404
    monkeypatch.setattr(settings, "internal_task_token", "s3cret-token")
    assert client.post("/internal/poll-replies", headers={"X-Internal-Token": "wrong"}).status_code == 403
    assert client.post("/internal/poll-replies", headers={"X-Internal-Token": "s3cret-token"}).status_code == 200


# --- Gmail parsing ------------------------------------------------------------------------

def test_parse_message_prefers_plain_text_and_reads_headers():
    raw = {
        "id": "abc", "threadId": "t9", "internalDate": "1757600000000", "snippet": "Thanks &amp; regards",
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [{"name": "From", "value": "Jane Doe <Jane@Acme.example>"}, {"name": "Subject", "value": "Re: hi"},
                        {"name": "Auto-Submitted", "value": "auto-replied"}],
            "parts": [
                {"mimeType": "text/plain", "body": {"data": _b64("Plain body")}},
                {"mimeType": "text/html", "body": {"data": _b64("<p>HTML body</p>")}},
            ],
        },
    }
    message = gmail.parse_message(raw)
    assert (message.from_address, message.subject, message.thread_id) == ("jane@acme.example", "Re: hi", "t9")
    assert message.body_text == "Plain body" and message.snippet == "Thanks & regards" and message.auto_submitted


def test_parse_message_falls_back_to_html_text():
    raw = {"id": "x", "payload": {"mimeType": "text/html", "headers": [],
                                  "body": {"data": _b64("<style>p{}</style><p>Hello&nbsp;there</p>")}}}
    assert gmail.parse_message(raw).body_text == "Hello there"
