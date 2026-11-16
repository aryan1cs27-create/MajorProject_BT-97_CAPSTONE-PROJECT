"""Baseline — the schema that existed before Alembic was introduced.

Revision ID: 0001_baseline
Revises: (none)
Create Date: 2026-11-15

Adoption strategy
-----------------
This project used ``Base.metadata.create_all`` plus a hand-written
``ALTER TABLE`` patch for the Phase 5 job columns. Existing databases (MySQL)
therefore already contain ``resumes`` / ``jobs`` / ``match_results``. A baseline
that blindly recreated them would fail — or, worse, wipe data.

So this revision is **guarded**: each table is created only when it does not
exist, and the Phase 5 columns / unique index are added only when missing. That
makes the migration equivalent to the old startup path on both a fresh database
and an already-populated one, and it lets ``alembic upgrade head`` adopt an
existing database in place.

Downgrade is intentionally a **no-op**: dropping ``resumes``, ``jobs`` and
``match_results`` would destroy data that predates Alembic. Use a scratch
database if you need to test a full teardown.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Columns Phase 5 added to an existing ``jobs`` table (same definitions the
#: old ``init_db`` applied), keyed by column name.
LEGACY_JOB_COLUMNS: dict[str, str] = {
    "source": "VARCHAR(20) NOT NULL DEFAULT 'manual'",
    "provider": "VARCHAR(100) NULL",
    "provider_job_id": "VARCHAR(255) NULL",
    "application_url": "VARCHAR(500) NULL",
    "posted_date": "DATETIME NULL",
    "fetched_at": "DATETIME NULL",
    "verified_at": "DATETIME NULL",
    "listing_status": "VARCHAR(20) NULL",
    "salary_min": "NUMERIC(12, 2) NULL",
    "salary_max": "NUMERIC(12, 2) NULL",
    "source_metadata": "JSON NULL",
    "application_status": "VARCHAR(20) NULL",
}

JOBS_INDEXES: tuple[tuple[str, list[str]], ...] = (
    ("ix_jobs_title", ["title"]),
    ("ix_jobs_company", ["company"]),
    ("ix_jobs_location", ["location"]),
    ("ix_jobs_employment_type", ["employment_type"]),
    ("ix_jobs_source", ["source"]),
    ("ix_jobs_provider", ["provider"]),
)

RESUME_INDEXES: tuple[tuple[str, list[str]], ...] = (
    ("ix_resumes_candidate_name", ["candidate_name"]),
    ("ix_resumes_original_filename", ["original_filename"]),
    ("ix_resumes_processing_status", ["processing_status"]),
)

MATCH_INDEXES: tuple[tuple[str, list[str]], ...] = (
    ("ix_match_resume_score", ["resume_id", "final_score"]),
    ("ix_match_job_score", ["job_id", "final_score"]),
)


def _inspector() -> sa.Inspector:
    return inspect(op.get_bind())


def _has_table(name: str) -> bool:
    return _inspector().has_table(name)


def _existing_indexes(table: str) -> set[str]:
    return {index["name"] for index in _inspector().get_indexes(table)}


def _create_missing_indexes(table: str, indexes: tuple[tuple[str, list[str]], ...]) -> None:
    present = _existing_indexes(table)
    for name, columns in indexes:
        if name not in present:
            op.create_index(name, table, columns)


def _create_jobs() -> None:
    if _has_table("jobs"):
        return
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("company", sa.String(length=255), nullable=False),
        sa.Column("location", sa.String(length=255), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("required_skills", sa.JSON(), nullable=False),
        sa.Column("preferred_skills", sa.JSON(), nullable=False),
        sa.Column("minimum_experience", sa.Numeric(precision=4, scale=1), nullable=True),
        sa.Column("employment_type", sa.String(length=50), nullable=True),
        sa.Column("is_sample", sa.Boolean(), nullable=False),
        sa.Column("application_status", sa.String(length=20), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=True),
        sa.Column("provider_job_id", sa.String(length=255), nullable=True),
        sa.Column("application_url", sa.String(length=500), nullable=True),
        sa.Column("posted_date", sa.DateTime(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(), nullable=True),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column("listing_status", sa.String(length=20), nullable=True),
        sa.Column("salary_min", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("salary_max", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("source_metadata", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_job_id"),
    )


def _adopt_existing_jobs() -> None:
    """Bring a pre-Alembic ``jobs`` table up to the current column set."""
    existing = {column["name"] for column in _inspector().get_columns("jobs")}
    for name, ddl in LEGACY_JOB_COLUMNS.items():
        if name not in existing:
            op.execute(sa.text(f"ALTER TABLE jobs ADD COLUMN {name} {ddl}"))

    # Deduplication key for imported listings. All pre-existing rows are NULL
    # and every supported dialect allows repeated NULLs in a unique index.
    has_unique_provider_id = any(
        index.get("unique") and list(index.get("column_names") or []) == ["provider_job_id"]
        for index in _inspector().get_indexes("jobs")
    )
    if not has_unique_provider_id:
        op.create_index(
            "uq_jobs_provider_job_id", "jobs", ["provider_job_id"], unique=True
        )

    # Backfill provenance for rows created before `source` existed. The column
    # is added with DEFAULT 'manual', so a NULL-only WHERE clause would never
    # match — correct any row that is evidently a provider/sample row instead.
    op.execute(
        sa.text(
            "UPDATE jobs SET source = CASE "
            "WHEN provider IS NOT NULL THEN 'provider' "
            "WHEN is_sample = 1 THEN 'sample' "
            "ELSE 'manual' END "
            "WHERE provider IS NOT NULL OR is_sample = 1"
        )
    )


def _create_resumes() -> None:
    if _has_table("resumes"):
        return
    op.create_table(
        "resumes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("candidate_name", sa.String(length=255), nullable=True),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("stored_filename", sa.String(length=255), nullable=False),
        sa.Column("file_type", sa.String(length=10), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=False),
        sa.Column("skills", sa.JSON(), nullable=False),
        sa.Column(
            "uploaded_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("processing_status", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stored_filename"),
    )


def _create_match_results() -> None:
    if _has_table("match_results"):
        return
    op.create_table(
        "match_results",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("resume_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("final_score", sa.Float(), nullable=False),
        sa.Column("required_score", sa.Float(), nullable=True),
        sa.Column("preferred_score", sa.Float(), nullable=True),
        sa.Column("semantic_score", sa.Float(), nullable=True),
        sa.Column("matched_skills", sa.JSON(), nullable=False),
        sa.Column("missing_required", sa.JSON(), nullable=False),
        sa.Column("missing_preferred", sa.JSON(), nullable=False),
        sa.Column("weights", sa.JSON(), nullable=False),
        sa.Column("signals", sa.JSON(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resume_id"], ["resumes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("resume_id", "job_id", name="uq_match_resume_job"),
    )


def upgrade() -> None:
    # Order matters: match_results references jobs and resumes.
    _create_jobs()
    _create_resumes()
    _create_match_results()
    _adopt_existing_jobs()

    # Indexes are created separately so an adopted, pre-Alembic database gets
    # the same indexes as a fresh one. The helper skips existing names.
    _create_missing_indexes("jobs", JOBS_INDEXES)
    _create_missing_indexes("resumes", RESUME_INDEXES)
    _create_missing_indexes("match_results", MATCH_INDEXES)


def downgrade() -> None:
    """No-op on purpose — see the module docstring.

    Dropping these tables would delete pre-Alembic data. A scratch database can
    be destroyed with ``alembic downgrade base`` followed by dropping the
    remaining tables manually.
    """
    pass
