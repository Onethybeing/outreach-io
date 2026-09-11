"""Resume library endpoints (upload + list). Parsing/OCR lands with the discovery graph."""

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.auth import require
from app.db import get_db
from app.models import Resume, User
from app.schemas import ResumeOut

router = APIRouter(prefix="/resumes", tags=["resumes"])

STORAGE_DIR = Path("storage/resumes")
ALLOWED_SUFFIXES = {".pdf", ".docx"}


@router.get("", response_model=list[ResumeOut])
def list_resumes(
    db: Session = Depends(get_db), _: User = Depends(require("dashboard.view"))
) -> list[Resume]:
    return list(db.scalars(select(Resume).order_by(Resume.uploaded_at.desc())))


# Plain `def` so FastAPI runs it in a worker thread; the DB calls below are blocking.
@router.post("", response_model=ResumeOut)
def upload_resume(
    file: UploadFile,
    db: Session = Depends(get_db),
    user: User = Depends(require("resumes.upload")),
) -> Resume:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(400, f"Unsupported file type '{suffix}'. Use PDF or DOCX.")

    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4()}{suffix}"
    dest = STORAGE_DIR / stored_name
    dest.write_bytes(file.file.read())

    resume = Resume(
        filename=file.filename or stored_name,
        storage_path=str(dest),
        status="uploaded",
    )
    try:
        db.add(resume)
        db.flush()
        audit.record(db, user, "resumes.upload", "resume", resume.id, {"filename": resume.filename})
        db.commit()
    except Exception:
        db.rollback()
        dest.unlink(missing_ok=True)
        raise
    db.refresh(resume)
    return resume
