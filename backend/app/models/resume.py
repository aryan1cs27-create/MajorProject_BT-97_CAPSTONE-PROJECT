"""Resume entity: metadata + extracted text for an uploaded document.

Uploaded binaries are stored on the local filesystem (see
``app.services.storage``); MySQL only keeps metadata and the extracted text.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base

# processing_status values
STATUS_PENDING = "pending"
STATUS_PROCESSED = "processed"
STATUS_NO_TEXT = "no_text"
STATUS_FAILED = "failed"


class Resume(Base):
    __tablename__ = "resumes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    candidate_name: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)

    # Original client filename (display only — never used as a filesystem path)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # Safe unique name under the uploads directory
    stored_filename: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)

    file_type: Mapped[str] = mapped_column(String(10), nullable=False)  # pdf | docx
    file_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    extracted_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Structured skill hints extracted from the text; empty until Phase 3.
    skills: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    processing_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=STATUS_PENDING, index=True
    )

    def __repr__(self) -> str:
        return f"<Resume id={self.id} file={self.original_filename!r} status={self.processing_status}>"
