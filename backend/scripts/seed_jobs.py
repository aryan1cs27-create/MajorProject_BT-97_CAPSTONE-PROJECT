"""Seed a few clearly-labelled sample jobs into MySQL.

Run from the backend/ folder (PowerShell):

    .\\.venv\\Scripts\\python.exe scripts\\seed_jobs.py

Idempotent: a job is only inserted when no row with the same title+company
exists, so re-running never creates duplicates. Seeded rows carry
``is_sample = True`` so the UI can label them as sample data.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.db.init import init_db  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.job import Job  # noqa: E402

SAMPLE_JOBS: list[dict] = [
    {
        "title": "Frontend Engineer",
        "company": "Northwind Digital",
        "location": "Amsterdam, NL",
        "description": (
            "Build and maintain customer-facing web applications with React and "
            "TypeScript. You will collaborate with designers and backend engineers "
            "to ship accessible, well-tested interfaces."
        ),
        "required_skills": ["React", "TypeScript", "CSS", "Git"],
        "preferred_skills": ["Tailwind CSS", "Vite", "Testing Library"],
        "minimum_experience": 2,
        "employment_type": "full-time",
    },
    {
        "title": "Data Analyst",
        "company": "Blue Harbor Retail",
        "location": "Remote",
        "description": (
            "Turn sales and operations data into clear insights. Responsibilities "
            "include building dashboards, maintaining SQL reporting models and "
            "presenting findings to non-technical stakeholders."
        ),
        "required_skills": ["SQL", "Excel", "Data Visualization"],
        "preferred_skills": ["Python", "pandas", "Power BI"],
        "minimum_experience": 1,
        "employment_type": "full-time",
    },
    {
        "title": "Backend Engineer (Python)",
        "company": "Northwind Digital",
        "location": "Berlin, DE",
        "description": (
            "Design and operate REST APIs in FastAPI with MySQL. You will own "
            "services end to end: schema design, implementation, testing and "
            "deployment."
        ),
        "required_skills": ["Python", "FastAPI", "MySQL", "REST"],
        "preferred_skills": ["SQLAlchemy", "Docker", "pytest"],
        "minimum_experience": 3,
        "employment_type": "full-time",
    },
    {
        "title": "Machine Learning Intern",
        "company": "Cedar Labs",
        "location": "Remote",
        "description": (
            "Support experimentation on text similarity and ranking models. "
            "A great fit for students comfortable with Python and basic NLP."
        ),
        "required_skills": ["Python", "scikit-learn"],
        "preferred_skills": ["PyTorch", "NLP", "Jupyter"],
        "minimum_experience": 0,
        "employment_type": "internship",
    },
]


def main() -> int:
    init_db()  # make sure the tables exist before seeding
    inserted = 0
    skipped = 0

    with SessionLocal() as db:
        for spec in SAMPLE_JOBS:
            existing = db.scalar(
                select(Job).where(Job.title == spec["title"], Job.company == spec["company"])
            )
            if existing is not None:
                skipped += 1
                continue
            db.add(Job(**spec, is_sample=True))
            inserted += 1
        db.commit()

    print(f"Sample jobs: {inserted} inserted, {skipped} already present (no duplicates).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
