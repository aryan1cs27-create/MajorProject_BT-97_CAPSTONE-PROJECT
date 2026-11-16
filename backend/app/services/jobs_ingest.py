"""Job ingestion: normalise provider results and persist them into Job rows.

Design points
-------------
- **Idempotent upsert.** ``provider`` + ``provider_job_id`` is the natural key;
  a listing whose id changed is still deduplicated by its canonical
  ``listing_url``. A repeated search updates the existing row instead of
  duplicating it.
- **Manual data is never touched.** Only rows with ``source == "provider"``
  (equivalently ``provider IS NOT NULL``) are created or updated here; manual
  jobs, sample rows and CSV/JSON imports are left alone.
- **Nothing is invented.** Missing provider fields stay ``NULL``; only the
  description/title/company fall back to neutral placeholder text because those
  columns are NOT NULL in the original schema. Verbatim provider skill lists
  are kept in ``source_metadata``; the taxonomy labels used by the matcher are
  derived from them with the existing Phase 3 extractor
  (:func:`app.services.extraction.extract_skills`), the same code path resumes
  use.
- **Full details on first sight.** Listings are stored from the search result,
  then :func:`enrich_jobs_with_details` fetches the provider's full details for
  *newly created* rows so the description and skills are complete before the
  row is ever matched. A failed detail call never discards the listing.
- **Listing lifecycle.** ``fetched_at`` = first import, ``verified_at`` = last
  time the provider returned the listing, and ``listing_status`` is refreshed
  on every ingest: ``expired`` (past its closing date or posted longer ago than
  ``IMPORT_EXPIRE_DAYS``), ``stale`` (not seen for ``IMPORT_STALE_DAYS``) or
  ``active``.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.job import Job
from app.services.extraction import extract_skills
from app.services.providers import (
    JobProvider,
    JobResult,
    ProviderError,
    ProviderJobClosedError,
    ProviderRateLimitError,
)

logger = logging.getLogger("matchwise.ingest")

STALE_AFTER_DAYS = int(os.getenv("IMPORT_STALE_DAYS", "14"))
EXPIRE_AFTER_DAYS = int(os.getenv("IMPORT_EXPIRE_DAYS", "45"))

#: Maximum detail requests per search (one page is at most 20 listings).
MAX_DETAIL_LOOKUPS = int(os.getenv("IMPORT_DETAIL_LOOKUPS", "20"))

MAX_SKILLS = 15

# Matches "3 years", "5+ years", "2 to 4 years" …
YEARS_RE = re.compile(r"\b(\d{1,2}(?:\.\d+)?)\s*(?:\+|to\s+\d+)?\s*years?\b", re.IGNORECASE)


def utcnow() -> datetime:
    """Naive UTC timestamp (all Phase 5 DateTime columns are naive UTC)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def parse_posted_date(value: str | None) -> datetime | None:
    """Parse an ISO-8601 provider timestamp/date (``…Z`` accepted)."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _experience_hint(text: str) -> float | None:
    match = YEARS_RE.search(text or "")
    if not match:
        return None
    try:
        value = float(match.group(1))
    except ValueError:  # pragma: no cover - regex guarantees a number
        return None
    return value if 0 <= value <= 50 else None


def skills_from_text(*parts: str | None) -> list[str]:
    """Extract skill labels from listing text with the Phase 3 extractor."""
    text = " ".join(part for part in parts if part).strip()
    if not text:
        return []
    labels: list[str] = []
    seen: set[str] = set()
    for skill in extract_skills(text):
        label = skill.label.strip()
        if not label:
            continue
        key = label.lower()
        if key in seen:
            continue
        seen.add(key)
        labels.append(label)
        if len(labels) >= MAX_SKILLS:
            break
    return labels


def normalize_result(result: JobResult, *, now: datetime | None = None) -> dict:
    """Map a provider result onto Job column values."""
    now = now or utcnow()
    title = (result.title or "").strip() or "Untitled listing"
    company = (result.company or "").strip() or "Not specified"
    description = (
        (result.description or "").strip()
        or f"{title} at {company} (no description provided)."
    )

    posted = parse_posted_date(result.posted_date)

    # Taxonomy labels for the matcher. The provider's own wording is kept in
    # source_metadata, never silently replaced.
    required_skills = skills_from_text(
        title, description, *(result.skills or [])
    )
    preferred_skills = skills_from_text(*(result.skills_preferred or []))

    minimum_experience = (
        result.experience_min
        if result.experience_min is not None
        else _experience_hint(description)
    )

    payload: dict = {
        "title": title[:255],
        "company": company[:255],
        "location": (result.location or "").strip()[:255] or None,
        "description": description,
        "required_skills": required_skills,
        "preferred_skills": preferred_skills,
        "employment_type": result.employment_type,
        "minimum_experience": minimum_experience,
        "salary_min": result.salary_min,
        "salary_max": result.salary_max,
        "salary_currency": result.salary_currency,
        "salary_period": result.salary_period,
        "listing_url": result.listing_url,
        "work_mode": result.work_mode,
        "category": result.category,
        "expires_date": result.expires_on,
        "hiring_type": result.hiring_type,
        "source": "provider",
        "provider": result.provider,
        "provider_job_id": result.provider_job_id,
        "application_url": result.application_url,
        "posted_date": posted,
        "verified_at": now,
        "is_sample": False,
        "source_metadata": {
            "source": "provider",
            "provider": result.provider,
            "listing_url": result.listing_url,
            "work_mode": result.work_mode,
            "category": result.category,
            "hiring": {
                "type": result.hiring_type,
                "name": result.hiring_name,
                "display": result.hiring_display,
            },
            "salary": {
                "min": result.salary_min,
                "max": result.salary_max,
                "currency": result.salary_currency,
                "period": result.salary_period,
            },
            "experience": {
                "min_years": result.experience_min,
                "max_years": result.experience_max,
                "fresher_friendly": result.fresher_friendly,
            },
            "posted_date": result.posted_date,
            "valid_through": result.expires_on.isoformat() if result.expires_on else None,
            "skills_raw": list(result.skills or []),
            "skills_preferred_raw": list(result.skills_preferred or []),
            "detail_loaded": result.has_details,
        },
    }

    payload["listing_status"] = _initial_status(payload, posted, now)
    return payload


def _initial_status(payload: dict, posted: datetime | None, now: datetime) -> str:
    """Closed/expired detection based on dates the provider actually supplied."""
    expires = payload.get("expires_date")
    if expires is not None:
        return "expired" if expires < now.date() else "active"
    if posted is not None and (now - posted).days >= EXPIRE_AFTER_DAYS:
        return "expired"
    return "active"


def _find_existing(db: Session, payload: dict) -> Job | None:
    provider = payload.get("provider")
    provider_job_id = payload.get("provider_job_id")
    if provider and provider_job_id:
        existing = db.scalar(
            select(Job).where(
                Job.provider == provider,
                Job.provider_job_id == provider_job_id,
            )
        )
        if existing is not None:
            return existing

    # The provider's id is its URL slug, but a re-slugged listing keeps the same
    # canonical page: deduplicate on the source URL as well.
    listing_url = payload.get("listing_url")
    if provider and listing_url:
        existing = db.scalar(
            select(Job).where(
                Job.provider == provider,
                Job.listing_url == listing_url,
            )
        )
        if existing is not None:
            return existing

    # Defensive fallback: providers that hand out no id are deduplicated on
    # provider + title + company, so manual jobs (provider IS NULL) never match.
    return db.scalar(
        select(Job).where(
            Job.provider == provider,
            Job.title == payload["title"],
            Job.company == payload["company"],
        )
    )


def refresh_listing_status(
    db: Session, provider: str | None = None, *, now: datetime | None = None
) -> None:
    """Recompute ``listing_status`` for imported rows (never for manual rows).

    - ``expired``: past the provider's closing date, or published longer ago
      than ``EXPIRE_AFTER_DAYS``.
    - ``stale``: not returned by a search for ``STALE_AFTER_DAYS``.
    - ``active``: seen recently and still open.
    """
    now = now or utcnow()
    stale_cutoff = now - timedelta(days=STALE_AFTER_DAYS)
    expire_cutoff = now - timedelta(days=EXPIRE_AFTER_DAYS)

    conditions = [Job.source == "provider"]
    if provider:
        conditions.append(Job.provider == provider)

    rows = db.scalars(select(Job).where(*conditions)).all()
    for row in rows:
        if row.expires_date is not None and row.expires_date < now.date():
            status = "expired"
        elif row.posted_date is not None and row.posted_date <= expire_cutoff:
            status = "expired"
        elif row.verified_at is None or row.verified_at <= stale_cutoff:
            status = "stale"
        else:
            status = "active"
        if row.listing_status != status:
            row.listing_status = status


def apply_result_to_job(job: Job, payload: dict, *, now: datetime) -> None:
    """Update a stored enquiry from a normalized payload without losing state."""
    for key, value in payload.items():
        setattr(job, key, value)
    job.fetched_at = job.fetched_at or now
    # application_status / created_at are deliberately untouched.


def ingest_provider_results(
    db: Session, provider: str, results: list[JobResult], *, now: datetime | None = None
) -> tuple[int, int]:
    """Upsert provider results into the jobs table.

    Returns ``(created, updated)``. The transaction is committed once at the
    end; on failure the session is rolled back and the exception re-raised.
    """
    if not results:
        refresh_listing_status(db, provider, now=now)
        db.commit()
        return 0, 0

    now = now or utcnow()
    created = 0
    updated = 0
    columns = set(Job.__table__.columns.keys())

    try:
        for result in results:
            payload = normalize_result(result, now=now)
            payload = {key: value for key, value in payload.items() if key in columns}
            existing = _find_existing(db, payload)
            if existing is None:
                job = Job(**payload)
                job.fetched_at = now  # first time we saw this listing
                db.add(job)
                created += 1
            else:
                apply_result_to_job(existing, payload, now=now)
                updated += 1

        refresh_listing_status(db, provider, now=now)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Could not ingest %d results from %s", len(results), provider)
        raise

    logger.info(
        "Ingested %d listing(s) from %s (%d new, %d updated)",
        len(results),
        provider,
        created,
        updated,
    )
    return created, updated


def enrich_jobs_with_details(
    db: Session,
    provider: JobProvider,
    provider_job_ids: list[str],
    *,
    limit: int = MAX_DETAIL_LOOKUPS,
    now: datetime | None = None,
) -> dict[str, int]:
    """Fetch full details for freshly ingested listings.

    Detail lookups are best-effort: a closed listing is marked ``expired``
    (never deleted), a failed lookup keeps the search-result row, and hitting
    the provider's rate limit stops the loop immediately instead of retrying.
    Returns counts for the API response.
    """
    report = {"enriched": 0, "closed": 0, "failed": 0, "requests": 0}
    if not provider_job_ids:
        return report

    now = now or utcnow()
    columns = set(Job.__table__.columns.keys())

    for provider_job_id in provider_job_ids[: max(0, limit)]:
        job = db.scalar(
            select(Job).where(
                Job.provider == provider.name,
                Job.provider_job_id == provider_job_id,
            )
        )
        if job is None:
            continue

        report["requests"] += 1
        try:
            detail = provider.get_job(provider_job_id)
        except ProviderJobClosedError:
            job.listing_status = "expired"
            report["closed"] += 1
            continue
        except ProviderRateLimitError:
            # Do not keep hammering a throttled API.
            report["failed"] += 1
            logger.warning("Stopped detail fetch for %s: rate limited", provider.name)
            break
        except ProviderError as exc:
            report["failed"] += 1
            logger.warning("Detail fetch failed for %s/%s: %s", provider.name, provider_job_id, exc)
            continue

        if detail is None:
            job.listing_status = "expired"
            report["closed"] += 1
            continue

        payload = normalize_result(detail, now=now)
        payload = {key: value for key, value in payload.items() if key in columns}
        apply_result_to_job(job, payload, now=now)
        report["enriched"] += 1

    try:
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Could not save detail enrichment for %s", provider.name)
        raise

    return report
