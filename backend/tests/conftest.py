"""Shared pytest fixtures.

Tests run against an **in-memory SQLite database** and an overridden
``get_db`` dependency, so no test can touch the real MySQL data.

API authentication is enabled for the suite (``API_TOKEN`` is set *before*
``app`` is imported, so the cached Settings pick it up). The ``client`` fixture
sends the token by default; use ``anon_client`` for unauthenticated requests and
``client.override_token`` to swap it per test.
"""

from __future__ import annotations

import os
import uuid

# Must run before `app` is imported anywhere below.
os.environ.setdefault("API_TOKEN", "test-token")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401  (registers the tables)
from app.db.session import Base, get_db
from app.main import app


@pytest.fixture(scope="function")
def engine():
    """A fresh in-memory database per test so nothing leaks between tests."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def db(engine):
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    yield session
    session.rollback()
    session.close()


@pytest.fixture()
def client(db):
    """FastAPI test client wired to the in-memory database.

    Note: the context manager (which would run the lifespan and touch MySQL)
    is deliberately not used.
    """
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db

    class AuthTestClient(TestClient):
        """TestClient that sends the suite token unless told otherwise."""

        token: str | None = os.environ["API_TOKEN"]

        def request(self, *args, **kwargs):  # type: ignore[override]
            if self.token is not None:
                headers = dict(kwargs.pop("headers", None) or {})
                headers.setdefault("Authorization", f"Bearer {self.token}")
                kwargs["headers"] = headers
            return super().request(*args, **kwargs)

    yield AuthTestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture()
def anon_client(db):
    """Client that sends no Authorization header at all."""

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db

    class AnonClient(TestClient):
        token: str | None = None

    yield AnonClient(app)
    app.dependency_overrides.clear()


# --------------------------------------------------------------------- builders
def make_resume(db, *, name: str, text: str, filename: str = "cv.pdf"):
    from app.models.resume import STATUS_PROCESSED, Resume

    resume = Resume(
        candidate_name=name,
        original_filename=filename,
        stored_filename=f"test-{uuid.uuid4().hex}.pdf",
        file_type="pdf",
        file_size=1000,
        extracted_text=text,
        skills=[],
        processing_status=STATUS_PROCESSED,
    )
    db.add(resume)
    db.commit()
    return resume


def make_job(
    db,
    *,
    title: str,
    company: str = "TestCo",
    description: str = "",
    required: list | None = None,
    preferred: list | None = None,
    min_experience: float | None = None,
    employment_type: str | None = "full-time",
    location: str | None = "Remote",
):
    from app.models.job import Job

    job = Job(
        title=title,
        company=company,
        location=location,
        description=description or f"{title} role",
        required_skills=required or [],
        preferred_skills=preferred or [],
        minimum_experience=min_experience,
        employment_type=employment_type,
        is_sample=False,
    )
    db.add(job)
    db.commit()
    return job


@pytest.fixture()
def backend_resume(db):
    return make_resume(
        db,
        name="Backend Candidate",
        text=(
            "Backend Candidate - Python Developer\n"
            "Skills: Python, FastAPI, PostgreSQL, Docker, Kubernetes, Redis, Git, CI/CD\n"
            "Experience: 6 years of experience building REST APIs.\n"
            "BSc in Computer Science\n"
        ),
    )


@pytest.fixture()
def frontend_resume(db):
    return make_resume(
        db,
        name="Frontend Candidate",
        text=(
            "Frontend Candidate - UI Engineer\n"
            "Skills: JavaScript, React, TypeScript, CSS, HTML, Git\n"
            "Experience: 4 years of experience building web applications.\n"
        ),
    )
