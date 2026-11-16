"""Pydantic schemas for job endpoints (CRUD + Phase 5 live/import)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EMPLOYMENT_TYPES = {"full-time", "part-time", "contract", "internship", "temporary"}

SKILL_MAX_LENGTH = 100


def _clean_skills(values: list[str]) -> list[str]:
    seen: list[str] = []
    for raw in values:
        skill = " ".join(str(raw).split())
        if not skill:
            continue
        if len(skill) > SKILL_MAX_LENGTH:
            raise ValueError(f"Skill entries must be at most {SKILL_MAX_LENGTH} characters.")
        if skill.lower() not in {s.lower() for s in seen}:
            seen.append(skill)
    return seen


class JobBase(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    company: str = Field(min_length=1, max_length=255)
    location: str | None = Field(default=None, max_length=255)
    description: str = Field(min_length=1)
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    minimum_experience: float | None = Field(default=None, ge=0, le=50)
    employment_type: str | None = None
    application_status: str | None = Field(default=None, max_length=20)
      # Application tracking status: saved | applied | interview | rejected | offer

    @field_validator("title", "company")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("location", "employment_type")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("description")
    @classmethod
    def _description_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()

    @field_validator("application_status")
    @classmethod
    def _application_status(cls, value: str | None) -> str | None:
        """saved | applied | interview | rejected | offer (or null/none)."""
        if value is None or not str(value).strip():
            return None
        normalised = str(value).strip().lower()
        if normalised == "none":
            return None
        if normalised not in APPLICATION_STATUSES:
            allowed = ", ".join(sorted(APPLICATION_STATUSES))
            raise ValueError(f"must be one of: {allowed}")
        return normalised

    @field_validator("required_skills", "preferred_skills")
    @classmethod
    def _skills(cls, value: list[str]) -> list[str]:
        return _clean_skills(value)

    @field_validator("employment_type")
    @classmethod
    def _employment_type(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalised = value.lower()
        if normalised not in EMPLOYMENT_TYPES:
            allowed = ", ".join(sorted(EMPLOYMENT_TYPES))
            raise ValueError(f"must be one of: {allowed}")
        return normalised


class JobCreate(JobBase):
    pass


APPLICATION_STATUSES = {"saved", "applied", "interview", "rejected", "offer"}


class JobUpdate(BaseModel):
    """All fields optional — only the provided ones are applied."""

    title: str | None = Field(default=None, min_length=1, max_length=255)
    company: str | None = Field(default=None, min_length=1, max_length=255)
    location: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, min_length=1)
    required_skills: list[str] | None = None
    preferred_skills: list[str] | None = None
    minimum_experience: float | None = Field(default=None, ge=0, le=50)
    employment_type: str | None = None
    application_status: str | None = Field(default=None, max_length=20)

    @field_validator("title", "company")
    @classmethod
    def _required_blank_to_none(cls, value: str | None) -> str | None:
        """Title/company may be omitted (unchanged) but never blank."""
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("location", "employment_type")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("description")
    @classmethod
    def _description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("required_skills", "preferred_skills")
    @classmethod
    def _skills(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _clean_skills(value)

    @field_validator("employment_type")
    @classmethod
    def _employment(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalised = value.lower()
        if normalised not in EMPLOYMENT_TYPES:
            allowed = ", ".join(sorted(EMPLOYMENT_TYPES))
            raise ValueError(f"must be one of: {allowed}")
        return normalised

    @field_validator("application_status")
    @classmethod
    def _application_status_update(cls, value: str | None) -> str | None:
        if value is None or not str(value).strip():
            return None
        normalised = str(value).strip().lower()
        if normalised == "none":
            return None
        if normalised not in APPLICATION_STATUSES:
            allowed = ", ".join(sorted(APPLICATION_STATUSES))
            raise ValueError(f"must be one of: {allowed}")
        return normalised


class JobOut(JobBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_sample: bool
    created_at: datetime
    updated_at: datetime
    application_status: str | None = None

    # --- Phase 5: provenance of the row ---
    source: str = "manual"            # provider | import | manual | sample
    provider: str | None = None
    provider_job_id: str | None = None
    application_url: str | None = None
    posted_date: datetime | None = None
    fetched_at: datetime | None = None
    verified_at: datetime | None = None
    listing_status: str | None = None  # active | stale | expired (imported rows only)
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    salary_period: str | None = None  # hourly | daily | weekly | monthly | yearly
    listing_url: str | None = None     # canonical job page on the provider's site
    work_mode: str | None = None       # remote | hybrid | onsite
    category: str | None = None
    expires_date: date | None = None   # date the listing closes
    hiring_type: str | None = None     # employer | portal | agency

    @field_validator("source", mode="before")
    @classmethod
    def _source_default(cls, value: Any) -> Any:
        return value or "manual"


# ---------------------------------------------------------------------------
# Phase 5: live provider search & manual import
# ---------------------------------------------------------------------------

class ProviderSearchRequest(BaseModel):
    """Body for POST /api/jobs/provider/search (JobsForce India).

    Every filter is applied by the provider itself — nothing is post-filtered
    locally. ``query`` is optional: a search may consist only of filters
    (e.g. city + category + work mode). ``location`` is shorthand for ``city``
    because the provider filters by city/state, and ``remote`` is shorthand for
    ``work_mode: "remote"``.

    Limits follow the API: ``page`` 1-10, ``page_size`` 1-20.
    """

    query: str = Field(default="", max_length=200)
    location: str | None = Field(default=None, max_length=80)
    country: str | None = Field(default=None, max_length=64)  # accepted, ignored (India-only)
    city: str | None = Field(default=None, max_length=80)
    state: str | None = Field(default=None, max_length=80)
    category: str | None = Field(default=None, max_length=80)
    employment_type: str | None = None
    work_mode: str | None = None
    remote: bool = False
    fresher: bool | None = None
    experience: float | None = Field(default=None, ge=0, le=50)
    min_monthly_pay: int | None = Field(default=None, ge=0, le=10_000_000)
    posted_within_days: int | None = None
    page: int = Field(default=1, ge=1, le=10)
    page_size: int = Field(default=20, ge=1, le=20)
    ingest: bool = True          # persist results into the jobs table (deduplicated)
    fetch_details: bool = True   # fetch full details for newly created rows

    @field_validator("employment_type")
    @classmethod
    def _employment(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        normalised = value.strip().lower()
        if normalised not in EMPLOYMENT_TYPES:
            allowed = ", ".join(sorted(EMPLOYMENT_TYPES))
            raise ValueError(f"must be one of: {allowed}")
        return normalised

    @field_validator("work_mode")
    @classmethod
    def _work_mode(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        normalised = value.strip().lower()
        if normalised not in {"remote", "hybrid", "onsite"}:
            raise ValueError("must be one of: hybrid, onsite, remote")
        return normalised

    @field_validator("posted_within_days")
    @classmethod
    def _posted(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if value not in {1, 3, 7, 14, 30}:
            raise ValueError("must be one of: 1, 3, 7, 14, 30")
        return value

    @field_validator("location", "city", "state", "category")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("query")
    @classmethod
    def _strip_query(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def _needs_a_filter(self) -> "ProviderSearchRequest":
        if any(
            (
                self.query,
                self.location,
                self.city,
                self.state,
                self.category,
                self.employment_type,
                self.work_mode,
                self.remote,
                self.fresher is not None,
                self.experience is not None,
                self.min_monthly_pay is not None,
                self.posted_within_days is not None,
            )
        ):
            return self
        raise ValueError("Provide keywords or at least one search filter.")


class ProviderJobOut(BaseModel):
    """One live result as returned to the client (after optional ingestion)."""

    job_id: int | None = None  # local id once the row has been persisted
    provider: str
    provider_job_id: str
    title: str
    company: str
    location: str | None = None
    description: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    salary_period: str | None = None
    employment_type: str | None = None
    work_mode: str | None = None
    category: str | None = None
    listing_url: str | None = None
    application_url: str | None = None
    posted_date: datetime | None = None
    expires_date: date | None = None
    hiring_type: str | None = None
    listing_status: str | None = None
    has_details: bool = False
    source: str = "provider"


class ProviderSearchResponse(BaseModel):
    provider: str
    query: str
    location: str | None = None
    country: str
    city: str | None = None
    state: str | None = None
    category: str | None = None
    work_mode: str | None = None
    remote: bool = False
    fresher: bool | None = None
    experience: float | None = None
    min_monthly_pay: int | None = None
    posted_within_days: int | None = None
    employment_type: str | None = None
    page: int
    page_size: int
    results: list[ProviderJobOut]
    total_available: int | None = None
    has_more: bool = False
    next_page: int | None = None
    created: int = 0
    updated: int = 0
    details_fetched: int = 0
    details_failed: int = 0
    notes: list[str] = Field(default_factory=list)
    provider_notes: list[str] = Field(default_factory=list)


class CatalogEntry(BaseModel):
    name: str
    jobs: int | None = None
    url: str | None = None


class CatalogValue(BaseModel):
    value: str
    jobs: int | None = None


class ProviderCatalogResponse(BaseModel):
    """Valid filter values for the active provider (cities, categories, …)."""

    provider: str
    total_jobs: int | None = None
    newest_job: str | None = None
    cities: list[CatalogEntry] = Field(default_factory=list)
    categories: list[CatalogEntry] = Field(default_factory=list)
    job_types: list[CatalogValue] = Field(default_factory=list)
    work_modes: list[CatalogValue] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ProviderStatus(BaseModel):
    """Configuration state of a provider — never exposes credentials."""

    name: str
    configured: bool
    default_country: str
    message: str | None = None


class CsvImportRequest(BaseModel):
    content: str = Field(min_length=1, description="Raw CSV text with a header row")


class JsonImportRequest(BaseModel):
    jobs: list[Any] = Field(min_length=1, description="Array of job objects")


class JobListResponse(BaseModel):
    items: list[JobOut]
    total: int
    page: int
    page_size: int
