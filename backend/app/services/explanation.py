"""Phase 4 — explainable matching, skill gaps, improvement plan, resume advice.

Everything in this module is **deterministic**: each statement is derived from

* the stored ``match_results`` row (Phase 3 engine output: scores, effective
  weights, matched/missing skills, match types, signals, notes), and
* the original ``resumes`` and ``jobs`` rows.

Rules honoured throughout:

* never claim the candidate has a skill, qualification, certification or
  experience the extracted resume data does not support;
* a skill marked ``missing`` means "not found in the extracted text" — never
  proof that the person lacks it;
* taxonomy ``related`` skills are surfaced as near-misses and are never
  counted as matches;
* experience / education that the documents do not state stays ``unknown``;
* the Phase 3 score, weights and persisted rows are never modified.

No LLM, no network calls: output is stable for the same database rows.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.job import Job
from app.models.match import MatchResult
from app.models.resume import Resume
from app.services import matching as matching_service
from app.services.features import (
    JobProfile,
    ResumeProfile,
    build_job_profile,
    build_resume_profile,
)

GENERATED_BY = "deterministic"

CAVEAT = (
    "“Missing” means the skill was not found in the extracted resume text — it may be "
    "absent from the resume or missed by extraction. It is not proof that the person "
    "lacks the skill."
)

OPTIONAL_NOTE = (
    "Preferred skills are optional: they never count as required and are not treated "
    "as mandatory."
)

RELATED_NOTE = (
    "Related skills are near-misses only — adjacent experience helps, but it is never "
    "counted as an exact match."
)

COMPONENT_LABELS = {
    "required": "Required skills",
    "preferred": "Preferred skills",
    "semantic": "Semantic similarity",
}


# --------------------------------------------------------------------- context
@dataclass
class _Ctx:
    row: MatchResult
    resume: Resume
    job: Job
    resume_profile: ResumeProfile
    job_profile: JobProfile
    details: dict
    signals: dict


def _load(db: Session, match_id: int) -> _Ctx:
    """Fetch a stored match plus its resume/job. Raises 404 for unknown ids."""
    row = matching_service.get_match(db, match_id)
    resume = row.resume if row.resume_id else None
    resume = resume or db.get(Resume, row.resume_id)
    job = row.job if row.job_id else None
    job = job or db.get(Job, row.job_id)
    if resume is None or job is None:
        # FKs cascade, so this should not happen — fail honestly anyway.
        from app.core.errors import not_found

        raise not_found("Match", match_id)

    details = row.details or {}
    return _Ctx(
        row=row,
        resume=resume,
        job=job,
        resume_profile=build_resume_profile(resume),
        job_profile=build_job_profile(job),
        details=details,
        signals=row.signals or {},
    )


@dataclass
class _Requirement:
    requirement: str   # required | preferred
    label: str
    key: str
    status: str        # matched | missing | related
    match_type: str    # exact | alias | fuzzy | related | none
    related: list[str]


def _requirement_rows(ctx: _Ctx) -> list[_Requirement]:
    """Every job requirement with its assessed status, in job order."""
    signals_exp = ctx.signals.get("related_skill_near_misses") or []
    related_map = {
        str(item.get("missing")): [str(s) for s in item.get("resume_has") or []]
        for item in signals_exp
    }

    rows: list[_Requirement] = []
    for requirement, keys, labels, types_key, matched_key in (
        (
            "required",
            ctx.job_profile.required_keys,
            ctx.job_profile.required_labels,
            "required_match_types",
            "matched_required",
        ),
        (
            "preferred",
            ctx.job_profile.preferred_keys,
            ctx.job_profile.preferred_labels,
            "preferred_match_types",
            "matched_preferred",
        ),
    ):
        match_types = ctx.details.get(types_key) or {}
        matched_labels = set(ctx.details.get(matched_key) or [])
        for key in keys:
            label = labels.get(key, key)
            related = related_map.get(label, [])
            if label in matched_labels:
                status = "matched"
                match_type = str(match_types.get(key) or "exact")
            elif related:
                status = "related"
                match_type = "related"
            else:
                status = "missing"
                match_type = "none"
            rows.append(
                _Requirement(
                    requirement=requirement,
                    label=label,
                    key=key,
                    status=status,
                    match_type=match_type,
                    related=related,
                )
            )
    return rows


# ------------------------------------------------------------- shared sections
def _score_factors(ctx: _Ctx) -> list[dict]:
    components = ctx.details.get("components") or {}
    configured = ctx.details.get("configured_weights") or {}
    effective = ctx.row.weights or {}
    unavailable = ctx.details.get("unavailable_components") or {}

    factors: list[dict] = []
    for name in ("required", "preferred", "semantic"):
        value = components.get(name)
        weight = effective.get(name)
        available = value is not None and weight is not None
        score = None if value is None else round(float(value), 2)
        factor_weight = None if weight is None else round(float(weight), 4)
        contribution = (
            None if not available else round(float(weight) * float(value), 2)
        )
        factors.append(
            {
                "component": name,
                "label": COMPONENT_LABELS[name],
                "available": available,
                "score": score,
                "weight": factor_weight,
                "contribution": contribution,
                "reason": None if available else str(
                    unavailable.get(name)
                    or "Component unavailable for this match."
                ),
            }
        )
    _ = configured  # configured weights are reported through notes/weights
    return factors


def _interpretation(score: float) -> str:
    if score >= 80:
        return (
            "Strong match — the resume covers most of this job's requirements "
            "with supporting evidence."
        )
    if score >= 60:
        return (
            "Good match — most required skills are covered, with a limited "
            "number of gaps."
        )
    if score >= 40:
        return (
            "Partial match — a substantial part of the requirements was not "
            "found in the resume."
        )
    if score >= 20:
        return (
            "Weak match — most requirements were not found in the resume; "
            "review the gaps below."
        )
    return (
        "Very low match — little overlap between the extracted resume and "
        "this posting."
    )


def _experience(ctx: _Ctx) -> dict:
    exp = ctx.signals.get("experience") or {}
    status = str(exp.get("status") or "unknown")
    resume_years = exp.get("resume_years")
    required_years = exp.get("required_years")

    if status == "meets":
        detail = (
            f"The resume states {resume_years:g} years of experience, meeting the "
            f"job's {required_years:g}-year minimum."
        )
    elif status == "insufficient":
        detail = (
            f"The resume states {resume_years:g} years while the job asks for "
            f"{required_years:g}+ years. This is based only on the years written "
            "in the resume — other relevant experience may not be captured there."
        )
    elif status == "not_required":
        detail = "The job posting states no minimum years of experience."
    else:
        detail = (
            "Years of experience could not be determined from the resume text. "
            "This is treated as unknown — not as a failure."
        )
    return {
        "status": status,
        "resume_years": resume_years,
        "required_years": required_years,
        "detail": detail,
    }


def _education(ctx: _Ctx) -> dict:
    qual = ctx.signals.get("qualifications") or {}
    resume_education = qual.get("resume_education")
    job_mentions = bool(qual.get("job_mentions_education"))

    if resume_education and job_mentions:
        status = "present"
        detail = (
            f"The resume states {resume_education} education and the job "
            "description mentions education."
        )
    elif resume_education:
        status = "present"
        detail = (
            f"The resume states {resume_education} education; the posting does not "
            "require a specific degree."
        )
    elif job_mentions:
        status = "unknown"
        detail = (
            "The job description mentions education, but the resume does not state "
            "a degree. Treated as unknown — not as a missing qualification."
        )
    else:
        status = "not_required"
        detail = "Neither document states an education requirement or level."
    return {
        "status": status,
        "resume_education": resume_education,
        "job_mentions_education": job_mentions,
        "detail": detail,
    }


def _certifications(ctx: _Ctx) -> dict:
    qual = ctx.signals.get("qualifications") or {}
    job_certs = sorted(qual.get("job_certifications") or [])
    resume_certs = sorted(qual.get("resume_certifications") or [])
    matched = sorted(qual.get("matched_certifications") or [])

    if matched:
        detail = "Holds certification(s) the job mentions: " + ", ".join(matched) + "."
    elif job_certs:
        detail = (
            "The job mentions " + ", ".join(job_certs)
            + ", but none were found in the extracted resume text — unknown, not a "
            "failure. Certifications may simply not be listed on the resume."
        )
    else:
        detail = "The job does not list required certifications."
    return {
        "job_certifications": job_certs,
        "resume_certifications": resume_certs,
        "matched": matched,
        "detail": detail,
    }


def _semantic(ctx: _Ctx) -> dict:
    components = ctx.details.get("components") or {}
    effective = ctx.row.weights or {}
    unavailable = ctx.details.get("unavailable_components") or {}
    value = components.get("semantic")
    weight = effective.get("semantic")
    if value is not None and weight is not None:
        return {
            "enabled": True,
            "score": round(float(value), 2),
            "weight": round(float(weight), 4),
            "reason": None,
        }
    return {
        "enabled": False,
        "score": None,
        "weight": None,
        "reason": str(unavailable.get("semantic") or "Semantic similarity unavailable."),
    }


def _related_items(ctx: _Ctx, rows: list[_Requirement]) -> list[dict]:
    return [
        {"requirement": req.label, "resume_has": req.related}
        for req in rows
        if req.status == "related"
    ]


# ----------------------------------------------------------------- 4.1 explain
def explain_match(db: Session, match_id: int) -> dict:
    ctx = _load(db, match_id)
    rows = _requirement_rows(ctx)

    required = [r for r in rows if r.requirement == "required"]
    preferred = [r for r in rows if r.requirement == "preferred"]
    req_matched = [r.label for r in required if r.status == "matched"]
    req_missing = [r.label for r in required if r.status == "missing"]
    pref_matched = [r.label for r in preferred if r.status == "matched"]
    pref_missing = [r.label for r in preferred if r.status == "missing"]
    related = _related_items(ctx, rows)

    experience = _experience(ctx)
    education = _education(ctx)
    certifications = _certifications(ctx)
    semantic = _semantic(ctx)

    score = float(ctx.row.final_score)

    # ------------------------------------------------------------ strengths
    strengths: list[str] = []
    if req_matched:
        strengths.append(
            f"Covers {len(req_matched)} of {len(required)} required skills: "
            + ", ".join(req_matched[:6])
            + ("…" if len(req_matched) > 6 else "")
            + "."
        )
    if experience["status"] == "meets":
        strengths.append(experience["detail"])
    if certifications["matched"]:
        strengths.append(certifications["detail"])
    if semantic["enabled"] and (semantic["score"] or 0) >= 50:
        strengths.append(
            f"Resume and posting read similarly (semantic similarity "
            f"{semantic['score']:.1f}%)."
        )
    overlap = ctx.signals.get("skill_categories_overlap") or []
    if overlap:
        strengths.append(
            "Resume skills overlap the job's areas: " + ", ".join(overlap) + "."
        )
    if not strengths:
        strengths.append(
            "No strong supporting evidence was found in the extracted resume for "
            "this posting."
        )

    # ---------------------------------------------------------------- gaps
    gaps: list[str] = []
    if req_missing:
        gaps.append(
            f"{len(req_missing)} required skill(s) not found in the extracted "
            "resume: " + ", ".join(req_missing[:8]) + "."
        )
    for item in related:
        gaps.append(
            f"{item['requirement']} is not on the resume; related experience exists "
            f"({', '.join(item['resume_has'])}) — helpful, but not counted as a match."
        )
    if experience["status"] == "insufficient":
        gaps.append(experience["detail"])
    if pref_missing:
        gaps.append(
            "Optional (preferred) skills not found: " + ", ".join(pref_missing[:8]) + "."
        )
    if not gaps:
        gaps.append("No required or preferred skill gaps were identified.")

    # ----------------------------------------------------------- limitations
    limitations: list[str] = [CAVEAT]
    unavailable = ctx.details.get("unavailable_components") or {}
    for name in ("required", "preferred", "semantic"):
        if name in unavailable:
            limitations.append(
                f"{COMPONENT_LABELS[name]} component unavailable: {unavailable[name]}"
            )
    for note in ctx.details.get("notes") or []:
        limitations.append(str(note))
    if experience["status"] == "unknown":
        limitations.append(experience["detail"])
    if education["status"] == "unknown":
        limitations.append(education["detail"])
    confidence = ctx.row.confidence
    if confidence is not None and confidence < 0.5:
        limitations.append(
            f"Confidence is {round(confidence * 100)}% — the resume provides "
            "limited evidence for this score."
        )

    # -------------------------------------------------------------- evidence
    evidence: list[dict] = []
    for req in rows:
        kind = f"{req.requirement}_skill"
        if req.status == "matched" and req.match_type == "alias":
            detail = "Found in the extracted resume text (matched via a known alias)."
        elif req.status == "matched" and req.match_type == "fuzzy":
            detail = (
                "Found with a similar spelling in the resume — verify it is the "
                "same skill."
            )
        elif req.status == "matched":
            detail = "Found in the extracted resume text."
        elif req.status == "related":
            detail = (
                "Not found; the resume shows related skills: "
                + ", ".join(req.related)
                + " (counted as a near-miss only)."
            )
        else:
            detail = "Not found in the extracted resume text."
        evidence.append(
            {
                "kind": kind,
                "status": req.status,
                "label": req.label,
                "detail": detail,
                "source": "job posting requirement × extracted resume evidence "
                "(stored match result)",
            }
        )

    evidence.append(
        {
            "kind": "experience",
            "status": experience["status"],
            "label": "Years of experience",
            "detail": experience["detail"],
            "source": "job posting minimum × resume text extraction",
        }
    )
    evidence.append(
        {
            "kind": "education",
            "status": education["status"],
            "label": "Education",
            "detail": education["detail"],
            "source": "job description × resume text extraction",
        }
    )
    evidence.append(
        {
            "kind": "certification",
            "status": "matched" if certifications["matched"] else (
                "unknown" if certifications["job_certifications"] else "info"
            ),
            "label": "Certifications",
            "detail": certifications["detail"],
            "source": "job posting × resume text extraction",
        }
    )
    evidence.append(
        {
            "kind": "semantic",
            "status": "info" if semantic["enabled"] else "unknown",
            "label": "Semantic similarity",
            "detail": (
                f"{semantic['score']:.1f}% similarity with weight "
                f"{semantic['weight'] * 100:.0f}% in the final score."
                if semantic["enabled"]
                else f"Disabled for this match: {semantic['reason']}"
            ),
            "source": "stored match result",
        }
    )
    evidence.append(
        {
            "kind": "note",
            "status": "info",
            "label": "Score provenance",
            "detail": (
                f"Final score {score:.2f} is the stored Phase 3 result for this "
                "pair, computed with effective weights "
                + ", ".join(
                    f"{COMPONENT_LABELS.get(k, k)} {float(v) * 100:.0f}%"
                    for k, v in (ctx.row.weights or {}).items()
                )
                + "."
            ),
            "source": "stored match result (match_results row)",
        }
    )

    return {
        "match_id": ctx.row.id,
        "resume_id": ctx.row.resume_id,
        "job_id": ctx.row.job_id,
        "final_score": score,
        "interpretation": _interpretation(score),
        "generated_by": GENERATED_BY,
        "score_factors": _score_factors(ctx),
        "required_matched": req_matched,
        "required_missing": req_missing,
        "preferred_matched": pref_matched,
        "preferred_missing": pref_missing,
        "related_skills": related,
        "experience": experience,
        "education": education,
        "certifications": certifications,
        "semantic": semantic,
        "strengths": strengths,
        "gaps": gaps,
        "limitations": limitations,
        "evidence": evidence,
        "confidence": ctx.row.confidence,
    }


# -------------------------------------------------------------- 4.2 skill gaps
def _priority(requirement: str, status: str) -> str:
    if status == "matched":
        return "none"
    if requirement == "required":
        return "high" if status == "missing" else "medium"
    return "low"


def _gap_explanation(req: _Requirement) -> str:
    if req.status == "matched":
        kind = {"alias": "a known alias", "fuzzy": "a similar spelling"}.get(
            req.match_type, "an exact match"
        )
        return (
            f"Required by the job and found in the extracted resume evidence "
            f"({kind})."
            if req.requirement == "required"
            else f"Preferred by the job and found in the extracted resume evidence ({kind})."
        )
    if req.status == "related":
        return (
            f"Required by the job, not found as such; the resume shows related "
            f"skills ({', '.join(req.related)}). Related skills are never counted "
            "as exact matches."
            if req.requirement == "required"
            else f"Preferred by the job; the resume shows related skills "
            f"({', '.join(req.related)}) — useful, but not an exact match."
        )
    if req.requirement == "required":
        return (
            "Required by the job posting and not found in the extracted resume "
            "text. The skill may be absent from the resume or missed by "
            "extraction — this is not proof the person lacks it."
        )
    return (
        "Preferred (optional) by the job and not found in the extracted resume "
        "text. Optional skills are never treated as mandatory."
    )


def _gap_action(req: _Requirement, job_title: str) -> str | None:
    if req.status == "missing" and req.requirement == "required":
        return (
            f"Study {req.label}, then add a truthful project or work example that "
            "uses it so the evidence appears on the resume."
        )
    if req.status == "related":
        return (
            f"Deepen {req.label} itself: your resume shows "
            + ", ".join(req.related)
            + f" — if you also use {req.label}, add a concrete example of that."
        )
    if req.status == "missing":
        return (
            f"After the required gaps are covered, learn {req.label} if it is "
            "relevant to the roles you want."
        )
    return None


def skill_gap_analysis(db: Session, match_id: int) -> dict:
    ctx = _load(db, match_id)
    rows = _requirement_rows(ctx)

    items: list[dict] = []
    priority_counts: dict[str, int] = {"high": 0, "medium": 0, "low": 0, "none": 0}
    for req in rows:
        priority = _priority(req.requirement, req.status)
        priority_counts[priority] = priority_counts.get(priority, 0) + 1
        items.append(
            {
                "skill": req.label,
                "requirement": req.requirement,
                "status": req.status,
                "priority": priority,
                "explanation": _gap_explanation(req),
                "related_resume_skills": req.related,
                "suggested_action": _gap_action(req, ctx.job.title),
            }
        )

    unknowns: list[dict] = []
    experience = _experience(ctx)
    if experience["status"] in {"unknown", "insufficient"}:
        unknowns.append(
            {
                "area": "experience",
                "status": "unknown" if experience["status"] == "unknown" else "unknown",
                "explanation": (
                    experience["detail"]
                    if experience["status"] == "unknown"
                    else "The stated years are below the job's minimum, but the "
                    "resume may not capture all relevant experience — treat this "
                    "as unverified rather than a definite failure."
                ),
            }
        )
    education = _education(ctx)
    if education["status"] == "unknown":
        unknowns.append(
            {
                "area": "education",
                "status": "unknown",
                "explanation": education["detail"],
            }
        )
    certifications = _certifications(ctx)
    if certifications["job_certifications"] and not certifications["matched"]:
        unknowns.append(
            {
                "area": "certification",
                "status": "unknown",
                "explanation": certifications["detail"],
            }
        )

    required = [r for r in rows if r.requirement == "required"]
    preferred = [r for r in rows if r.requirement == "preferred"]

    notes = [CAVEAT, OPTIONAL_NOTE, RELATED_NOTE]
    notes.extend(str(note) for note in ctx.details.get("notes") or [])

    return {
        "match_id": ctx.row.id,
        "resume_id": ctx.row.resume_id,
        "job_id": ctx.row.job_id,
        # matched / missing / related are three distinct buckets: a related
        # skill is counted as neither a match nor a plain miss.
        "required_total": len(required),
        "required_matched": sum(1 for r in required if r.status == "matched"),
        "required_missing": sum(1 for r in required if r.status == "missing"),
        "preferred_total": len(preferred),
        "preferred_matched": sum(1 for r in preferred if r.status == "matched"),
        "preferred_missing": sum(1 for r in preferred if r.status == "missing"),
        "items": items,
        "unknowns": unknowns,
        "related_not_counted": _related_items(ctx, rows),
        "priority_counts": priority_counts,
        "notes": notes,
        "generated_by": GENERATED_BY,
    }


# --------------------------------------------------------- 4.3 improvement plan
def _activity(stage: str, kind: str, title: str, detail: str,
               skill: str | None = None, estimate: str | None = None) -> dict:
    return {
        "stage": stage,
        "type": kind,
        "skill": skill,
        "title": title,
        "detail": detail,
        "estimate": estimate,
    }


def improvement_plan(db: Session, match_id: int) -> dict:
    ctx = _load(db, match_id)
    rows = _requirement_rows(ctx)

    missing_required = [r for r in rows if r.requirement == "required" and r.status == "missing"]
    related_required = [r for r in rows if r.requirement == "required" and r.status == "related"]
    missing_preferred = [r for r in rows if r.requirement == "preferred" and r.status == "missing"]
    matched = [r.label for r in rows if r.status == "matched"]

    focus = [r.label for r in missing_required[:3]]
    job_title = ctx.job.title

    start_now: list[dict] = []
    practice_next: list[dict] = []
    consider_later: list[dict] = []

    for skill in focus:
        start_now.append(
            _activity(
                "start_now",
                "learning",
                f"Learn {skill}",
                f"{skill} is a required skill for the {job_title} role and was not "
                "found in the extracted resume. Work through current, reputable "
                "material until you can explain the basics and one real use case.",
                skill=skill,
                estimate="rough estimate: 6–10 hours of focused study",
            )
        )

    for skill in focus:
        practice_next.append(
            _activity(
                "practice_next",
                "practice",
                f"Practice {skill}",
                f"Complete a small exercise with {skill} and write down the decisions "
                "you made — practice turns study into evidence you can discuss.",
                skill=skill,
                estimate="rough estimate: a few evenings",
            )
        )

    if focus:
        practice_next.append(
            _activity(
                "practice_next",
                "project",
                f"Portfolio project for {job_title}",
                "Build one small project that uses "
                + ", ".join(focus)
                + " together, and write up the problem, your approach and what you "
                "learned. It gives concrete evidence for each identified gap.",
                estimate="rough estimate: 1–2 weeks part-time",
            )
        )

    for req in related_required[:2]:
        consider_later.append(
            _activity(
                "consider_later",
                "practice",
                f"Bridge from {', '.join(req.related)} to {req.label}",
                f"Your resume shows {', '.join(req.related)}, which is adjacent to "
                f"{req.label} but not equivalent. If you use {req.label} at work, "
                "add a truthful example; otherwise close the gap deliberately.",
                skill=req.label,
            )
        )

    for req in missing_preferred[:3]:
        consider_later.append(
            _activity(
                "consider_later",
                "learning",
                f"Learn {req.label} (preferred, optional)",
                f"The job lists {req.label} as preferred, not required. Cover it "
                "after the required gaps — it strengthens the application but is "
                "not mandatory.",
                skill=req.label,
            )
        )

    qualifications = ctx.signals.get("qualifications") or {}
    job_certs = sorted(qualifications.get("job_certifications") or [])
    matched_certs = set(qualifications.get("matched_certifications") or [])
    for cert in [c for c in job_certs if c not in matched_certs][:2]:
        consider_later.append(
            _activity(
                "consider_later",
                "certification",
                f"Consider {cert}",
                f"The job mentions {cert} and the resume does not list it. Only "
                "pursue it if it fits your path, and never list a certification "
                "you have not earned.",
            )
        )

    experience = _experience(ctx)
    if experience["status"] == "unknown":
        consider_later.append(
            _activity(
                "consider_later",
                "clarification",
                "State your experience accurately",
                "The resume does not state total years of experience. Add a short, "
                "truthful summary of your experience so this signal can be "
                "assessed instead of staying unknown.",
            )
        )
    education = _education(ctx)
    if education["status"] == "unknown":
        consider_later.append(
            _activity(
                "consider_later",
                "clarification",
                "Clarify your education if relevant",
                "The job description mentions education but the resume does not "
                "state a degree. Add it only if it is accurate.",
            )
        )

    existing = list(dict.fromkeys(matched))
    sequencing = (
        "Suggested order — start now: "
        + ("; ".join(a["title"] for a in start_now) or "no required gaps to address")
        + ". Practice next: "
        + ("; ".join(a["title"] for a in practice_next) or "reinforce existing skills")
        + ". Consider later: "
        + ("; ".join(a["title"] for a in consider_later) or "nothing further for this job")
        + "."
    )

    return {
        "match_id": ctx.row.id,
        "resume_id": ctx.row.resume_id,
        "job_id": ctx.row.job_id,
        "generated_by": GENERATED_BY,
        "focus_skills": focus,
        "existing_skills": existing,
        "start_now": start_now,
        "practice_next": practice_next,
        "consider_later": consider_later,
        "sequencing": sequencing,
        "prioritisation": (
            "Priorities come from the stored match: required-skill gaps first "
            "(they carry the largest score weight), then related near-misses, then "
            "optional preferred skills, then certifications and clarifications. "
            "Only requirements actually present in this job posting are used."
        ),
        "disclaimers": [
            "Completing these suggestions does not guarantee an interview or a job.",
            "All timelines are rough, generic estimates — not personalised "
            "measurements of your learning speed.",
            "Skills you already have are listed separately as "
            "“existing_skills”; they are not recommendations.",
        ],
        "notes": [CAVEAT, OPTIONAL_NOTE]
        + [str(note) for note in ctx.details.get("notes") or []],
    }


# ------------------------------------------------------- 4.4 resume suggestions
def resume_suggestions(db: Session, match_id: int) -> dict:
    ctx = _load(db, match_id)
    rows = _requirement_rows(ctx)

    matched_labels = list(dict.fromkeys(
        r.label for r in rows if r.status == "matched"
    ))
    missing_required = [r.label for r in rows if r.requirement == "required" and r.status == "missing"]
    missing_preferred = [r.label for r in rows if r.requirement == "preferred" and r.status == "missing"]

    # Job keywords: explicit requirements plus skills extracted from the
    # description, minus anything already evidenced in the resume.
    resume_keys = ctx.resume_profile.skill_keys
    description_keywords = [
        skill.label
        for skill in ctx.job_profile.description_skills
        if skill.key not in resume_keys
        and (skill.category or "") != "certification"
    ]
    keywords_absent = list(dict.fromkeys(missing_required + missing_preferred + description_keywords))[:12]
    keywords_present = matched_labels[:12]

    suggestions: list[dict] = []

    for skill in matched_labels[:4]:
        suggestions.append(
            {
                "category": "highlight",
                "title": f"Make “{skill}” easy to find",
                "detail": (
                    f"{skill} is relevant to this job and is already supported by "
                    "your resume — surface it in your skills summary and next to "
                    "the role or project where you used it."
                ),
                "verify": (
                    "Confirm the section you point to really used this skill; the "
                    "suggestion only re-arranges what is already true."
                ),
                "skill": skill,
            }
        )

    for skill in matched_labels[:3]:
        suggestions.append(
            {
                "category": "strengthen",
                "title": f"Add concrete detail for {skill}",
                "detail": (
                    f"Your resume mentions {skill} but not how you used it. Add a "
                    "bullet describing a real project or task where you applied it "
                    "and what it produced."
                ),
                "verify": (
                    "Only describe work you actually did — do not invent a project, "
                    "employer or outcome."
                ),
                "skill": skill,
            }
        )

    suggestions.append(
        {
            "category": "quantify",
            "title": "Add real numbers to achievements you already list",
            "detail": (
                "Metrics make existing claims verifiable (for example team size, "
                "users served, latency or delivery dates). Add figures to "
                "achievements already on the resume where you know them to be true."
            ),
            "verify": (
                "Provide only accurate numbers; if a figure is an estimate, label "
                "it as one. Never fabricate metrics."
            ),
            "skill": None,
        }
    )

    experience = _experience(ctx)
    if experience["status"] == "unknown":
        suggestions.append(
            {
                "category": "clarify",
                "title": "State your experience level",
                "detail": (
                    "Years of experience could not be determined from the resume, "
                    "so this signal stayed unknown. Add a short, accurate summary "
                    "of your total relevant experience."
                ),
                "verify": "Only state years of experience you can stand behind.",
                "skill": None,
            }
        )
    elif experience["status"] == "insufficient":
        suggestions.append(
            {
                "category": "clarify",
                "title": "Explain your relevant experience",
                "detail": (
                    f"The resume states {experience['resume_years']:g} years while "
                    f"the job asks for {experience['required_years']:g}+. If you "
                    "have additional relevant experience that is not captured, "
                    "describe it accurately."
                ),
                "verify": (
                    "Do not round up or reframe experience you do not have; only "
                    "add what is true."
                ),
                "skill": None,
            }
        )

    education = _education(ctx)
    if education["status"] == "unknown":
        suggestions.append(
            {
                "category": "clarify",
                "title": "Mention your education if relevant",
                "detail": (
                    "The job description mentions education and the resume does "
                    "not state a degree, so this stayed unknown. Add it only if "
                    "you have it."
                ),
                "verify": "Never add education you have not completed.",
                "skill": None,
            }
        )

    if keywords_absent:
        suggestions.append(
            {
                "category": "keyword",
                "title": "Relevant job keywords absent from the resume",
                "detail": (
                    "The posting emphasises "
                    + ", ".join(keywords_absent[:8])
                    + ". Include any you genuinely have, phrased the way you used "
                    "them; the rest are simply gaps to acknowledge."
                ),
                "verify": (
                    "Include a keyword only when truthful — never add a skill you "
                    "do not have to pass a keyword filter."
                ),
                "skill": None,
            }
        )

    return {
        "match_id": ctx.row.id,
        "resume_id": ctx.row.resume_id,
        "job_id": ctx.row.job_id,
        "generated_by": GENERATED_BY,
        "suggestions": suggestions,
        "keywords_present": keywords_present,
        "keywords_absent": keywords_absent,
        "disclaimers": [
            "Nothing is written to your stored resume — review each suggestion and "
            "apply it manually.",
            "Never fabricate work experience, achievements, metrics, education or "
            "certifications. Every suggestion must be verified by you before it "
            "goes on the resume.",
            "Suggested keywords are suggestions to include only when they are true.",
        ],
        "notes": [CAVEAT] + [str(note) for note in ctx.details.get("notes") or []],
    }