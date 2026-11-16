"""Phase 7 — JobsForce India listing metadata.

Revision ID: 0003_jobsforce_fields
Revises: 0002_match_feedback
Create Date: 2026-11-15

Adds the columns needed to preserve what the JobsForce India API returns:
salary currency/period, the canonical listing URL, work mode, category, the
listing's closing date and the hiring type (employer/portal/agency).

Guarded like the baseline: a column is only added when it is missing, so the
same revision works on a fresh database and on an existing MySQL one. No
existing column, row or score is modified, and historical rows simply have
NULLs for the new fields.

Downgrade drops only these columns (the Phase 7 addition); jobs rows, resumes,
matches and feedback are untouched.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "0003_jobsforce_fields"
down_revision: str | None = "0002_match_feedback"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: column name -> portable DDL used for both fresh and adopted databases
NEW_JOB_COLUMNS: dict[str, str] = {
    "salary_currency": "VARCHAR(10) NULL",
    "salary_period": "VARCHAR(20) NULL",
    "listing_url": "VARCHAR(500) NULL",
    "work_mode": "VARCHAR(20) NULL",
    "category": "VARCHAR(120) NULL",
    "expires_date": "DATE NULL",
    "hiring_type": "VARCHAR(20) NULL",
}

NEW_JOB_INDEXES: tuple[tuple[str, list[str]], ...] = (
    ("ix_jobs_work_mode", ["work_mode"]),
    ("ix_jobs_category", ["category"]),
)


def _inspector() -> sa.Inspector:
    return inspect(op.get_bind())


def upgrade() -> None:
    if not _inspector().has_table("jobs"):
        return

    existing = {column["name"] for column in _inspector().get_columns("jobs")}
    for name, ddl in NEW_JOB_COLUMNS.items():
        if name not in existing:
            op.execute(sa.text(f"ALTER TABLE jobs ADD COLUMN {name} {ddl}"))

    present = {index["name"] for index in _inspector().get_indexes("jobs")}
    for name, columns in NEW_JOB_INDEXES:
        if name not in present:
            op.create_index(name, "jobs", columns)


def downgrade() -> None:
    if not _inspector().has_table("jobs"):
        return

    present = {index["name"] for index in _inspector().get_indexes("jobs")}
    for name, _columns in NEW_JOB_INDEXES:
        if name in present:
            op.drop_index(name, table_name="jobs")

    existing = {column["name"] for column in _inspector().get_columns("jobs")}
    for name in NEW_JOB_COLUMNS:
        if name in existing:
            op.drop_column("jobs", name)
