"""Job endpoints: CRUD, live provider search, catalog and manual import.

Authentication (Phase 5.1): the whole router is guarded by ``require_api_token``
(active when ``API_TOKEN`` is configured).

JobsForce India is the **only** external job source. It needs no credentials
(public API), so nothing secret is read or returned here.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.core.security import require_api_token
from app.db.session import get_db
from app.models.job import Job
from app.schemas.job import (
    CatalogEntry,
    CatalogValue,
    CsvImportRequest,
    JobCreate,
    JobListResponse,
    JobOut,
    JobUpdate,
    JsonImportRequest,
    ProviderCatalogResponse,
    ProviderJobOut,
    ProviderSearchRequest,
    ProviderSearchResponse,
    ProviderStatus,
)
from app.services import jobs as job_service
from app.services.jobs_import import ImportSummary, import_jobs_from_csv, import_jobs_from_json
from app.services.jobs_ingest import (
    enrich_jobs_with_details,
    ingest_provider_results,
    parse_posted_date,
)
from app.services.providers import (
    ProviderAuthError,
    ProviderError,
    ProviderInvalidRequestError,
    ProviderNotConfiguredError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    registry,
)
from app.services.providers import jobsforce as _jobsforce  # noqa: F401  (registers "jobsforce")

router = APIRouter(
    prefix="/api/jobs",
    tags=["jobs"],
    dependencies=[Depends(require_api_token)],
)

Db = Annotated[Session, Depends(get_db)]

#: The single active live provider. Changing it is a one-line change here.
DEFAULT_PROVIDER = "jobsforce"

PROVIDER_DISPLAY_NAMES = {"jobsforce": "JobsForce India"}


def _provider_or_501(name: str):
    provider_class = registry.get(name)
    if provider_class is None:
        raise APIError(501, "provider_not_supported", f"Provider {name!r} is not registered.")
    return provider_class()


def _search_or_http(provider, **kwargs):
    """Run ``provider.search`` and translate provider errors to API errors."""
    try:
        return provider.search(**kwargs)
    except ProviderNotConfiguredError as exc:
        raise APIError(400, "provider_credentials_missing", str(exc)) from exc
    except ProviderRateLimitError as exc:
        raise APIError(429, "provider_rate_limit", str(exc)) from exc
    except ProviderTimeoutError as exc:
        raise APIError(504, "provider_timeout", str(exc)) from exc
    except ProviderInvalidRequestError as exc:
        raise APIError(400, "provider_invalid_request", str(exc)) from exc
    except ProviderAuthError as exc:
        raise APIError(401, "provider_auth_failed", str(exc)) from exc
    except ProviderError as exc:
        raise APIError(502, "provider_error", str(exc)) from exc


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #

@router.post("", response_model=JobOut, status_code=201, summary="Create a job")
def create_job(payload: JobCreate, db: Db) -> JobOut:
    return JobOut.model_validate(job_service.create_job(db, payload))


@router.get("", response_model=JobListResponse, summary="List jobs (paginated, filterable)")
def list_jobs(
    db: Db,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 10,
    title: Annotated[str | None, Query(max_length=255)] = None,
    company: Annotated[str | None, Query(max_length=255)] = None,
    location: Annotated[str | None, Query(max_length=255)] = None,
    employment_type: Annotated[str | None, Query(max_length=50)] = None,
    source: Annotated[str | None, Query(max_length=20)] = None,
    category: Annotated[str | None, Query(max_length=120)] = None,
    work_mode: Annotated[str | None, Query(max_length=20)] = None,
    provider: Annotated[str | None, Query(max_length=100)] = None,
) -> JobListResponse:
    """List jobs; ``source`` accepts ``provider``/``live``, ``import``,
    ``manual`` or ``sample`` to narrow the list to one provenance."""
    rows, total = job_service.list_jobs(
        db,
        page=page,
        page_size=page_size,
        title=title,
        company=company,
        location=location,
        employment_type=employment_type,
        source=source,
        category=category,
        work_mode=work_mode,
        provider=provider,
    )
    return JobListResponse(
        items=[JobOut.model_validate(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


# --------------------------------------------------------------------------- #
# Phase 7 — JobsForce India discovery & manual import.
# Declared before "/{job_id}" so static paths are not swallowed by the
# integer path parameter.
# --------------------------------------------------------------------------- #

@router.get(
    "/providers",
    response_model=list[ProviderStatus],
    summary="Configuration state of the live job providers",
)
def list_providers() -> list[ProviderStatus]:
    """Reports whether each provider is usable — never any credentials."""
    statuses: list[ProviderStatus] = []
    for provider_class in registry.all():
        provider = provider_class()
        configured = provider.requires_credentials
        statuses.append(
            ProviderStatus(
                name=provider.name,
                configured=configured,
                default_country=provider.resolve_country(None, None),
                message=None if configured else provider.get_setup_message(),
            )
        )
    return statuses


@router.get(
    "/provider/catalog",
    response_model=ProviderCatalogResponse,
    summary="Cities, categories, job types and work modes that have open jobs",
)
def provider_catalog() -> ProviderCatalogResponse:
    """Filter values the provider will accept, so callers never guess."""
    provider = _provider_or_501(DEFAULT_PROVIDER)
    catalog_method = getattr(provider, "catalog", None)
    if catalog_method is None:  # pragma: no cover - the active provider has one
        raise APIError(501, "catalog_not_supported", "This provider has no catalog endpoint.")
    try:
        data = catalog_method()
    except ProviderRateLimitError as exc:
        raise APIError(429, "provider_rate_limit", str(exc)) from exc
    except ProviderTimeoutError as exc:
        raise APIError(504, "provider_timeout", str(exc)) from exc
    except ProviderError as exc:
        raise APIError(502, "provider_error", str(exc)) from exc

    def entries(key: str, label: str = "name") -> list[CatalogEntry]:
        raw = data.get(key)
        if not isinstance(raw, list):
            return []
        items: list[CatalogEntry] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = item.get(label)
            if not isinstance(name, str) or not name.strip():
                continue
            jobs = item.get("jobs")
            items.append(
                CatalogEntry(
                    name=name.strip(),
                    jobs=jobs if isinstance(jobs, int) else None,
                    url=item.get("url") if isinstance(item.get("url"), str) else None,
                )
            )
        return items

    def values(key: str) -> list[CatalogValue]:
        raw = data.get(key)
        if not isinstance(raw, list):
            return []
        items: list[CatalogValue] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            value = item.get("value")
            if not isinstance(value, str) or not value.strip():
                continue
            jobs = item.get("jobs")
            items.append(
                CatalogValue(value=value.strip(), jobs=jobs if isinstance(jobs, int) else None)
            )
        return items

    newest = data.get("newest_job")
    note = data.get("note")
    total = data.get("total_jobs")
    return ProviderCatalogResponse(
        provider=provider.name,
        total_jobs=total if isinstance(total, int) else None,
        newest_job=newest if isinstance(newest, str) else None,
        cities=entries("cities"),
        categories=entries("categories"),
        job_types=values("job_types"),
        work_modes=values("work_modes"),
        notes=[note] if isinstance(note, str) and note.strip() else [],
    )


@router.post(
    "/provider/search",
    response_model=ProviderSearchResponse,
    summary="Search live jobs from JobsForce India",
)
def provider_search(payload: ProviderSearchRequest, db: Db) -> ProviderSearchResponse:
    """Search JobsForce India and, by default, persist the results into
    ``jobs`` — deduplicated by provider job id/source URL, so repeated searches
    update rows instead of creating duplicates.

    Newly created listings are enriched with the provider's full details
    (description, responsibilities, qualifications, skills) before they are
    matched; a failed detail call keeps the search-result row.
    """
    provider = _provider_or_501(DEFAULT_PROVIDER)
    country = provider.resolve_country(payload.country, payload.city or payload.location)

    page = _search_or_http(
        provider,
        query=payload.query or "",
        location=payload.location,
        country=payload.country,
        remote=payload.remote,
        experience=payload.experience,
        employment_type=payload.employment_type,
        page=payload.page,
        page_size=payload.page_size,
        city=payload.city,
        state=payload.state,
        category=payload.category,
        work_mode=payload.work_mode,
        fresher=payload.fresher,
        min_monthly_pay=payload.min_monthly_pay,
        posted_within_days=payload.posted_within_days,
    )
    results = page.results

    created = updated = 0
    details = {"enriched": 0, "closed": 0, "failed": 0}
    local: dict[str, tuple[int, str | None]] = {}

    if payload.ingest and results:
        ids = [result.provider_job_id for result in results]
        already_stored = set(
            db.scalars(
                select(Job.provider_job_id).where(
                    Job.provider == provider.name,
                    Job.provider_job_id.in_(ids),
                )
            ).all()
        )
        created, updated = ingest_provider_results(db, provider.name, results)

        if payload.fetch_details:
            new_ids = [pid for pid in ids if pid and pid not in already_stored]
            details = enrich_jobs_with_details(db, provider, new_ids)

        rows = db.execute(
            select(Job.id, Job.provider_job_id, Job.listing_status).where(
                Job.provider == provider.name,
                Job.provider_job_id.in_(ids),
            )
        ).all()
        local = {
            row.provider_job_id: (row.id, row.listing_status)
            for row in rows
            if row.provider_job_id is not None
        }

    display_name = PROVIDER_DISPLAY_NAMES.get(provider.name, provider.name)
    notes: list[str] = []
    if payload.location and not payload.city:
        notes.append("Location was searched as a city (JobsForce India filters by city/state).")
    if payload.remote and not payload.work_mode:
        notes.append("Remote preference was sent as work_mode=remote.")
    if payload.experience is not None:
        notes.append(
            "Experience was applied by the provider: it returns jobs whose "
            "experience range includes the requested years."
        )
    if payload.fetch_details and details.get("requests"):
        notes.append(
            f"Fetched full details for {details['enriched']} new listing(s); "
            f"{details['closed']} closed, {details['failed']} failed."
        )
    notes.append(
        f"Source: {display_name} — listing data is provided by {display_name}; "
        "links go to the original job page and application form."
    )

    return ProviderSearchResponse(
        provider=provider.name,
        query=payload.query or "",
        location=payload.location,
        country=country,
        city=payload.city,
        state=payload.state,
        category=payload.category,
        work_mode=payload.work_mode or ("remote" if payload.remote else None),
        remote=payload.remote,
        fresher=payload.fresher,
        experience=payload.experience,
        min_monthly_pay=payload.min_monthly_pay,
        posted_within_days=payload.posted_within_days,
        employment_type=payload.employment_type,
        page=page.page,
        page_size=page.page_size,
        total_available=page.total,
        has_more=page.has_more,
        next_page=page.next_page,
        created=created,
        updated=updated,
        details_fetched=details["enriched"],
        details_failed=details["failed"],
        notes=notes,
        provider_notes=page.notes,
        results=[
            ProviderJobOut(
                job_id=local.get(result.provider_job_id, (None, None))[0],
                provider=result.provider,
                provider_job_id=result.provider_job_id,
                title=result.title,
                company=result.company,
                location=result.location,
                description=result.description,
                salary_min=result.salary_min,
                salary_max=result.salary_max,
                salary_currency=result.salary_currency,
                salary_period=result.salary_period,
                employment_type=result.employment_type,
                work_mode=result.work_mode,
                category=result.category,
                listing_url=result.listing_url,
                application_url=result.application_url,
                posted_date=parse_posted_date(result.posted_date),
                expires_date=result.expires_on,
                hiring_type=result.hiring_type,
                listing_status=local.get(result.provider_job_id, (None, None))[1],
                has_details=result.has_details,
                source="provider",
            )
            for result in results
        ],
    )


@router.post(
    "/import/csv",
    response_model=ImportSummary,
    summary="Import jobs from CSV (validated, duplicate-safe)",
)
def import_jobs_csv(payload: CsvImportRequest, db: Db) -> ImportSummary:
    """Import jobs from CSV text with a header row.

    Columns: ``title``, ``company``, ``description`` (required) plus
    ``location``, ``required_skills``, ``preferred_skills``,
    ``minimum_experience``, ``employment_type`` (optional). Invalid rows are
    reported one by one; rows matching a manual/sample/provider job are
    skipped instead of overwriting them.
    """
    return import_jobs_from_csv(db, payload.content)


@router.post(
    "/import/json",
    response_model=ImportSummary,
    summary="Import jobs from JSON (validated, duplicate-safe)",
)
def import_jobs_json(payload: JsonImportRequest, db: Db) -> ImportSummary:
    """Import jobs from a JSON array of objects with the same fields and the
    same validation/duplicate rules as the CSV importer."""
    return import_jobs_from_json(db, payload.jobs)


# --------------------------------------------------------------------------- #
# Single-job CRUD (declared last: the static paths above must win)
# --------------------------------------------------------------------------- #

@router.get("/{job_id}", response_model=JobOut, summary="Get one job")
def get_job(job_id: int, db: Db) -> JobOut:
    return JobOut.model_validate(job_service.get_job(db, job_id))


@router.put("/{job_id}", response_model=JobOut, summary="Update a job")
def update_job(job_id: int, payload: JobUpdate, db: Db) -> JobOut:
    return JobOut.model_validate(job_service.update_job(db, job_id, payload))


@router.delete("/{job_id}", status_code=204, summary="Delete a job")
def delete_job(job_id: int, db: Db) -> Response:
    job_service.delete_job(db, job_id)
    return Response(status_code=204)
