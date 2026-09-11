"""Resume text extraction: text layer first, vision OCR only when the file has no usable text."""

import base64
import io
from pathlib import Path

import docx
import pdfplumber
import pypdfium2
from sqlalchemy.orm import Session

from app import llm
from app.storage import StorageError, get_storage

MIN_TEXT_CHARS = 300  # below this a PDF is treated as scanned
MAX_OCR_PAGES = 4
OCR_MODEL = "qwen/qwen3.8-27b"  # checked: transcribes image text cleanly on Groq
OCR_PROMPT = (
    "Transcribe all text in this resume page exactly as written, top to bottom. "
    "Keep section headings and bullet points on their own lines. Output only the text."
)


class ExtractionError(Exception):
    pass


def _pdf_text(data: bytes) -> str:
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages).strip()


def _docx_text(data: bytes) -> str:
    document = docx.Document(io.BytesIO(data))
    lines = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            lines.append(" | ".join(cell.text for cell in row.cells))
    return "\n".join(line for line in lines if line.strip()).strip()


def _pdf_ocr(db: Session, data: bytes) -> str:
    pdf = pypdfium2.PdfDocument(data)
    try:
        pages = []
        for index in range(min(len(pdf), MAX_OCR_PAGES)):
            image = pdf[index].render(scale=2).to_pil()
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            data_url = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
            pages.append(
                llm.complete(
                    db, OCR_MODEL, OCR_PROMPT, 0.0, 4096,
                    image_data_url=data_url, name="ocr_page", metadata={"page": index + 1},
                )
            )
        return "\n\n".join(pages).strip()
    finally:
        pdf.close()


def extract_text(db: Session, storage_path: str) -> tuple[str, str]:
    """Returns (text, method) where method is pdf_text | docx | vision_ocr."""
    storage = get_storage()
    name = Path(storage_path).name
    if not storage.exists(storage_path):
        raise ExtractionError(f"Resume file is missing on the server: {name}")
    try:
        data = storage.read(storage_path)
    except StorageError as exc:
        raise ExtractionError(str(exc))

    suffix = Path(storage_path).suffix.lower()
    if suffix == ".docx":
        text, method = _docx_text(data), "docx"
    elif suffix == ".pdf":
        text, method = _pdf_text(data), "pdf_text"
        if len(text) < MIN_TEXT_CHARS:
            text, method = _pdf_ocr(db, data), "vision_ocr"
    else:
        raise ExtractionError(f"Unsupported resume type '{suffix}'")

    if len(text) < 100:
        raise ExtractionError("Could not read enough text from the resume (is it blank or an image-only DOCX?)")
    return text, method
