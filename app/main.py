import logging

from fastapi import Depends, FastAPI, Response, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db import get_db
from app.routers import resumes
from app.schemas import HealthOut

logger = logging.getLogger(__name__)

app = FastAPI(title="outreach-io")

app.include_router(resumes.router)


@app.get("/health", response_model=HealthOut)
def health(response: Response, db: Session = Depends(get_db)) -> HealthOut:
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.exception("Health check: database unreachable")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthOut(status="degraded", db="unreachable")
    return HealthOut(status="ok", db="ok")
