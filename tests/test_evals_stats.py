import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update

from app import evals, llm, prompts, telemetry
from app.config import get_settings
from app.models import (
    Candidate, CandidateStatus, Contact, DraftStatus, ReplyClassification, ReplyStatus, Resume, Run, RunEvent,
    RunStatus, SendStatus, Startup, UserRole, VerificationStatus,
)
from app.parse_eval import score_case


@pytest.fixture(autouse=True)
def no_tracing(monkeypatch):
    monkeypatch.setattr(telemetry, "langfuse_client", lambda db: None)


@pytest.fixture
def scores(monkeypatch):
    """Capture Langfuse scores instead of sending them."""
    captured = []
    client = object()
    monkeypatch.setattr(telemetry, "langfuse_client", lambda db: client)
    monkeypatch.setattr(telemetry, "score", lambda c, trace_id, name, value, data_type="NUMERIC", comment=None:
                        captured.append({"trace": trace_id, "name": name, "value": value, "type": data_type}))
    monkeypatch.setattr(telemetry, "flush", lambda c: None)
    return captured


@pytest.fixture
def world(db):
    prompts.seed_defaults(db)
    resume = Resume(filename=f"cv-{uuid.uuid4().hex[:6]}.pdf", storage_path="x.pdf", parsed_profile={"name": "Priya", "skills": ["RAG"]})
    db.add(resume)
    db.flush()
    run = Run(resume_id=resume.id, status=RunStatus.completed, langfuse_trace_id="run-trace",
              started_at=datetime.now(timezone.utc) - timedelta(seconds=90), finished_at=datetime.now(timezone.utc),
              usage={"tavily_searches": 8, "llm_calls": 6, "prompt_tokens": 9000})
    db.add(run)
    db.flush()
    startups = [Startup(run_id=run.id, name=n, description="AI infra") for n in ("Acme", "Beta")]
    db.add_all(startups)
    db.flush()
    db.commit()
    return {"resume": resume, "run": run, "startups": startups}


def _contact(db, world, **fields):
    c = Contact(
        startup_id=world["startups"][0].id, run_id=world["run"].id, resume_id=world["resume"].id,
        cv_used_id=world["resume"].id, name="Jane", linkedin_url=f"https://www.linkedin.com/in/j-{uuid.uuid4().hex[:8]}",
        **fields,
    )
    db.add(c)
    db.commit()
    return c


# --- judges ------------------------------------------------------------------------------

def test_judge_run_startups_scores_clamps_and_skips_judged(db, world, monkeypatch, scores):
    answers = iter([{"score": 1.7, "reason": "strong fit"}, {"score": "n/a"}])
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: next(answers))
    assert evals.judge_run_startups(db, world["run"].id) == 1
    acme, beta = world["startups"]
    db.refresh(acme), db.refresh(beta)
    assert acme.relevance_judge == 1.0 and acme.relevance_judge_reason == "strong fit"
    assert beta.relevance_judge is None  # unusable score is skipped, not stored as 0
    assert scores == [{"trace": "run-trace", "name": "startup_relevance", "value": 1.0, "type": "NUMERIC"}]

    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: {"score": 0.2})
    assert evals.judge_run_startups(db, world["run"].id) == 1  # only Beta is still unjudged


def test_judge_skips_incomplete_runs(db, world, monkeypatch):
    world["run"].status = RunStatus.failed
    db.commit()
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: pytest.fail("should not call the LLM"))
    assert evals.judge_run_startups(db, world["run"].id) == 0


def test_judge_draft_stores_result_and_scores(db, world, monkeypatch, scores):
    contact = _contact(db, world, draft_subject="Hi", draft_text="I built RAG at a bank.", draft_status=DraftStatus.generated,
                       draft_generated_at=datetime.now(timezone.utc), draft_trace_id="draft-trace")
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: {
        "score": 0.35, "personalization": 9, "clarity": 4, "tone": 4, "length_ok": True,
        "unsupported_claims": ["worked at a bank"], "reason": "claims a bank job",
    })
    result = evals.judge_draft(db, contact.id)
    db.refresh(contact)
    assert contact.draft_eval["score"] == 0.35 and contact.draft_eval["personalization"] == 5  # clamped to 1-5
    assert contact.draft_eval["unsupported_claims"] == ["worked at a bank"] and result == contact.draft_eval
    assert {s["name"] for s in scores} == {"draft_quality", "unsupported_claims"}
    assert all(s["trace"] == "draft-trace" for s in scores)


def test_judge_draft_discards_stale_result(db, world, monkeypatch):
    contact = _contact(db, world, draft_subject="Hi", draft_text="Version one", draft_status=DraftStatus.generated)

    def edit_while_judging(*args, **kwargs):
        db.execute(update(Contact).where(Contact.id == contact.id).values(draft_text="Version two"))
        db.commit()
        return {"score": 0.9, "unsupported_claims": []}

    monkeypatch.setattr(llm, "complete_json", edit_while_judging)
    assert evals.judge_draft(db, contact.id) is None
    db.refresh(contact)
    assert contact.draft_eval is None


# --- triggers and human signals ------------------------------------------------------------------

def test_auto_evals_setting_controls_background_jobs(monkeypatch):
    submitted = []
    monkeypatch.setattr(evals._pool, "submit", lambda fn, *args: submitted.append(args))
    monkeypatch.setattr(get_settings(), "auto_evals", False)
    evals.submit_draft(uuid.uuid4())
    evals.submit_run_startups(uuid.uuid4())
    assert submitted == []
    monkeypatch.setattr(get_settings(), "auto_evals", True)
    evals.submit_draft(uuid.uuid4())
    assert len(submitted) == 1


def test_candidate_decisions_and_draft_approval_become_scores(db, world, make_user, login, scores, monkeypatch):
    from app.contacts import verification

    monkeypatch.setattr(verification, "submit_verification", lambda cid: None)
    candidates = [Candidate(run_id=world["run"].id, startup_id=world["startups"][0].id, name=f"P{i}",
                            linkedin_url=f"https://www.linkedin.com/in/p-{uuid.uuid4().hex[:8]}") for i in range(2)]
    db.add_all(candidates)
    db.commit()
    operator = login(make_user(UserRole.operator))
    operator.post(f"/candidates/{candidates[0].id}/approve")
    operator.post(f"/candidates/{candidates[1].id}/reject")
    assert [(s["name"], s["value"], s["trace"]) for s in scores] == [
        ("kdm_approved", 1.0, "run-trace"), ("kdm_approved", 0.0, "run-trace"),
    ]

    contact = _contact(db, world, email="j@acme.example", email_source="manual", draft_subject="Hi", draft_text="Body",
                       draft_status=DraftStatus.generated, draft_edited=True, draft_trace_id="draft-trace")
    operator.post(f"/contacts/{contact.id}/draft/approve")
    assert scores[-1] == {"trace": "draft-trace", "name": "draft_edited_before_approval", "value": 1.0, "type": "BOOLEAN"}


def test_scoring_failure_never_breaks_the_action(db, world, make_user, login, monkeypatch):
    from app.contacts import verification

    monkeypatch.setattr(verification, "submit_verification", lambda cid: None)
    monkeypatch.setattr(evals, "record_candidate_decision", lambda *a: (_ for _ in ()).throw(RuntimeError("langfuse down")))
    candidate = Candidate(run_id=world["run"].id, startup_id=world["startups"][0].id, name="P",
                          linkedin_url=f"https://www.linkedin.com/in/p-{uuid.uuid4().hex[:8]}")
    db.add(candidate)
    db.commit()
    assert login(make_user(UserRole.operator)).post(f"/candidates/{candidate.id}/approve").status_code == 200


def test_reply_outcome_score(db, world, scores):
    contact = _contact(db, world, draft_trace_id="draft-trace")
    evals.record_reply(db, contact, ReplyClassification.reply)
    assert scores == [{"trace": "draft-trace", "name": "reply_outcome", "value": "reply", "type": "CATEGORICAL"}]


# --- stats --------------------------------------------------------------------------------------

def test_stats_numbers_for_one_resume(db, world, make_user, login):
    from app import stats

    now = datetime.now(timezone.utc)
    prompt = prompts.get_active(db, "generate_draft")
    db.add_all([
        Candidate(run_id=world["run"].id, startup_id=world["startups"][0].id, name="A", linkedin_url="https://www.linkedin.com/in/a1", status=CandidateStatus.approved),
        Candidate(run_id=world["run"].id, startup_id=world["startups"][0].id, name="B", linkedin_url="https://www.linkedin.com/in/b1", status=CandidateStatus.rejected),
        Candidate(run_id=world["run"].id, startup_id=world["startups"][0].id, name="C", linkedin_url="https://www.linkedin.com/in/c1"),
        RunEvent(run_id=world["run"].id, node="find_kdms", kind="failed", message="x"),
    ])
    world["startups"][0].relevance_judge = 0.8
    base = dict(verification_status=VerificationStatus.verified, email="x@acme.example", draft_prompt_version_id=prompt.id)
    _contact(db, world, **base, draft_status=DraftStatus.approved, draft_edited=True, send_status=SendStatus.sent, sent_at=now,
             reply_status=ReplyStatus.replied, replied_at=now, draft_eval={"score": 0.9, "unsupported_claims": []})
    _contact(db, world, **base, draft_status=DraftStatus.approved, send_status=SendStatus.sent, sent_at=now,
             reply_status=ReplyStatus.bounced, draft_eval={"score": 0.3, "unsupported_claims": ["made up"]})
    _contact(db, world, **base, draft_status=DraftStatus.approved, send_status=SendStatus.sent_dev, sent_at=now)
    _contact(db, world, verification_status=VerificationStatus.mismatch)

    s = stats.build(db, days=7, resume_id=world["resume"].id)
    assert s["kpis"]["contacts"] == 4 and s["kpis"]["emails_sent"] == 2  # dev send excluded by default
    assert s["kpis"]["reply_rate"] == {"value": 0.5, "n": 2} and s["kpis"]["bounce_rate"] == {"value": 0.5, "n": 2}
    assert dict((f["step"], f["count"]) for f in s["funnel"]) == {
        "candidates_found": 3, "approved": 1, "employment_verified": 3, "email_found": 3,
        "draft_generated": 3, "draft_approved": 3, "sent": 2, "replied": 1,
    }
    assert s["per_resume"][0]["sent"] == 2 and s["per_resume"][0]["reply_rate"] == {"value": 0.5, "n": 2}
    version = s["per_prompt_version"][0]
    assert version["drafts"] == 3 and version["avg_judge_score"] == {"value": 0.6, "n": 2}
    assert version["edited_before_approval"] == {"value": 0.333, "n": 3}
    assert s["quality"]["drafts_with_unsupported_claims"] == {"value": 0.5, "n": 2}
    assert s["quality"]["candidate_approval_rate"] == {"value": 0.5, "n": 2}
    assert s["quality"]["startup_relevance_avg"] == {"value": 0.8, "n": 1}
    assert s["agent_health"]["failures_by_node"] == {"find_kdms": 1} and s["agent_health"]["avg_run_seconds"] == pytest.approx(90, abs=2)
    assert s["providers"]["tavily_searches"] == 8
    assert sum(d["sent"] for d in s["over_time"]) == 2

    with_dev = stats.build(db, days=7, resume_id=world["resume"].id, include_dev=True)
    assert with_dev["kpis"]["emails_sent"] == 3

    assert login(make_user(UserRole.viewer)).get(f"/stats?resume_id={world['resume'].id}").status_code == 200


def test_stats_counts_unjudged_misses_and_dev_replies_correctly(db, world):
    from app import stats
    from app.models import EmailLookupStatus

    now = datetime.now(timezone.utc)
    cleared = _contact(db, world, draft_subject="s", draft_text="t", draft_status=DraftStatus.generated,
                       draft_eval={"score": 0.5, "unsupported_claims": []})
    cleared.draft_eval = None  # e.g. after an edit; must be SQL NULL, not JSON null
    db.commit()
    _contact(db, world, email="hit@acme.example", email_source="apollo", email_lookup_status=EmailLookupStatus.found, email_looked_up_at=now)
    # A paid miss, later fixed by typing the email in: still a miss for the provider.
    _contact(db, world, email="m@acme.example", email_source="manual", email_lookup_status=EmailLookupStatus.not_found, email_looked_up_at=now)
    _contact(db, world, email_lookup_status=EmailLookupStatus.failed, email_looked_up_at=now)  # provider never answered
    _contact(db, world, email="only-manual@acme.example", email_source="manual")  # no lookup at all
    _contact(db, world, send_status=SendStatus.sent_dev, sent_at=now, reply_status=ReplyStatus.replied, replied_at=now)

    s = stats.build(db, days=3, resume_id=world["resume"].id)
    assert s["quality"]["draft_score_avg"]["n"] == 0
    assert s["quality"]["drafts_with_unsupported_claims"] == {"value": None, "n": 0}
    # 1 hit + 1 miss; the failed attempt and the manual-only contact aren't lookups the provider answered.
    assert s["providers"]["email_lookup_hit_rate"] == {"value": 0.5, "n": 2}
    assert sum(d["replied"] for d in s["over_time"]) == 0  # the reply to a dev send is excluded by default


def test_eval_endpoints_guard_state(db, world, make_user, login):
    operator = login(make_user(UserRole.operator))
    contact = _contact(db, world)
    assert operator.post(f"/evals/contacts/{contact.id}/draft").status_code == 409
    world["run"].status = RunStatus.running
    db.commit()
    assert operator.post(f"/evals/runs/{world['run'].id}/startups").status_code == 409
    assert login(make_user(UserRole.viewer)).post(f"/evals/contacts/{contact.id}/draft").status_code == 403


# --- parse eval scoring ------------------------------------------------------------------------------

def test_parse_eval_scoring():
    expected = {"name": "Priya Sharma", "skills": ["Python", "PyTorch", "RAG"], "years_experience": 4, "current_role": "ML Engineer"}
    actual = {"name": "priya  sharma", "skills": ["python", "RAG", "Docker", "FastAPI"], "years_experience": 5, "current_role": "Senior ML Engineer"}
    result = score_case(expected, actual)
    assert result["fields"]["name"] == 1.0
    assert result["fields"]["skills"] == pytest.approx(0.571, abs=0.001)  # P=2/4, R=2/3
    assert result["fields"]["years_experience"] == 1.0  # within ±1
    assert result["fields"]["current_role"] == pytest.approx(0.667, abs=0.001)
    assert score_case({}, actual)["overall"] is None
