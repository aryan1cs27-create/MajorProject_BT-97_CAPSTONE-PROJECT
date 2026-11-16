"""Application configuration loaded from environment variables.

All secrets must come from the environment (or a local .env file that is
never committed). Nothing here is ever echoed back in an API response.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[2]

# Load .env from the backend/ directory if it exists (local development only).
load_dotenv(BACKEND_DIR / ".env")

DEFAULT_MYSQL_URL = "mysql+pymysql://root:@127.0.0.1:3306/matchwise_db"


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    app_name: str = "MatchWise"
    app_version: str = "0.1.0"
    environment: str = field(
        default_factory=lambda: os.getenv("ENVIRONMENT", "development")
    )

    # MySQL connection (no credentials are ever exposed through the API).
    database_url: str = field(
        default_factory=lambda: os.getenv("DATABASE_URL", DEFAULT_MYSQL_URL)
    )

    # CORS: the Vite dev server by default. Vite uses 5173 and silently falls
    # back to 5174+ when the port is taken, so both localhost and 127.0.0.1
    # variants of those ports are allowed (specific origins, never "*").
    allowed_origins: list[str] = field(
        default_factory=lambda: _csv(
            os.getenv(
                "ALLOWED_ORIGINS",
                ",".join(
                    [
                        "http://localhost:5173",
                        "http://127.0.0.1:5173",
                        "http://localhost:5174",
                        "http://127.0.0.1:5174",
                        "http://localhost:5175",
                        "http://127.0.0.1:5175",
                    ]
                ),
            )
        )
    )

    # Local upload storage (kept outside the frontend directory and never
    # exposed through a public static route).
    upload_dir: Path = field(
        default_factory=lambda: Path(
            os.getenv("UPLOAD_DIR", str(BACKEND_DIR / "uploads"))
        ).expanduser()
    )

    # Configurable resume upload size limit (default 5 MB).
    max_upload_bytes: int = field(
        default_factory=lambda: int(os.getenv("UPLOAD_MAX_BYTES", str(5 * 1024 * 1024)))
    )

    allowed_resume_extensions: tuple[str, ...] = (".pdf", ".docx")

    # ---------------- API authentication (Phase 5.1) ----------------
    # A single locally-operated token. When set, every /api/resumes*,
    # /api/jobs* and /api/matches* request must send it as
    # ``Authorization: Bearer <token>`` or ``X-API-Token: <token>``.
    # When unset the API stays open (local development only) and a warning is
    # logged at startup — see docs/development-plan.md.
    api_token: str | None = field(
        default_factory=lambda: (os.getenv("API_TOKEN") or "").strip() or None
    )

    # ---------------- Matching engine (Phase 3) ----------------
    # Configurable scoring weights (must sum to 1.0; they are renormalised
    # over the *available* components at scoring time).
    match_weight_required: float = field(
        default_factory=lambda: float(os.getenv("MATCH_WEIGHT_REQUIRED", "0.60"))
    )
    match_weight_preferred: float = field(
        default_factory=lambda: float(os.getenv("MATCH_WEIGHT_PREFERRED", "0.15"))
    )
    match_weight_semantic: float = field(
        default_factory=lambda: float(os.getenv("MATCH_WEIGHT_SEMANTIC", "0.25"))
    )

    # Fuzzy skill matching (typo tolerance). 0 disables it.
    match_fuzzy_threshold: float = field(
        default_factory=lambda: float(os.getenv("MATCH_FUZZY_THRESHOLD", "0.9"))
    )

    # Sentence-transformers semantic similarity; gracefully disabled when the
    # model cannot be installed or loaded (see app/services/semantic.py).
    semantic_enabled: bool = field(
        default_factory=lambda: os.getenv("MATCH_SEMANTIC_ENABLED", "true").lower()
        in {"1", "true", "yes", "on"}
    )
    semantic_model: str = field(
        default_factory=lambda: os.getenv(
            "MATCH_SEMANTIC_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
        )
    )

    # ---------------- Feedback-aware ranking (Phase 6) ----------------
    # Feedback only ever applies a **bounded adjustment** on top of the stored
    # Phase 3 score. With no feedback every signal is exactly 0, so the blended
    # ranking is identical to the engine ranking (see app/services/ranking.py).
    feedback_enabled: bool = field(
        default_factory=lambda: os.getenv("FEEDBACK_RANKING_ENABLED", "true").lower()
        in {"1", "true", "yes", "on"}
    )
    # Strict maximum absolute adjustment, in score points (0..100 scale).
    feedback_max_adjustment: float = field(
        default_factory=lambda: float(os.getenv("FEEDBACK_MAX_ADJUSTMENT", "10"))
    )
    feedback_weight_job: float = field(
        default_factory=lambda: float(os.getenv("FEEDBACK_WEIGHT_JOB", "0.5"))
    )
    feedback_weight_category: float = field(
        default_factory=lambda: float(os.getenv("FEEDBACK_WEIGHT_CATEGORY", "0.3"))
    )
    feedback_weight_company: float = field(
        default_factory=lambda: float(os.getenv("FEEDBACK_WEIGHT_COMPANY", "0.2"))
    )
    # Additive smoothing constant: a single event is damped, several consistent
    # events converge towards the full signal.
    feedback_smoothing: float = field(
        default_factory=lambda: float(os.getenv("FEEDBACK_SMOOTHING", "2"))
    )

    @property
    def feedback_weights(self) -> dict[str, float]:
        return {
            "job": self.feedback_weight_job,
            "skill_category": self.feedback_weight_category,
            "company": self.feedback_weight_company,
        }

    @property
    def match_weights(self) -> dict[str, float]:
        return {
            "required": self.match_weight_required,
            "preferred": self.match_weight_preferred,
            "semantic": self.match_weight_semantic,
        }

    @property
    def is_development(self) -> bool:
        return self.environment.lower() in {"development", "dev", "local"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
