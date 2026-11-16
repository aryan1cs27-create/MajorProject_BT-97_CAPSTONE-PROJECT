"""Job use-cases: create, list (with filters), retrieve, update, delete."""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import APIError, not_found
from app.models.job import Job
from app.schemas.job import JobCreate, JobUpdate

logger = logging.getLogger("matchwise.jobs")


def create_job(db: Session, payload: JobCreate) -> Job:
    job = Job(**payload.model_dump())
    try:
        db.add(job)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Could not create job %r", payload.title)
        raise APIError(500, "create_failed", "The job could not be created.") from None
    db.refresh(job)
    return job


def list_jobs(
    db: Session,
    *,
    page: int,
    page_size: int,
    title: str | None = None,
    company: str | None = None,
    location: str | None = None,
    employment_type: str | None = None,
    source: str | None = None,
    category: str | None = None,
    work_mode: str | None = None,
    provider: str | None = None,
) -> tuple[list[Job], int]:
    conditions = []
    if title:
        conditions.append(Job.title.ilike(f"%{title}%"))
    if company:
        conditions.append(Job.company.ilike(f"%{company}%"))
    if location:
        conditions.append(Job.location.ilike(f"%{location}%"))
    if employment_type:
        conditions.append(Job.employment_type == employment_type.lower())
    if category:
        conditions.append(Job.category.ilike(f"%{category}%"))
    if work_mode:
        conditions.append(Job.work_mode == work_mode.strip().lower())
    if provider:
        conditions.append(Job.provider == provider.strip().lower())
    if source:
        key = source.strip().lower()
        if key == "live":  # convenience alias for the UI
            key = "provider"
        if key not in {"provider", "import", "manual", "sample"}:
            raise APIError(
                400,
                "invalid_source",
                "source must be one of: provider, import, manual, sample (or 'live').",
            )
        conditions.append(Job.source == key)

    query = select(Job)
    if conditions:
        query = query.where(*conditions)

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(
        query.order_by(Job.created_at.desc(), Job.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return list(rows), int(total)


def get_job(db: Session, job_id: int) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise not_found("Job", job_id)
    return job


def update_job(db: Session, job_id: int, payload: JobUpdate) -> Job:
    job = get_job(db, job_id)
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise APIError(400, "empty_update", "No fields were provided to update.")
    for field, value in changes.items():
        setattr(job, field, value)
    try:
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Could not update job %s", job_id)
        raise APIError(500, "update_failed", "The job could not be updated.") from None
    db.refresh(job)
    return job


def delete_job(db: Session, job_id: int) -> None:
    job = get_job(db, job_id)
    try:
        db.delete(job)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Could not delete job %s", job_id)
        raise APIError(500, "delete_failed", "The job could not be deleted.") from None
