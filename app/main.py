import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import bootstrap
from app.config import get_settings
from app.db import SessionLocal, get_db
from app.prompts import PromptError
from app.contacts.service import ActionError
from app.routers import audit_log, auth, contacts, prompts, replies, resumes, runs, users, vault
from app.schemas import HealthOut
from app.vault import VaultError

logger = logging.getLogger(__name__)


def _bootstrap() -> None:
    with SessionLocal() as db:
        bootstrap.run(db, get_settings())


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await run_in_threadpool(_bootstrap)
    yield


app = FastAPI(title="outreach-io", lifespan=lifespan)

for router in (
    auth.router, users.router, audit_log.router, vault.router, prompts.router, resumes.router,
    runs.router, runs.candidates_router,
    contacts.candidates_router, contacts.contacts_router, contacts.settings_router,
    replies.router, replies.internal_router,
):
    app.include_router(router)


@app.exception_handler(VaultError)
@app.exception_handler(PromptError)
async def user_facing_error(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"detail": str(exc)})


@app.exception_handler(ActionError)
async def action_error(_: Request, exc: ActionError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})


@app.get("/health", response_model=HealthOut)
def health(response: Response, db: Session = Depends(get_db)) -> HealthOut:
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.exception("Health check: database unreachable")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthOut(status="degraded", db="unreachable")
    return HealthOut(status="ok", db="ok")
