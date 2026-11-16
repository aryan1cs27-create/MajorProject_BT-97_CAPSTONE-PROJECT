"""Phase 4 tests: explanations, skill gaps, improvement plan, resume suggestions.

Guard rails encoded here:

* required vs preferred are separated everywhere;
* taxonomy “related” skills are never reported as exact matches;
* missing experience / education stays ``unknown`` — never a failure;
* a missing extracted skill is never described as proof of incompetence;
* recommendations track the selected job's actual requirements;
* resume suggestions never fabricate skills or achievements;
* unknown match ids return 404 and reading a report never mutates the stored
  score or weights;
* output stays deterministic and usable with semantic matching disabled.
"""

from __future__ import annotations

import pytest

from app.models.match import MatchResult
from app.services import semantic
from conftest import make_job, make_resume


@pytest.fixture(autouse=True)
def _stub_semantic(monkeypatch):
    monkeypatch.setattr(semantic, "availability", lambda: (False, "stubbed off"))
    monkeypatch.setattr(semantic, "similarity", lambda a, b: None)


@pytest.fixture()
def dataset(db):
    resume = make_resume(
        db,
        name="Grace Hopper",
        text=(
            "Grace Hopper - Senior Software Engineer\n"
            "Skills: Python, FastAPI, Docker, Git, React\n"
            "Experience: 8 years of experience building web platforms.\n"
            "MSc in Computer Science\n"
        ),
    )
    strong = make_job(
        db,
        title="Backend Engineer",
        company="StrongCo",
        description="Build Python FastAPI services with Docker",
        required=["Python", "FastAPI", "Docker"],
        preferred=["Git"],
        min_experience=3,
    )
    weak = make_job(
        db,
        title="iOS Engineer",
        company="WeakCo",
        description="Swift mobile development",
        required=["Swift", "Kotlin", "iOS", "Objective-C"],
        preferred=["Xcode"],
    )
    related = make_job(
        db,
        title="Frontend Engineer",
        company="RelCo",
        description="React and Next.js frontends",
        required=["Next.js", "React"],
        preferred=[],
    )
    return resume, {"strong": strong, "weak": weak, "related": related}


def calculate(client, resume) -> dict:
    response = client.post(f"/api/matches/calculate/{resume.id}", json={})
    assert response.status_code == 200
    return response.json()


def match_for(payload: dict, job) -> dict:
    return next(item for item in payload["items"] if item["job_id"] == job.id)


def get(client, match_id: int, path: str) -> dict:
    response = client.get(f"/api/matches/{match_id}/{path}")
    assert response.status_code == 200, response.text
    return response.json()


# ------------------------------------------------------------ 4.1 explanation
def test_high_match_reports_coverage_and_few_gaps(client, dataset):
    resume, jobs = dataset
    payload = calculate(client, resume)
    match = match_for(payload, jobs["strong"])

    explanation = get(client, match["id"], "explanation")
    assert explanation["final_score"] == match["final_score"]
    assert explanation["required_missing"] == []
    assert explanation["required_matched"] == ["Python", "FastAPI", "Docker"]
    assert explanation["preferred_matched"] == ["Git"]
    assert any("Covers 3 of 3 required skills" in s for s in explanation["strengths"])
    assert explanation["interpretation"]
    # semantic was stubbed off -> reported honestly, with a reason
    assert explanation["semantic"]["enabled"] is False
    assert explanation["semantic"]["reason"]

    # score factors are consistent with the stored score and weights
    factors = {f["component"]: f for f in explanation["score_factors"]}
    assert factors["required"]["available"] is True
    assert factors["semantic"]["available"] is False
    assert factors["semantic"]["reason"] == "stubbed off"
    used = sum(f["contribution"] for f in explanation["score_factors"] if f["available"])
    assert round(used, 1) == round(explanation["final_score"], 1)


def test_low_match_reports_multiple_required_gaps(client, dataset):
    resume, jobs = dataset
    payload = calculate(client, resume)
    match = match_for(payload, jobs["weak"])

    explanation = get(client, match["id"], "explanation")
    assert len(explanation["required_missing"]) >= 4
    assert explanation["required_matched"] == []
    assert any("required skill(s) not found" in gap for gap in explanation["gaps"])
    assert explanation["strengths"]  # still reports whatever evidence exists

    gaps = get(client, match["id"], "skill-gaps")
    assert gaps["required_total"] == 4
    assert gaps["required_missing"] == 4
    high = [item for item in gaps["items"] if item["priority"] == "high"]
    assert len(high) == 4
    assert all(item["requirement"] == "required" for item in high)


def test_required_and_preferred_are_separated(client, dataset):
    resume, jobs = dataset
    payload = calculate(client, resume)
    match = match_for(payload, jobs["weak"])

    gaps = get(client, match["id"], "skill-gaps")
    by_requirement = {item["skill"]: item for item in gaps["items"]}
    assert gaps["preferred_total"] == 1
    assert by_requirement["Xcode"]["requirement"] == "preferred"
    # an optional skill is never presented as mandatory
    assert by_requirement["Xcode"]["priority"] == "low"
    assert "optional" in by_requirement["Xcode"]["explanation"].lower()
    assert gaps["required_total"] == 4
    assert any("never treat" in note.lower() or "never count" in note.lower()
               for note in gaps["notes"])

    explanation = get(client, match["id"], "explanation")
    assert "Xcode" not in explanation["required_missing"]
    assert "Xcode" in explanation["preferred_missing"]


def test_related_skills_are_never_reported_as_matches(client, dataset):
    resume, jobs = dataset
    payload = calculate(client, resume)
    match = match_for(payload, jobs["related"])

    explanation = get(client, match["id"], "explanation")
    assert explanation["required_matched"] == ["React"]  # Next.js is NOT a match
    assert "Next.js" not in explanation["required_missing"]  # it is “related”, not plain missing
    assert [r for r in explanation["related_skills"] if r["requirement"] == "Next.js"]
    assert any("not counted as a match" in gap for gap in explanation["gaps"])

    gaps = get(client, match["id"], "skill-gaps")
    assert gaps["required_total"] == 2
    assert gaps["required_matched"] == 1
    assert gaps["required_missing"] == 0  # the uncovered one is “related”
    item = next(i for i in gaps["items"] if i["skill"] == "Next.js")
    assert item["status"] == "related"
    assert item["priority"] == "medium"
    assert "React" in item["related_resume_skills"]
    assert any(r["requirement"] == "Next.js" for r in gaps["related_not_counted"])

    # the stored Phase 3 result is untouched by reading the report
    assert "Next.js" not in match["matched_skills"]


def test_missing_skill_is_never_proof_of_incompetence(client, dataset):
    resume, jobs = dataset
    payload = calculate(client, resume)
    match = match_for(payload, jobs["weak"])

    explanation = get(client, match["id"], "explanation")
    assert any("not proof" in text for text in explanation["limitations"])
    assert any("may be absent from the resume or missed by extraction" in text
               for text in explanation["limitations"])

    gaps = get(client, match["id"], "skill-gaps")
    for item in gaps["items"]:
        if item["status"] == "missing" and item["requirement"] == "required":
            lowered = item["explanation"].lower()
            assert "not proof" in lowered
            assert "missed by extraction" in lowered
            assert "does not know" not in lowered
            assert "incompetent" not in lowered


def test_unknown_experience_and_education_stay_unknown(client, db):
    resume = make_resume(
        db,
        name="New Developer",
        text="New Developer. Skills: Python, SQL. Built internal reporting tools.",
    )
    job = make_job(
        db,
        title="Data Engineer",
        description="A bachelor's degree and 3+ years of experience are required.",
        required=["Python", "Airflow"],
        min_experience=3,
    )
    payload = calculate(client, resume)
    match = payload["items"][0]

    explanation = get(client, match["id"], "explanation")
    assert explanation["experience"]["status"] == "unknown"
    assert "unknown" in explanation["experience"]["detail"].lower()
    assert explanation["education"]["status"] == "unknown"

    gaps = get(client, match["id"], "skill-gaps")
    areas = {item["area"] for item in gaps["unknowns"]}
    assert "experience" in areas
    assert "education" in areas
    for item in gaps["unknowns"]:
        assert item["status"] == "unknown"
        # phrased as “not enough information”, never as a failure
        assert "unknown" in item["explanation"].lower()


def test_explanation_is_traceable_and_deterministic(client, dataset):
    resume, jobs = dataset
    payload = calculate(client, resume)
    match = match_for(payload, jobs["strong"])
    match_id = match["id"]

    first = get(client, match_id, "explanation")
    second = get(client, match_id, "explanation")
    assert first == second
    assert first["generated_by"] == "deterministic"
    # every evidence item names where it came from
    assert all(item["source"] for item in first["evidence"])
    assert any(item["kind"] == "required_skill" for item in first["evidence"])
    assert any(item["kind"] == "semantic" for item in first["evidence"])


# ----------------------------------------------------------- 4.3 improvement
def test_improvement_plan_uses_the_selected_jobs_requirements(client, dataset):
    resume, jobs = dataset
    payload = calculate(client, resume)

    weak_match = match_for(payload, jobs["weak"])
    plan = get(client, weak_match["id"], "improvement-plan")
    assert plan["focus_skills"] == ["Swift", "Kotlin", "iOS"]  # capped, job-specific
    assert set(plan["focus_skills"]).issubset(set(jobs["weak"].required_skills))
    # nothing from the other job leaks in
    assert "Python" not in plan["focus_skills"]
    assert plan["start_now"] and plan["practice_next"]
    assert all(activity["skill"] in plan["focus_skills"]
               for activity in plan["start_now"])
    assert any(activity["type"] == "project" for activity in plan["practice_next"])
    # preferred skill appears only as optional, in "consider later"
    assert all(activity["stage"] == "consider_later" for activity in plan["consider_later"]
               if activity["skill"] == "Xcode")
    assert any("does not guarantee" in d.lower() for d in plan["disclaimers"])
    assert any("rough" in d.lower() for d in plan["disclaimers"])
    assert plan["sequencing"].startswith("Suggested order")

    # strong match: recommendations separate existing skills from new ones
    strong_match = match_for(payload, jobs["strong"])
    strong_plan = get(client, strong_match["id"], "improvement-plan")
    assert strong_plan["focus_skills"] == []
    assert set(strong_plan["existing_skills"]) >= {"Python", "FastAPI", "Docker"}


# ------------------------------------------------------- 4.4 resume suggestions
def test_resume_suggestions_never_fabricate(client, dataset):
    resume, jobs = dataset
    payload = calculate(client, resume)
    match = match_for(payload, jobs["weak"])

    suggestions = get(client, match["id"], "resume-suggestions")
    assert suggestions["suggestions"], "expected at least one suggestion"
    assert suggestions["disclaimers"]

    matched = set(match["matched_skills"])
    for item in suggestions["suggestions"]:
        assert item["verify"], "every suggestion must state what to verify"
        if item["category"] in {"highlight", "strengthen"} and item["skill"]:
            # only skills the resume already supports may be highlighted
            assert item["skill"] in matched

    # absent keywords come from this job only (explicit requirements plus
    # skills in its description that the resume does not have)
    from app.services.features import build_job_profile, build_resume_profile

    job_profile = build_job_profile(jobs["weak"])
    resume_profile = build_resume_profile(resume)
    allowed_absent = set(jobs["weak"].required_skills) | set(jobs["weak"].preferred_skills)
    allowed_absent |= {
        skill.label
        for skill in job_profile.description_skills
        if skill.key not in resume_profile.skill_keys
    }
    assert set(suggestions["keywords_absent"]).issubset(allowed_absent)
    # the weak job's real gaps must be surfaced
    assert {"Swift", "Kotlin", "iOS"}.issubset(set(suggestions["keywords_absent"]))
    assert set(suggestions["keywords_present"]).issubset(matched)
    keyword_suggestions = [s for s in suggestions["suggestions"] if s["category"] == "keyword"]
    if keyword_suggestions:
        assert "truthful" in keyword_suggestions[0]["verify"].lower()
    assert any(
        "truth" in note.lower() or "true" in note.lower()
        for note in suggestions["disclaimers"]
    ), "disclaimers must insist suggestions are only applied when truthful"
    joined = " ".join(s["detail"].lower() for s in suggestions["suggestions"])
    assert "guarantee" not in joined


def test_experience_clarification_suggestion_appears_when_unknown(client, db):
    resume = make_resume(db, name="Quiet CV", text="Skills: Python, SQL")
    job = make_job(
        db,
        title="Backend Engineer",
        description="Python services",
        required=["Python"],
        min_experience=5,
    )
    payload = calculate(client, resume)
    match = payload["items"][0]

    suggestions = get(client, match["id"], "resume-suggestions")
    clarify = [s for s in suggestions["suggestions"] if s["category"] == "clarify"]
    assert clarify, "unknown experience should surface as a clarification, not a failure"
    assert any("only state" in s["verify"].lower() or "true" in s["verify"].lower()
               for s in clarify)


# ------------------------------------------------------------------- 4.5 API
def test_invalid_match_ids_return_404(client, dataset):
    for path in ("explanation", "skill-gaps", "improvement-plan", "resume-suggestions"):
        response = client.get(f"/api/matches/99999/{path}")
        assert response.status_code == 404, path
        assert response.json()["error"]["code"] == "not_found"


def test_reading_reports_never_changes_the_stored_score(client, db, dataset):
    resume, jobs = dataset
    payload = calculate(client, resume)
    match = match_for(payload, jobs["weak"])
    match_id = match["id"]

    def snapshot() -> dict:
        return {
            "final_score": match["final_score"],
            "weights": match["weights"],
            "matched": match["matched_skills"],
            "missing": match["missing_required"],
            "components": match["components"],
        }

    before = snapshot()
    get(client, match_id, "explanation")
    get(client, match_id, "skill-gaps")
    get(client, match_id, "improvement-plan")
    get(client, match_id, "resume-suggestions")
    after = get(client, match_id, "explanation")
    listing = client.get(f"/api/matches/resume/{resume.id}").json()
    current = next(i for i in listing["items"] if i["id"] == match_id)
    stored = {f: getattr(db.get(MatchResult, match_id), f) for f in ("final_score", "weights", "matched_skills", "missing_required")}

    assert {
        "final_score": current["final_score"],
        "weights": current["weights"],
        "matched": current["matched_skills"],
        "missing": current["missing_required"],
        "components": current["components"],
    } == before
    assert stored["final_score"] == before["final_score"]
    assert after["final_score"] == before["final_score"]


def test_reports_work_with_semantic_disabled(client, dataset):
    resume, jobs = dataset
    payload = calculate(client, resume)
    match = payload["items"][0]

    explanation = get(client, match["id"], "explanation")
    assert explanation["semantic"]["enabled"] is False
    factor = next(f for f in explanation["score_factors"] if f["component"] == "semantic")
    assert factor["available"] is False and factor["contribution"] is None
    # remaining components were renormalised, so the math still adds up
    used = sum(f["contribution"] for f in explanation["score_factors"] if f["available"])
    assert round(used, 1) == round(explanation["final_score"], 1)

    # and the rest of the report is still complete
    assert explanation["evidence"] and explanation["interpretation"]
    assert get(client, match["id"], "skill-gaps")["items"]
    assert get(client, match["id"], "improvement-plan")["sequencing"]
    assert get(client, match["id"], "resume-suggestions")["disclaimers"]