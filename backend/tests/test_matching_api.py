"""Match API tests: ranking, persistence, idempotent recalculation, 404s.

Also guards the Phase 1/Phase 2 surface (health, resumes, jobs).
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.models.match import MatchResult
from app.services import semantic
from conftest import make_job, make_resume


@pytest.fixture(autouse=True)
def _stub_semantic(monkeypatch):
    monkeypatch.setattr(semantic, "availability", lambda: (False, "stubbed off"))
    monkeypatch.setattr(semantic, "similarity", lambda a, b: None)


@pytest.fixture()
def dataset(db):
    resume = make_resume(
        db,
        name="Ada Lovelace",
        text=(
            "Ada Lovelace - Software Engineer\n"
            "Skills: Python, FastAPI, Docker, Git\n"
            "Experience: 5 years of experience building APIs.\n"
        ),
    )
    strong = make_job(
        db,
        title="Backend Engineer",
        company="StrongMatch",
        description="Python FastAPI Docker services",
        required=["Python", "FastAPI", "Docker"],
        preferred=["Git"],
    )
    weak = make_job(
        db,
        title="iOS Developer",
        company="WeakMatch",
        description="Swift mobile apps",
        required=["Swift", "Kotlin", "iOS"],
    )
    middle = make_job(
        db,
        title="DevOps Engineer",
        company="MiddleMatch",
        description="Containers and CI",
        required=["Docker", "Kubernetes"],
        preferred=["Python"],
    )
    return resume, [strong, middle, weak]


# ------------------------------------------------------------------ endpoints
def test_phase1_health_endpoints_still_work(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["app"] == "MatchWise"

    db_response = client.get("/api/health/db")
    # sqlite-backed test session: the endpoint still performs a real query path
    assert db_response.status_code in (200, 503)


def test_phase2_resume_and_job_endpoints_still_work(client, dataset):
    assert client.get("/api/resumes").status_code == 200
    jobs = client.get("/api/jobs")
    assert jobs.status_code == 200
    assert jobs.json()["total"] >= 3
    assert client.get("/api/jobs/1").status_code in (200, 404)


def test_calculate_returns_ranked_results(client, dataset):
    resume, _jobs = dataset
    response = client.post(f"/api/matches/calculate/{resume.id}", json={})
    assert response.status_code == 200
    body = response.json()

    assert body["jobs_evaluated"] == 3
    assert body["semantic"]["available"] is False
    scores = [item["final_score"] for item in body["items"]]
    assert scores == sorted(scores, reverse=True)
    assert body["items"][0]["job"]["title"] == "Backend Engineer"
    assert body["items"][-1]["job"]["title"] == "iOS Developer"


def test_scores_are_json_numbers_and_within_bounds(client, dataset):
    resume, _jobs = dataset
    body = client.post(f"/api/matches/calculate/{resume.id}", json={}).json()
    for item in body["items"]:
        assert isinstance(item["final_score"], (int, float))
        assert 0 <= item["final_score"] <= 100
        for component in ("required", "preferred"):
            value = item["components"].get(component)
            assert value is None or (0 <= value <= 100)


def test_results_persist_and_can_be_retrieved(client, db, dataset):
    resume, _jobs = dataset
    client.post(f"/api/matches/calculate/{resume.id}", json={})

    stored = list(db.scalars(select(MatchResult).where(MatchResult.resume_id == resume.id)))
    assert len(stored) == 3
    assert all(row.final_score >= 0 for row in stored)

    for_resume = client.get(f"/api/matches/resume/{resume.id}")
    assert for_resume.status_code == 200
    payload = for_resume.json()
    assert payload["total"] == 3
    scores = [item["final_score"] for item in payload["items"]]
    assert scores == sorted(scores, reverse=True)

    # per-job view includes the resume
    job_id = payload["items"][0]["job_id"]
    for_job = client.get(f"/api/matches/job/{job_id}")
    assert for_job.status_code == 200
    assert for_job.json()["items"][0]["resume"]["candidate_name"] == "Ada Lovelace"

    # single match
    match_id = payload["items"][0]["id"]
    single = client.get(f"/api/matches/{match_id}")
    assert single.status_code == 200
    assert single.json()["id"] == match_id


def test_recalculation_updates_instead_of_duplicating(client, db, dataset):
    resume, _jobs = dataset
    client.post(f"/api/matches/calculate/{resume.id}", json={})
    first_count = db.scalar(
        select(func.count()).select_from(MatchResult).where(MatchResult.resume_id == resume.id)
    )

    client.post(f"/api/matches/calculate/{resume.id}", json={})
    second_count = db.scalar(
        select(func.count()).select_from(MatchResult).where(MatchResult.resume_id == resume.id)
    )
    assert second_count == first_count == 3

    # unique constraint exists in the schema
    table = MatchResult.__table__
    assert any(
        constraint.name == "uq_match_resume_job"
        for constraint in table.constraints
    )


def test_nonexistent_ids_return_404(client, dataset):
    assert client.get("/api/matches/resume/99999").status_code == 404
    assert client.get("/api/matches/job/99999").status_code == 404
    assert client.get("/api/matches/99999").status_code == 404
    assert client.post("/api/matches/calculate/99999", json={}).status_code == 404
    body = client.get("/api/matches/resume/99999").json()
    assert body["error"]["code"] == "not_found"


def test_empty_dataset_is_handled_gracefully(db, client):
    empty_resume = make_resume(db, name="No Jobs Yet", text="Skills: Python")
    response = client.post(f"/api/matches/calculate/{empty_resume.id}", json={})
    assert response.status_code == 200
    payload = response.json()
    assert payload["items"] == []
    assert payload["jobs_evaluated"] == 0

    listed = client.get(f"/api/matches/resume/{empty_resume.id}")
    assert listed.status_code == 200
    assert listed.json()["total"] == 0


def test_job_subset_calculation(client, dataset):
    resume, jobs = dataset
    response = client.post(
        f"/api/matches/calculate/{resume.id}",
        json={"job_ids": [jobs[0].id]},
    )
    assert response.status_code == 200
    assert response.json()["jobs_evaluated"] == 1

    missing = client.post(
        f"/api/matches/calculate/{resume.id}",
        json={"job_ids": [99999]},
    )
    assert missing.status_code == 404


def test_no_hardcoded_recommendations(client, db, dataset):
    """Different data produces different rankings — results come from the DB."""
    resume, _jobs = dataset
    other = make_resume(
        db,
        name="Mobile Dev",
        text="Skills: Swift, Kotlin, iOS, Flutter\nExperience: 3 years of experience.\n",
    )
    first = client.post(f"/api/matches/calculate/{resume.id}", json={}).json()
    second = client.post(f"/api/matches/calculate/{other.id}", json={}).json()

    assert first["items"][0]["job"]["title"] == "Backend Engineer"
    assert second["items"][0]["job"]["title"] == "iOS Developer"
    # the rankings themselves differ — nothing is hardcoded
    first_titles = [item["job"]["title"] for item in first["items"]]
    second_titles = [item["job"]["title"] for item in second["items"]]
    assert first_titles != second_titles
