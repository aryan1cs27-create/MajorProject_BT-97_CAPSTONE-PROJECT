"""Pydantic schemas for match endpoints."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.feedback import FeedbackOut, RankExplanation
from app.schemas.job import JobOut
from app.schemas.resume import ResumePreview


class MatchOut(BaseModel):
    """One scored resume↔job pair, returned as JSON numbers."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    resume_id: int
    job_id: int
    final_score: float
    required_score: float | None = None
    preferred_score: float | None = None
    semantic_score: float | None = None
    matched_skills: list[str] = Field(default_factory=list)
    missing_required: list[str] = Field(default_factory=list)
    missing_preferred: list[str] = Field(default_factory=list)
    weights: dict[str, float] = Field(default_factory=dict)
    components: dict[str, float | None] = Field(default_factory=dict)
    discovered_skills: list[dict] = Field(default_factory=list)
    signals: dict = Field(default_factory=dict)
    confidence: float | None = None
    created_at: datetime
    updated_at: datetime
    job: JobOut | None = None
    resume: ResumePreview | None = None

    # ---- Phase 6: feedback-aware ranking (all optional, additive) ----
    # ``final_score`` above is ALWAYS the stored engine score. These fields
    # only appear when the feedback-adjusted ranking is requested.
    blended_score: float | None = None
    feedback_adjustment: float | None = None
    rank_explanation: RankExplanation | None = None
    # Latest feedback event for this resume↔job pair (history stays in the DB).
    latest_feedback: FeedbackOut | None = None


class SemanticInfo(BaseModel):
    available: bool
    reason: str | None = None


class MatchListResponse(BaseModel):
    items: list[MatchOut]
    total: int
    page: int
    page_size: int
    semantic: SemanticInfo
    # Phase 6: "engine" (stored Phase 3 order) or "blended" (feedback-adjusted).
    rank: str = "engine"
    feedback_coverage: float = 0.0


class CalculateRequest(BaseModel):
    """Optional subset of jobs to evaluate; defaults to every job."""

    job_ids: list[int] | None = None


class CalculateResponse(BaseModel):
    items: list[MatchOut]
    jobs_evaluated: int
    semantic: SemanticInfo
    configured_weights: dict[str, float]
