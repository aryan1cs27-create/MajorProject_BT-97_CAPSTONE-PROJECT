"""Resume endpoints.

Authentication (Phase 5.1): the whole router is guarded by ``require_api_token``,
so it requires ``API_TOKEN`` when that environment variable is configured.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from sqlalchemy.orm import Session

from app.core.security import require_api_token
from app.db.session import get_db
from app.models.resume import Resume
from app.schemas.resume import (
    ResumeDetail,
    ResumeListResponse,
    ResumePreview,
    ResumeUploadResponse,
)
from app.services import resumes as resume_service

router = APIRouter(
    prefix="/api/resumes",
    tags=["resumes"],
    dependencies=[Depends(require_api_token)],
)

Db = Annotated[Session, Depends(get_db)]


def _preview(resume: Resume) -> ResumePreview:
    return ResumePreview(
        id=resume.id,
        candidate_name=resume.candidate_name,
        original_filename=resume.original_filename,
        file_type=resume.file_type,
        file_size=resume.file_size,
        processing_status=resume.processing_status,
        uploaded_at=resume.uploaded_at,
        text_preview=resume_service.text_preview(resume),
    )


@router.post(
    "/upload",
    response_model=ResumeUploadResponse,
    status_code=201,
    summary="Upload and parse a PDF/DOCX resume",
)
def upload_resume(
    db: Db,
    file: Annotated[UploadFile, File(description="PDF or DOCX (limit: UPLOAD_MAX_BYTES)")],
    candidate_name: Annotated[str | None, Form(max_length=255)] = None,
) -> ResumeUploadResponse:
    """Stores the file locally, extracts its text, returns a short preview only."""
    resume = resume_service.create_resume(db, file, candidate_name)
    return ResumeUploadResponse(
        id=resume.id,
        candidate_name=resume.candidate_name,
        original_filename=resume.original_filename,
        file_type=resume.file_type,
        processing_status=resume.processing_status,
        text_preview=resume_service.text_preview(resume),
        extracted_chars=len(resume.extracted_text),
        uploaded_at=resume.uploaded_at,
    )


@router.get("", response_model=ResumeListResponse, summary="List resumes (paginated)")
def list_resumes(
    db: Db,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 10,
) -> ResumeListResponse:
    rows, total = resume_service.list_resumes(db, page, page_size)
    return ResumeListResponse(
        items=[_preview(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{resume_id}", response_model=ResumeDetail, summary="Get one resume with its text")
def get_resume(resume_id: int, db: Db) -> ResumeDetail:
    resume = resume_service.get_resume(db, resume_id)
    return ResumeDetail(
        **_preview(resume).model_dump(),
        extracted_text=resume.extracted_text,
        skills=list(resume.skills or []),
        stored_filename=resume.stored_filename,
    )


@router.delete("/{resume_id}", status_code=204, summary="Delete a resume record and its file")
def delete_resume(resume_id: int, db: Db) -> Response:
    resume_service.delete_resume(db, resume_id)
    return Response(status_code=204)
