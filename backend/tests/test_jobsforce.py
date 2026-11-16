"""Phase 7 — JobsForce India provider integration.

All HTTP is intercepted: every test runs without the network and without
credentials (the API is public). The one *live* request lives in
``scripts/verify_jobsforce.py`` and is run manually.

What is covered here
--------------------
* provider registration/status and the catalog endpoint (cached);
* full-detail retrieval and merging into the stored row;
* provider-side search filters, pagination metadata and response notes;
* empty results, missing fields (nothing invented) and the agency rule;
* duplicates by provider id **and** source URL;
* HTTP 400/404/410/429/5xx, malformed bodies and timeouts;
* expired/closed listings are kept and marked, never deleted;
* imported jobs feed the existing matching + explanation engine.
"""

from __future__ import annotations

from datetime import date, timedelta

import httpx
import pytest

from sqlalchemy import func, select

from app.models.job import Job
from app.services import semantic
from app.services.providers import jobsforce
from conftest import make_resume

BASE = "https://jobsforceindia.com/api/public/v1"
SEARCH_URL = f"{BASE}/jobs"


def iso(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


# --------------------------------------------------------------------------- #
# Fake JobsForce India HTTP layer
# --------------------------------------------------------------------------- #

class FakeJobsForce:
    """Stands in for ``httpx.Client.get`` and records the outgoing requests."""

    def __init__(self) -> None:
        self.search_body: dict = search_body([])
        self.details: dict[str, dict] = {}
        self.catalog_body: dict = catalog_body()
        self.search_status: int = 200
        self.search_headers: dict[str, str] = {}
        self.search_text: str | None = None
        self.search_exception: Exception | None = None
        self.catalog_status: int = 200
        self.detail_status: dict[str, int] = {}
        self.detail_exception: Exception | None = None
        self.detail_bodies: dict[str, dict] = {}
        self.calls: list[tuple[str, dict]] = []

    # -- helpers used by tests ------------------------------------------------
    def add(self, record: dict, detail: dict | None = None) -> dict:
        self.search_body = search_body(
            [record],
            total=self.search_body.get("total") or 1,
            page=self.search_body.get("page", 1),
            per_page=self.search_body.get("per_page", 10),
            has_more=self.search_body.get("has_more", False),
        )
        if detail is not None:
            self.details[record["id"]] = detail
        return record

    def search_calls(self) -> list[dict]:
        return [params for path, params in self.calls if path == "/jobs"]

    def detail_calls(self) -> list[str]:
        return [path.split("/jobs/", 1)[1] for path, _ in self.calls if "/jobs/" in path]

    def catalog_calls(self) -> int:
        return sum(1 for path, _ in self.calls if path == "/catalog")

    # -- the fake transport ---------------------------------------------------
    def handle(self, url: str, params: dict) -> httpx.Response:
        path = url.split("/api/public/v1", 1)[1]
        params = {key: value for key, value in params.items() if value is not None}
        self.calls.append((path, params))
        request = httpx.Request("GET", url)

        if path == "/catalog":
            return httpx.Response(self.catalog_status, json=self.catalog_body, request=request)

        if path == "/jobs":
            if self.search_exception is not None:
                raise self.search_exception
            if self.search_text is not None:
                return httpx.Response(
                    self.search_status,
                    text=self.search_text,
                    headers=self.search_headers,
                    request=request,
                )
            return httpx.Response(
                self.search_status, json=self.search_body, headers=self.search_headers, request=request
            )

        job_id = path.split("/jobs/", 1)[1]
        if self.detail_exception is not None:
            raise self.detail_exception
        status = self.detail_status.get(job_id)
        body = self.detail_bodies.get(job_id) or self.details.get(job_id)
        if status is not None:
            return httpx.Response(status, json=body or {}, request=request)
        if body is None:
            return httpx.Response(404, json={"error": {"code": "not_found"}}, request=request)
        return httpx.Response(200, json=body, request=request)


@pytest.fixture()
def fake(monkeypatch):
    fake = FakeJobsForce()
    jobsforce._catalog_cache = None
    jobsforce._catalog_cached_at = 0.0

    def _get(self, url, *args, **kwargs):
        return fake.handle(url, dict(kwargs.get("params") or {}))

    monkeypatch.setattr(httpx.Client, "get", _get)
    return fake


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #

def search_body(records, *, total=None, page=1, per_page=10, has_more=False, next_page=None):
    return {
        "results": records,
        "total": len(records) if total is None else total,
        "page": page,
        "per_page": per_page,
        "has_more": has_more,
        "next_page": next_page,
        "notes": [],
    }


def employer_hiring(name: str = "Acme Technologies") -> dict:
    return {"type": "employer", "name": name, "employer": name, "display": f"{name}"}


def agency_hiring() -> dict:
    return {
        "type": "agency",
        "name": "JobsForce India",
        "employer": "confidential",
        "display": "JobsForce India (recruiting for a confidential client)",
    }


def record(**overrides) -> dict:
    data = {
        "id": "python-developer-bengaluru",
        "title": "Python Developer",
        "hiring": employer_hiring(),
        "location": {"city": "Bengaluru", "state": "Karnataka", "country": "India", "remote": False},
        "work_mode": "hybrid",
        "job_type": "full-time",
        "category": "IT",
        "salary": {
            "min": 30000,
            "max": 50000,
            "currency": "INR",
            "period": "monthly",
            "label": "30,000-50,000 a month",
        },
        "experience": {
            "min_years": 2,
            "max_years": 5,
            "label": "2-5 yrs",
            "fresher_friendly": False,
        },
        "summary": "Build Python services for a fintech platform.",
        "posted_date": iso(-2),
        "valid_through": iso(20),
        "url": "https://jobsforceindia.com/jobs/python-developer-bengaluru",
        "apply_url": "https://jobsforceindia.com/apply/python-developer-bengaluru",
    }
    data.update(overrides)
    return data


def detail(**overrides) -> dict:
    data = record(
        description=(
            "We are looking for a Python Developer to build reliable APIs.\n"
            "Python, FastAPI, Docker and PostgreSQL are used daily."
        ),
        responsibilities=["Design REST APIs", "Write tests"],
        qualifications=["2+ years of Python experience", "BSc in Computer Science"],
        benefits=["Health insurance", "Remote-friendly"],
        skills=["Python", "FastAPI", "Docker", "PostgreSQL"],
        skills_preferred=["Kubernetes", "Git"],
    )
    data.update(overrides)
    return data


def catalog_body() -> dict:
    return {
        "total_jobs": 1234,
        "newest_job": iso(-1),
        "cities": [
            {"name": "Bengaluru", "jobs": 400, "url": "https://jobsforceindia.com/jobs?city=Bengaluru"},
            {"name": "Remote", "jobs": 200, "url": "https://jobsforceindia.com/jobs?city=Remote"},
        ],
        "categories": [{"name": "IT", "jobs": 900, "url": "https://jobsforceindia.com/jobs?category=IT"}],
        "job_types": [{"value": "full-time", "jobs": 1000}],
        "work_modes": [{"value": "remote", "jobs": 300}, {"value": "hybrid", "jobs": 150}],
        "note": "Listing counts are refreshed daily.",
    }


# --------------------------------------------------------------------------- #
# Status & catalog
# --------------------------------------------------------------------------- #

def test_provider_is_registered_and_needs_no_credentials(client):
    response = client.get("/api/jobs/providers")
    assert response.status_code == 200
    body = response.json()
    assert [provider["name"] for provider in body] == ["jobsforce"]
    assert body[0]["configured"] is True
    assert body[0]["message"] is None
    assert body[0]["default_country"] == "in"


def test_catalog_endpoint_maps_filters_and_is_cached(client, fake):
    first = client.get("/api/jobs/provider/catalog")
    assert first.status_code == 200
    body = first.json()

    assert body["provider"] == "jobsforce"
    assert body["total_jobs"] == 1234
    assert body["cities"][0] == {
        "name": "Bengaluru",
        "jobs": 400,
        "url": "https://jobsforceindia.com/jobs?city=Bengaluru",
    }
    assert [value["value"] for value in body["job_types"]] == ["full-time"]
    assert body["work_modes"][1] == {"value": "hybrid", "jobs": 150}
    assert body["notes"] == ["Listing counts are refreshed daily."]

    second = client.get("/api/jobs/provider/catalog")
    assert second.status_code == 200
    # 600s TTL: the second page load must not spend a provider request
    assert fake.catalog_calls() == 1


def test_catalog_rate_limit_is_mapped(client, fake):
    fake.catalog_status = 429
    response = client.get("/api/jobs/provider/catalog")
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "provider_rate_limit"


# --------------------------------------------------------------------------- #
# Search + detail ingestion
# --------------------------------------------------------------------------- #

def test_search_ingests_new_listing_with_full_details(client, db, fake):
    fake.add(record(), detail=detail())

    response = client.post("/api/jobs/provider/search", json={"query": "python developer"})
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["provider"] == "jobsforce"
    assert body["created"] == 1 and body["updated"] == 0
    assert body["details_fetched"] == 1 and body["details_failed"] == 0
    assert body["total_available"] == 1

    item = body["results"][0]
    assert item["job_id"] is not None
    assert item["provider_job_id"] == "python-developer-bengaluru"
    assert item["title"] == "Python Developer"
    assert item["employment_type"] == "full-time"
    assert item["work_mode"] == "hybrid"
    assert item["category"] == "IT"
    assert item["salary_currency"] == "INR" and item["salary_period"] == "monthly"
    assert item["listing_url"] == record()["url"]
    assert item["application_url"] == record()["apply_url"]
    assert item["expires_date"] == iso(20)
    assert item["posted_date"].startswith(iso(-2))

    job = db.scalar(select(Job).where(Job.provider == "jobsforce"))
    assert job is not None
    assert job.source == "provider"
    assert job.company == "Acme Technologies"
    assert job.salary_min == 30000 and job.salary_max == 50000
    assert job.salary_currency == "INR" and job.salary_period == "monthly"
    assert job.work_mode == "hybrid" and job.category == "IT"
    assert job.hiring_type == "employer"
    assert job.listing_url == record()["url"]
    assert job.application_url == record()["apply_url"]
    assert job.expires_date == date.fromisoformat(iso(20))
    assert job.posted_date is not None
    assert job.listing_status == "active"
    # description + skills came from the detail endpoint, using the taxonomy
    assert "Responsibilities" in job.description
    assert "Python" in job.required_skills and "Docker" in job.required_skills
    assert "Kubernetes" in job.preferred_skills
    metadata = job.source_metadata
    assert metadata["skills_raw"] == ["Python", "FastAPI", "Docker", "PostgreSQL"]
    assert metadata["skills_preferred_raw"] == ["Kubernetes", "Git"]
    assert metadata["detail_loaded"] is True


def test_agency_listing_never_exposes_the_confidential_client(client, db, fake):
    fake.add(record(hiring=agency_hiring()))

    response = client.post("/api/jobs/provider/search", json={"query": "accountant"})
    assert response.status_code == 200

    job = db.scalar(select(Job).where(Job.provider == "jobsforce"))
    assert job.company == "JobsForce India (recruiting for a confidential client)"
    assert job.hiring_type == "agency"
    assert "confidential client" in job.company
    # the hidden employer value must not leak into the stored company
    assert job.company.lower() != "confidential"


def test_missing_fields_are_left_empty_not_invented(client, db, fake):
    minimal = {
        "id": "minimal-listing",
        "title": "Field Technician",
        "url": "https://jobsforceindia.com/jobs/minimal-listing",
    }
    fake.add(minimal)

    response = client.post("/api/jobs/provider/search", json={"query": "technician"})
    assert response.status_code == 200
    body = response.json()
    item = body["results"][0]
    assert item["salary_min"] is None and item["salary_currency"] is None
    assert item["work_mode"] is None and item["category"] is None
    assert item["location"] is None
    assert body["created"] == 1

    job = db.scalar(select(Job).where(Job.provider_job_id == "minimal-listing"))
    assert job.company == "Not specified"
    assert job.salary_min is None and job.salary_max is None
    assert job.expires_date is None
    assert job.required_skills == []


def test_empty_search_results_create_nothing(client, db, fake):
    fake.search_body = search_body([])
    response = client.post("/api/jobs/provider/search", json={"query": "astronaut"})
    assert response.status_code == 200
    body = response.json()
    assert body["results"] == []
    assert body["created"] == 0 and body["updated"] == 0
    assert body["details_fetched"] == 0
    assert fake.detail_calls() == []
    assert db.scalar(select(func.count(Job.id))) == 0


def test_search_without_ingest_skips_persistence_and_details(client, db, fake):
    fake.add(record(), detail=detail())
    response = client.post(
        "/api/jobs/provider/search",
        json={"query": "python", "ingest": False},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 0
    assert body["results"][0]["job_id"] is None
    assert fake.detail_calls() == []
    assert db.scalar(select(func.count(Job.id))) == 0


# --------------------------------------------------------------------------- #
# Filters & pagination are applied by the provider
# --------------------------------------------------------------------------- #

def test_every_filter_is_forwarded_to_the_provider(client, fake):
    fake.search_body = search_body([], total=0)
    payload = {
        "query": "  backend engineer  ",
        "city": "Bengaluru",
        "state": "Karnataka",
        "category": "IT",
        "employment_type": "full-time",
        "work_mode": "remote",
        "fresher": True,
        "experience": 3,
        "min_monthly_pay": 25000,
        "posted_within_days": 7,
        "page": 2,
        "page_size": 5,
    }
    response = client.post("/api/jobs/provider/search", json=payload)
    assert response.status_code == 200, response.text

    params = fake.search_calls()[0]
    assert params == {
        "page": 2,
        "per_page": 5,
        "q": "backend engineer",
        "city": "Bengaluru",
        "state": "Karnataka",
        "category": "IT",
        "job_type": "full-time",
        "work_mode": "remote",
        "fresher": "true",
        "experience_years": 3,
        "min_monthly_pay": 25000,
        "posted_within_days": 7,
    }


def test_location_is_shorthand_for_city_and_remote_for_work_mode(client, fake):
    fake.search_body = search_body([])
    response = client.post(
        "/api/jobs/provider/search",
        json={"query": "designer", "location": "Pune", "remote": True},
    )
    assert response.status_code == 200
    params = fake.search_calls()[0]
    assert params["city"] == "Pune"
    assert params["work_mode"] == "remote"
    assert "state" not in params
    assert response.json()["work_mode"] == "remote"


def test_pagination_metadata_is_surfaced(client, fake):
    fake.search_body = search_body(
        [record()], total=45, page=2, per_page=5, has_more=True, next_page=3
    )
    response = client.post(
        "/api/jobs/provider/search", json={"query": "python", "page": 2, "page_size": 5}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["page"] == 2 and body["page_size"] == 5
    assert body["total_available"] == 45
    assert body["has_more"] is True and body["next_page"] == 3


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"page": 11},
        {"page": 0, "query": "x"},
        {"page_size": 21, "query": "x"},
        {"page_size": 0, "query": "x"},
        {"query": "x", "posted_within_days": 2},
        {"query": "x", "work_mode": "teleport"},
        {"query": "x", "employment_type": "freelance"},
    ],
)
def test_invalid_search_requests_are_rejected(client, fake, payload):
    response = client.post("/api/jobs/provider/search", json=payload)
    assert response.status_code == 422
    assert fake.calls == []


def test_notes_document_attribution_and_shorthands(client, fake):
    fake.add(record())
    body = client.post(
        "/api/jobs/provider/search", json={"query": "python", "location": "Pune"}
    ).json()
    text = " ".join(body["notes"])
    assert "Source: JobsForce India" in text
    assert "city" in text
    assert "listing data is provided by JobsForce India" in text


# --------------------------------------------------------------------------- #
# Detail endpoint behaviour
# --------------------------------------------------------------------------- #

def test_closed_listing_410_is_marked_expired_and_never_deleted(client, db, fake):
    fake.add(record())
    fake.detail_status["python-developer-bengaluru"] = 410
    fake.detail_bodies["python-developer-bengaluru"] = {
        "error": {"message": "This job has closed."},
        "alternatives": [record(id="other-open-job", title="Other Python Role")],
    }

    response = client.post("/api/jobs/provider/search", json={"query": "python"})
    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 1
    assert body["details_fetched"] == 0

    job = db.scalar(select(Job).where(Job.provider == "jobsforce"))
    assert job is not None
    assert job.listing_status == "expired"
    assert job.title == "Python Developer"  # summary data is kept


def test_detail_404_marks_listing_expired(client, db, fake):
    fake.add(record())  # no detail registered -> fake answers 404
    response = client.post("/api/jobs/provider/search", json={"query": "python"})
    assert response.status_code == 200
    job = db.scalar(select(Job).where(Job.provider == "jobsforce"))
    assert job.listing_status == "expired"


def test_detail_server_error_keeps_the_summary_row(client, db, fake):
    fake.add(record())
    fake.detail_status["python-developer-bengaluru"] = 503

    response = client.post("/api/jobs/provider/search", json={"query": "python"})
    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 1 and body["details_failed"] == 1

    job = db.scalar(select(Job).where(Job.provider == "jobsforce"))
    assert job.title == "Python Developer"
    assert "fintech platform" in job.description


def test_provider_detail_is_normalized_for_direct_callers(fake):
    fake.details["abc-123"] = detail(id="abc-123")
    provider = jobsforce.JobsForceIndiaProvider()

    result = provider.get_job("abc-123")
    assert result is not None
    assert result.has_details is True
    assert result.provider_job_id == "abc-123"
    assert result.work_mode == "hybrid"
    assert result.salary_currency == "INR"
    assert "Responsibilities:\n- Design REST APIs" in result.description
    assert "Skills listed:" in result.description
    assert result.skills == ["Python", "FastAPI", "Docker", "PostgreSQL"]

    assert provider.get_job("missing-id") is None


# --------------------------------------------------------------------------- #
# Duplicates
# --------------------------------------------------------------------------- #

def test_repeated_search_updates_the_same_row_and_keeps_user_state(client, db, fake):
    fake.add(record(), detail=detail())
    client.post("/api/jobs/provider/search", json={"query": "python"})

    job = db.scalar(select(Job).where(Job.provider == "jobsforce"))
    first_seen = job.fetched_at
    job.application_status = "applied"
    db.commit()

    # The provider now reports a higher salary for the same listing.
    changed = record()
    changed["salary"]["min"] = 35000
    fake.search_body = search_body([changed])
    response = client.post("/api/jobs/provider/search", json={"query": "python"})
    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 0 and body["updated"] == 1

    db.expire_all()
    rows = db.scalars(select(Job).where(Job.provider == "jobsforce")).all()
    assert len(rows) == 1
    assert rows[0].id == job.id
    assert rows[0].salary_min == 35000
    assert rows[0].application_status == "applied"   # user state preserved
    assert rows[0].fetched_at == first_seen          # first-seen timestamp preserved


def test_listing_is_deduplicated_by_source_url_when_the_id_changes(client, db, fake):
    fake.add(record())
    client.post("/api/jobs/provider/search", json={"query": "python"})

    re_slugged = record(id="python-developer-bengaluru-2")
    re_slugged["url"] = record()["url"]  # same canonical page
    fake.search_body = search_body([re_slugged])
    response = client.post("/api/jobs/provider/search", json={"query": "python"})
    assert response.status_code == 200
    assert response.json()["created"] == 0

    rows = db.scalars(select(Job).where(Job.provider == "jobsforce")).all()
    assert len(rows) == 1
    assert rows[0].provider_job_id == "python-developer-bengaluru-2"


# --------------------------------------------------------------------------- #
# Provider failures
# --------------------------------------------------------------------------- #

def test_rate_limit_response_and_retry_after(client, fake):
    fake.search_status = 429
    fake.search_headers = {"Retry-After": "30"}
    response = client.post("/api/jobs/provider/search", json={"query": "python"})
    assert response.status_code == 429
    error = response.json()["error"]
    assert error["code"] == "provider_rate_limit"
    assert "30" in error["message"]


def test_provider_rejecting_a_filter_returns_400(client, fake):
    fake.search_status = 400
    fake.search_body = {"error": {"code": "bad_request", "message": "Unknown city"}}
    response = client.post("/api/jobs/provider/search", json={"query": "python", "city": "Atlantis"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "provider_invalid_request"
    assert "Unknown city" in response.json()["error"]["message"]


def test_provider_timeout_returns_504(client, fake):
    fake.search_exception = httpx.ReadTimeout("timed out")
    response = client.post("/api/jobs/provider/search", json={"query": "python"})
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "provider_timeout"


def test_network_error_is_reported_as_timeout(client, fake):
    fake.search_exception = httpx.ConnectError("unreachable")
    response = client.post("/api/jobs/provider/search", json={"query": "python"})
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "provider_timeout"


@pytest.mark.parametrize("status", [500, 502, 503])
def test_provider_server_errors_return_502(client, fake, status):
    fake.search_status = status
    response = client.post("/api/jobs/provider/search", json={"query": "python"})
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "provider_error"


def test_malformed_json_body_returns_502(client, fake):
    fake.search_status = 200
    fake.search_text = "<html>not json</html>"
    response = client.post("/api/jobs/provider/search", json={"query": "python"})
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "provider_error"


def test_malformed_results_shape_returns_502(client, fake):
    fake.search_body = {"results": "nope"}
    response = client.post("/api/jobs/provider/search", json={"query": "python"})
    assert response.status_code == 502
    assert "malformed" in response.json()["error"]["message"]


# --------------------------------------------------------------------------- #
# Imported listings use the existing matching engine
# --------------------------------------------------------------------------- #

def test_imported_job_scores_with_the_existing_matching_engine(monkeypatch, client, db, fake):
    monkeypatch.setattr(semantic, "availability", lambda: (False, "stubbed off"))
    monkeypatch.setattr(semantic, "similarity", lambda a, b: None)

    fake.add(record(), detail=detail())
    search = client.post("/api/jobs/provider/search", json={"query": "python developer"})
    assert search.status_code == 200
    job_id = search.json()["results"][0]["job_id"]

    resume = make_resume(
        db,
        name="Ada Lovelace",
        text=(
            "Ada Lovelace - Software Engineer\n"
            "Skills: Python, FastAPI, Docker, PostgreSQL, Kubernetes, Git\n"
            "Experience: 4 years of experience building APIs.\n"
        ),
    )

    calculation = client.post(f"/api/matches/calculate/{resume.id}", json={})
    assert calculation.status_code == 200
    body = calculation.json()
    assert body["jobs_evaluated"] == 1
    item = body["items"][0]
    assert item["job"]["id"] == job_id
    assert item["job"]["provider"] == "jobsforce"
    assert item["final_score"] > 0
    assert {"required", "preferred", "semantic"} <= set(item["components"])

    explanation = client.get(f"/api/matches/{item['id']}/explanation")
    assert explanation.status_code == 200
    assert explanation.json()["job_id"] == job_id
    assert explanation.json()["final_score"] == item["final_score"]


def test_imported_job_can_be_listed_and_filtered(client, db, fake):
    fake.add(record())
    client.post("/api/jobs/provider/search", json={"query": "python"})

    all_jobs = client.get("/api/jobs", params={"source": "provider"})
    assert all_jobs.status_code == 200
    assert all_jobs.json()["total"] == 1

    filtered = client.get(
        "/api/jobs", params={"category": "IT", "work_mode": "hybrid", "provider": "jobsforce"}
    )
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    item = filtered.json()["items"][0]
    assert item["salary_currency"] == "INR"
    assert item["listing_url"] == record()["url"]
    assert item["application_url"] == record()["apply_url"]

    assert client.get("/api/jobs", params={"work_mode": "onsite"}).json()["total"] == 0


def test_closing_date_in_the_past_is_expired_on_ingest(client, db, fake):
    expired = record()
    expired["valid_through"] = iso(-1)
    fake.add(expired, detail=detail(valid_through=iso(-1)))

    body = client.post("/api/jobs/provider/search", json={"query": "python"}).json()
    assert body["created"] == 1
    job = db.scalar(select(Job).where(Job.provider == "jobsforce"))
    assert job.listing_status == "expired"
    assert job.expires_date == date.fromisoformat(iso(-1))
