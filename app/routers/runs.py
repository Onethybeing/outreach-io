import asyncio
import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import audit, prompts, vault
from app.auth import require, require_for_stream
from app.db import SessionLocal, get_db
from app.discovery import graph as discovery
from app.models import Candidate, CandidateStatus, Resume, Run, RunEvent, RunStatus, Startup, User
from app.schemas import CandidateOut, RunCreate, RunDetailOut, RunEventOut, RunOut, StartupOut

router = APIRouter(prefix="/runs", tags=["runs"])

FINISHED = (RunStatus.completed, RunStatus.failed)


@router.post("", response_model=RunOut, status_code=status.HTTP_202_ACCEPTED)
def start_run(
    body: RunCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require("runs.start")),
) -> Run:
    resume = db.get(Resume, body.resume_id)
    if resume is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Resume not found")

    # Fail before spending anything if a required provider is missing.
    for provider in ("groq", "tavily"):
        vault.get_credential(db, provider)

    discovery.fail_stale_runs(db)
    in_progress = db.scalar(
        select(Run).where(Run.resume_id == resume.id, Run.status.in_([RunStatus.pending, RunStatus.running]))
    )
    if in_progress:
        raise HTTPException(status.HTTP_409_CONFLICT, f"A run for this resume is already in progress ({in_progress.id})")

    active = prompts.active_versions(db)
    missing = [node for node in discovery.PROMPT_NODES if node not in active]
    if missing:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"No active prompt for: {', '.join(missing)}")

    run = Run(
        resume_id=resume.id,
        num_startups=body.num_startups,
        num_kdms_per_company=body.num_kdms_per_company,
        status=RunStatus.pending,
        started_by=user.id,
        prompt_versions={node: str(active[node].id) for node in discovery.PROMPT_NODES},
    )
    db.add(run)
    try:
        db.flush()
    except IntegrityError:
        # The check above can race with a simultaneous request; the partial unique index can't.
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "A run for this resume is already in progress")
    audit.record(db, user, "runs.start", "run", run.id, {
        "resume_id": str(resume.id), "num_startups": run.num_startups, "num_kdms": run.num_kdms_per_company,
    })
    db.commit()
    db.refresh(run)
    discovery.submit_run(run.id)
    return run


@router.get("", response_model=list[RunOut])
def list_runs(
    resume_id: uuid.UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: User = Depends(require("dashboard.view")),
) -> list[Run]:
    query = select(Run).order_by(Run.created_at.desc()).limit(limit)
    if resume_id:
        query = query.where(Run.resume_id == resume_id)
    return list(db.scalars(query))


def _candidates(db: Session, *conditions) -> list[CandidateOut]:
    rows = db.execute(
        select(Candidate, Startup.name, Resume.filename)
        .join(Startup, Candidate.startup_id == Startup.id)
        .join(Run, Candidate.run_id == Run.id)
        .join(Resume, Run.resume_id == Resume.id)
        .where(*conditions)
        .order_by(Startup.relevance.desc().nulls_last(), Candidate.created_at)
    )
    return [
        CandidateOut(
            id=c.id, run_id=c.run_id, startup_id=c.startup_id, startup_name=startup_name, name=c.name,
            title=c.title, linkedin_url=c.linkedin_url, reason=c.reason,
            existing_contact_id=c.existing_contact_id, status=c.status.value, cv_filename=cv,
        )
        for c, startup_name, cv in rows
    ]


@router.get("/{run_id}", response_model=RunDetailOut)
def get_run(
    run_id: uuid.UUID, db: Session = Depends(get_db), _: User = Depends(require("dashboard.view"))
) -> RunDetailOut:
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    startups = db.scalars(
        select(Startup).where(Startup.run_id == run.id).order_by(Startup.relevance.desc().nulls_last())
    )
    return RunDetailOut(
        **RunOut.model_validate(run).model_dump(),
        resume_filename=db.get(Resume, run.resume_id).filename,
        startups=[StartupOut.model_validate(s) for s in startups],
        candidates=_candidates(db, Candidate.run_id == run.id),
    )


@router.get("/{run_id}/events", response_model=list[RunEventOut])
def list_events(
    run_id: uuid.UUID,
    after: int = 0,
    db: Session = Depends(get_db),
    _: User = Depends(require("dashboard.view")),
) -> list[RunEvent]:
    return list(db.scalars(
        select(RunEvent).where(RunEvent.run_id == run_id, RunEvent.id > after).order_by(RunEvent.id).limit(500)
    ))


@router.get("/{run_id}/stream")
def stream_events(
    run_id: uuid.UUID,
    after: int = 0,
    last_event_id: int | None = Header(None),
    _: uuid.UUID = Depends(require_for_stream("dashboard.view")),
) -> StreamingResponse:
    """Server-sent events for the live progress panel. Ends with an `end` event once the run finishes."""

    async def events():
        last = max(after, last_event_id or 0)
        idle_polls = 0
        while True:
            # Async generator: waiting between polls holds no thread; only the DB read borrows one.
            run_status, batch = await run_in_threadpool(_poll, run_id, last)
            if run_status is None:
                yield "event: error\ndata: {\"detail\": \"Run not found\"}\n\n"
                return
            for payload in batch:
                last = payload["id"]
                yield f"id: {payload['id']}\nevent: {payload['kind']}\ndata: {json.dumps(payload)}\n\n"
            if run_status in FINISHED and not batch:
                yield f"event: end\ndata: {json.dumps({'status': run_status.value})}\n\n"
                return
            idle_polls = 0 if batch else idle_polls + 1
            if idle_polls and idle_polls % 15 == 0:
                yield ": keepalive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


def _poll(run_id: uuid.UUID, after: int) -> tuple[RunStatus | None, list[dict]]:
    # A short-lived session per poll, so a watching tab never pins a pooled connection.
    with SessionLocal() as db:
        run = db.get(Run, run_id)
        if run is not None and run.status not in FINISHED:
            # A dead worker never writes its final event; without this the stream would wait forever.
            if (run.heartbeat_at or run.created_at) < datetime.now(timezone.utc) - discovery.STALE_AFTER:
                discovery.fail_stale_runs(db)
        # Status first: the final event commits together with the status change, so a finished
        # status guarantees its events are visible to the read that follows.
        run_status = db.scalar(select(Run.status).where(Run.id == run_id))
        events = db.scalars(
            select(RunEvent).where(RunEvent.run_id == run_id, RunEvent.id > after).order_by(RunEvent.id).limit(200)
        )
        return run_status, [RunEventOut.model_validate(e).model_dump(mode="json") for e in events]


candidates_router = APIRouter(prefix="/candidates", tags=["candidates"])


@candidates_router.get("", response_model=list[CandidateOut])
def list_candidates(
    status_filter: CandidateStatus | None = Query(None, alias="status"),
    run_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(require("dashboard.view")),
) -> list[CandidateOut]:
    # Only completed runs: an in-progress run's list is still growing, and a failed run's is deleted.
    conditions = [Run.status == RunStatus.completed]
    if status_filter:
        conditions.append(Candidate.status == status_filter)
    if run_id:
        conditions.append(Candidate.run_id == run_id)
    return _candidates(db, *conditions)
