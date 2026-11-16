"""Job entity: a job posting that resumes will be matched against (Phase 3)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, Boolean, Date, DateTime, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    title: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    company: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)

    required_skills: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    preferred_skills: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    # Years of experience, e.g. 2.0 — nullable because some jobs do not state one.
    minimum_experience: Mapped[Decimal | None] = mapped_column(Numeric(4, 1), nullable=True)
    employment_type: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)

    # True for rows created by scripts/seed_jobs.py so the UI can label
    # sample data separately from user-created data.
    is_sample: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Application tracking status for Phase 5
    # Statuses: saved | applied | interview | rejected | offer
    # Null means the job is not tracked in the user's application workflow
    application_status: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # --- Provenance / live provider fields (Phase 5) ---
    # Where this row came from: "provider" (live API), "import" (CSV/JSON),
    # "manual" (created through the API/UI) or "sample" (seed script).
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="manual", index=True)
    # Provider name (e.g. "jobsforce") — only set for rows imported from a live API.
    provider: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    # Provider's internal job ID (unique within the provider's system). This is
    # the deduplication key: a re-import updates the row instead of duplicating.
    provider_job_id: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    # Original application URL handed out by the provider (never rewritten).
    application_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # When the listing was published by the provider (UTC), when it was first
    # imported (fetched_at) and when it was last seen again (verified_at).
    posted_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Lifecycle of an *imported* listing: active | stale | expired.
    # NULL for rows that were never imported (manual/sample data is untouched).
    listing_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Salary advertised by the provider, when available. The unit is preserved
    # separately so a monthly INR range is never rendered as a yearly figure.
    salary_min: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    salary_max: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    salary_currency: Mapped[str | None] = mapped_column(String(10), nullable=True)
    salary_period: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )  # hourly | daily | weekly | monthly | yearly

    # --- JobsForce India listing metadata (Phase 7) ---
    # Canonical job page on the provider's site (never rewritten).
    listing_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # remote | hybrid | onsite
    work_mode: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    # Provider category, e.g. "IT", "Finance"
    category: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    # Date the listing closes (provider's valid_through).
    expires_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    # employer | portal | agency — "agency" means the client stays confidential.
    hiring_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Optional provider-specific metadata stored as JSON
    source_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=dict)

    @property
    def source_label(self) -> str:
        """Human-facing provenance label used by the API and the UI."""
        return self.source or "manual"

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<Job id={self.id} title={self.title!r} company={self.company!r}>"