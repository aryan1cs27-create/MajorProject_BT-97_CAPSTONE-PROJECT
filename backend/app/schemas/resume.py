"""Pydantic schemas for resume endpoints."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ResumePreview(BaseModel):
    """One row of the resume list — never includes the full extracted text."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    candidate_name: str | None = None
    original_filename: str
    file_type: str
    file_size: int
    processing_status: str
    uploaded_at: datetime
    text_preview: str = ""


class ResumeUploadResponse(BaseModel):
    """Response of POST /api/resumes/upload (preview only, no full text)."""

    id: int
    candidate_name: str | None = None
    original_filename: str
    file_type: str
    processing_status: str
    text_preview: str = ""
    extracted_chars: int = 0
    uploaded_at: datetime


class ResumeDetail(ResumePreview):
    """Full resume record including extracted text (internal application use)."""

    extracted_text: str
    skills: list[str] = Field(default_factory=list)
    stored_filename: str


class ResumeListResponse(BaseModel):
    items: list[ResumePreview]
    total: int
    page: int
    page_size: int
