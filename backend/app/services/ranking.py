"""Feedback-aware ranking (Phase 6).

The Phase 3 engine remains the source of truth. Feedback only ever adds a
**bounded, explainable adjustment** on top of the stored score:

    blended_score = clamp(engine_score + adjustment, 0, 100)
    adjustment    = clamp(cap * Σ(weight_i * signal_i), -cap, +cap)

Signals
-------
``job``            latest feedback event for this exact resume↔job pair.
                   ``verdict`` (helpful +1 / not_relevant -1) and ``outcome``
                   (interview +1, shortlisted +0.6, applied +0.4; ``rejected``
                   is neutral — a rejection is not evidence that the match was
                   wrong) are averaged when both are present.
``skill_category`` feedback on *other* jobs of the same resume that share a
                   taxonomy skill category with this job, e.g. backend /
                   frontend / cloud. Verdict only.
``company``        feedback on other jobs at the same company (case-insensitive).

Each bucket uses additive smoothing::

    signal = (positive - negative) / (positive + negative + K)

so one event can never swing the ranking, contradictory feedback cancels out,
and with no events the signal is exactly ``0.0``. ``K`` is ``FEEDBACK_SMOOTHING``
(default 2), the weights default to job 0.5 / category 0.3 / company 0.2 and the
cap defaults to ±10 score points — all configurable via ``Settings``.

Zero-feedback guarantee
-----------------------
With no feedback every signal is ``0.0``, so ``adjustment`` is exactly ``0.0``,
``blended_score == engine_score`` bit-for-bit, and sorting by
``(-blended_score, match id)`` gives the same order as the engine ranking
(``-final_score, match id``). No rounding, tie-break or float drift is
introduced. This is covered by ``tests/test_feedback.py``.

Nothing here mutates persisted rows: the engine score, weights and components in
``match_results`` stay untouched.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.feedback import (
    OUTCOME_APPLIED,
    OUTCOME_INTERVIEW,
    OUTCOME_SHORTLISTED,
    VERDICT_HELPFUL,
    VERDICT_NOT_RELEVANT,
    MatchFeedback,
)
from app.models.job import Job
from app.services.features import build_job_profile
from app.services.taxonomy import get_taxonomy

SIGNAL_LABELS = {
    "job": "This job",
    "skill_category": "Similar skill categories",
    "company": "This company",
}

#: Outcome contribution to the job-level signal. ``rejected`` is deliberately
#: absent (0.0): the process ending says nothing about the match's usefulness.
OUTCOME_SIGNAL = {
    OUTCOME_INTERVIEW: 1.0,
    OUTCOME_SHORTLISTED: 0.6,
    OUTCOME_APPLIED: 0.4,
}

NOTE_SCORE_UNTOUCHED = (
    "Feedback adjusts ranking only — the stored engine score, weights and "
    "component breakdown are never modified."
)
NOTE_CAP = (
    "The adjustment is capped, so feedback can reorder close matches but can "
    "never overturn the evidence behind a large score gap."
)


# --------------------------------------------------------------------- index
@dataclass
class FeedbackIndex:
    """Every feedback event for one resume, pre-aggregated for ranking."""

    resume_id: int
    latest: dict[int, MatchFeedback] = field(default_factory=dict)      # job_id -> latest
    events: list[MatchFeedback] = field(default_factory=list)
    by_category: dict[str, list[int]] = field(default_factory=dict)     # category -> [pos, neg]
    by_company: dict[str, list[int]] = field(default_factory=dict)      # company -> [pos, neg]

    @property
    def is_empty(self) -> bool:
        return not self.events

    def coverage(self, matches_total: int) -> float:
        if matches_total <= 0:
            return 0.0
        return round(len(self.latest) / matches_total, 4)


def job_categories(job: Job) -> list[str]:
    """Taxonomy categories of a job's required + preferred skills (sorted)."""
    taxonomy = get_taxonomy()
    profile = build_job_profile(job)
    categories = {
        taxonomy.category_of(key)
        for key in profile.required_keys + profile.preferred_keys
    }
    return sorted(category for category in categories if category)


def _company_key(company: str | None) -> str:
    return (company or "").strip().lower()


def build_index(db: Session, resume_id: int) -> FeedbackIndex:
    """Load and aggregate every feedback event for a resume (oldest first)."""
    events = list(
        db.scalars(
            select(MatchFeedback)
            .where(MatchFeedback.resume_id == resume_id)
            .order_by(MatchFeedback.created_at, MatchFeedback.id)
        ).all()
    )

    index = FeedbackIndex(resume_id=resume_id, events=events)
    for event in events:
        index.latest[event.job_id] = event  # ascending order -> last wins

        job = event.job
        if job is None:  # pragma: no cover - FK cascade removes these rows
            continue

        positive = 1 if event.verdict == VERDICT_HELPFUL else 0
        negative = 1 if event.verdict == VERDICT_NOT_RELEVANT else 0

        for category in job_categories(job):
            bucket = index.by_category.setdefault(category, [0, 0])
            bucket[0] += positive
            bucket[1] += negative

        key = _company_key(job.company)
        if key:
            bucket = index.by_company.setdefault(key, [0, 0])
            bucket[0] += positive
            bucket[1] += negative

    return index


# ------------------------------------------------------------------- signals
def _bucket_signal(positive: int, negative: int, smoothing: float) -> float:
    """Smoothed signal in -1..1; exactly 0.0 when there is no evidence."""
    total = positive + negative
    if total == 0:
        return 0.0
    denominator = total + max(smoothing, 0.0)
    if denominator <= 0:
        return 0.0
    return round((positive - negative) / denominator, 4)


def _job_signal(event: MatchFeedback | None) -> tuple[float, int, int, str]:
    if event is None:
        return 0.0, 0, 0, "No feedback for this job yet."

    parts: list[float] = []
    bits: list[str] = []
    if event.verdict == VERDICT_HELPFUL:
        parts.append(1.0)
        bits.append("marked helpful")
    elif event.verdict == VERDICT_NOT_RELEVANT:
        parts.append(-1.0)
        bits.append("marked not relevant")

    outcome_signal = OUTCOME_SIGNAL.get(event.outcome or "")
    if outcome_signal is not None:
        parts.append(outcome_signal)
        bits.append(f"outcome {event.outcome}")
    elif event.outcome:
        bits.append(f"outcome {event.outcome} (neutral for ranking)")

    raw = round(sum(parts) / len(parts), 4) if parts else 0.0

    positive = 1 if event.verdict == VERDICT_HELPFUL else 0
    negative = 1 if event.verdict == VERDICT_NOT_RELEVANT else 0
    detail = (
        f"Latest feedback for this job: {', '.join(bits)}." if bits
        else "Latest feedback for this job carries no ranking signal."
    )
    return raw, positive, negative, detail


def _category_signal(
    job: Job, index: FeedbackIndex, smoothing: float
) -> tuple[float, int, int, str, list[str]]:
    categories = job_categories(job)

    seen: list[str] = []
    values: list[float] = []
    positive = negative = 0
    for category in categories:
        pos, neg = index.by_category.get(category, [0, 0])
        if pos + neg == 0:
            continue
        seen.append(category)
        positive += pos
        negative += neg
        values.append(_bucket_signal(pos, neg, smoothing))

    if not values:
        return 0.0, 0, 0, (
            "No feedback yet for other jobs in this job's skill categories."
            if categories else "This job has no taxonomy skill categories."
        ), categories

    raw = round(sum(values) / len(values), 4)
    detail = (
        f"{positive} helpful / {negative} not relevant on other jobs in "
        f"{', '.join(seen)}."
    )
    return raw, positive, negative, detail, categories


def _company_signal(
    job: Job, index: FeedbackIndex, smoothing: float
) -> tuple[float, int, int, str]:
    key = _company_key(job.company)
    if not key:
        return 0.0, 0, 0, "This job has no company to compare against."
    pos, neg = index.by_company.get(key, [0, 0])
    if pos + neg == 0:
        return 0.0, 0, 0, "No feedback yet for other jobs at this company."
    raw = _bucket_signal(pos, neg, smoothing)
    detail = f"{pos} helpful / {neg} not relevant on other jobs at {job.company}."
    return raw, pos, neg, detail


def normalised_weights(settings: Settings) -> dict[str, float]:
    raw = {name: max(value, 0.0) for name, value in settings.feedback_weights.items()}
    total = sum(raw.values())
    if total <= 0:
        return {name: 0.0 for name in raw}
    return {name: round(value / total, 4) for name, value in raw.items()}


# ------------------------------------------------------------------ decision
@dataclass
class RankAdjustment:
    engine_score: float
    adjustment: float
    blended_score: float
    explanation: dict

    def as_dict(self) -> dict:
        return self.explanation


def _describe(adjustment: float, signals: list[dict], cap: float, empty: bool) -> str:
    if empty:
        return (
            "No feedback yet — the original engine ranking is used unchanged."
        )
    if adjustment == 0:
        return (
            "Feedback exists but produced no net adjustment (signals cancel out "
            f"or fall below rounding); cap ±{cap:g} points."
        )

    parts: list[str] = []
    for signal in signals:
        if round(signal["points"], 2) == 0:
            continue
        sign = "+" if signal["points"] > 0 else ""
        parts.append(f"{signal['label'].lower()} {sign}{signal['points']:.1f}")
    body = "; ".join(parts) if parts else "no individual signal dominated"
    direction = "increased" if adjustment > 0 else "reduced"
    return (
        f"Feedback {direction} the engine score by {abs(adjustment):.1f} points "
        f"({body}); cap ±{cap:g}."
    )


def adjust(
    engine_score: float,
    job: Job,
    index: FeedbackIndex,
    settings: Settings | None = None,
) -> RankAdjustment:
    """Bounded feedback adjustment for one stored match."""
    settings = settings or get_settings()
    cap = max(float(settings.feedback_max_adjustment), 0.0)
    smoothing = max(float(settings.feedback_smoothing), 0.0)
    engine = round(float(engine_score), 2)

    weights = normalised_weights(settings)
    latest = index.latest.get(job.id)

    job_raw, job_pos, job_neg, job_detail = _job_signal(latest)
    cat_raw, cat_pos, cat_neg, cat_detail, _ = _category_signal(job, index, smoothing)
    comp_raw, comp_pos, comp_neg, comp_detail = _company_signal(job, index, smoothing)

    disabled_reason = None
    if not settings.feedback_enabled:
        disabled_reason = "Feedback ranking is disabled by configuration."
    elif cap <= 0:
        disabled_reason = "The feedback adjustment cap is zero."

    raw_signals = [
        ("job", job_raw, job_pos, job_neg, job_detail),
        ("skill_category", cat_raw, cat_pos, cat_neg, cat_detail),
        ("company", comp_raw, comp_pos, comp_neg, comp_detail),
    ]

    signal_items: list[dict] = []
    raw_points_total = 0.0
    for name, raw, positive, negative, detail in raw_signals:
        weight = 0.0 if disabled_reason else weights.get(name, 0.0)
        points = round(weight * raw * cap, 2)
        raw_points_total += points
        signal_items.append(
            {
                "signal": name,
                "label": SIGNAL_LABELS[name],
                "weight": round(weight, 4),
                "raw": raw,
                "points": points,
                "positive": int(positive),
                "negative": int(negative),
                "detail": detail,
            }
        )

    capped = abs(raw_points_total) > cap + 1e-9
    adjustment = round(max(-cap, min(cap, raw_points_total)), 2)
    blended = round(min(100.0, max(0.0, engine + adjustment)), 2)
    # Bounds clamping (score can't leave 0..100) means the applied adjustment
    # may differ from the calculated one; report what actually happened.
    applied = round(blended - engine, 2)
    if abs(applied - adjustment) > 1e-9:
        adjustment = applied

    notes = [NOTE_SCORE_UNTOUCHED, NOTE_CAP]
    if disabled_reason:
        notes.append(disabled_reason)

    summary = _describe(adjustment, signal_items, cap, index.is_empty)
    if disabled_reason and adjustment == 0:
        summary = f"{disabled_reason} The engine ranking is unchanged."

    explanation = {
        "mode": "blended",
        "enabled": not bool(disabled_reason),
        "engine_score": engine,
        "adjustment": adjustment,
        "blended_score": blended,
        "max_adjustment": cap,
        "capped": capped,
        "signals": signal_items,
        "summary": summary,
        "notes": notes,
    }
    return RankAdjustment(
        engine_score=engine,
        adjustment=adjustment,
        blended_score=blended,
        explanation=explanation,
    )


def rank_rows(
    rows: list,
    index: FeedbackIndex,
    settings: Settings | None = None,
) -> tuple[list, dict[int, RankAdjustment]]:
    """Sort stored match rows by blended score.

    Tie-break is the match id — exactly the engine listing's tie-break — so a
    zero-feedback blended listing is ordered identically to the engine listing.
    """
    adjustments = {
        row.id: adjust(row.final_score, row.job, index, settings) for row in rows
    }
    ordered = sorted(rows, key=lambda row: (-adjustments[row.id].blended_score, row.id))
    return ordered, adjustments
