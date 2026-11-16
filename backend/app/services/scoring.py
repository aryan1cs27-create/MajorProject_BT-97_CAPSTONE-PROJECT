"""Deterministic hybrid scoring engine.

Components
----------
``required``   coverage of the job's required skills by the resume
``preferred``  coverage of the job's preferred skills by the resume
``semantic``   cosine similarity of the two documents (optional)

Weights come from configuration (default 60 / 15 / 25) and are **renormalised
over the components that are actually available**, so a job with no required
skills, or a system without a semantic model, still produces a sensible score.
The effective weights are always returned with the result.

Fallback rules (documented behaviour)
-------------------------------------
* No required skills defined      -> the ``required`` component is unavailable;
                                     its weight is redistributed proportionally.
* No preferred skills defined     -> same for ``preferred``.
* Semantic model unavailable, disabled, or a document is empty
                                   -> ``semantic`` unavailable, weight redistributed.
* No component available at all   -> score 0 with ``confidence`` low and a note;
                                     we never invent a number.

Related skills (taxonomy ``related``) are **never** counted as matches; they
only surface as a near-miss signal, because knowing React does not imply
knowing Next.js.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

from app.core.config import get_settings
from app.services import semantic
from app.services.extraction import ExtractedSkill
from app.services.features import JobProfile, ResumeProfile
from app.services.taxonomy import get_taxonomy


@dataclass
class SkillMatch:
    skill: str                # display label from the job
    key: str
    matched: bool
    match_type: str           # exact | alias | fuzzy | related | none
    resume_has: list[str] = field(default_factory=list)   # labels for fuzzy/related
    confidence: float = 1.0


@dataclass
class ScoreResult:
    final_score: float
    components: dict[str, float | None]          # None = unavailable
    effective_weights: dict[str, float]          # only available components, sum = 1
    configured_weights: dict[str, float]
    unavailable_components: dict[str, str]       # component -> reason
    required_matches: list[SkillMatch]
    preferred_matches: list[SkillMatch]
    matched_required: list[str]
    missing_required: list[str]
    matched_preferred: list[str]
    missing_preferred: list[str]
    discovered_skills: list[dict]
    signals: dict = field(default_factory=dict)
    confidence: float = 0.0
    notes: list[str] = field(default_factory=list)


def _coverage(
    wanted: list[str],
    wanted_labels: dict[str, str],
    resume_keys: set[str],
    resume_labels: dict[str, str],
    *,
    fuzzy_threshold: float,
) -> tuple[list[SkillMatch], float | None]:
    """Return (per-skill matches, coverage 0..100 or None when not applicable)."""
    if not wanted:
        return [], None

    taxonomy = get_taxonomy()
    matches: list[SkillMatch] = []
    matched_count = 0

    for key in wanted:
        label = wanted_labels.get(key, key)
        if key in resume_keys:
            matches.append(SkillMatch(label, key, True, "exact"))
            matched_count += 1
            continue

        # fuzzy (typo tolerance) — deterministic string similarity
        if fuzzy_threshold > 0:
            best_ratio = 0.0
            best_key: str | None = None
            for resume_key in resume_keys:
                ratio = difflib.SequenceMatcher(None, key, resume_key).ratio()
                if ratio > best_ratio:
                    best_ratio, best_key = ratio, resume_key
            if best_key is not None and best_ratio >= fuzzy_threshold:
                matches.append(
                    SkillMatch(
                        label,
                        key,
                        True,
                        "fuzzy",
                        resume_has=[resume_labels.get(best_key, best_key)],
                        confidence=round(best_ratio, 2),
                    )
                )
                matched_count += 1
                continue

        # related-but-not-equivalent (signal only, never a match)
        related_resumes: list[str] = []
        related = {r.lower() for r in taxonomy.related_to(key)}
        for resume_key in resume_keys:
            resume_related = {r.lower() for r in taxonomy.related_to(resume_key)}
            if resume_key in related or key in resume_related:
                related_resumes.append(resume_labels.get(resume_key, resume_key))
        matches.append(
            SkillMatch(
                label,
                key,
                False,
                "related" if related_resumes else "none",
                resume_has=sorted(related_resumes),
                confidence=0.0,
            )
        )

    return matches, matched_count / len(wanted) * 100


def _effective_weights(available: dict[str, bool]) -> tuple[dict[str, float], dict[str, str], list[str]]:
    """Renormalise configured weights over available components."""
    settings = get_settings()
    configured = settings.match_weights
    unavailable: dict[str, str] = {}
    usable: dict[str, float] = {}

    reasons = {
        "required": "The job defines no required skills.",
        "preferred": "The job defines no preferred skills.",
        "semantic": "Semantic similarity is unavailable or disabled.",
    }

    for name, weight in configured.items():
        if available.get(name):
            usable[name] = max(weight, 0.0)
        else:
            unavailable[name] = reasons.get(name, "Component unavailable.")

    # a component explicitly turned off gets its weight removed before renormalising
    total = sum(usable.values())
    if total <= 0:
        return {}, unavailable, ["No scoring component is available for this pair."]

    effective = {name: round(weight / total, 4) for name, weight in usable.items()}
    # guard against float drift
    drift = round(1.0 - sum(effective.values()), 4)
    if drift:
        largest = max(effective, key=effective.get)
        effective[largest] = round(effective[largest] + drift, 4)
    return effective, unavailable, []


def _experience_signal(resume: ResumeProfile, job: JobProfile) -> dict:
    resume_years = resume.years_experience
    required_years = job.minimum_experience

    if required_years is None:
        status = "not_required"
    elif resume_years is None:
        status = "unknown"          # absent CV data is NOT evidence of inexperience
    elif resume_years >= required_years:
        status = "meets"
    else:
        status = "insufficient"

    return {
        "resume_years": resume_years,
        "required_years": required_years,
        "status": status,
        "resume_titles": resume.titles[:3],
    }


def _qualification_signal(resume: ResumeProfile, job: JobProfile) -> dict:
    taxonomy = get_taxonomy()
    job_cert_keys = {
        taxonomy.normalize(name) or name
        for name in job.required_certifications
    }
    resume_cert_keys = {
        taxonomy.normalize(name) or name
        for name in resume.certifications
    }
    # certifications mentioned anywhere in the job description
    mentioned = {
        skill.canonical or skill.phrase
        for skill in job.description_skills
        if taxonomy.category_of(skill.canonical or "") == "certification"
    }
    matched = sorted(key for key in resume_cert_keys if key in mentioned or key in job_cert_keys)
    return {
        "resume_education": resume.education,
        "job_mentions_education": job.education_mentioned,
        "resume_certifications": sorted(resume_cert_keys),
        "job_certifications": sorted(job_cert_keys | mentioned),
        "matched_certifications": matched,
    }


def score_pair(
    resume: ResumeProfile,
    job: JobProfile,
    *,
    resume_text: str = "",
    job_text: str = "",
) -> ScoreResult:
    """Score one resume against one job. Pure function: same input, same output."""
    settings = get_settings()
    fuzzy_threshold = settings.match_fuzzy_threshold

    required_matches, required_pct = _coverage(
        job.required_keys,
        job.required_labels,
        resume.skill_keys,
        resume.skill_labels,
        fuzzy_threshold=fuzzy_threshold,
    )
    preferred_matches, preferred_pct = _coverage(
        job.preferred_keys,
        job.preferred_labels,
        resume.skill_keys,
        resume.skill_labels,
        fuzzy_threshold=fuzzy_threshold,
    )

    # ---- semantic component (never fabricated)
    semantic_score: float | None = None

    ok, reason = semantic.availability()
    if not ok:
        semantic_available, semantic_reason = False, reason or "unavailable"
    elif not resume_text.strip() or not job_text.strip():
        semantic_available, semantic_reason = False, "One of the documents has no text to compare."
    else:
        value = semantic.similarity(resume_text, job_text)
        if value is None:
            semantic_available, semantic_reason = False, "Similarity could not be computed."
        else:
            semantic_available = True
            semantic_reason = None
            semantic_score = round(value * 100, 2)

    available = {
        "required": required_pct is not None,
        "preferred": preferred_pct is not None,
        "semantic": semantic_available,
    }
    effective, unavailable, notes = _effective_weights(available)
    if semantic_reason and not semantic_available:
        unavailable["semantic"] = semantic_reason

    components: dict[str, float | None] = {
        "required": None if required_pct is None else round(required_pct, 2),
        "preferred": None if preferred_pct is None else round(preferred_pct, 2),
        "semantic": semantic_score,
    }

    if effective:
        final = sum(effective[name] * (components[name] or 0.0) for name in effective)
        confidence = _confidence(resume, available, effective)
    else:
        final = 0.0
        confidence = 0.0

    final = round(min(max(final, 0.0), 100.0), 2)

    matched_required = [m.skill for m in required_matches if m.matched]
    missing_required = [m.skill for m in required_matches if not m.matched]
    matched_preferred = [m.skill for m in preferred_matches if m.matched]
    missing_preferred = [m.skill for m in preferred_matches if not m.matched]

    related_near_misses = [
        {"missing": m.skill, "resume_has": m.resume_has}
        for m in required_matches + preferred_matches
        if m.match_type == "related"
    ]

    signals = {
        "experience": _experience_signal(resume, job),
        "qualifications": _qualification_signal(resume, job),
        "related_skill_near_misses": related_near_misses,
        "role_family": job.role_family,
        "seniority": job.seniority,
        "skill_categories_overlap": sorted(
            resume.categories
            & {
                get_taxonomy().category_of(key)
                for key in job.required_keys + job.preferred_keys
                if get_taxonomy().category_of(key)
            }
        ),
    }

    discovered = [
        {
            "skill": skill.label,
            "phrase": skill.phrase,
            "confidence": skill.confidence,
            "category": skill.category,
        }
        for skill in resume.skills
        if skill.source == "discovered"
    ]

    return ScoreResult(
        final_score=final,
        components=components,
        effective_weights=effective,
        configured_weights=settings.match_weights,
        unavailable_components=unavailable,
        required_matches=required_matches,
        preferred_matches=preferred_matches,
        matched_required=matched_required,
        missing_required=missing_required,
        matched_preferred=matched_preferred,
        missing_preferred=missing_preferred,
        discovered_skills=discovered,
        signals=signals,
        confidence=confidence,
        notes=notes,
    )


def _confidence(resume: ResumeProfile, available: dict[str, bool], effective: dict[str, float]) -> float:
    """0..1 confidence: how much evidence supports this score."""
    score = 0.0
    # evidence volume
    if resume.text_length >= 800:
        score += 0.3
    elif resume.text_length >= 300:
        score += 0.2
    elif resume.text_length >= 80:
        score += 0.1
    # number of comparable components
    score += 0.2 * sum(1 for flag in available.values() if flag)
    # semantic share (a semantic component adds confidence when present)
    score += 0.2 * effective.get("semantic", 0.0) / max(sum(effective.values()), 1e-9)
    # skill evidence
    if resume.skill_keys:
        score += 0.2 if len(resume.skill_keys) >= 5 else 0.1
    return round(min(score, 1.0), 2)
