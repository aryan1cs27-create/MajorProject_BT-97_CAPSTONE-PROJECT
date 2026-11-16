"""Matching orchestration: profile building, scoring, persistence and ranking.

Kept independent of FastAPI and of the frontend — it only needs a Session.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import APIError, not_found
from app.models.job import Job
from app.models.match import MatchResult
from app.models.resume import Resume
from app.services import semantic
from app.services.features import JobProfile, ResumeProfile, build_job_profile, build_resume_profile
from app.services.scoring import ScoreResult, score_pair

logger = logging.getLogger("matchwise.matching")


def _job_text(profile: JobProfile) -> str:
    parts = [profile.title, profile.description]
    parts.extend(profile.required_labels.values())
    parts.extend(profile.preferred_labels.values())
    return "\n".join(part for part in parts if part)


def semantic_info() -> dict:
    ok, reason = semantic.availability()
    return {"available": ok, "reason": None if ok else (reason or "unavailable")}


def _persist(
    db: Session,
    resume_id: int,
    job_id: int,
    result: ScoreResult,
) -> tuple[MatchResult, bool]:
    """Upsert: update the existing row for this pair, or insert a new one."""
    row = db.scalar(
        select(MatchResult).where(
            MatchResult.resume_id == resume_id,
            MatchResult.job_id == job_id,
        )
    )
    created = row is None
    if row is None:
        row = MatchResult(resume_id=resume_id, job_id=job_id)
        db.add(row)

    row.final_score = result.final_score
    row.required_score = result.components["required"]
    row.preferred_score = result.components["preferred"]
    row.semantic_score = result.components["semantic"]
    row.matched_skills = result.matched_required + [
        m for m in result.matched_preferred if m not in result.matched_required
    ]
    row.missing_required = result.missing_required
    row.missing_preferred = result.missing_preferred
    row.weights = result.effective_weights
    row.signals = result.signals
    row.confidence = result.confidence
    row.details = {
        "components": result.components,
        "configured_weights": result.configured_weights,
        "effective_weights": result.effective_weights,
        "unavailable_components": result.unavailable_components,
        "matched_required": result.matched_required,
        "matched_preferred": result.matched_preferred,
        "required_match_types": {
            m.key: m.match_type for m in result.required_matches if m.matched
        },
        "preferred_match_types": {
            m.key: m.match_type for m in result.preferred_matches if m.matched
        },
        "discovered_skills": result.discovered_skills,
        "notes": result.notes,
    }
    db.flush()
    return row, created


def calculate_matches_for_resume(
    db: Session,
    resume_id: int,
    job_ids: list[int] | None = None,
) -> tuple[list[MatchResult], int]:
    """Score one resume against jobs (all, or a supplied subset) and persist."""
    resume = db.get(Resume, resume_id)
    if resume is None:
        raise not_found("Resume", resume_id)

    query = select(Job)
    if job_ids:
        query = query.where(Job.id.in_(job_ids))
    jobs = list(db.scalars(query.order_by(Job.id)).all())
    if job_ids:
        found = {job.id for job in jobs}
        missing = [job_id for job_id in job_ids if job_id not in found]
        if missing:
            raise not_found("Job", missing[0])

    resume_profile = build_resume_profile(resume)
    resume_text = resume.extracted_text or ""

    results: list[MatchResult] = []
    for job in jobs:
        job_profile = build_job_profile(job)
        outcome = score_pair(
            resume_profile,
            job_profile,
            resume_text=resume_text,
            job_text=_job_text(job_profile),
        )
        row, _ = _persist(db, resume.id, job.id, outcome)
        results.append(row)

    try:
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Could not persist match results for resume %s", resume_id)
        raise APIError(500, "match_persist_failed", "Match results could not be saved.") from None

    results.sort(key=lambda r: (-r.final_score, r.job_id))
    logger.info("Calculated %d matches for resume %s", len(results), resume_id)
    return results, len(results)


def calculate_matches_for_job(
    db: Session,
    job_id: int,
    resume_ids: list[int] | None = None,
) -> tuple[list[MatchResult], int]:
    """Score one job against resumes (used by tests and future endpoints)."""
    job = db.get(Job, job_id)
    if job is None:
        raise not_found("Job", job_id)

    query = select(Resume)
    if resume_ids:
        query = query.where(Resume.id.in_(resume_ids))
    resumes = list(db.scalars(query.order_by(Resume.id)).all())

    job_profile = build_job_profile(job)
    job_text = _job_text(job_profile)

    results: list[MatchResult] = []
    for resume in resumes:
        resume_profile = build_resume_profile(resume)
        outcome = score_pair(
            resume_profile,
            job_profile,
            resume_text=resume.extracted_text or "",
            job_text=job_text,
        )
        row, _ = _persist(db, resume.id, job.id, outcome)
        results.append(row)

    db.commit()
    results.sort(key=lambda r: (-r.final_score, r.resume_id))
    return results, len(results)


def _paginate(
    db: Session,
    filters: list,
    page: int,
    page_size: int,
) -> tuple[list[MatchResult], int]:
    base = select(MatchResult).where(*filters)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.scalars(
        base.order_by(MatchResult.final_score.desc(), MatchResult.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return list(rows), int(total)


def matches_for_resume(
    db: Session, resume_id: int, page: int, page_size: int
) -> tuple[list[MatchResult], int]:
    if db.get(Resume, resume_id) is None:
        raise not_found("Resume", resume_id)
    return _paginate(db, [MatchResult.resume_id == resume_id], page, page_size)


def matches_for_job(
    db: Session, job_id: int, page: int, page_size: int
) -> tuple[list[MatchResult], int]:
    if db.get(Job, job_id) is None:
        raise not_found("Job", job_id)
    return _paginate(db, [MatchResult.job_id == job_id], page, page_size)


def stored_matches_for_resume(db: Session, resume_id: int) -> list[MatchResult]:
    """Every stored match for a resume, in engine order (score desc, id asc).

    Needed for feedback-adjusted ranking, which reorders the full set before
    paginating — with no feedback the order is identical to ``_paginate``.
    """
    if db.get(Resume, resume_id) is None:
        raise not_found("Resume", resume_id)
    return list(
        db.scalars(
            select(MatchResult)
            .where(MatchResult.resume_id == resume_id)
            .order_by(MatchResult.final_score.desc(), MatchResult.id.asc())
        ).all()
    )


def get_match(db: Session, match_id: int) -> MatchResult:
    row = db.get(MatchResult, match_id)
    if row is None:
        raise not_found("Match", match_id)
    return row
