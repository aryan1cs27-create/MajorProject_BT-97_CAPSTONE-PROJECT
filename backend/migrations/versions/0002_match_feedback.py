"""Phase 6 — append-only match feedback events.

Revision ID: 0002_match_feedback
Revises: 0001_baseline
Create Date: 2026-11-15

Creates ``match_feedback``: one immutable row per feedback submission, tied to
an existing ``match_results`` row (and therefore to its resume/job pair). No
existing table, row or score is modified.

Downgrade drops only this table (the Phase 6 addition); ``resumes``, ``jobs``
and ``match_results`` are untouched.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "0002_match_feedback"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(name: str) -> bool:
    return inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    if _has_table("match_feedback"):
        return

    op.create_table(
        "match_feedback",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("match_id", sa.Integer(), nullable=False),
        sa.Column("resume_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("verdict", sa.String(length=20), nullable=False),
        sa.Column("relevance", sa.Integer(), nullable=True),
        sa.Column("outcome", sa.String(length=20), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("event_key", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["match_id"], ["match_results.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resume_id"], ["resumes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_key", name="uq_feedback_event_key"),
    )
    op.create_index("ix_match_feedback_match_id", "match_feedback", ["match_id"])
    op.create_index(
        "ix_feedback_pair_created", "match_feedback", ["resume_id", "job_id", "created_at"]
    )
    op.create_index(
        "ix_feedback_resume_created", "match_feedback", ["resume_id", "created_at"]
    )


def downgrade() -> None:
    if not _has_table("match_feedback"):
        return

    for index in (
        "ix_feedback_resume_created",
        "ix_feedback_pair_created",
        "ix_match_feedback_match_id",
    ):
        existing = {item["name"] for item in inspect(op.get_bind()).get_indexes("match_feedback")}
        if index in existing:
            op.drop_index(index, table_name="match_feedback")
    op.drop_table("match_feedback")
