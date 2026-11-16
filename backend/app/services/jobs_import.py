"""Manual job import (CSV and JSON) with validation and duplicate handling.

Every row is validated on its own: a bad row produces a row-numbered message in
``errors`` and never aborts the rest of the import. Duplicates are detected with
an exact (trimmed, case-insensitive) ``title`` + ``company`` match:

- an existing **import** row is updated in place (re-importing the same file is
  idempotent and never creates a second copy);
- an existing **manual**, **sample** or **provider** row is left untouched and
  reported in ``messages`` — imports must not overwrite data the user created
  or sample rows, and must not duplicate them either.

Imported rows carry ``source = "import"`` (or ``"provider"`` for live listings).
"""

from __future__ import annotations

import csv
import io
import json
import logging
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.models.job import Job
from app.schemas.job import EMPLOYMENT_TYPES

logger = logging.getLogger("matchwise.imports")

REQUIRED_COLUMNS = ("title", "company", "description")
OPTIONAL_COLUMNS = (
    "location",
    "required_skills",
    "preferred_skills",
    "minimum_experience",
    "employment_type",
)
MAX_SKILL_CHARS = 100
MAX_SKILLS = 30


class ImportSummary(BaseModel):
    """Summary of an import operation, returned to the user."""

    total_rows: int = Field(default=0, ge=0)
    valid_rows: int = Field(default=0, ge=0)
    invalid_rows: int = Field(default=0, ge=0)
    created: int = Field(default=0, ge=0)
    updated: int = Field(default=0, ge=0)
    skipped: int = Field(default=0, ge=0)
    errors: list[str] = Field(default_factory=list)
    messages: list[str] = Field(default_factory=list)

    model_config = {"from_attributes": True}


# --------------------------------------------------------------------------- #
# Row validation
# --------------------------------------------------------------------------- #

def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ("" if value is None else str(value).strip())


def _parse_skills(value: Any, *, field: str) -> list[str]:
    """Skills accept a JSON list or a comma-separated string."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return []
    if isinstance(value, str):
        raw_items: list[Any] = [part for part in value.split(",")]
    elif isinstance(value, list):
        raw_items = value
    else:
        raise ValueError(f"{field} must be a list or a comma-separated string")

    skills: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        if not isinstance(item, (str, int, float)):
            raise ValueError(f"{field} entries must be text")
        skill = " ".join(str(item).split())
        if not skill:
            continue
        if len(skill) > MAX_SKILL_CHARS:
            raise ValueError(
                f"{field} entry {skill[:30]!r} is longer than {MAX_SKILL_CHARS} characters"
            )
        if skill.lower() in seen:
            continue
        seen.add(skill.lower())
        skills.append(skill)
        if len(skills) >= MAX_SKILLS:
            break
    return skills


def _parse_experience(value: Any) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise ValueError("minimum_experience must be a number of years")
    try:
        years = float(value)
    except (TypeError, ValueError):
        raise ValueError(
            f"minimum_experience must be a number of years (got {value!r})"
        ) from None
    if not 0 <= years <= 50:
        raise ValueError("minimum_experience must be between 0 and 50")
    return years


def _parse_employment_type(value: Any) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str):
        raise ValueError("employment_type must be text")
    normalised = value.strip().lower()
    if normalised not in EMPLOYMENT_TYPES:
        allowed = ", ".join(sorted(EMPLOYMENT_TYPES))
        raise ValueError(
            f"employment_type {value!r} is invalid — expected one of: {allowed}"
        )
    return normalised


def normalize_row(row: Any, row_number: int) -> tuple[dict[str, Any] | None, str | None]:
    """Validate one imported row.

    Returns ``(payload, None)`` when the row is valid, otherwise
    ``(None, "Row N: reason")``.
    """
    if not isinstance(row, dict):
        return None, f"Row {row_number}: expected an object with title, company and description"

    title = _text(row.get("title"))
    company = _text(row.get("company"))
    description = _text(row.get("description"))

    missing = [
        name
        for name, value in (("title", title), ("company", company), ("description", description))
        if not value
    ]
    if missing:
        return None, f"Row {row_number}: missing required field(s): {', '.join(missing)}"

    try:
        required_skills = _parse_skills(row.get("required_skills"), field="required_skills")
        preferred_skills = _parse_skills(row.get("preferred_skills"), field="preferred_skills")
        minimum_experience = _parse_experience(row.get("minimum_experience"))
        employment_type = _parse_employment_type(row.get("employment_type"))
    except ValueError as exc:
        return None, f"Row {row_number}: {exc}"

    location = _text(row.get("location")) or None
    if location and len(location) > 255:
        return None, f"Row {row_number}: location must be at most 255 characters"
    if len(title) > 255:
        return None, f"Row {row_number}: title must be at most 255 characters"
    if len(company) > 255:
        return None, f"Row {row_number}: company must be at most 255 characters"

    return (
        {
            "title": title,
            "company": company,
            "location": location,
            "description": description,
            "required_skills": required_skills,
            "preferred_skills": preferred_skills,
            "minimum_experience": minimum_experience,
            "employment_type": employment_type,
        },
        None,
    )


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #

def _find_existing(db: Session, payload: dict[str, Any]) -> Job | None:
    return db.scalar(
        select(Job).where(
            func.lower(func.trim(Job.title)) == payload["title"].lower(),
            func.lower(func.trim(Job.company)) == payload["company"].lower(),
        )
    )


def _import_rows(db: Session, rows: list[Any], origin: str) -> ImportSummary:
    summary = ImportSummary(total_rows=len(rows))
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    for index, raw_row in enumerate(rows, start=1):
        payload, error = normalize_row(raw_row, index)
        if payload is None:
            summary.invalid_rows += 1
            summary.errors.append(error or f"Row {index}: invalid")
            continue

        summary.valid_rows += 1
        existing = _find_existing(db, payload)

        if existing is not None and existing.source != "import":
            # Never overwrite manual, sample or live-provider rows; and never
            # insert a duplicate next to them either.
            summary.skipped += 1
            summary.messages.append(
                f"Row {index}: matches existing {existing.source} job "
                f"{existing.title!r} at {existing.company!r} (id {existing.id}) — left unchanged"
            )
            continue

        if existing is None:
            job = Job(
                **payload,
                source="import",
                is_sample=False,
                source_metadata={"source": "import", "origin": origin, "imported_at": now.isoformat()},
            )
            db.add(job)
            # Flush so a duplicate row later in the same file updates this row
            # instead of inserting a second copy.
            db.flush()
            summary.created += 1
        else:
            for key, value in payload.items():
                setattr(existing, key, value)
            existing.source = "import"
            existing.is_sample = False
            metadata = dict(existing.source_metadata or {})
            metadata.update(
                {"source": "import", "origin": origin, "imported_at": now.isoformat()}
            )
            existing.source_metadata = metadata
            summary.updated += 1

    try:
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Job import failed")
        raise APIError(500, "import_failed", "The job import could not be completed.") from None

    return summary


# --------------------------------------------------------------------------- #
# Public entry points
# --------------------------------------------------------------------------- #

def import_jobs_from_csv(db: Session, content: str) -> ImportSummary:
    """Import jobs from CSV text with a header row."""
    if not content or not content.strip():
        return ImportSummary(errors=["The uploaded CSV is empty."])

    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames:
        return ImportSummary(
            errors=["The CSV must start with a header row (title, company, description, …)."]
        )

    headers = {(name or "").strip().lower() for name in reader.fieldnames if name}
    missing = [name for name in REQUIRED_COLUMNS if name not in headers]
    if missing:
        return ImportSummary(
            errors=[
                "The CSV header is missing required column(s): "
                + ", ".join(missing)
                + f". Expected at least: {', '.join(REQUIRED_COLUMNS)}."
            ]
        )
    unknown = sorted(headers - set(REQUIRED_COLUMNS) - set(OPTIONAL_COLUMNS))
    notes: list[str] = []
    if unknown:
        notes.append(f"Ignored unknown column(s): {', '.join(unknown)}.")

    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(reader, start=1):
        row = {
            (key or "").strip().lower(): value
            for key, value in raw.items()
            if key is not None and key.strip()
        }
        rows.append(row)

    summary = _import_rows(db, rows, origin="csv")
    summary.errors = notes + summary.errors  # keep informational notes visible
    return summary


def import_jobs_from_json(db: Session, content: Any) -> ImportSummary:
    """Import jobs from a JSON array (or the raw text of one)."""
    if isinstance(content, str):
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            return ImportSummary(errors=[f"Invalid JSON: {exc}"])
    else:
        data = content

    if not isinstance(data, list):
        return ImportSummary(
            errors=["The JSON payload must be an array of job objects."]
        )
    if not data:
        return ImportSummary(errors=["The JSON payload contains no job objects."])

    return _import_rows(db, data, origin="json")
