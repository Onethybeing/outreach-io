"""Stats tab numbers (PLAN.md §10). Every rate carries its sample size; no winners on tiny n."""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import Float, and_, case, cast, func, select
from sqlalchemy.orm import Session

from app.models import (
    Candidate, CandidateStatus, Contact, DraftStatus, EmailLookupStatus, Prompt, ReplyStatus, Resume, Run,
    RunEvent, RunStatus, SendStatus, Startup, VerificationStatus,
)

USAGE_KEYS = ("tavily_searches", "llm_calls", "prompt_tokens", "completion_tokens", "rate_limit_wait_seconds")


def rate(hits: int, n: int) -> dict:
    return {"value": round(hits / n, 3) if n else None, "n": n}


def build(db: Session, days: int = 30, resume_id: uuid.UUID | None = None, include_dev: bool = False) -> dict:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    sent_statuses = [SendStatus.sent, SendStatus.sent_dev] if include_dev else [SendStatus.sent]
    is_sent = Contact.send_status.in_(sent_statuses)

    contact_filter = [Contact.created_at >= since]
    run_filter = [Run.created_at >= since]
    if resume_id:
        contact_filter.append(Contact.cv_used_id == resume_id)
        run_filter.append(Run.resume_id == resume_id)

    # One round trip for every contact count (the DB is remote; ~20 separate queries took ~13s live).
    draft_score = cast(Contact.draft_eval["score"].astext, Float)
    claims_len = func.jsonb_array_length(Contact.draft_eval["unsupported_claims"])
    counters = {
        "all": None,
        "sent": is_sent,
        "replied": and_(is_sent, Contact.reply_status == ReplyStatus.replied),
        "bounced": and_(is_sent, Contact.reply_status == ReplyStatus.bounced),
        "verified": Contact.verification_status == VerificationStatus.verified,
        "email": Contact.email.isnot(None),
        "drafted": Contact.draft_status != DraftStatus.none,
        "draft_approved": Contact.draft_status == DraftStatus.approved,
        "approved_edited": and_(Contact.draft_status == DraftStatus.approved, Contact.draft_edited.is_(True)),
        "judged": Contact.draft_eval.isnot(None),
        "with_claims": and_(Contact.draft_eval.isnot(None), claims_len > 0),
    }
    row = db.execute(
        select(*[func.count() if cond is None else func.count().filter(cond) for cond in counters.values()],
               func.avg(draft_score))
        .select_from(Contact).where(*contact_filter)
    ).one()
    totals = dict(zip(counters, row[:-1]))
    avg_draft_score = row[-1]

    def count(name: str) -> int:
        return totals[name] or 0

    # --- funnel over contacts created in the window (candidates counted from runs in the window)
    candidates = db.scalar(select(func.count()).select_from(Candidate).join(Run, Candidate.run_id == Run.id).where(*run_filter)) or 0
    decided = db.execute(
        select(
            func.count().filter(Candidate.status != CandidateStatus.pending),
            func.count().filter(Candidate.status.in_([CandidateStatus.approved, CandidateStatus.reused, CandidateStatus.updated])),
        ).select_from(Candidate).join(Run, Candidate.run_id == Run.id).where(*run_filter)
    ).one()
    sent, replied, bounced = count("sent"), count("replied"), count("bounced")
    funnel = [
        {"step": "candidates_found", "count": candidates},
        {"step": "approved", "count": decided[1]},
        {"step": "employment_verified", "count": count("verified")},
        {"step": "email_found", "count": count("email")},
        {"step": "draft_generated", "count": count("drafted")},
        {"step": "draft_approved", "count": count("draft_approved")},
        {"step": "sent", "count": sent},
        {"step": "replied", "count": replied},
    ]

    runs_row = db.execute(
        select(
            func.count(),
            func.count().filter(Run.status == RunStatus.completed),
            func.count().filter(Run.status == RunStatus.failed),
            func.avg(case((Run.status == RunStatus.completed, func.extract("epoch", Run.finished_at - Run.started_at)))),
        ).where(*run_filter)
    ).one()

    kpis = {
        "resumes": db.scalar(select(func.count()).select_from(Resume)) or 0,
        "runs": runs_row[0],
        "candidates_found": candidates,
        "contacts": count("all"),
        "emails_sent": sent,
        "reply_rate": rate(replied, sent),
        "bounce_rate": rate(bounced, sent),
    }

    # --- per CV
    per_resume = [
        {"resume_id": str(rid), "filename": name, "contacts": n, "sent": s, "reply_rate": rate(r, s)}
        for rid, name, n, s, r in db.execute(
            select(
                Resume.id, Resume.filename, func.count(Contact.id),
                func.count().filter(is_sent),
                func.count().filter(and_(is_sent, Contact.reply_status == ReplyStatus.replied)),
            )
            .join(Contact, Contact.cv_used_id == Resume.id)
            .where(*contact_filter)
            .group_by(Resume.id, Resume.filename)
            .order_by(func.count(Contact.id).desc())
        )
    ]

    # --- per draft prompt version
    per_prompt_version = [
        {
            "prompt_id": str(pid), "version": version, "drafts": drafts, "sent": s, "reply_rate": rate(r, s),
            "edited_before_approval": rate(edited, approved), "avg_judge_score": {"value": round(avg, 3) if avg is not None else None, "n": judged},
        }
        for pid, version, drafts, s, r, edited, approved, avg, judged in db.execute(
            select(
                Prompt.id, Prompt.version, func.count(Contact.id),
                func.count().filter(is_sent),
                func.count().filter(and_(is_sent, Contact.reply_status == ReplyStatus.replied)),
                func.count().filter(and_(Contact.draft_edited.is_(True), Contact.draft_status == DraftStatus.approved)),
                func.count().filter(Contact.draft_status == DraftStatus.approved),
                func.avg(draft_score), func.count(draft_score),
            )
            .join(Contact, Contact.draft_prompt_version_id == Prompt.id)
            .where(*contact_filter)
            .group_by(Prompt.id, Prompt.version)
            .order_by(Prompt.version)
        )
    ]

    # --- over time (by event date, not contact creation)
    day = func.date_trunc("day", Contact.sent_at)
    sends_by_day = {
        d.date().isoformat(): n
        for d, n in db.execute(
            select(day, func.count()).where(is_sent, Contact.sent_at >= since, *contact_filter[1:]).group_by(day)
        )
    }
    reply_day = func.date_trunc("day", Contact.replied_at)
    replies_by_day = {
        d.date().isoformat(): n
        for d, n in db.execute(
            select(reply_day, func.count()).where(Contact.replied_at >= since, *contact_filter[1:]).group_by(reply_day)
        )
    }
    over_time = [
        {"date": (since + timedelta(days=i)).date().isoformat(),
         "sent": sends_by_day.get((since + timedelta(days=i)).date().isoformat(), 0),
         "replied": replies_by_day.get((since + timedelta(days=i)).date().isoformat(), 0)}
        for i in range(days + 1)
    ]

    # --- providers
    usage_row = db.execute(
        select(*[func.coalesce(func.sum(cast(Run.usage[key].astext, Float)), 0) for key in USAGE_KEYS]).where(*run_filter)
    ).one()
    usage = {key: round(float(value or 0), 1) for key, value in zip(USAGE_KEYS, usage_row)}
    usage["brightdata_profiles"] = db.scalar(
        select(func.count()).select_from(Contact).where(Contact.verification_source == "brightdata", Contact.verified_at >= since)
    ) or 0
    lookups = db.execute(
        select(func.count(), func.count().filter(Contact.email_lookup_status == EmailLookupStatus.found))
        .where(Contact.email_looked_up_at >= since, Contact.email_source != "manual")
    ).one()
    usage["email_lookup_hit_rate"] = rate(lookups[1], lookups[0])
    verifications = db.execute(
        select(func.count(), func.count().filter(Contact.verification_status == VerificationStatus.verified))
        .where(Contact.verified_at >= since, Contact.verification_status != VerificationStatus.failed)
    ).one()
    usage["verification_success_rate"] = rate(verifications[1], verifications[0])

    # --- agent health
    failures_by_node = {
        node: n for node, n in db.execute(
            select(RunEvent.node, func.count()).join(Run, RunEvent.run_id == Run.id)
            .where(RunEvent.kind == "failed", *run_filter).group_by(RunEvent.node)
        )
    }
    agent_health = {
        "runs": runs_row[0],
        "success_rate": rate(runs_row[1], runs_row[1] + runs_row[2]),
        "avg_run_seconds": round(float(runs_row[3]), 1) if runs_row[3] is not None else None,
        "failures_by_node": failures_by_node,
    }

    # --- quality (evals + human signals)
    judge = db.execute(
        select(func.avg(Startup.relevance_judge), func.count(Startup.relevance_judge))
        .join(Run, Startup.run_id == Run.id).where(*run_filter)
    ).one()
    quality = {
        "startup_relevance_avg": {"value": round(float(judge[0]), 3) if judge[0] is not None else None, "n": judge[1]},
        "draft_score_avg": {"value": round(float(avg_draft_score), 3) if avg_draft_score is not None else None, "n": count("judged")},
        "drafts_with_unsupported_claims": rate(count("with_claims"), count("judged")),
        "candidate_approval_rate": rate(decided[1], decided[0]),
        "draft_edit_rate": rate(count("approved_edited"), count("draft_approved")),
    }

    return {
        "window_days": days, "resume_id": str(resume_id) if resume_id else None, "include_dev_sends": include_dev,
        "kpis": kpis, "funnel": funnel, "per_resume": per_resume, "per_prompt_version": per_prompt_version,
        "over_time": over_time, "providers": usage, "agent_health": agent_health, "quality": quality,
    }
