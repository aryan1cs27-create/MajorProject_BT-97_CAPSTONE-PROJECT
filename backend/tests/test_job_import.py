"""Manual CSV/JSON import — validation, idempotency and duplicate safety.

These tests were previously mixed into the (removed) Adzuna provider test file;
they exercise :mod:`app.services.jobs_import` through the API and are unrelated
to the live provider.
"""

from __future__ import annotations

from sqlalchemy import func, select

from app.models.job import Job
from conftest import make_job


def test_csv_import_creates_valid_rows(client, db):
    content = (
        "title,company,description,location,required_skills,preferred_skills,"
        "minimum_experience,employment_type\n"
        "Data Analyst,InsightCo,SQL dashboards,Pune,\"SQL, Python\",Excel,2,full-time\n"
        "QA Engineer,TestLabs,Manual and API testing,Remote,Testing,,1,contract\n"
    )
    response = client.post("/api/jobs/import/csv", json={"content": content})
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["total_rows"] == 2
    assert body["valid_rows"] == 2 and body["invalid_rows"] == 0
    assert body["created"] == 2 and body["updated"] == 0

    rows = db.scalars(select(Job).order_by(Job.title)).all()
    assert [row.source for row in rows] == ["import", "import"]
    analyst = rows[0]
    assert analyst.company == "InsightCo"
    assert analyst.required_skills == ["SQL", "Python"]
    assert analyst.preferred_skills == ["Excel"]
    assert analyst.minimum_experience == 2
    assert analyst.employment_type == "full-time"


def test_csv_import_reports_bad_rows_without_aborting(client, db):
    content = (
        "title,company,description\n"
        "Good Role,GoodCo,A real description\n"
        "Bad Role,,Missing company\n"
        ",NoTitleCo,Missing title\n"
    )
    response = client.post("/api/jobs/import/csv", json={"content": content})
    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 1
    assert body["invalid_rows"] == 2
    assert len(body["errors"]) == 2
    assert all("row" in error.lower() for error in body["errors"])
    assert db.scalar(select(func.count(Job.id))) == 1


def test_reimporting_the_same_csv_updates_instead_of_duplicating(client, db):
    content = "title,company,description\nBackend Dev,Acme,First description\n"
    assert client.post("/api/jobs/import/csv", json={"content": content}).json()["created"] == 1

    updated_content = "title,company,description\nbackend dev,ACME,Updated description\n"
    body = client.post("/api/jobs/import/csv", json={"content": updated_content}).json()
    assert body["created"] == 0 and body["updated"] == 1
    rows = db.scalars(select(Job)).all()
    assert len(rows) == 1
    assert rows[0].description == "Updated description"

    # duplicate rows inside one file are collapsed too
    twice = "title,company,description\nSame One,Co,Desc\nSame One,Co,Desc\n"
    body = client.post("/api/jobs/import/csv", json={"content": twice}).json()
    assert body["created"] == 1 and body["updated"] == 1


def test_import_never_overwrites_manual_or_provider_rows(client, db):
    make_job(db, title="Manual Job", company="ManualCo", description="hand written")
    live = Job(
        title="Live Job",
        company="LiveCo",
        description="from jobsforce",
        required_skills=[],
        preferred_skills=[],
        source="provider",
        provider="jobsforce",
        provider_job_id="live-1",
        is_sample=False,
    )
    db.add(live)
    db.commit()

    content = (
        "title,company,description\n"
        "Manual Job,ManualCo,imported text\n"
        "Live Job,LiveCo,imported text\n"
    )
    body = client.post("/api/jobs/import/csv", json={"content": content}).json()
    assert body["created"] == 0 and body["updated"] == 0
    assert body["skipped"] == 2
    assert len(body["messages"]) == 2

    db.expire_all()
    assert db.scalar(select(Job.description).where(Job.title == "Manual Job")) == "hand written"
    assert db.scalar(select(Job.description).where(Job.title == "Live Job")) == "from jobsforce"


def test_json_import_validates_each_row(client, db):
    payload = {
        "jobs": [
            {
                "title": "Frontend Engineer",
                "company": "UICo",
                "description": "React and TypeScript",
                "required_skills": ["React", "TypeScript"],
                "minimum_experience": 3,
                "employment_type": "full-time",
            },
            {"title": "Broken", "company": "NoDescCo"},
            {"company": "NoTitleCo", "description": "x"},
        ]
    }
    response = client.post("/api/jobs/import/json", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 1 and body["invalid_rows"] == 2
    job = db.scalar(select(Job))
    assert job.title == "Frontend Engineer"
    assert job.required_skills == ["React", "TypeScript"]
    assert job.source == "import"


def test_json_import_rejects_unparseable_experience(client, db):
    payload = {
        "jobs": [
            {
                "title": "Role",
                "company": "Co",
                "description": "desc",
                "minimum_experience": "many years",
            }
        ]
    }
    body = client.post("/api/jobs/import/json", json=payload).json()
    assert body["created"] == 0
    assert body["invalid_rows"] == 1
    assert "minimum_experience" in body["errors"][0]
    assert db.scalar(select(func.count(Job.id))) == 0


def test_imported_jobs_are_matchable(client, db):
    content = (
        "title,company,description,required_skills\n"
        "Python Engineer,ImportCo,Python FastAPI services,Python\n"
    )
    client.post("/api/jobs/import/csv", json={"content": content})
    from conftest import make_resume

    resume = make_resume(db, name="Imported Match", text="Skills: Python, FastAPI\n")
    body = client.post(f"/api/matches/calculate/{resume.id}", json={}).json()
    assert body["jobs_evaluated"] == 1
    assert body["items"][0]["job"]["source"] == "import"
