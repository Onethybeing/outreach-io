"""Resume library endpoints (upload + list). Parsing/OCR lands in Phase 2."""

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Resume
from app.schemas import ResumeOut

router = APIRouter(prefix="/resumes", tags=["resumes"])

STORAGE_DIR = Path("storage/resumes")
ALLOWED_SUFFIXES = {".pdf", ".docx"}


@router.get("", response_model=list[ResumeOut])
def list_resumes(db: Session = Depends(get_db)) -> list[Resume]:
    return list(db.scalars(select(Resume).order_by(Resume.uploaded_at.desc())))


@router.post("", response_model=ResumeOut)
async def upload_resume(file: UploadFile, db: Session = Depends(get_db)) -> Resume:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(400, f"Unsupported file type '{suffix}'. Use PDF or DOCX.")

    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4()}{suffix}"
    dest = STORAGE_DIR / stored_name
    dest.write_bytes(await file.read())

    resume = Resume(
        filename=file.filename or stored_name,
        storage_path=str(dest),
        status="uploaded",
    )
    db.add(resume)
    db.commit()
    db.refresh(resume)
    return resume
