"""Pydantic schemas for Phase 6 feedback and feedback-aware ranking."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Verdict = Literal["helpful", "not_relevant"]
Outcome = Literal["shortlisted", "applied", "interview", "rejected"]


class FeedbackCreate(BaseModel):
    """POST /api/matches/{match_id}/feedback — resume/job ids are server-side."""

    verdict: Verdict
    relevance: int | None = Field(
        default=None, ge=1, le=5, description="Optional relevance rating, 1 (low) – 5 (high)."
    )
    outcome: Outcome | None = None
    reason: str | None = Field(default=None, max_length=500)

    @field_validator("reason")
    @classmethod
    def _strip_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class FeedbackOut(BaseModel):
    """One stored feedback event."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    match_id: int
    resume_id: int
    job_id: int
    verdict: str
    relevance: int | None = None
    outcome: str | None = None
    reason: str | None = None
    created_at: datetime


class RankSignal(BaseModel):
    """One contribution to the feedback adjustment — fully traceable."""

    signal: str                 # job | skill_category | company
    label: str
    weight: float               # normalised weight (0..1)
    raw: float                  # signal strength in -1..1 before weighting
    points: float               # weight * raw * cap, in score points
    positive: int = 0           # supporting feedback events
    negative: int = 0
    detail: str


class RankExplanation(BaseModel):
    """Why a match sits where it does in feedback-adjusted ranking."""

    mode: str = "blended"                   # engine | blended
    enabled: bool = True
    engine_score: float                     # stored Phase 3 score (never changed)
    adjustment: float                       # applied adjustment, after the cap
    blended_score: float
    max_adjustment: float
    capped: bool = False
    signals: list[RankSignal] = Field(default_factory=list)
    summary: str
    notes: list[str] = Field(default_factory=list)


class FeedbackSummaryResponse(BaseModel):
    """GET /api/matches/resume/{resume_id}/feedback-summary."""

    resume_id: int
    total_events: int = 0
    helpful_count: int = 0
    not_relevant_count: int = 0
    average_relevance: float | None = None
    outcome_distribution: dict[str, int] = Field(default_factory=dict)
    most_helpful_skill_categories: list[dict] = Field(default_factory=list)
    most_rejected_skill_categories: list[dict] = Field(default_factory=list)
    feedback_coverage: float = 0.0          # 0..1 of stored matches with feedback
    matches_total: int = 0
    matches_with_feedback: int = 0
    notes: list[str] = Field(default_factory=list)
