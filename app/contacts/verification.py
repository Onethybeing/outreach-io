"""Employment verification (PLAN.md §2B): does this person still work at the startup?

enrich_company → scrape_profile → cross_check_company_match, as a LangGraph graph run in a background
job per contact. Evidence, strongest first: the profile's current-company LinkedIn page equals the
startup's page; the company names match; otherwise the LLM tie-break prompt decides.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy import update
from sqlalchemy.orm import Session

from app import llm, prompts, telemetry, vault
from app.contacts import apollo, brightdata
from app.db import SessionLocal
from app.discovery.normalize import linkedin_company_slug, names_match
from app.jobs import WorkerPool
from app.models import Contact, EmailLookupStatus, Startup, VerificationStatus

logger = logging.getLogger(__name__)

LLM_CONFIDENCE = 0.7
EXPECTED_ERRORS = (brightdata.ProfileError, vault.VaultError, llm.LLMError, prompts.PromptError)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Decision:
    status: VerificationStatus
    note: str


def decide(
    profile: brightdata.Profile,
    startup_name: str,
    startup_website: str | None,
    startup_company_slug: str | None,
    tiebreak: Callable[[], dict],
) -> Decision:
    company, slug = profile.current_company, profile.current_company_slug
    if not company and not slug:
        return Decision(VerificationStatus.unconfirmed, "LinkedIn profile shows no current company")
    if slug and startup_company_slug and slug == startup_company_slug:
        return Decision(VerificationStatus.verified, f"Current company's LinkedIn page matches {startup_name}")
    if names_match(company, startup_name, startup_website):
        return Decision(VerificationStatus.verified, f"Current company '{company}' matches {startup_name}")
    if slug and startup_company_slug:
        # Both LinkedIn pages are known and differ, and the names differ too: a different company.
        return Decision(VerificationStatus.mismatch, f"Currently at {company or slug} (linkedin.com/company/{slug}), not {startup_name}")

    verdict = tiebreak()
    try:
        confidence = float(verdict.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0.0
    reason = str(verdict.get("reason") or "").strip()
    if confidence < LLM_CONFIDENCE:
        return Decision(VerificationStatus.unconfirmed, f"Unsure whether '{company}' is {startup_name}: {reason}")
    if verdict.get("match") is True:
        return Decision(VerificationStatus.verified, f"'{company}' judged to be {startup_name}: {reason}")
    return Decision(VerificationStatus.mismatch, f"Currently at '{company}', not {startup_name}: {reason}")


class State(TypedDict, total=False):
    company_slug: str | None
    profile: dict
    decision: dict


def build_graph(db: Session, contact: Contact, startup: Startup):
    def enrich_company(state: State) -> dict:
        if startup.company_enriched_at is None:
            # Lock the startup row, re-check, and commit straight away: parallel verifications for the
            # same startup wait a few seconds and reuse the result, instead of each paying for Apollo
            # and holding the lock through a minutes-long scrape.
            db.refresh(startup, with_for_update=True)
            if startup.company_enriched_at is None:
                lookup = apollo.enrich_org(db, startup.website, startup.name)
                org = lookup.org or {}
                startup.linkedin_url = org.get("linkedin_url") or startup.linkedin_url
                startup.website = startup.website or org.get("website_url")  # email lookups need a domain later
                if lookup.definitive:  # a transient Apollo problem must not block future attempts
                    startup.company_enriched_at = _now()
            db.commit()
        return {"company_slug": linkedin_company_slug(startup.linkedin_url)}

    def scrape_profile(state: State) -> dict:
        return {"profile": brightdata.scrape_profile(db, contact.linkedin_url).__dict__}

    def cross_check_company_match(state: State) -> dict:
        profile = brightdata.Profile(**state["profile"])

        def tiebreak() -> dict:
            prompt = prompts.get_active(db, "cross_check_company_match")
            text = prompts.render("cross_check_company_match", prompt.template, {
                "expected_company": startup.name,
                "expected_website": startup.website or "",
                "profile_company": profile.current_company or "",
                "profile_title": profile.position or contact.title or "",
            })
            return llm.complete_json(
                db, prompt.model, text, prompt.temperature,
                name="cross_check_company_match", metadata={"prompt_version": prompt.version},
            )

        decision = decide(profile, startup.name, startup.website, state.get("company_slug"), tiebreak)
        return {"decision": {"status": decision.status, "note": decision.note}}

    graph = StateGraph(State)
    graph.add_node("enrich_company", enrich_company)
    graph.add_node("scrape_profile", scrape_profile)
    graph.add_node("cross_check_company_match", cross_check_company_match)
    graph.add_edge(START, "enrich_company")
    graph.add_edge("enrich_company", "scrape_profile")
    graph.add_edge("scrape_profile", "cross_check_company_match")
    graph.add_edge("cross_check_company_match", END)
    return graph.compile()


def execute_verification(db: Session, contact_id: uuid.UUID) -> None:
    # Claim: only a queued verification starts, so double-submits run once.
    claimed = db.execute(
        update(Contact)
        .where(Contact.id == contact_id, Contact.verification_status == VerificationStatus.queued)
        .values(verification_status=VerificationStatus.running)
    ).rowcount
    db.commit()
    if not claimed:
        return
    contact = db.get(Contact, contact_id)
    db.refresh(contact)
    startup = db.get(Startup, contact.startup_id)

    client = telemetry.langfuse_client(db)
    tel = telemetry.RunTelemetry(langfuse=client)
    trace_id = telemetry.trace_id_for(f"verify:{contact_id}:{_now().isoformat()}")
    try:
        with telemetry.activate(tel), telemetry.observation(
            "verify_employment", as_type="agent", trace_context={"trace_id": trace_id},
            input={"contact": contact.name, "linkedin_url": contact.linkedin_url, "startup": startup.name},
        ) as root, telemetry.trace_attributes(
            trace_name="verify_employment", session_id=str(contact_id), tags=["contact", "verification"],
        ):
            state = build_graph(db, contact, startup).invoke({})
            root.update(output=state["decision"])
        profile, decision = state["profile"], state["decision"]
        contact.verification_status = decision["status"]
        contact.verification_note = decision["note"]
        contact.company_at_scrape = profile.get("current_company")
        contact.verified_company_url = profile.get("current_company_url")
        contact.verified_title = profile.get("position")
        contact.employment_verified = decision["status"] == VerificationStatus.verified
    except EXPECTED_ERRORS as exc:
        db.rollback()
        contact.verification_status, contact.verification_note = VerificationStatus.failed, str(exc)
    except Exception as exc:  # noqa: BLE001 — a job must always leave a final status
        db.rollback()
        logger.exception("Verification for contact %s crashed", contact_id)
        contact.verification_status = VerificationStatus.failed
        contact.verification_note = f"Unexpected error ({type(exc).__name__}) — see server logs"
    finally:
        contact.verification_source = "brightdata"
        contact.verified_at = _now()
        db.commit()
        telemetry.flush(client)


_pool = WorkerPool("verification", workers=4)


def run_verification_in_background(contact_id: uuid.UUID) -> None:
    with SessionLocal() as db:
        execute_verification(db, contact_id)


def submit_verification(contact_id: uuid.UUID) -> None:
    _pool.submit(lambda cid: run_verification_in_background(cid), contact_id)


def fail_interrupted_jobs(db: Session) -> int:
    """At startup, queued/running verifications and running lookups belonged to the old process."""
    note = "Interrupted by a server restart — run it again"
    verifications = db.execute(
        update(Contact)
        .where(Contact.verification_status.in_([VerificationStatus.queued, VerificationStatus.running]))
        .values(verification_status=VerificationStatus.failed, verification_note=note)
    ).rowcount
    lookups = db.execute(
        update(Contact)
        .where(Contact.email_lookup_status == EmailLookupStatus.running)
        .values(email_lookup_status=EmailLookupStatus.failed, email_lookup_note=note)
    ).rowcount
    db.commit()
    return verifications + lookups
