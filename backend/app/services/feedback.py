"""Feedback persistence, lookup and aggregation (Phase 6).

Feedback is append-only and idempotent:

* the resume/job ids are taken from the stored ``match_results`` row, never from
  the request, so an inconsistent match/resume/job combination is impossible;
* ``event_key`` is a SHA-256 of the normalised submission (plus an optional
  ``Idempotency-Key`` header), so replaying the same request returns the
  original event instead of appending a duplicate;
* a *new* submission always appends a new event — history is never rewritten.

Nothing here reads or writes match scores; ranking lives in
``app.services.ranking``.
"""

from __future__ import annotations

import hashlib
import logging

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import APIError, not_found
from app.models.feedback import (
    OUTCOME_APPLIED,
    OUTCOME_INTERVIEW,
    OUTCOME_REJECTED,
    OUTCOME_SHORTLISTED,
    VERDICT_HELPFUL,
    VERDICT_NOT_RELEVANT,
    MatchFeedback,
)
from app.models.job import Job
from app.models.match import MatchResult
from app.models.resume import Resume
from app.schemas.feedback import FeedbackCreate
from app.services.ranking import build_index, job_categories

logger = logging.getLogger("matchwise.feedback")

SUMMARY_NOTE = (
    "Counts cover only the feedback you explicitly submitted; a missing skill is "
    "never inferred from it."
)


def event_key(
    match_id: int,
    payload: FeedbackCreate,
    idempotency_key: str | None = None,
) -> str:
    """Deterministic identity of one submission."""
    if idempotency_key:
        material = f"idem|{idempotency_key}"
    else:
        material = "|".join(
            [
                "payload",
                str(match_id),
                payload.verdict,
                "" if payload.relevance is None else str(payload.relevance),
                payload.outcome or "",
                payload.reason or "",
            ]
        )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _require_match(db: Session, match_id: int) -> MatchResult:
    match = db.get(MatchResult, match_id)
    if match is None:
        raise not_found("Match", match_id)
    # FKs cascade, so this is defensive only — but fail honestly rather than
    # storing feedback that points at a deleted resume/job.
    if db.get(Resume, match.resume_id) is None or db.get(Job, match.job_id) is None:
        raise APIError(
            409,
            "match_inconsistent",
            "This match refers to a resume or job that no longer exists.",
        )
    return match


def submit_feedback(
    db: Session,
    match_id: int,
    payload: FeedbackCreate,
    idempotency_key: str | None = None,
) -> tuple[MatchFeedback, bool]:
    """Store one feedback event. Returns ``(event, created)``.

    ``created`` is False when the identical submission already exists.
    """
    match = _require_match(db, match_id)
    key = event_key(match.id, payload, idempotency_key)

    existing = db.scalar(select(MatchFeedback).where(MatchFeedback.event_key == key))
    if existing is not None:
        return existing, False

    event = MatchFeedback(
        match_id=match.id,
        resume_id=match.resume_id,
        job_id=match.job_id,
        verdict=payload.verdict,
        relevance=payload.relevance,
        outcome=payload.outcome,
        reason=payload.reason,
        event_key=key,
    )
    db.add(event)
    try:
        db.commit()
    except IntegrityError:
        # Concurrent replay: the unique event_key won the race.
        db.rollback()
        existing = db.scalar(select(MatchFeedback).where(MatchFeedback.event_key == key))
        if existing is not None:
            return existing, False
        logger.exception("Could not store feedback for match %s", match_id)
        raise APIError(409, "feedback_conflict", "Feedback could not be stored.") from None

    db.refresh(event)
    logger.info(
        "Stored feedback %s for match %s (verdict=%s)", event.id, match_id, event.verdict
    )
    return event, True


def events_for_resume(db: Session, resume_id: int) -> list[MatchFeedback]:
    return list(
        db.scalars(
            select(MatchFeedback)
            .where(MatchFeedback.resume_id == resume_id)
            .order_by(MatchFeedback.created_at, MatchFeedback.id)
        ).all()
    )


def latest_feedback(db: Session, resume_id: int) -> dict[int, MatchFeedback]:
    """Latest event per job id for a resume (append-only history preserved)."""
    return build_index(db, resume_id).latest


def feedback_summary(db: Session, resume_id: int) -> dict:
    """Aggregate the feedback history for one resume."""
    if db.get(Resume, resume_id) is None:
        raise not_found("Resume", resume_id)

    index = build_index(db, resume_id)
    events = index.events

    helpful = sum(1 for event in events if event.verdict == VERDICT_HELPFUL)
    not_relevant = sum(1 for event in events if event.verdict == VERDICT_NOT_RELEVANT)

    ratings = [event.relevance for event in events if event.relevance is not None]
    average_relevance = round(sum(ratings) / len(ratings), 2) if ratings else None

    outcome_distribution = {
        outcome: 0
        for outcome in (
            OUTCOME_SHORTLISTED,
            OUTCOME_APPLIED,
            OUTCOME_INTERVIEW,
            OUTCOME_REJECTED,
        )
    }
    for event in events:
        if event.outcome:
            outcome_distribution[event.outcome] = outcome_distribution.get(event.outcome, 0) + 1

    helpful_categories: dict[str, int] = {}
    rejected_categories: dict[str, int] = {}
    for event in events:
        if event.job is None:  # pragma: no cover - FK cascade
            continue
        for category in job_categories(event.job):
            if event.verdict == VERDICT_HELPFUL:
                helpful_categories[category] = helpful_categories.get(category, 0) + 1
            elif event.verdict == VERDICT_NOT_RELEVANT:
                rejected_categories[category] = rejected_categories.get(category, 0) + 1

    def top(counts: dict[str, int]) -> list[dict]:
        ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        return [{"category": name, "count": count} for name, count in ordered[:5]]

    matches_total = int(
        db.scalar(
            select(func.count()).select_from(MatchResult).where(
                MatchResult.resume_id == resume_id
            )
        )
        or 0
    )

    return {
        "resume_id": resume_id,
        "total_events": len(events),
        "helpful_count": helpful,
        "not_relevant_count": not_relevant,
        "average_relevance": average_relevance,
        "outcome_distribution": outcome_distribution,
        "most_helpful_skill_categories": top(helpful_categories),
        "most_rejected_skill_categories": top(rejected_categories),
        "feedback_coverage": index.coverage(matches_total),
        "matches_total": matches_total,
        "matches_with_feedback": len(index.latest),
        "notes": [SUMMARY_NOTE],
    }
