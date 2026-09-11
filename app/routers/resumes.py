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
from app.storage import get_storage

router = APIRouter(prefix="/resumes", tags=["resumes"])

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

    storage = get_storage()
    key = f"resumes/{uuid.uuid4()}{suffix}"
    content_type = "application/pdf" if suffix == ".pdf" else \
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    storage.save(key, file.file.read(), content_type)

    resume = Resume(filename=file.filename or key.rsplit("/", 1)[-1], storage_path=key, status="uploaded")
    try:
        db.add(resume)
        db.flush()
        audit.record(db, user, "resumes.upload", "resume", resume.id, {"filename": resume.filename})
        db.commit()
    except Exception:
        db.rollback()
        storage.delete(key)  # don't leave an orphan file when the row wasn't saved
        raise
    db.refresh(resume)
    return resume
