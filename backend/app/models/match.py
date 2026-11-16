"""MatchResult entity: one scored resume↔job pair.

Rows are upserted on ``(resume_id, job_id)``, so recalculating a match updates
the existing record instead of creating duplicates.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class MatchResult(Base):
    __tablename__ = "match_results"
    __table_args__ = (
        UniqueConstraint("resume_id", "job_id", name="uq_match_resume_job"),
        Index("ix_match_resume_score", "resume_id", "final_score"),
        Index("ix_match_job_score", "job_id", "final_score"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    resume_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("resumes.id", ondelete="CASCADE"),
        nullable=False,
    )
    job_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )

    final_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    required_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    preferred_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    semantic_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    matched_skills: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    missing_required: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    missing_preferred: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    # Effective weights actually used (after renormalisation) and configured weights
    weights: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # Experience / qualification / related-skill signals (not part of the score)
    signals: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # Components, match types, discovered skills, unavailable components, notes
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    resume = relationship("Resume", lazy="joined")
    job = relationship("Job", lazy="joined")

    def __repr__(self) -> str:
        return f"<MatchResult id={self.id} resume={self.resume_id} job={self.job_id} score={self.final_score}>"
