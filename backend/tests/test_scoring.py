"""Scoring tests: coverage, weights, domains, false positives, boundaries.

Semantic similarity is stubbed in most tests so they stay fast and fully
deterministic; ``test_real_semantic_component`` exercises the real model when
it is available.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import semantic
from app.services.features import build_job_profile, build_resume_profile
from app.services.scoring import score_pair


def resume_profile(text: str, name: str = "Candidate"):
    resume = SimpleNamespace(id=1, candidate_name=name, extracted_text=text)
    return build_resume_profile(resume), text


def job_profile(**kwargs):
    job = SimpleNamespace(
        id=kwargs.get("id", 1),
        title=kwargs.get("title", "Engineer"),
        company=kwargs.get("company", "TestCo"),
        location=kwargs.get("location", "Remote"),
        description=kwargs.get("description", "Role description"),
        required_skills=kwargs.get("required", []),
        preferred_skills=kwargs.get("preferred", []),
        minimum_experience=kwargs.get("min_experience"),
        employment_type=kwargs.get("employment_type", "full-time"),
    )
    return build_job_profile(job)


@pytest.fixture()
def semantic_disabled(monkeypatch):
    monkeypatch.setattr(semantic, "availability", lambda: (False, "stubbed off"))
    monkeypatch.setattr(semantic, "similarity", lambda a, b: None)


@pytest.fixture()
def semantic_stub(monkeypatch):
    """Deterministic semantic score so the wiring can be asserted."""
    monkeypatch.setattr(semantic, "availability", lambda: (True, None))
    monkeypatch.setattr(semantic, "similarity", lambda a, b: 0.5)


BASIC_RESUME = (
    "Jane Developer\nSkills: Python, FastAPI, PostgreSQL, Docker, Git\n"
    "Experience: 5 years of experience building web services.\n"
)


def test_complete_partial_and_zero_overlap(semantic_disabled):
    full, _ = resume_profile(BASIC_RESUME)
    complete = job_profile(required=["Python", "FastAPI", "PostgreSQL", "Docker"])
    partial = job_profile(required=["Python", "GraphQL", "Kubernetes"])
    none = job_profile(required=["React", "Swift"])

    s_complete = score_pair(full, complete)
    s_partial = score_pair(full, partial)
    s_none = score_pair(full, none)

    assert s_complete.components["required"] == 100.0
    assert {m.lower() for m in s_complete.matched_required} == set(complete.required_keys)
    assert s_partial.components["required"] == pytest.approx(100 / 3, abs=0.01)
    assert set(s_partial.missing_required) == {"GraphQL", "Kubernetes"}
    assert s_none.components["required"] == 0.0
    assert s_none.final_score == 0.0
    assert s_complete.final_score > s_partial.final_score > s_none.final_score


def test_same_engine_across_domains(semantic_disabled):
    """One engine, no per-category code: every domain scores identically-shaped."""
    resume, text = resume_profile(
        "Security Engineer\nSkills: SIEM, Splunk, Incident Response, Python, AWS\n"
        "Certification: CISSP\nExperience: 7 years of experience in security operations.\n"
    )
    jobs = [
        job_profile(title="SOC Analyst", required=["SIEM", "Splunk", "Incident Response"]),
        job_profile(title="Backend Engineer", required=["Python", "FastAPI", "Kubernetes"]),
        job_profile(title="Cloud Engineer", required=["AWS", "Terraform", "Docker"]),
    ]
    results = [score_pair(resume, job) for job in jobs]
    for result in results:
        assert 0.0 <= result.final_score <= 100.0
        assert result.components["required"] is not None
        assert set(result.effective_weights) <= {"required", "preferred", "semantic"}
        assert abs(sum(result.effective_weights.values()) - 1.0) < 1e-6
    # the security resume should rank the SOC role first
    assert results[0].final_score == max(r.final_score for r in results)


def test_alias_normalisation_in_matching(semantic_disabled):
    resume, _ = resume_profile("Skills: JS, Postgres, K8s, Docker")
    job = job_profile(required=["JavaScript", "PostgreSQL", "Kubernetes"])
    result = score_pair(resume, job)
    assert result.components["required"] == 100.0
    assert set(result.matched_required) == {"JavaScript", "PostgreSQL", "Kubernetes"}


def test_related_skills_are_not_treated_as_equivalent(semantic_disabled):
    """Knowing Next.js must not count as knowing React."""
    resume, _ = resume_profile("Skills: Next.js, React Native, TypeScript")
    job = job_profile(required=["React"])
    result = score_pair(resume, job)

    assert result.components["required"] == 0.0
    assert "React" in result.missing_required
    # …but the defensible relationship is reported as a signal
    near_misses = result.signals["related_skill_near_misses"]
    assert any(item["missing"] == "React" and item["resume_has"] for item in near_misses)


def test_fuzzy_matching_is_configurable(semantic_disabled):
    resume, _ = resume_profile("Skills: Kubernets, Python")   # typo
    job = job_profile(required=["Kubernetes", "Python"])
    result = score_pair(resume, job)
    assert result.components["required"] == 100.0
    fuzzy_used = [m for m in result.required_matches if m.match_type == "fuzzy"]
    assert fuzzy_used and fuzzy_used[0].skill == "Kubernetes"


def test_empty_skill_lists_do_not_divide_by_zero(semantic_disabled):
    resume, _ = resume_profile(BASIC_RESUME)
    job = job_profile(required=[], preferred=[])
    result = score_pair(resume, job)

    assert result.components["required"] is None
    assert result.components["preferred"] is None
    assert result.effective_weights == {}
    assert result.final_score == 0.0
    assert result.unavailable_components["required"]
    assert "no scoring component" in " ".join(result.notes).lower()


def test_missing_preferred_only_redistributes(semantic_disabled):
    resume, _ = resume_profile(BASIC_RESUME)
    job = job_profile(required=["Python"], preferred=[])
    result = score_pair(resume, job)

    assert set(result.effective_weights) == {"required"}
    assert result.effective_weights["required"] == 1.0
    assert result.components["preferred"] is None


def test_weights_redistributed_when_semantic_unavailable(semantic_disabled):
    resume, _ = resume_profile(BASIC_RESUME)
    job = job_profile(required=["Python", "Docker"], preferred=["Git"])
    result = score_pair(resume, job, resume_text=BASIC_RESUME, job_text="Python Docker role")

    assert result.components["semantic"] is None
    # 0.60 / 0.15 renormalised -> 0.80 / 0.20
    assert result.effective_weights["required"] == pytest.approx(0.8, abs=1e-3)
    assert result.effective_weights["preferred"] == pytest.approx(0.2, abs=1e-3)
    assert abs(sum(result.effective_weights.values()) - 1.0) < 1e-6
    expected = 0.8 * result.components["required"] + 0.2 * (result.components["preferred"] or 0)
    assert result.final_score == pytest.approx(expected, abs=0.01)


def test_semantic_component_included_when_available(semantic_stub):
    resume, _ = resume_profile(BASIC_RESUME)
    job = job_profile(required=["Python"], preferred=["Git"])
    result = score_pair(resume, job, resume_text=BASIC_RESUME, job_text="Python role")

    assert result.components["semantic"] == 50.0        # stubbed 0.5 * 100
    assert result.effective_weights["semantic"] == pytest.approx(0.25, abs=0.01)
    assert result.effective_weights["required"] == pytest.approx(0.6, abs=0.01)
    assert result.effective_weights["preferred"] == pytest.approx(0.15, abs=0.01)
    expected = (
        result.effective_weights["required"] * result.components["required"]
        + result.effective_weights["preferred"] * (result.components["preferred"] or 0)
        + result.effective_weights["semantic"] * 50.0
    )
    assert result.final_score == pytest.approx(expected, abs=0.01)


def test_empty_documents_do_not_produce_a_semantic_score(semantic_stub):
    """Missing text is unknown, not 'unrelated'."""
    resume, _ = resume_profile(BASIC_RESUME)
    job = job_profile(required=["Python"])
    result = score_pair(resume, job, resume_text="", job_text="Python role")

    assert result.components["semantic"] is None
    assert "text" in result.unavailable_components["semantic"].lower()


def test_scores_stay_within_bounds(semantic_stub):
    resume, _ = resume_profile(BASIC_RESUME * 5)
    perfect = job_profile(required=["Python", "FastAPI", "PostgreSQL", "Docker", "Git"])
    terrible = job_profile(required=["Swift", "Kotlin", "iOS", "Flutter"])
    for job in (perfect, terrible):
        for text_pair in ((BASIC_RESUME, "Swift role"), ("", "")):
            result = score_pair(resume, job, resume_text=text_pair[0], job_text=text_pair[1])
            assert 0.0 <= result.final_score <= 100.0
            assert 0.0 <= result.confidence <= 1.0


def test_experience_and_qualification_signals_are_not_scored(semantic_disabled):
    resume, _ = resume_profile(BASIC_RESUME)          # 5 years stated
    job = job_profile(required=["Python"], min_experience=10.0)
    result = score_pair(resume, job)

    # reported as a signal only — never silently folded into the score
    assert result.signals["experience"]["resume_years"] == 5.0
    assert result.signals["experience"]["required_years"] == 10.0
    assert result.signals["experience"]["status"] == "insufficient"
    assert result.components["required"] == 100.0


def test_unknown_experience_is_not_penalised(semantic_disabled):
    resume, _ = resume_profile("Skills: Python, Docker")   # no years mentioned
    job = job_profile(required=["Python"], min_experience=3.0)
    result = score_pair(resume, job)

    assert result.signals["experience"]["resume_years"] is None
    assert result.signals["experience"]["status"] == "unknown"
    assert result.components["required"] == 100.0


def test_scoring_is_deterministic(semantic_stub):
    resume_a, _ = resume_profile(BASIC_RESUME)
    resume_b, _ = resume_profile(BASIC_RESUME)
    job = job_profile(required=["Python", "Docker"], preferred=["Git"])

    first = score_pair(resume_a, job, resume_text=BASIC_RESUME, job_text="role")
    second = score_pair(resume_b, job, resume_text=BASIC_RESUME, job_text="role")
    assert first.final_score == second.final_score
    assert first.components == second.components
    assert first.matched_required == second.matched_required


def test_real_semantic_component_when_model_available():
    """Integration check with the real model (skips when unavailable)."""
    ok, reason = semantic.availability()
    if not ok:
        pytest.skip(f"semantic model unavailable: {reason}")
    value = semantic.similarity(
        "Designed and built REST APIs in Python with FastAPI",
        "Develop backend services and REST endpoints",
    )
    assert value is not None and 0.0 <= value <= 1.0
    unrelated = semantic.similarity("Enterprise Java Spring services", "Underwater basket weaving")
    assert unrelated is not None
    assert value > unrelated
