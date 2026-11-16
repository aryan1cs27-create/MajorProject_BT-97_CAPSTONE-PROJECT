"""Phase 5.1 — Alembic migration behaviour.

These tests never touch MySQL: each one points ``ALEMBIC_DATABASE_URL`` at a
throwaway SQLite file. They prove the two things that matter for adoption:

1. a fresh database is created by ``upgrade head``;
2. a database created *before* Alembic (the old ``create_all`` layout) is
   adopted in place — rows preserved, missing columns added, nothing recreated.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

BACKEND_DIR = Path(__file__).resolve().parents[1]
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"

LEGACY_SCHEMA = """
CREATE TABLE jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title VARCHAR(255) NOT NULL,
  company VARCHAR(255) NOT NULL,
  location VARCHAR(255),
  description TEXT NOT NULL,
  required_skills JSON NOT NULL,
  preferred_skills JSON NOT NULL,
  minimum_experience NUMERIC(4, 1),
  employment_type VARCHAR(50),
  is_sample BOOLEAN NOT NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
);
CREATE INDEX ix_jobs_title ON jobs (title);
CREATE TABLE resumes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  candidate_name VARCHAR(255),
  original_filename VARCHAR(255) NOT NULL,
  stored_filename VARCHAR(255) NOT NULL UNIQUE,
  file_type VARCHAR(10) NOT NULL,
  file_size INTEGER NOT NULL,
  extracted_text TEXT NOT NULL,
  skills JSON NOT NULL,
  uploaded_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
  processing_status VARCHAR(32) NOT NULL
);
INSERT INTO jobs (title, company, description, required_skills, preferred_skills, is_sample)
  VALUES ('Legacy Job', 'OldCo', 'desc', '["Python"]', '[]', 1);
INSERT INTO resumes (candidate_name, original_filename, stored_filename, file_type,
                     file_size, extracted_text, skills, processing_status)
  VALUES ('Ada', 'a.pdf', 'stored-a.pdf', 'pdf', 1, 'Python', '[]', 'processed');
"""


@pytest.fixture()
def alembic_config(tmp_path, monkeypatch):
    db_path = tmp_path / "migrations.db"
    url = f"sqlite:///{db_path.as_posix()}"
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", url)
    return Config(str(ALEMBIC_INI)), url


def test_fresh_database_upgrade_is_idempotent(alembic_config):
    config, url = alembic_config
    command.upgrade(config, "head")

    engine = create_engine(url)
    inspector = inspect(engine)
    assert {"resumes", "jobs", "match_results", "match_feedback"} <= set(
        inspector.get_table_names()
    )
    # every Phase 5 job column is present
    job_columns = {column["name"] for column in inspector.get_columns("jobs")}
    assert {
        "source",
        "provider",
        "provider_job_id",
        "application_url",
        "posted_date",
        "listing_status",
        "salary_min",
        "salary_max",
        # Phase 7 — JobsForce India metadata
        "salary_currency",
        "salary_period",
        "listing_url",
        "work_mode",
        "category",
        "expires_date",
        "hiring_type",
    } <= job_columns

    with engine.connect() as connection:
        revisions = [
            row[0] for row in connection.execute(text("SELECT version_num FROM alembic_version"))
        ]
    assert revisions == ["0003_jobsforce_fields"]

    command.upgrade(config, "head")  # no-op, must not raise
    engine.dispose()


def test_existing_database_is_adopted_without_data_loss(alembic_config):
    config, url = alembic_config
    engine = create_engine(url)
    with engine.begin() as connection:
        for statement in LEGACY_SCHEMA.split(";"):
            if statement.strip():
                connection.execute(text(statement))
    assert len(inspect(engine).get_columns("jobs")) == 12

    command.upgrade(config, "head")
    engine.dispose()
    engine = create_engine(url)

    with engine.connect() as connection:
        job = connection.execute(text("SELECT title, source, is_sample FROM jobs")).one()
        resume = connection.execute(text("SELECT candidate_name FROM resumes")).one()
    # data survived, and provenance was backfilled from is_sample
    assert job.title == "Legacy Job"
    assert job.source == "sample"
    assert resume.candidate_name == "Ada"
    # the missing table was created, existing ones were not recreated
    assert "match_results" in inspect(engine).get_table_names()
    assert len(inspect(engine).get_columns("jobs")) == 31
    engine.dispose()


def test_downgrade_removes_only_the_feedback_table(alembic_config):
    config, url = alembic_config
    command.upgrade(config, "head")

    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO resumes (candidate_name, original_filename, stored_filename, "
                "file_type, file_size, extracted_text, skills, processing_status) "
                "VALUES ('Grace', 'g.pdf', 'stored-g.pdf', 'pdf', 1, 'Python', '[]', 'processed')"
            )
        )
    engine.dispose()

    command.downgrade(config, "0001_baseline")
    engine = create_engine(url)
    tables = set(inspect(engine).get_table_names())
    assert "match_feedback" not in tables
    # the Phase 7 columns are removed again by the 0003 downgrade
    remaining = {column["name"] for column in inspect(engine).get_columns("jobs")}
    assert "listing_url" not in remaining and "work_mode" not in remaining
    assert {"resumes", "jobs", "match_results"} <= tables
    with engine.connect() as connection:
        assert connection.execute(text("SELECT candidate_name FROM resumes")).scalar() == "Grace"
    engine.dispose()


def test_baseline_downgrade_never_drops_existing_tables(alembic_config):
    """The baseline downgrade is a documented no-op: no data destruction."""
    config, url = alembic_config
    command.upgrade(config, "head")
    command.downgrade(config, "base")

    engine = create_engine(url)
    assert {"resumes", "jobs", "match_results"} <= set(inspect(engine).get_table_names())
    engine.dispose()
