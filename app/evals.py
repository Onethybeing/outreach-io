"""Evaluation (PLAN.md §11): LLM-as-judge scores and human-signal scores, stored in Postgres and
sent to Langfuse as scores on the matching trace. Nothing here may ever break the action it grades.
"""

import json
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import llm, prompts, telemetry, vault
from app.config import get_settings
from app.db import SessionLocal
from app.jobs import WorkerPool
from app.models import Candidate, Contact, ReplyClassification, Resume, Run, RunStatus, Startup

logger = logging.getLogger(__name__)

JUDGE_ERRORS = (llm.LLMError, prompts.PromptError, vault.VaultError)


def _clamp(value, low: float, high: float) -> float | None:
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return None


# --- LLM judges ------------------------------------------------------------------------

def judge_run_startups(db: Session, run_id: uuid.UUID) -> int:
    """Score every not-yet-judged startup of a completed run. Returns how many were scored."""
    run = db.get(Run, run_id)
    if run is None or run.status != RunStatus.completed:
        return 0
    profile = json.dumps((db.get(Resume, run.resume_id).parsed_profile or {}))
    startups = list(db.scalars(select(Startup).where(Startup.run_id == run_id, Startup.relevance_judge.is_(None))))
    if not startups:
        return 0
    prompt = prompts.get_active(db, "eval_startup_relevance")
    client = telemetry.langfuse_client(db)
    scored = 0
    with telemetry.activate(telemetry.RunTelemetry(langfuse=client)):
        for startup in startups:
            text = prompts.render("eval_startup_relevance", prompt.template, {
                "candidate_profile": profile, "startup_name": startup.name,
                "startup_description": startup.description or "", "startup_domain": startup.domain or "",
            })
            try:
                verdict = llm.complete_json(db, prompt.model, text, prompt.temperature, name="eval_startup_relevance",
                                            metadata={"prompt_version": prompt.version, "startup": startup.name})
            except JUDGE_ERRORS as exc:
                logger.warning("Startup relevance judge failed for %s: %s", startup.name, exc)
                continue
            value = _clamp(verdict.get("score"), 0, 1)
            if value is None:
                continue
            startup.relevance_judge, startup.relevance_judge_reason = value, str(verdict.get("reason") or "")[:1000]
            db.commit()
            telemetry.score(client, run.langfuse_trace_id, "startup_relevance", value, comment=f"{startup.name}: {startup.relevance_judge_reason}")
            scored += 1
    telemetry.flush(client)
    return scored


def judge_draft(db: Session, contact_id: uuid.UUID) -> dict | None:
    contact = db.get(Contact, contact_id)
    if contact is None or not contact.draft_text:
        return None
    startup = db.get(Startup, contact.startup_id)
    resume = db.get(Resume, contact.cv_used_id)
    prompt = prompts.get_active(db, "eval_draft_quality")
    text = prompts.render("eval_draft_quality", prompt.template, {
        "candidate_profile": json.dumps(resume.parsed_profile or {}),
        "email_subject": contact.draft_subject or "", "email_body": contact.draft_text,
        "startup_name": startup.name, "startup_description": startup.description or "", "contact_name": contact.name,
    })
    graded = (contact.draft_subject, contact.draft_text)  # captured before the slow call
    client = telemetry.langfuse_client(db)
    with telemetry.activate(telemetry.RunTelemetry(langfuse=client)):
        verdict = llm.complete_json(db, prompt.model, text, prompt.temperature, name="eval_draft_quality",
                                    metadata={"prompt_version": prompt.version})
    claims = verdict.get("unsupported_claims")
    claims = [str(c) for c in claims] if isinstance(claims, list) else []
    result = {
        "score": _clamp(verdict.get("score"), 0, 1),
        "personalization": _clamp(verdict.get("personalization"), 1, 5),
        "clarity": _clamp(verdict.get("clarity"), 1, 5),
        "tone": _clamp(verdict.get("tone"), 1, 5),
        "length_ok": verdict.get("length_ok") is True,
        "unsupported_claims": claims,
        "reason": str(verdict.get("reason") or "")[:1000],
        "judge_prompt_version": prompt.version,
        "judged_at": datetime.now(timezone.utc).isoformat(),
    }
    db.refresh(contact)  # reload from the database: the draft may have been edited or regenerated while judging
    if (contact.draft_subject, contact.draft_text) != graded:
        return None  # stale; the edit/regeneration queued its own eval
    contact.draft_eval = result
    db.commit()
    if result["score"] is not None:
        telemetry.score(client, contact.draft_trace_id, "draft_quality", result["score"], comment=result["reason"])
    telemetry.score(client, contact.draft_trace_id, "unsupported_claims", float(len(claims)),
                    comment="; ".join(claims)[:500] or None)
    telemetry.flush(client)
    return result


# --- human signals (free labels, PLAN.md §11 #3-5) ---------------------------------------------

# No flush() in these: they run inside user requests and reply polls, and flush() blocks on the
# Langfuse API. The client's own background batching delivers the scores.

def record_candidate_decision(db: Session, candidate: Candidate, approved: bool) -> None:
    run = db.get(Run, candidate.run_id)
    telemetry.score(telemetry.langfuse_client(db), run.langfuse_trace_id if run else None, "kdm_approved",
                    1.0 if approved else 0.0, data_type="BOOLEAN", comment=candidate.name)


def record_draft_approved(db: Session, contact: Contact) -> None:
    telemetry.score(telemetry.langfuse_client(db), contact.draft_trace_id, "draft_edited_before_approval",
                    1.0 if contact.draft_edited else 0.0, data_type="BOOLEAN")


def record_reply(db: Session, contact: Contact, label: ReplyClassification) -> None:
    telemetry.score(telemetry.langfuse_client(db), contact.draft_trace_id, "reply_outcome", label.value,
                    data_type="CATEGORICAL")


# --- background triggers ---------------------------------------------------------------------

_pool = WorkerPool("evals", workers=1)  # judges share Groq's tokens/minute with real work


def _safely(fn, *args) -> None:
    try:
        with SessionLocal() as db:
            fn(db, *args)
    except Exception:  # noqa: BLE001: evals are best-effort
        logger.exception("Eval job %s%r failed", fn.__name__, args)


def submit_run_startups(run_id: uuid.UUID) -> None:
    if get_settings().auto_evals:
        _pool.submit(_safely, judge_run_startups, run_id)


def submit_draft(contact_id: uuid.UUID) -> None:
    if get_settings().auto_evals:
        _pool.submit(_safely, judge_draft, contact_id)


def safe_signal(fn, *args) -> None:
    """Call a record_* helper without letting a scoring problem affect the user's action."""
    try:
        fn(*args)
    except Exception:  # noqa: BLE001
        logger.warning("Recording eval signal %s failed", fn.__name__, exc_info=True)
