"""JobsForce India — the only external job-listing provider for MatchWise.

API: https://jobsforceindia.com/openapi.json (OpenAPI 3.1)
Base URL: https://jobsforceindia.com
Authentication: none. Rate limit: 120 requests/minute per client IP.

Endpoints used (all under ``/api/public/v1``)
---------------------------------------------
``GET /jobs``           search, page 1-10, per_page 1-20 (default 10)
``GET /jobs/{id}``      full details; 404 = unknown, 410 = closed
``GET /catalog``        valid cities, categories, job types, work modes

Normalization rules (deliberately conservative)
-----------------------------------------------
* **Nothing is invented.** A missing city, salary, skill list or closing date
  stays ``None``/empty; only the storage layer adds a neutral placeholder for
  NOT NULL columns.
* **Agency attribution is preserved.** When ``hiring.type == "agency"`` the
  client employer is confidential, so the company shown is the provider's
  ``hiring.display`` (e.g. "JobsForce India (recruiting for a confidential
  client)") — never the hidden client name.
* **Salary keeps its unit.** The provider returns amounts with a currency and a
  period (hourly … yearly); both are carried through so the UI can render
  "₹25,000–35,000 a month" without converting or guessing.
* **Skill lists are preserved verbatim** in ``JobResult.skills`` /
  ``skills_preferred`` and stored in ``source_metadata``; the matching engine
  receives taxonomy labels derived from that same text by the existing Phase 3
  extractor, so no second scoring system exists.
* ``q``, city, state, category, job type, work mode, fresher, experience, pay
  and posted-within filters are applied **server-side by the provider**, which
  is what makes the results trustworthy (no local post-filtering).
"""

from __future__ import annotations

import os
import time
from datetime import date, datetime
from typing import Any
from urllib.parse import quote

import httpx

from app.services.providers import (
    JobProvider,
    JobResult,
    JobSearchPage,
    ProviderError,
    ProviderInvalidRequestError,
    ProviderJobClosedError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    registry,
)

BASE_URL = (os.getenv("JOBSFORCE_BASE_URL") or "https://jobsforceindia.com").rstrip("/")
API_PREFIX = "/api/public/v1"

DEFAULT_COUNTRY = "in"          # India-only provider
MAX_PAGE = 10
MAX_PER_PAGE = 20
REQUEST_TIMEOUT = httpx.Timeout(15.0, connect=5.0)

#: The catalog changes slowly; cache it for this many seconds to stay well
#: inside the 120 requests/minute limit while the UI loads filter options.
CATALOG_TTL_SECONDS = int(os.getenv("JOBSFORCE_CATALOG_TTL", "600"))

_catalog_cache: dict[str, Any] | None = None
_catalog_cached_at: float = 0.0

VALID_JOB_TYPES = ("full-time", "part-time", "internship", "contract", "temporary")
VALID_WORK_MODES = ("remote", "hybrid", "onsite")
VALID_POSTED_WITHIN = (1, 3, 7, 14, 30)


# --------------------------------------------------------------------------- #
# Small parsing helpers — every one defaults to None instead of a guess
# --------------------------------------------------------------------------- #

def _text(value: Any) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _integer(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _date(value: Any) -> date | None:
    """Parse the provider's ``YYYY-MM-DD`` dates."""
    text = _text(value)
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for entry in value:
        text = _text(entry)
        if text and text not in items:
            items.append(text)
    return items


def _hiring(record: dict[str, Any]) -> tuple[str, str | None, str | None, str | None]:
    """Return ``(company, type, name, display)`` for a listing.

    Agency roles never expose the confidential client: the provider's
    ``display`` text is the attribution, and the recruiter ``name`` is used only
    as a labelled fallback.
    """
    hiring = record.get("hiring")
    hiring = hiring if isinstance(hiring, dict) else {}
    hiring_type = _text(hiring.get("type"))
    name = _text(hiring.get("name"))
    employer = _text(hiring.get("employer"))
    display = _text(hiring.get("display"))

    if hiring_type == "agency":
        if display:
            company = display
        elif name:
            company = f"{name} (recruiting for a confidential client)"
        else:
            company = "Confidential client (agency listing)"
    else:
        if employer and employer.lower() not in {"confidential", "private"}:
            company = display or employer or name or ""
        else:
            company = display or name or ""
    return company, hiring_type, name, display


def _location(record: dict[str, Any]) -> str | None:
    location = record.get("location")
    if not isinstance(location, dict):
        return None
    city = _text(location.get("city"))
    state = _text(location.get("state"))
    country = _text(location.get("country"))

    parts: list[str] = []
    if city:
        parts.append(city)
    if state and state.lower() != (city or "").lower():
        parts.append(state)
    if parts:
        return ", ".join(parts)
    return country


def _salary(record: dict[str, Any]) -> dict[str, Any]:
    salary = record.get("salary")
    return salary if isinstance(salary, dict) else {}


def _experience(record: dict[str, Any]) -> dict[str, Any]:
    experience = record.get("experience")
    return experience if isinstance(experience, dict) else {}


def _compose_description(record: dict[str, Any]) -> str | None:
    """Full plain-text description, keeping the provider's own sections.

    Sections are only concatenated when the provider sent them — headings are
    labels for existing content, not invented requirements.
    """
    description = _text(record.get("description")) or _text(record.get("summary"))

    sections: list[str] = []
    for label, key in (
        ("Responsibilities", "responsibilities"),
        ("Qualifications", "qualifications"),
        ("Benefits", "benefits"),
    ):
        items = _string_list(record.get(key))
        if items:
            sections.append(label + ":\n" + "\n".join(f"- {item}" for item in items))

    skills = _string_list(record.get("skills"))
    preferred = _string_list(record.get("skills_preferred"))
    if skills:
        sections.append("Skills listed:\n" + "\n".join(f"- {skill}" for skill in skills))
    if preferred:
        sections.append(
            "Preferred skills:\n" + "\n".join(f"- {skill}" for skill in preferred)
        )

    parts = [description, *sections]
    return "\n\n".join(part for part in parts if part) or None


def _to_result(record: Any, *, detail: bool) -> JobResult | None:
    """Normalize one JobSummary/JobDetail object, or None when unusable."""
    if not isinstance(record, dict):
        return None
    job_id = _text(str(record.get("id"))) if record.get("id") is not None else None
    title = _text(record.get("title"))
    if not job_id or not title:
        return None  # no id or no title — skip rather than persist junk

    company, hiring_type, hiring_name, hiring_display = _hiring(record)
    salary = _salary(record)
    experience = _experience(record)

    description = _compose_description(record) if detail else _text(record.get("summary"))

    return JobResult(
        provider="jobsforce",
        provider_job_id=job_id,
        title=title,
        company=company,
        location=_location(record),
        description=description,
        salary_min=_number(salary.get("min")),
        salary_max=_number(salary.get("max")),
        salary_currency=_text(salary.get("currency")),
        salary_period=_text(salary.get("period")),
        employment_type=_text(record.get("job_type")),
        application_url=_text(record.get("apply_url")),
        listing_url=_text(record.get("url")),
        posted_date=_text(record.get("posted_date")),
        work_mode=_text(record.get("work_mode")),
        category=_text(record.get("category")),
        experience_min=_number(experience.get("min_years")),
        experience_max=_number(experience.get("max_years")),
        fresher_friendly=(
            bool(experience["fresher_friendly"])
            if isinstance(experience.get("fresher_friendly"), bool)
            else None
        ),
        expires_on=_date(record.get("valid_through")),
        hiring_type=hiring_type,
        hiring_name=hiring_name,
        hiring_display=hiring_display,
        skills=_string_list(record.get("skills")),
        skills_preferred=_string_list(record.get("skills_preferred")),
        has_details=detail,
        raw=record,
    )


def _alternatives(payload: Any) -> list[JobResult]:
    if not isinstance(payload, dict):
        return []
    items = payload.get("alternatives")
    if not isinstance(items, list):
        return []
    parsed = [_to_result(item, detail=False) for item in items]
    return [item for item in parsed if item is not None]


@registry.register
class JobsForceIndiaProvider(JobProvider):
    """JobsForce India open jobs API (no credentials required)."""

    name = "jobsforce"

    # ------------------------------------------------------------- config
    @property
    def requires_credentials(self) -> bool:
        # The API is public; there is nothing to configure. The property is
        # named for the registry contract ("is this provider usable?").
        return True

    def get_setup_message(self) -> str:
        return "JobsForce India needs no credentials; it is always available."

    def resolve_country(self, country: str | None, location: str | None) -> str:
        # India-only provider: the country filter is not part of the API.
        return DEFAULT_COUNTRY

    # ------------------------------------------------------------- search
    def search(
        self,
        *,
        query: str = "",
        location: str | None = None,
        country: str | None = None,
        remote: bool = False,
        experience: float | None = None,
        employment_type: str | None = None,
        page: int = 1,
        page_size: int = 20,
        city: str | None = None,
        state: str | None = None,
        category: str | None = None,
        work_mode: str | None = None,
        fresher: bool | None = None,
        min_monthly_pay: int | None = None,
        posted_within_days: int | None = None,
    ) -> JobSearchPage:
        """Search JobsForce India. Every filter is sent upstream."""
        page = min(max(int(page or 1), 1), MAX_PAGE)
        page_size = min(max(int(page_size or 20), 1), MAX_PER_PAGE)

        params: dict[str, Any] = {"page": page, "per_page": page_size}

        text = (query or "").strip()
        if text:
            params["q"] = text[:200]

        # The API has no generic "location": it filters by city or state. A
        # caller that sends only `location` gets a city search (documented).
        resolved_city = (city or "").strip() or (location or "").strip() or None
        if resolved_city:
            params["city"] = resolved_city[:80]
        if state and state.strip():
            params["state"] = state.strip()[:80]
        if category and category.strip():
            params["category"] = category.strip()[:80]
        if employment_type:
            params["job_type"] = employment_type
        mode = (work_mode or ("remote" if remote else "")).strip()
        if mode:
            params["work_mode"] = mode
        if fresher is not None:
            params["fresher"] = "true" if fresher else "false"
        if experience is not None:
            params["experience_years"] = experience
        if min_monthly_pay is not None:
            params["min_monthly_pay"] = min_monthly_pay
        if posted_within_days is not None:
            params["posted_within_days"] = posted_within_days

        data = self._request("/jobs", params)

        records = data.get("results")
        if not isinstance(records, list):
            raise ProviderError(
                "JobsForce India returned a malformed response (missing 'results' list)."
            )

        results = [item for item in (_to_result(record, detail=False) for record in records) if item]

        notes = [note for note in data.get("notes") or [] if isinstance(note, str)]
        return JobSearchPage(
            results=results,
            total=_integer(data.get("total")),
            page=_integer(data.get("page")) or page,
            page_size=_integer(data.get("per_page")) or page_size,
            has_more=bool(data.get("has_more")),
            next_page=_integer(data.get("next_page")),
            notes=notes,
        )

    # ------------------------------------------------------------- details
    def get_job(self, provider_job_id: str) -> JobResult | None:
        """Fetch one full listing. Returns None for an unknown id (404)."""
        if not provider_job_id or not str(provider_job_id).strip():
            return None
        data = self._request(f"/jobs/{quote(str(provider_job_id).strip(), safe='')}", None)
        if data is None:  # 404 — the provider does not know this listing
            return None
        result = _to_result(data, detail=True)
        if result is None:
            raise ProviderError(
                "JobsForce India returned a malformed job detail (missing id or title)."
            )
        return result

    # ------------------------------------------------------------- catalog
    def catalog(self, *, force_refresh: bool = False) -> dict[str, Any]:
        """Cities / categories / job types / work modes that have open jobs.

        Cached for ``JOBSFORCE_CATALOG_TTL`` seconds so opening the search page
        does not spend the provider's rate limit.
        """
        global _catalog_cache, _catalog_cached_at
        fresh = (
            _catalog_cache is not None
            and not force_refresh
            and (time.monotonic() - _catalog_cached_at) < CATALOG_TTL_SECONDS
        )
        if fresh:
            return _catalog_cache  # type: ignore[return-value]

        data = self._request("/catalog", None)
        if not isinstance(data, dict):
            raise ProviderError("JobsForce India returned a malformed catalog.")
        _catalog_cache = data
        _catalog_cached_at = time.monotonic()
        return data

    # -------------------------------------------------------------- http
    def _request(self, path: str, params: dict[str, Any] | None) -> Any:
        url = f"{BASE_URL}{API_PREFIX}{path}"
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
                response = client.get(
                    url,
                    params=params,
                    headers={"Accept": "application/json", "User-Agent": "MatchWise/0.1"},
                )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("JobsForce India request timed out.") from exc
        except httpx.NetworkError as exc:
            raise ProviderTimeoutError(
                "Could not reach JobsForce India (network error)."
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError("JobsForce India request failed.") from exc

        return self._parse_response(response)

    @staticmethod
    def _parse_response(response: httpx.Response) -> Any:
        status = response.status_code

        if status == 429:
            retry_after = response.headers.get("Retry-After")
            try:
                seconds = int(retry_after) if retry_after else None
            except ValueError:
                seconds = None
            raise ProviderRateLimitError(
                "JobsForce India is throttling requests (HTTP 429). "
                + (f"Retry after {seconds}s." if seconds else "Try again shortly."),
                retry_after=seconds,
            )
        if status == 400:
            message = _error_message(response) or "the request was rejected"
            raise ProviderInvalidRequestError(
                f"JobsForce India rejected a search parameter: {message}"
            )
        if status in (401, 403):
            raise ProviderError(
                "JobsForce India refused the request (HTTP "
                f"{status}); the API is public, so this is unexpected."
            )
        if status == 404:
            return None
        if status == 410:
            payload = _json_or_none(response)
            message = _error_message(response) or "The listing has closed."
            raise ProviderJobClosedError(message, alternatives=_alternatives(payload))
        if status >= 500:
            raise ProviderError(
                f"JobsForce India is unavailable (HTTP {status}). Try again later."
            )
        if status >= 400:
            raise ProviderError(f"JobsForce India API error {status}.")

        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError(
                "JobsForce India returned a malformed response (body is not JSON)."
            ) from exc


def _json_or_none(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _error_message(response: httpx.Response) -> str | None:
    payload = _json_or_none(response)
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if isinstance(error, dict):
        return _text(error.get("message"))
    return None
