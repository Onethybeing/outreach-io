import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app import evals, llm, prompts, stats, vault
from app.auth import require
from app.db import get_db
from app.models import Contact, Run, RunStatus, User

router = APIRouter(tags=["stats"])


CACHE_SECONDS = 60
_cache: dict[tuple, tuple[float, dict]] = {}


@router.get("/stats")
def get_stats(
    days: int = Query(30, ge=1, le=365),
    resume_id: uuid.UUID | None = None,
    include_dev: bool = False,
    fresh: bool = False,
    db: Session = Depends(get_db),
    _: User = Depends(require("dashboard.view")),
) -> dict:
    """Cached for a minute per filter combination (PLAN.md §10); fresh=true recomputes."""
    key = (days, resume_id, include_dev)
    hit = _cache.get(key)
    if hit and not fresh and hit[0] > time.monotonic():
        return hit[1]
    result = stats.build(db, days=days, resume_id=resume_id, include_dev=include_dev)
    _cache[key] = (time.monotonic() + CACHE_SECONDS, result)
    return result


@router.post("/evals/runs/{run_id}/startups", status_code=status.HTTP_202_ACCEPTED)
def evaluate_run_startups(run_id: uuid.UUID, db: Session = Depends(get_db), _: User = Depends(require("contacts.act"))) -> dict:
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    if run.status != RunStatus.completed:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only completed runs can be evaluated")
    evals._pool.submit(evals._safely, evals.judge_run_startups, run_id)  # explicit request: ignores AUTO_EVALS
    return {"queued": True}


@router.post("/evals/contacts/{contact_id}/draft")
def evaluate_draft(contact_id: uuid.UUID, db: Session = Depends(get_db), _: User = Depends(require("drafts.act"))) -> dict:
    contact = db.get(Contact, contact_id)
    if contact is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contact not found")
    if not contact.draft_text:
        raise HTTPException(status.HTTP_409_CONFLICT, "This contact has no draft to evaluate")
    try:
        result = evals.judge_draft(db, contact_id)
    except (llm.LLMError, prompts.PromptError, vault.VaultError) as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Evaluation failed: {exc}")
    if result is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "The draft changed while it was being evaluated. Try again")
    return result
