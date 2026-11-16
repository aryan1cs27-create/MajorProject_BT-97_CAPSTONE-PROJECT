"""Pydantic schemas for the Phase 4 explainability endpoints.

Every field is populated from the stored ``match_results`` row and the
original resume/job records — the schemas deliberately keep the evidence
vocabulary (``matched`` / ``missing`` / ``related`` / ``unknown`` / ``info``)
so the frontend can render statuses consistently.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# --------------------------------------------------------------------- shared


class ScoreFactor(BaseModel):
    """One component of the Phase 3 score, with its weight and contribution."""

    component: str                 # required | preferred | semantic
    label: str
    available: bool
    score: float | None = None     # 0..100 when available
    weight: float | None = None    # effective weight (after renormalisation)
    contribution: float | None = None  # weight * score, in score points
    reason: str | None = None      # why the component is unavailable


class EvidenceItem(BaseModel):
    """A single piece of evidence backing the explanation."""

    kind: str        # required_skill | preferred_skill | experience | education
                     # | certification | semantic | confidence | note
    status: str      # matched | missing | related | unknown | info
    label: str
    detail: str
    source: str      # where the statement comes from


class RelatedSkill(BaseModel):
    """A near-miss: adjacent skills in the resume, never counted as a match."""

    requirement: str
    resume_has: list[str] = Field(default_factory=list)


class SemanticSummary(BaseModel):
    enabled: bool
    score: float | None = None
    weight: float | None = None
    reason: str | None = None


class ExperienceSummary(BaseModel):
    status: str                      # meets | insufficient | unknown | not_required
    resume_years: float | None = None
    required_years: float | None = None
    detail: str


class EducationSummary(BaseModel):
    status: str                      # present | unknown | not_required
    resume_education: str | None = None
    job_mentions_education: bool = False
    detail: str


class CertificationSummary(BaseModel):
    job_certifications: list[str] = Field(default_factory=list)
    resume_certifications: list[str] = Field(default_factory=list)
    matched: list[str] = Field(default_factory=list)
    detail: str


# --------------------------------------------------------------- explanation


class MatchExplanationResponse(BaseModel):
    """GET /api/matches/{match_id}/explanation."""

    match_id: int
    resume_id: int
    job_id: int
    final_score: float
    interpretation: str
    generated_by: str = "deterministic"
    score_factors: list[ScoreFactor] = Field(default_factory=list)
    required_matched: list[str] = Field(default_factory=list)
    required_missing: list[str] = Field(default_factory=list)
    preferred_matched: list[str] = Field(default_factory=list)
    preferred_missing: list[str] = Field(default_factory=list)
    related_skills: list[RelatedSkill] = Field(default_factory=list)
    experience: ExperienceSummary
    education: EducationSummary
    certifications: CertificationSummary
    semantic: SemanticSummary
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    confidence: float | None = None


# ---------------------------------------------------------------- skill gaps


class SkillGapItem(BaseModel):
    """One job requirement, assessed against the extracted resume evidence."""

    skill: str
    requirement: str                 # required | preferred
    status: str                      # matched | missing | related
    priority: str                    # high | medium | low | none
    explanation: str
    related_resume_skills: list[str] = Field(default_factory=list)
    suggested_action: str | None = None


class UnknownItem(BaseModel):
    """An area that cannot be assessed reliably — never treated as failure."""

    area: str                        # experience | education | certification
    status: str = "unknown"
    explanation: str


class SkillGapResponse(BaseModel):
    """GET /api/matches/{match_id}/skill-gaps."""

    match_id: int
    resume_id: int
    job_id: int
    required_total: int = 0
    required_matched: int = 0
    required_missing: int = 0
    preferred_total: int = 0
    preferred_matched: int = 0
    preferred_missing: int = 0
    items: list[SkillGapItem] = Field(default_factory=list)
    unknowns: list[UnknownItem] = Field(default_factory=list)
    related_not_counted: list[RelatedSkill] = Field(default_factory=list)
    priority_counts: dict[str, int] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    generated_by: str = "deterministic"


# ------------------------------------------------------------ improvement plan


class PlanActivity(BaseModel):
    stage: str                       # start_now | practice_next | consider_later
    type: str                        # learning | practice | project | certification | clarification
    skill: str | None = None
    title: str
    detail: str
    estimate: str | None = None      # always a rough, generic estimate


class ImprovementPlanResponse(BaseModel):
    """GET /api/matches/{match_id}/improvement-plan."""

    match_id: int
    resume_id: int
    job_id: int
    generated_by: str = "deterministic"
    focus_skills: list[str] = Field(default_factory=list)
    existing_skills: list[str] = Field(default_factory=list)
    start_now: list[PlanActivity] = Field(default_factory=list)
    practice_next: list[PlanActivity] = Field(default_factory=list)
    consider_later: list[PlanActivity] = Field(default_factory=list)
    sequencing: str
    prioritisation: str
    disclaimers: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


# --------------------------------------------------------- resume suggestions


class ResumeSuggestion(BaseModel):
    category: str                     # highlight | strengthen | quantify | clarify | keyword
    title: str
    detail: str
    verify: str                       # what the user must verify before acting
    skill: str | None = None


class ResumeSuggestionsResponse(BaseModel):
    """GET /api/matches/{match_id}/resume-suggestions."""

    match_id: int
    resume_id: int
    job_id: int
    generated_by: str = "deterministic"
    suggestions: list[ResumeSuggestion] = Field(default_factory=list)
    keywords_present: list[str] = Field(default_factory=list)
    keywords_absent: list[str] = Field(default_factory=list)
    disclaimers: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)