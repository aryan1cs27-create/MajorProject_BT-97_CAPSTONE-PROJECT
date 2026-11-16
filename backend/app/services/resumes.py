"""Resume use-cases: upload + parse, list, retrieve, delete.

Transaction/file consistency: a database failure rolls back the record and
removes the stored file; an extraction failure happens *before* any insert, so
no orphan rows are possible. Conversely, deleting a resume removes both the
row and its file.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import APIError, not_found
from app.models.resume import STATUS_NO_TEXT, STATUS_PROCESSED, Resume
from app.services import storage
from app.services.resume_parser import (
    extract_text,
    make_preview,
    validate_content,
    validate_extension,
)

logger = logging.getLogger("matchwise.resumes")

MAX_FILENAME_LENGTH = 255
MAX_CANDIDATE_NAME_LENGTH = 255


def _clean_filename(filename: str) -> str:
    """Display-only: basename only, never a path, capped length."""
    name = Path(filename.replace("\\", "/")).name
    return name[:MAX_FILENAME_LENGTH]


def create_resume(db: Session, upload: UploadFile, candidate_name: str | None) -> Resume:
    filename = _clean_filename((upload.filename or "").strip())
    if not filename:
        raise APIError(400, "missing_file", "No file was provided in the 'file' field.")

    extension = validate_extension(filename)
    data = storage.read_upload(upload)                      # may raise 413
    file_type = validate_content(data, extension)           # may raise 415

    stored_filename = storage.build_stored_filename(extension)
    stored = storage.save_bytes(data, stored_filename)

    try:
        text = extract_text(stored.path, file_type)
    except APIError:
        storage.delete_file(stored_filename)
        raise
    except Exception:
        storage.delete_file(stored_filename)
        logger.exception("Unexpected parsing failure for %s", filename)
        raise APIError(422, "unreadable_document", "The document could not be processed.") from None

    name = (candidate_name or "").strip()[:MAX_CANDIDATE_NAME_LENGTH] or None
    resume = Resume(
        candidate_name=name,
        original_filename=filename,
        stored_filename=stored_filename,
        file_type=file_type,
        file_size=stored.size,
        extracted_text=text,
        skills=[],
        processing_status=STATUS_PROCESSED if text else STATUS_NO_TEXT,
    )

    try:
        db.add(resume)
        db.commit()
    except Exception:
        db.rollback()
        storage.delete_file(stored_filename)
        logger.exception("Could not persist resume record for %s", filename)
        raise APIError(500, "storage_error", "The resume could not be saved.") from None

    db.refresh(resume)
    logger.info("Stored resume %s (%s, %s bytes)", resume.id, file_type, stored.size)
    return resume


def list_resumes(db: Session, page: int, page_size: int) -> tuple[list[Resume], int]:
    total = db.scalar(select(func.count()).select_from(Resume)) or 0
    rows = db.scalars(
        select(Resume)
        .order_by(Resume.uploaded_at.desc(), Resume.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return list(rows), int(total)


def get_resume(db: Session, resume_id: int) -> Resume:
    resume = db.get(Resume, resume_id)
    if resume is None:
        raise not_found("Resume", resume_id)
    return resume


def delete_resume(db: Session, resume_id: int) -> None:
    resume = get_resume(db, resume_id)
    stored_filename = resume.stored_filename
    try:
        db.delete(resume)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Could not delete resume %s", resume_id)
        raise APIError(500, "delete_failed", "The resume could not be deleted.") from None
    # Row is gone — remove the file, tolerating a file that is already missing.
    storage.delete_file(stored_filename)


def text_preview(resume: Resume) -> str:
    return make_preview(resume.extracted_text)
