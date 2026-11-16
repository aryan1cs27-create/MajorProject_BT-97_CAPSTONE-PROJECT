"""Match endpoints: scoring, ranked listings, explanations and feedback.

Authentication (Phase 5.1): the whole router is guarded by ``require_api_token``
(active when ``API_TOKEN`` is configured).
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import require_api_token
from app.db.session import get_db
from app.schemas.explanation import (
    ImprovementPlanResponse,
    MatchExplanationResponse,
    ResumeSuggestionsResponse,
    SkillGapResponse,
)
from app.schemas.feedback import (
    FeedbackCreate,
    FeedbackOut,
    FeedbackSummaryResponse,
    RankExplanation,
)
from app.schemas.job import JobOut
from app.schemas.match import (
    CalculateRequest,
    CalculateResponse,
    MatchListResponse,
    MatchOut,
    SemanticInfo,
)
from app.schemas.resume import ResumePreview
from app.services import explanation as explanation_service
from app.services import feedback as feedback_service
from app.services import matching as matching_service
from app.services import ranking as ranking_service

router = APIRouter(
    prefix="/api/matches",
    tags=["matches"],
    dependencies=[Depends(require_api_token)],
)

Db = Annotated[Session, Depends(get_db)]


def _to_out(
    row,
    *,
    include_job: bool,
    include_resume: bool,
    adjustment: ranking_service.RankAdjustment | None = None,
    feedback=None,
) -> MatchOut:
    details = row.details or {}
    out = MatchOut(
        id=row.id,
        resume_id=row.resume_id,
        job_id=row.job_id,
        final_score=float(row.final_score),
        required_score=float(row.required_score) if row.required_score is not None else None,
        preferred_score=float(row.preferred_score) if row.preferred_score is not None else None,
        semantic_score=float(row.semantic_score) if row.semantic_score is not None else None,
        matched_skills=list(row.matched_skills or []),
        missing_required=list(row.missing_required or []),
        missing_preferred=list(row.missing_preferred or []),
        weights={k: float(v) for k, v in (row.weights or {}).items()},
        components={k: (None if v is None else float(v)) for k, v in (details.get("components") or {}).items()},
        discovered_skills=list(details.get("discovered_skills") or []),
        signals=row.signals or {},
        confidence=float(row.confidence) if row.confidence is not None else None,
        created_at=row.created_at,
        updated_at=row.updated_at,
        blended_score=None if adjustment is None else adjustment.blended_score,
        feedback_adjustment=None if adjustment is None else adjustment.adjustment,
        rank_explanation=(
            None if adjustment is None else RankExplanation(**adjustment.explanation)
        ),
        latest_feedback=None if feedback is None else FeedbackOut.model_validate(feedback),
    )
    if include_job and row.job is not None:
        out.job = JobOut.model_validate(row.job)
    if include_resume and row.resume is not None:
        preview = ResumePreview(
            id=row.resume.id,
            candidate_name=row.resume.candidate_name,
            original_filename=row.resume.original_filename,
            file_type=row.resume.file_type,
            file_size=row.resume.file_size,
            processing_status=row.resume.processing_status,
            uploaded_at=row.resume.uploaded_at,
            text_preview=" ".join((row.resume.extracted_text or "").split())[:240],
        )
        out.resume = preview
    return out


def _semantic() -> SemanticInfo:
    info = matching_service.semantic_info()
    return SemanticInfo(available=info["available"], reason=info["reason"])


@router.post(
    "/calculate/{resume_id}",
    response_model=CalculateResponse,
    summary="Calculate matches for a resume (ranked jobs)",
)
def calculate(
    resume_id: int,
    db: Db,
    payload: CalculateRequest | None = None,
) -> CalculateResponse:
    """Scores the resume against jobs and persists one row per job pair."""
    job_ids = payload.job_ids if payload else None
    rows, evaluated = matching_service.calculate_matches_for_resume(db, resume_id, job_ids)
    index = ranking_service.build_index(db, resume_id)
    return CalculateResponse(
        items=[
            _to_out(
                row,
                include_job=True,
                include_resume=False,
                feedback=index.latest.get(row.job_id),
            )
            for row in rows
        ],
        jobs_evaluated=evaluated,
        semantic=_semantic(),
        configured_weights=get_settings().match_weights,
    )


@router.get(
    "/resume/{resume_id}",
    response_model=MatchListResponse,
    summary="Stored ranked jobs for a resume (engine or feedback-adjusted)",
)
def matches_for_resume(
    resume_id: int,
    db: Db,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    rank: Annotated[
        Literal["engine", "blended"],
        Query(description="engine = stored Phase 3 order; blended = feedback-adjusted"),
    ] = "engine",
) -> MatchListResponse:
    """Stored matches, optionally reordered by the feedback-aware ranking.

    ``rank=engine`` (default) keeps the exact Phase 3 query and ordering —
    adding feedback never changes it. ``rank=blended`` applies the bounded
    adjustment from ``app.services.ranking``; with no feedback the result is
    byte-for-byte identical to ``rank=engine``. Stored scores are never mutated.
    """
    index = ranking_service.build_index(db, resume_id)

    if rank == "engine":
        rows, total = matching_service.matches_for_resume(db, resume_id, page, page_size)
        items = [
            _to_out(
                row,
                include_job=True,
                include_resume=False,
                feedback=index.latest.get(row.job_id),
            )
            for row in rows
        ]
        return MatchListResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            semantic=_semantic(),
            rank="engine",
            feedback_coverage=index.coverage(total),
        )

    all_rows = matching_service.stored_matches_for_resume(db, resume_id)
    ordered, adjustments = ranking_service.rank_rows(all_rows, index)
    total = len(ordered)
    start = (page - 1) * page_size
    window = ordered[start : start + page_size]
    return MatchListResponse(
        items=[
            _to_out(
                row,
                include_job=True,
                include_resume=False,
                adjustment=adjustments[row.id],
                feedback=index.latest.get(row.job_id),
            )
            for row in window
        ],
        total=total,
        page=page,
        page_size=page_size,
        semantic=_semantic(),
        rank="blended",
        feedback_coverage=index.coverage(total),
    )


@router.get(
    "/job/{job_id}",
    response_model=MatchListResponse,
    summary="Stored ranked resumes for a job",
)
def matches_for_job(
    job_id: int,
    db: Db,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> MatchListResponse:
    rows, total = matching_service.matches_for_job(db, job_id, page, page_size)
    return MatchListResponse(
        items=[_to_out(row, include_job=False, include_resume=True) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
        semantic=_semantic(),
    )


@router.get(
    "/resume/{resume_id}/feedback-summary",
    response_model=FeedbackSummaryResponse,
    summary="Aggregated feedback for a resume",
)
def resume_feedback_summary(resume_id: int, db: Db) -> FeedbackSummaryResponse:
    """Counts, average relevance, outcomes, category tendencies and coverage.

    Everything is derived from the feedback events themselves — no score is
    inferred or recalculated here.
    """
    return FeedbackSummaryResponse.model_validate(
        feedback_service.feedback_summary(db, resume_id)
    )


# ---------------------------------------------------------------- Phase 4
# Declared before GET /{match_id} so the specific paths always win.


@router.get(
    "/{match_id}/explanation",
    response_model=MatchExplanationResponse,
    summary="Evidence-based explanation of one match",
)
def match_explanation(match_id: int, db: Db) -> MatchExplanationResponse:
    """Why the score is what it is, with strengths, gaps and limitations."""
    return MatchExplanationResponse.model_validate(
        explanation_service.explain_match(db, match_id)
    )


@router.get(
    "/{match_id}/skill-gaps",
    response_model=SkillGapResponse,
    summary="Structured required/preferred skill-gap analysis",
)
def match_skill_gaps(match_id: int, db: Db) -> SkillGapResponse:
    return SkillGapResponse.model_validate(
        explanation_service.skill_gap_analysis(db, match_id)
    )


@router.get(
    "/{match_id}/improvement-plan",
    response_model=ImprovementPlanResponse,
    summary="Personalised learning and portfolio plan",
)
def match_improvement_plan(match_id: int, db: Db) -> ImprovementPlanResponse:
    return ImprovementPlanResponse.model_validate(
        explanation_service.improvement_plan(db, match_id)
    )


@router.get(
    "/{match_id}/resume-suggestions",
    response_model=ResumeSuggestionsResponse,
    summary="Truthful resume improvement suggestions for this job",
)
def match_resume_suggestions(match_id: int, db: Db) -> ResumeSuggestionsResponse:
    return ResumeSuggestionsResponse.model_validate(
        explanation_service.resume_suggestions(db, match_id)
    )


# ---------------------------------------------------------------- Phase 6
@router.post(
    "/{match_id}/feedback",
    response_model=FeedbackOut,
    status_code=201,
    summary="Submit feedback for one match (append-only, idempotent)",
)
def submit_feedback(
    match_id: int,
    payload: FeedbackCreate,
    db: Db,
    response: Response,
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            max_length=128,
            description="Optional client key; the same key returns the original event.",
        ),
    ] = None,
) -> FeedbackOut:
    """Append a feedback event for the stored match.

    ``resume_id``/``job_id`` are taken from the match row, so a request can
    never attach feedback to an inconsistent pair. Replaying an identical
    submission (same payload, or the same ``Idempotency-Key``) returns the
    original event with HTTP 200 and an ``Idempotent-Replay: true`` header
    instead of duplicating history.
    """
    event, created = feedback_service.submit_feedback(db, match_id, payload, idempotency_key)
    if not created:
        response.status_code = 200
        response.headers["Idempotent-Replay"] = "true"
    return FeedbackOut.model_validate(event)


@router.get("/{match_id}", response_model=MatchOut, summary="Get one match result")
def get_match(match_id: int, db: Db) -> MatchOut:
    row = matching_service.get_match(db, match_id)
    latest = feedback_service.latest_feedback(db, row.resume_id).get(row.job_id)
    return _to_out(
        row, include_job=True, include_resume=True, feedback=latest
    )
