from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_db
from app.routers import resumes
from app.schemas import HealthOut

app = FastAPI(title="outreach-io")

app.include_router(resumes.router)


@app.get("/health", response_model=HealthOut)
def health(db: Session = Depends(get_db)) -> HealthOut:
    try:
        db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as exc:  # noqa: BLE001 — surfaced to caller, not swallowed
        db_status = f"error: {exc}"
    return HealthOut(status="ok", db=db_status)
