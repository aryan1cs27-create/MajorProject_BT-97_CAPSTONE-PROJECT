"""Provider abstraction for live job discovery.

MatchWise has exactly one active external provider, **JobsForce India**
(:mod:`app.services.providers.jobsforce`). The small interface here keeps the
API layer provider-agnostic and makes it obvious where a future provider would
plug in — it is not a plugin system for its own sake.

Each provider knows how to:
- search for jobs and return normalized results plus pagination metadata;
- fetch the full details of one listing;
- report whether it is usable (credentials present, or none required);
- report its source name for attribution in the UI.
"""

from __future__ import annotations

import abc
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

logger = logging.getLogger("matchwise.providers")


class ProviderError(RuntimeError):
    """Raised when a provider request fails (HTTP error, malformed body …)."""

    pass


class ProviderTimeoutError(ProviderError):
    """Raised when a provider request times out or the network is unreachable."""

    pass


class ProviderRateLimitError(ProviderError):
    """Raised when the provider throttles us (HTTP 429) or a quota is exhausted."""

    def __init__(self, message: str, *, retry_after: int | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class ProviderAuthError(ProviderError):
    """Raised when the provider rejects the configured credentials."""

    pass


class ProviderInvalidRequestError(ProviderError):
    """Raised when the provider rejects a query parameter (HTTP 400)."""

    pass


class ProviderJobClosedError(ProviderError):
    """Raised when a listing has closed (HTTP 410).

    ``alternatives`` holds the similar open jobs the provider returned with the
    closed listing, so callers can surface them instead of a dead end.
    """

    def __init__(self, message: str, *, alternatives: list["JobResult"] | None = None):
        super().__init__(message)
        self.alternatives = alternatives or []


class ProviderNotConfiguredError(ProviderError):
    """Raised when required credentials are absent from the environment."""

    def __init__(self, message: str):
        super().__init__(message)
        self.setup_message = message


@dataclass
class JobResult:
    """One normalized job listing from a provider.

    Optional fields stay ``None`` when the provider did not supply them —
    nothing here is ever invented to fill a gap.
    """

    provider: str
    provider_job_id: str
    title: str
    company: str
    location: str | None = None
    description: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    employment_type: str | None = None
    application_url: str | None = None
    posted_date: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    # --- Provenance and filters added for JobsForce India ---
    listing_url: str | None = None
    salary_currency: str | None = None
    salary_period: str | None = None
    work_mode: str | None = None
    category: str | None = None
    experience_min: float | None = None
    experience_max: float | None = None
    fresher_friendly: bool | None = None
    expires_on: date | None = None
    hiring_type: str | None = None
    hiring_name: str | None = None
    hiring_display: str | None = None
    #: Skill labels exactly as the provider lists them (preserved verbatim in
    #: ``source_metadata``; the matcher uses taxonomy labels derived from them).
    skills: list[str] = field(default_factory=list)
    skills_preferred: list[str] = field(default_factory=list)
    #: True when this result came from the provider's full-details endpoint.
    has_details: bool = False

    def __repr__(self) -> str:
        return f"<JobResult provider={self.provider} id={self.provider_job_id} title={self.title!r}>"


@dataclass
class JobSearchPage:
    """Normalized result page from a provider search."""

    results: list[JobResult]
    total: int | None = None
    page: int = 1
    page_size: int = 20
    has_more: bool = False
    next_page: int | None = None
    #: How the provider interpreted/relaxed the query — passed through verbatim.
    notes: list[str] = field(default_factory=list)


class JobProvider(abc.ABC):
    """Abstract base class for a job provider."""

    name: str = "unknown"

    @abc.abstractmethod
    def resolve_country(self, country: str | None, location: str | None) -> str:
        """Country/region the provider searches (used for reporting)."""
        ...

    @abc.abstractmethod
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
        """Search the provider and return one normalized page.

        Subclasses raise ProviderNotConfiguredError, ProviderAuthError,
        ProviderRateLimitError, ProviderTimeoutError, ProviderInvalidRequestError
        or ProviderError on failure.
        """
        ...

    @abc.abstractmethod
    def get_job(self, provider_job_id: str) -> JobResult | None:
        """Fetch full details for one listing.

        Returns ``None`` when the provider no longer knows the listing and
        raises :class:`ProviderJobClosedError` when it reports the listing as
        closed. Other failures raise :class:`ProviderError`.
        """
        ...

    @property
    @abc.abstractmethod
    def requires_credentials(self) -> bool:
        """Whether this provider is usable (credentials present, or none needed)."""

    @abc.abstractmethod
    def get_setup_message(self) -> str:
        """Human-readable message explaining what configuration is missing."""


class ProviderRegistry:
    """Discover and load registered job providers."""

    def __init__(self) -> None:
        self._providers: dict[str, type[JobProvider]] = {}

    def register(self, provider_class: type[JobProvider]) -> type[JobProvider]:
        """Register a provider class. Usable as a class decorator (returns the
        class so ``@registry.register`` does not rebind the name to ``None``)."""
        instance = provider_class()
        self._providers[instance.name] = provider_class
        logger.debug("Registered provider: %s", instance.name)
        return provider_class

    def get(self, name: str) -> type[JobProvider] | None:
        return self._providers.get(name)

    def all(self) -> list[type[JobProvider]]:
        return list(self._providers.values())

    def list_names(self) -> list[str]:
        return sorted(self._providers.keys())


#: Global registry instance — populated at module import time
registry = ProviderRegistry()


def register_provider(provider_class: type[JobProvider]) -> None:
    """Decorator/helper to register a provider at module level."""
    registry.register(provider_class)
