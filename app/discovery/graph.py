"""Discovery pipeline (PLAN.md §2A): resume → profile → startups → decision-makers → dedupe.

Autonomous and side-effect free outside our own database: it only proposes candidates.
Every step writes a RunEvent (the dashboard's live progress feed) and a Langfuse span.
"""

import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app import evals, llm, telemetry, vault
from app.db import SessionLocal, engine
from app.jobs import WorkerPool
from app.discovery import tavily
from app.discovery.extract import ExtractionError, extract_text
from app.discovery.normalize import company_key, linkedin_profile_url, title_names_other_company, website_domain
from app.models import Candidate, Contact, Prompt, Resume, Run, RunEvent, RunStatus, Startup
from app.prompts import PromptError, render

logger = logging.getLogger(__name__)

PROMPT_NODES = ("parse_resume", "build_search_queries", "discover_startups", "find_kdms")
MAX_QUERIES = 6
RESULTS_PER_QUERY = 5
MAX_RESULTS_TO_LLM = 40
KDM_SEARCH_RESULTS = 10
MAX_RESUME_CHARS = 12_000  # keeps parse_resume inside Groq's 8k tokens/minute
STALE_AFTER = timedelta(minutes=10)
KDM_QUERY = '"{name}" founder OR co-founder OR CEO OR CTO OR "head of talent" OR recruiter OR "hiring manager"'

EXPECTED_ERRORS = (
    llm.LLMError, tavily.SearchError, ExtractionError, vault.VaultError, PromptError,
)


class RunFailed(Exception):
    def __init__(self, message: str, node: str = "agent") -> None:
        super().__init__(message)
        self.node = node


class CandidateProfile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    current_role: str | None = None
    seniority: str | None = None
    years_experience: float | None = None
    skills: list[str] = []
    domains: list[str] = []
    target_titles: list[str] = []

    @field_validator("skills", "domains", "target_titles", mode="before")
    @classmethod
    def _string_list(cls, value: Any) -> list[str]:
        return [str(v).strip() for v in value if v and str(v).strip()] if isinstance(value, list) else []

    @field_validator("years_experience", mode="before")
    @classmethod
    def _number(cls, value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None


class State(TypedDict, total=False):
    profile: dict
    queries: list[str]
    startup_ids: list[str]
    stop: bool


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RunStopped(Exception):
    """This run's row is no longer 'running' (e.g. marked stale by another process). Stop quietly."""


HEARTBEAT_EVERY = timedelta(seconds=30)


@dataclass
class RunContext:
    db: Session
    run: Run
    resume: Resume
    last_heartbeat: datetime | None = None

    def still_running(self) -> bool:
        # no_autoflush: read the committed status, not this session's own unflushed changes.
        with self.db.no_autoflush:
            return self.db.scalar(select(Run.status).where(Run.id == self.run.id)) == RunStatus.running

    def emit(self, node: str, kind: str, message: str, data: dict | None = None, *, final: bool = False) -> None:
        # Every commit is guarded, so a run marked failed elsewhere can't keep writing candidates
        # or flip itself back to completed. `final` events carry the status change themselves.
        if not final and not self.still_running():
            raise RunStopped()
        now = _now()
        self.db.add(RunEvent(run_id=self.run.id, node=node, kind=kind, message=message, data=data, created_at=now))
        self.run.heartbeat_at = self.last_heartbeat = now
        self.db.commit()

    def heartbeat(self) -> None:
        now = _now()
        if self.last_heartbeat and now - self.last_heartbeat < HEARTBEAT_EVERY:
            return
        if not self.still_running():
            raise RunStopped()
        # Own short transaction: it must be visible to other processes now, without committing this
        # session's half-finished node work. The lock timeout means a missed beat, never a hang.
        try:
            with engine.begin() as conn:
                conn.execute(text("SET LOCAL lock_timeout = '5s'"))
                conn.execute(Run.__table__.update().where(Run.id == self.run.id).values(heartbeat_at=now))
        except OperationalError:
            logger.warning("Heartbeat for run %s skipped (row busy)", self.run.id)
            return
        self.last_heartbeat = now

    def prompt(self, node: str) -> Prompt:
        prompt_id = (self.run.prompt_versions or {}).get(node)
        prompt = self.db.get(Prompt, uuid.UUID(prompt_id)) if prompt_id else None
        if prompt is None:
            raise RunFailed(f"No prompt recorded for {node} at run start")
        return prompt

    def llm_json(self, node: str, variables: dict[str, str]) -> dict:
        prompt = self.prompt(node)
        return llm.complete_json(
            self.db, prompt.model, render(node, prompt.template, variables), prompt.temperature,
            name=node, metadata={"prompt_version": prompt.version, "prompt_id": str(prompt.id)},
        )


# --- nodes ---------------------------------------------------------------------
# Each returns (state update, completion message, event data).

NodeResult = tuple[dict, str, dict | None]


def ingest_resume(ctx: RunContext, state: State) -> NodeResult:
    resume = ctx.resume
    if resume.extracted_text:
        return {}, f"Using text already read from {resume.filename}", None
    text, method = extract_text(ctx.db, resume.storage_path)
    resume.extracted_text, resume.extraction_method = text, method
    label = {"pdf_text": "PDF text", "docx": "Word document", "vision_ocr": "scanned pages via OCR"}[method]
    return {}, f"Read {len(text):,} characters from {resume.filename} ({label})", {"method": method}


def parse_resume(ctx: RunContext, state: State) -> NodeResult:
    raw = ctx.llm_json("parse_resume", {"resume_text": ctx.resume.extracted_text[:MAX_RESUME_CHARS]})
    profile = CandidateProfile.model_validate(raw)
    if not profile.target_titles and not profile.current_role:
        raise RunFailed("Couldn't work out the candidate's role or target titles from the resume")
    data = profile.model_dump()
    ctx.resume.parsed_profile, ctx.resume.status = data, "parsed"
    targets = ", ".join(profile.target_titles[:3]) or profile.current_role
    return {"profile": data}, f"{profile.current_role or 'Candidate'} with {len(profile.skills)} skills, targeting {targets}", data


def build_search_queries(ctx: RunContext, state: State) -> NodeResult:
    raw = ctx.llm_json("build_search_queries", {
        "candidate_profile": json.dumps(state["profile"]), "num_startups": str(ctx.run.num_startups),
    })
    queries: list[str] = []
    for query in raw.get("queries") or []:
        if isinstance(query, str) and query.strip() and query.strip().lower() not in {q.lower() for q in queries}:
            queries.append(query.strip())
    queries = queries[:MAX_QUERIES]
    if not queries:
        raise RunFailed("The model returned no search queries")
    return {"queries": queries}, f"Wrote {len(queries)} search queries", {"queries": queries}


def _previous_startups(ctx: RunContext) -> list[Startup]:
    # Only startups that actually produced candidates in a completed run. A failed run, or a
    # startup where nobody was found, proposed no one, so later runs may pick it again.
    has_candidates = select(Candidate.id).where(Candidate.startup_id == Startup.id).exists()
    return list(ctx.db.scalars(
        select(Startup).join(Run, Startup.run_id == Run.id)
        .where(
            Run.resume_id == ctx.resume.id, Run.id != ctx.run.id, Run.status == RunStatus.completed,
            has_candidates,
        )
    ))


def discover_startups(ctx: RunContext, state: State) -> NodeResult:
    results, seen_urls = [], set()
    for query in state["queries"]:
        for result in tavily.search(ctx.db, query, RESULTS_PER_QUERY):
            if result["url"] not in seen_urls:
                seen_urls.add(result["url"])
                results.append(result)
    ctx.emit("discover_startups", "info", f"Searched {len(state['queries'])} queries, {len(results)} unique results")
    if not results:
        ctx.emit("discover_startups", "warning", "Search returned nothing")
        return {"stop": True}, "No search results to pick startups from", None

    previous = _previous_startups(ctx)
    previous_keys = {company_key(s.name) for s in previous}
    previous_domains = {website_domain(s.website) for s in previous if s.website}
    raw = ctx.llm_json("discover_startups", {
        "candidate_profile": json.dumps(state["profile"]),
        "search_results": json.dumps(results[:MAX_RESULTS_TO_LLM]),
        "num_startups": str(ctx.run.num_startups),
        "exclude_startups": ", ".join(sorted({s.name for s in previous})[:50]),
    })

    def relevance(item: Any) -> float:
        try:
            return max(0.0, min(1.0, float(item.get("relevance", 0))))
        except (TypeError, ValueError, AttributeError):
            return 0.0

    picked: list[Startup] = []
    keys, domains, skipped_previous = set(), set(), []
    for item in sorted((s for s in raw.get("startups") or [] if isinstance(s, dict)), key=relevance, reverse=True):
        name = str(item.get("name") or "").strip()
        key, domain = company_key(name), website_domain(item.get("website"))
        if not key or key in keys or (domain and domain in domains):
            continue
        if key in previous_keys or (domain and domain in previous_domains):
            skipped_previous.append(name)
            continue
        keys.add(key)
        if domain:
            domains.add(domain)
        source = item.get("source_url")
        startup = Startup(
            run_id=ctx.run.id,
            name=name[:256],
            domain=(str(item.get("domain") or "").strip()[:128]) or None,
            website=(str(item.get("website") or "").strip()[:512]) or None,
            description=str(item.get("description") or "").strip() or None,
            source_url=source if source in seen_urls else None,  # never store a URL we didn't see
            relevance=relevance(item),
        )
        ctx.db.add(startup)
        picked.append(startup)
        if len(picked) == ctx.run.num_startups:
            break
    ctx.db.flush()

    if skipped_previous:
        ctx.emit("discover_startups", "info", f"Skipped {len(skipped_previous)} already found in earlier runs: {', '.join(skipped_previous)}")
    if not picked:
        ctx.emit("discover_startups", "warning", "No matching startups found")
        return {"stop": True}, "No startups picked", None
    if len(picked) < ctx.run.num_startups:
        ctx.emit("discover_startups", "warning", f"Found {len(picked)} of the {ctx.run.num_startups} startups requested")
    names = ", ".join(s.name for s in picked)
    return (
        {"startup_ids": [str(s.id) for s in picked]},
        f"Picked {len(picked)} startups: {names}",
        {"startups": [{"name": s.name, "relevance": s.relevance} for s in picked]},
    )


def find_kdms(ctx: RunContext, state: State) -> NodeResult:
    profile = state["profile"]
    target_role = (profile.get("target_titles") or [profile.get("current_role") or "engineer"])[0]
    seen: set[str] = set()
    total = 0
    for startup_id in state["startup_ids"]:
        startup = ctx.db.get(Startup, uuid.UUID(startup_id))
        results = tavily.search(
            ctx.db, KDM_QUERY.format(name=startup.name.replace('"', "")), KDM_SEARCH_RESULTS,
            include_domains=["linkedin.com/in"], content_chars=200,
        )
        found_urls = {u for u in (linkedin_profile_url(r["url"]) for r in results) if u}
        if not found_urls:
            ctx.emit("find_kdms", "warning", f"No LinkedIn profiles found for {startup.name}")
            continue

        raw = ctx.llm_json("find_kdms", {
            "startup_name": startup.name,
            "startup_description": startup.description or "",
            "target_role": target_role,
            "search_results": json.dumps(results),
            "num_kdms": str(ctx.run.num_kdms_per_company),
        })
        added, invented, other_company = 0, 0, 0
        for person in raw.get("people") or []:
            if not isinstance(person, dict):
                continue
            url = linkedin_profile_url(person.get("linkedin_url"))
            name = str(person.get("name") or "").strip()
            title = str(person.get("title") or "").strip()[:256] or None
            if not url or not name:
                continue
            if url not in found_urls:
                invented += 1  # model produced a profile link that search never returned
                continue
            if title_names_other_company(title, startup.name, startup.website):
                other_company += 1  # e.g. "Founder at Sapling Says" for Sapling.ai
                continue
            if url in seen:
                continue
            seen.add(url)
            ctx.db.add(Candidate(
                run_id=ctx.run.id, startup_id=startup.id, name=name[:256], title=title,
                linkedin_url=url, reason=str(person.get("why") or "").strip() or None,
            ))
            added += 1
            if added == ctx.run.num_kdms_per_company:
                break
        total += added
        ignored = []
        if invented:
            ignored.append(f"{invented} with links not in the search results")
        if other_company:
            ignored.append(f"{other_company} whose title names another company")
        note = f" (ignored {'; '.join(ignored)})" if ignored else ""
        ctx.emit("find_kdms", "info" if added else "warning", f"{added} decision-maker(s) at {startup.name}{note}")

    if total == 0:
        return {"stop": True}, "No decision-makers found at any startup", None
    return {}, f"Found {total} decision-makers across {len(state['startup_ids'])} startups", None


def dedupe_against_db(ctx: RunContext, state: State) -> NodeResult:
    candidates = list(ctx.db.scalars(select(Candidate).where(Candidate.run_id == ctx.run.id)))
    urls = [c.linkedin_url for c in candidates]
    # Contacts store normalized URLs too, so an exact match is the dedupe.
    existing = {c.linkedin_url: c.id for c in ctx.db.scalars(select(Contact).where(Contact.linkedin_url.in_(urls)))}
    earlier = set(ctx.db.scalars(
        select(Candidate.linkedin_url).where(Candidate.linkedin_url.in_(urls), Candidate.run_id != ctx.run.id)
    ))
    for candidate in candidates:
        candidate.existing_contact_id = existing.get(candidate.linkedin_url)
    message = f"{len(candidates)} candidates ready for review, {len(existing)} already in contacts"
    if earlier:
        message += f", {len(earlier)} also proposed in earlier runs"
    return {}, message, {"candidates": len(candidates), "existing_contacts": len(existing), "proposed_before": len(earlier)}


STEPS: tuple[tuple[str, str, Callable[[RunContext, State], NodeResult]], ...] = (
    ("ingest_resume", "Reading the resume", ingest_resume),
    ("parse_resume", "Understanding the candidate's profile", parse_resume),
    ("build_search_queries", "Writing search queries", build_search_queries),
    ("discover_startups", "Searching for matching startups", discover_startups),
    ("find_kdms", "Finding decision-makers on LinkedIn", find_kdms),
    ("dedupe_against_db", "Checking for people already in contacts", dedupe_against_db),
)


def _wrap(ctx: RunContext, name: str, start_message: str, fn: Callable[[RunContext, State], NodeResult]):
    def node(state: State) -> dict:
        ctx.emit(name, "started", start_message)
        try:
            with telemetry.observation(name, as_type="chain", input={k: v for k, v in state.items() if k != "profile"}) as obs:
                update, message, data = fn(ctx, state)
                obs.update(output={"message": message, **(data or {})})
        except RunStopped:
            raise
        except (RunFailed, *EXPECTED_ERRORS) as exc:
            raise RunFailed(str(exc), name) from exc
        except Exception as exc:
            logger.exception("Discovery step %s crashed", name)
            raise RunFailed(f"Unexpected error in {name} ({type(exc).__name__}) — see server logs", name) from exc
        ctx.emit(name, "completed", message, data)
        return update

    return node


def build_graph(ctx: RunContext):
    graph = StateGraph(State)
    for name, start_message, fn in STEPS:
        graph.add_node(name, _wrap(ctx, name, start_message, fn))
    graph.add_edge(START, "ingest_resume")
    graph.add_edge("ingest_resume", "parse_resume")
    graph.add_edge("parse_resume", "build_search_queries")
    graph.add_edge("build_search_queries", "discover_startups")
    graph.add_conditional_edges("discover_startups", lambda s: END if s.get("stop") else "find_kdms")
    graph.add_conditional_edges("find_kdms", lambda s: END if s.get("stop") else "dedupe_against_db")
    graph.add_edge("dedupe_against_db", END)
    return graph.compile()


# --- execution -------------------------------------------------------------------

def execute_run(db: Session, run_id: uuid.UUID) -> None:
    # Claim atomically: only one worker can move a run from pending to running.
    now = _now()
    claimed = db.execute(
        Run.__table__.update()
        .where(Run.id == run_id, Run.status == RunStatus.pending.name)
        .values(status=RunStatus.running.name, started_at=now, heartbeat_at=now)
    ).rowcount
    db.commit()
    if not claimed:
        return
    run = db.get(Run, run_id)
    db.refresh(run)
    ctx = RunContext(db, run, db.get(Resume, run.resume_id), last_heartbeat=now)
    client = telemetry.langfuse_client(db)
    tel = telemetry.RunTelemetry(
        langfuse=client, on_notice=lambda msg: ctx.emit("agent", "info", msg), on_heartbeat=ctx.heartbeat,
    )

    trace_id = telemetry.trace_id_for(str(run.id))
    stopped = False
    if client is not None:
        run.langfuse_trace_id, run.langfuse_trace_url = trace_id, telemetry.trace_url(client, trace_id)
    ctx.emit(
        "agent", "started",
        f"Run started for {ctx.resume.filename}: up to {run.num_startups} startups, {run.num_kdms_per_company} people each",
        {"trace_url": run.langfuse_trace_url},
    )

    try:
        with telemetry.activate(tel), telemetry.observation(
            "discovery_run", as_type="agent", trace_context={"trace_id": trace_id},
            input={"resume": ctx.resume.filename, "num_startups": run.num_startups, "num_kdms": run.num_kdms_per_company},
        ) as root, telemetry.trace_attributes(
            trace_name="discovery_run", session_id=str(run.id), tags=["discovery"],
            metadata={"resume_id": str(ctx.resume.id)},
        ):
            build_graph(ctx).invoke({})
            candidates = db.scalar(select(func.count()).select_from(Candidate).where(Candidate.run_id == run.id))
            summary = f"Done: {candidates} candidate(s) to review"
            root.update(output={"candidates": candidates})
        if not ctx.still_running():
            raise RunStopped()
        run.status = RunStatus.completed
        ctx.emit("agent", "completed", summary, final=True)
    except RunStopped:
        # Someone else already ended this run (stale cleanup) and removed its candidates; leave it be.
        db.rollback()
        stopped = True
        logger.warning("Discovery run %s was stopped elsewhere; worker exiting", run_id)
    except Exception as exc:  # noqa: BLE001 — every failure must end the run cleanly
        db.rollback()
        # Progress events commit as they go, so earlier steps' candidates are already saved. A failed
        # run's partial list must not be reviewable/approvable; startups stay for the record.
        db.execute(delete(Candidate).where(Candidate.run_id == run.id))
        if isinstance(exc, RunFailed):
            node, run.error = exc.node, str(exc)
        else:
            logger.exception("Discovery run %s crashed outside a step", run_id)
            node, run.error = "agent", f"Unexpected error ({type(exc).__name__})"
        run.status = RunStatus.failed
        ctx.emit(node, "failed", run.error, final=True)
    finally:
        run.usage = {k: round(v, 1) for k, v in tel.usage.items()}
        if not stopped:
            run.finished_at = _now()
        db.commit()
        telemetry.flush(client)
    if run.status == RunStatus.completed:
        evals.submit_run_startups(run.id)


def run_in_background(run_id: uuid.UUID) -> None:
    try:
        with SessionLocal() as db:
            execute_run(db, run_id)
    except Exception:  # noqa: BLE001 — a pool thread has nobody to raise to
        logger.exception("Discovery run %s crashed before it could record a failure", run_id)


# Own workers, so minutes-long runs never occupy the web server's request threads. Interrupted runs
# are failed at next startup (fail_interrupted_runs). Extra runs queue as 'pending'.
_run_pool = WorkerPool("discovery", workers=4)


def submit_run(run_id: uuid.UUID) -> None:
    # Looked up at call time so tests can replace run_in_background.
    _run_pool.submit(lambda rid: run_in_background(rid), run_id)


def _fail_active_runs(db: Session, message: str, only_stale: bool) -> int:
    cutoff = _now() - STALE_AFTER
    active = list(db.scalars(select(Run).where(Run.status.in_([RunStatus.pending, RunStatus.running]))))
    count = 0
    for run in active:
        if only_stale and (run.heartbeat_at or run.created_at) >= cutoff:
            continue
        run.status, run.finished_at, run.error = RunStatus.failed, _now(), message
        db.execute(delete(Candidate).where(Candidate.run_id == run.id))
        db.add(RunEvent(run_id=run.id, node="agent", kind="failed", message=message, created_at=_now()))
        count += 1
    db.commit()
    return count


def fail_stale_runs(db: Session) -> int:
    """Runs whose worker died without the server restarting would otherwise show 'running' forever."""
    return _fail_active_runs(db, "Stopped: the run stopped responding", only_stale=True)


def fail_interrupted_runs(db: Session) -> int:
    """At startup every pending/running run is dead: its worker thread lived in the old process.

    Assumes one app instance (Cloud Run max-instances=1) until runs move to an external queue.
    """
    return _fail_active_runs(db, "Stopped: the server restarted during this run", only_stale=False)
