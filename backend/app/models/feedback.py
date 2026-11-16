"""MatchFeedback entity — explicit user feedback on one recommendation.

Design (Phase 6)
----------------
* **Append-only**: a new row per feedback event. Nothing is updated in place,
  so history is preserved; the *latest* event per resume↔job pair is derived by
  ordering on ``id`` (or ``created_at``) descending.
* **Idempotent**: ``event_key`` is a deterministic hash of the submitted payload
  (+ an optional ``Idempotency-Key`` header). The same submission twice returns
  the original row instead of duplicating it.
* **Scores are untouched**: this table never stores or mutates a match score.
  The engine score stays in ``match_results``; feedback only feeds the bounded
  adjustment computed by ``app.services.ranking``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

#: Allowed ``verdict`` values.
VERDICT_HELPFUL = "helpful"
VERDICT_NOT_RELEVANT = "not_relevant"
VERDICTS: tuple[str, ...] = (VERDICT_HELPFUL, VERDICT_NOT_RELEVANT)

#: Allowed ``outcome`` values (optional). ``rejected`` is neutral for ranking:
#: a rejected application is not evidence that the recommendation was wrong.
OUTCOME_SHORTLISTED = "shortlisted"
OUTCOME_APPLIED = "applied"
OUTCOME_INTERVIEW = "interview"
OUTCOME_REJECTED = "rejected"
OUTCOMES: tuple[str, ...] = (
    OUTCOME_SHORTLISTED,
    OUTCOME_APPLIED,
    OUTCOME_INTERVIEW,
    OUTCOME_REJECTED,
)


class MatchFeedback(Base):
    __tablename__ = "match_feedback"
    __table_args__ = (
        # Fast lookup of the latest verdict per resume↔job pair, and of every
        # event for a resume (used by the ranking index and the summary).
        Index("ix_feedback_pair_created", "resume_id", "job_id", "created_at"),
        Index("ix_feedback_resume_created", "resume_id", "created_at"),
        UniqueConstraint("event_key", name="uq_feedback_event_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # All three references are derived server-side from the match row, so a
    # client can never create an inconsistent match/resume/job combination.
    match_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("match_results.id", ondelete="CASCADE"), nullable=False, index=True
    )
    resume_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("resumes.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )

    verdict: Mapped[str] = mapped_column(String(20), nullable=False)
    # Optional 1..5 relevance rating (NULL = not rated).
    relevance: Mapped[int | None] = mapped_column(Integer, nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(20), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Deterministic identity of this submission (idempotency).
    event_key: Mapped[str] = mapped_column(String(64), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    match = relationship("MatchResult", lazy="joined")
    job = relationship("Job", lazy="joined")

    def __repr__(self) -> str:
        return (
            f"<MatchFeedback id={self.id} match={self.match_id} "
            f"verdict={self.verdict!r} relevance={self.relevance}>"
        )
