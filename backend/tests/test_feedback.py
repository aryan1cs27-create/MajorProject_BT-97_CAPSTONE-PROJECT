"""Phase 6 tests — feedback capture, idempotency, summary and ranking fusion.

The most important test in this file is
``test_zero_feedback_blended_equals_engine``: with no feedback the blended
listing must be identical to the Phase 3 listing — same order, same scores, same
components, same weights — so the deterministic engine is provably untouched.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.feedback import MatchFeedback
from app.services import ranking, semantic
from app.core.config import Settings
from conftest import make_job, make_resume


@pytest.fixture(autouse=True)
def _stub_semantic(monkeypatch):
    monkeypatch.setattr(semantic, "availability", lambda: (False, "stubbed off"))
    monkeypatch.setattr(semantic, "similarity", lambda a, b: None)


@pytest.fixture()
def dataset(db):
    """One resume, four jobs with deliberately close/equal engines."""
    resume = make_resume(
        db,
        name="Feedback Candidate",
        text=(
            "Feedback Candidate - Software Engineer\\n"
            "Skills: Python, FastAPI, Docker, Git\\n"
            "Experience: 5 years of experience building services.\\n"
        ),
    )
    # Identical requirements -> identical engine scores (so ordering is decided
    # only by feedback and the id tie-break). The resume lacks AWS, keeping the
    # engine score at 2/3 so a bounded adjustment can actually reorder it.
    first = make_job(
        db,
        title="Backend Engineer I",
        company="Acme",
        description="Python APIs",
        required=["Python", "FastAPI", "AWS"],
    )
    second = make_job(
        db,
        title="Backend Engineer II",
        company="Acme",
        description="Python APIs",
        required=["Python", "FastAPI", "AWS"],
    )
    # Different category (devops) but same company as the first two.
    devops = make_job(
        db,
        title="Platform Engineer",
        company="Acme",
        description="Docker and containers",
        required=["Docker", "Kubernetes"],
    )
    # Different company, same categories.
    other = make_job(
        db,
        title="Python Engineer",
        company="Globex",
        description="Python services",
        required=["Python", "FastAPI", "AWS"],
    )
    return resume, [first, second, devops, other]


def calculate(client, resume) -> dict:
    response = client.post(f"/api/matches/calculate/{resume.id}", json={})
    assert response.status_code == 200, response.text
    return response.json()


def match_for(items, job) -> dict:
    return next(item for item in items if item["job_id"] == job.id)


def give(client, match_id: int, **payload):
    return client.post(f"/api/matches/{match_id}/feedback", json=payload)


def listing(client, resume_id: int, rank: str = "engine") -> dict:
    response = client.get(f"/api/matches/resume/{resume_id}", params={"rank": rank})
    assert response.status_code == 200, response.text
    return response.json()


def order_ids(payload: dict) -> list[int]:
    return [item["job_id"] for item in payload["items"]]


# ------------------------------------------------------------- persistence
def test_feedback_persists_and_links_the_match(client, db, dataset):
    resume, jobs = dataset
    match = match_for(calculate(client, resume)["items"], jobs[0])

    response = give(
        client,
        match["id"],
        verdict="helpful",
        relevance=5,
        outcome="interview",
        reason="Strong skill alignment",
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["match_id"] == match["id"]
    # resume/job are derived from the stored match, never from the request
    assert body["resume_id"] == resume.id
    assert body["job_id"] == jobs[0].id
    assert body["verdict"] == "helpful"
    assert body["relevance"] == 5
    assert body["outcome"] == "interview"

    row = db.get(MatchFeedback, body["id"])
    assert row is not None
    assert (row.resume_id, row.job_id) == (resume.id, jobs[0].id)


def test_history_is_append_only_and_latest_verdict_is_retrievable(client, db, dataset):
    resume, jobs = dataset
    match = match_for(calculate(client, resume)["items"], jobs[0])

    assert give(client, match["id"], verdict="not_relevant").status_code == 201
    assert give(client, match["id"], verdict="helpful", relevance=4).status_code == 201

    rows = list(
        db.scalars(select(MatchFeedback).where(MatchFeedback.match_id == match["id"])).all()
    )
    assert len(rows) == 2  # both events preserved
    assert {row.verdict for row in rows} == {"helpful", "not_relevant"}

    # the latest event is what the API reports for the pair
    stored = client.get(f"/api/matches/{match['id']}").json()
    assert stored["latest_feedback"]["verdict"] == "helpful"
    assert stored["latest_feedback"]["relevance"] == 4


def test_identical_submission_is_idempotent(client, db, dataset):
    resume, jobs = dataset
    match = match_for(calculate(client, resume)["items"], jobs[0])

    first = give(client, match["id"], verdict="helpful", relevance=3, reason="same")
    second = give(client, match["id"], verdict="helpful", relevance=3, reason="same")

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.headers.get("Idempotent-Replay") == "true"
    assert first.json()["id"] == second.json()["id"]
    count = db.scalar(select(MatchFeedback.id).where(MatchFeedback.match_id == match["id"]))
    assert count is not None
    assert (
        len(list(db.scalars(select(MatchFeedback).where(MatchFeedback.match_id == match["id"]))))
        == 1
    )


def test_idempotency_key_returns_the_original_event(client, db, dataset):
    resume, jobs = dataset
    match = match_for(calculate(client, resume)["items"], jobs[0])

    first = client.post(
        f"/api/matches/{match['id']}/feedback",
        json={"verdict": "helpful"},
        headers={"Idempotency-Key": "submit-1"},
    )
    second = client.post(
        f"/api/matches/{match['id']}/feedback",
        json={"verdict": "not_relevant", "relevance": 1},
        headers={"Idempotency-Key": "submit-1"},
    )
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["verdict"] == "helpful"
    assert (
        len(list(db.scalars(select(MatchFeedback).where(MatchFeedback.match_id == match["id"]))))
        == 1
    )


def test_feedback_rejects_client_supplied_pair_ids(client, db, dataset):
    """A payload cannot attach feedback to another resume/job pair."""
    resume, jobs = dataset
    items = calculate(client, resume)["items"]
    match = match_for(items, jobs[0])

    response = give(
        client,
        match["id"],
        verdict="helpful",
        resume_id=9999,
        job_id=jobs[1].id,
    )
    assert response.status_code == 201
    body = response.json()
    assert (body["resume_id"], body["job_id"]) == (resume.id, jobs[0].id)


# ------------------------------------------------------------- validation
def test_unknown_match_returns_404(client, dataset):
    response = give(client, 99999, verdict="helpful")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize(
    "payload",
    [
        {"verdict": "excellent"},
        {"verdict": "helpful", "relevance": 0},
        {"verdict": "helpful", "relevance": 6},
        {"verdict": "helpful", "outcome": "hired"},
        {"relevance": 3},
    ],
)
def test_invalid_payloads_are_rejected(client, dataset, payload):
    resume, jobs = dataset
    match = match_for(calculate(client, resume)["items"], jobs[0])
    response = client.post(f"/api/matches/{match['id']}/feedback", json=payload)
    assert response.status_code == 422, response.text


def test_feedback_requires_authentication(anon_client, client, dataset):
    resume, jobs = dataset
    match = match_for(calculate(client, resume)["items"], jobs[0])
    response = anon_client.post(
        f"/api/matches/{match['id']}/feedback", json={"verdict": "helpful"}
    )
    assert response.status_code == 401


# --------------------------------------------------------- ranking modes
def test_engine_and_blended_share_the_response_shape(client, dataset):
    resume, _jobs = dataset
    calculate(client, resume)

    engine = listing(client, resume.id, "engine")
    blended = listing(client, resume.id, "blended")
    assert engine["rank"] == "engine"
    assert blended["rank"] == "blended"
    assert engine["total"] == blended["total"] == 4
    assert blended["feedback_coverage"] == 0.0
    for item in engine["items"]:
        assert item["blended_score"] is None
        assert item["rank_explanation"] is None
    for item in blended["items"]:
        assert item["blended_score"] is not None
        assert item["rank_explanation"]["mode"] == "blended"


def test_zero_feedback_blended_equals_engine(client, dataset):
    """THE regression guard: no feedback => blended output == engine output."""
    resume, _jobs = dataset
    calculate(client, resume)

    engine = listing(client, resume.id, "engine")
    blended = listing(client, resume.id, "blended")

    # identical order, scores, components, weights and evidence
    assert order_ids(engine) == order_ids(blended)
    for left, right in zip(engine["items"], blended["items"], strict=True):
        assert left["id"] == right["id"]
        assert left["final_score"] == right["final_score"]
        assert right["blended_score"] == left["final_score"]
        assert left["components"] == right["components"]
        assert left["weights"] == right["weights"]
        assert left["matched_skills"] == right["matched_skills"]
        assert left["missing_required"] == right["missing_required"]
        assert right["feedback_adjustment"] == 0.0
        assert right["latest_feedback"] is None
        explanation = right["rank_explanation"]
        assert explanation["engine_score"] == right["final_score"]
        assert explanation["blended_score"] == right["final_score"]
        assert explanation["adjustment"] == 0.0
        assert all(signal["points"] == 0.0 for signal in explanation["signals"])
        assert "unchanged" in explanation["summary"]

    # pagination must not change with the mode either
    paged_engine = client.get(
        f"/api/matches/resume/{resume.id}", params={"rank": "engine", "page_size": 2}
    ).json()
    paged_blended = client.get(
        f"/api/matches/resume/{resume.id}", params={"rank": "blended", "page_size": 2}
    ).json()
    assert order_ids(paged_engine) == order_ids(paged_blended)


def test_positive_feedback_promotes_a_tied_match(client, dataset):
    resume, jobs = dataset
    items = calculate(client, resume)["items"]
    first, second = jobs[0], jobs[1]
    assert match_for(items, first)["final_score"] == match_for(items, second)["final_score"]
    assert order_ids(listing(client, resume.id)) == [first.id, second.id, jobs[3].id, jobs[2].id]

    assert give(client, match_for(items, second)["id"], verdict="helpful").status_code == 201

    blended = listing(client, resume.id, "blended")
    assert order_ids(blended)[0] == second.id  # promoted above the tie
    promoted = next(item for item in blended["items"] if item["job_id"] == second.id)
    assert promoted["blended_score"] > promoted["final_score"]
    assert promoted["final_score"] == match_for(items, second)["final_score"]  # unchanged


def test_negative_feedback_demotes_a_tied_match(client, dataset):
    resume, jobs = dataset
    items = calculate(client, resume)["items"]
    first, second = jobs[0], jobs[1]

    assert give(client, match_for(items, first)["id"], verdict="not_relevant").status_code == 201

    blended = listing(client, resume.id, "blended")
    # the penalised job falls below its identical twin (extra same-company
    # spill-over may also reorder unrelated jobs — see the company test)
    assert order_ids(blended).index(first.id) > order_ids(blended).index(second.id)
    demoted = next(item for item in blended["items"] if item["job_id"] == first.id)
    assert demoted["blended_score"] < demoted["final_score"]
    # the engine listing is untouched by feedback
    assert order_ids(listing(client, resume.id, "engine"))[0] == first.id


def test_skill_category_feedback_reaches_other_jobs(client, dataset):
    resume, jobs = dataset
    items = calculate(client, resume)["items"]
    # `other` shares Python/FastAPI categories with job 0 but is another company
    assert give(client, match_for(items, jobs[0])["id"], verdict="helpful").status_code == 201

    blended = listing(client, resume.id, "blended")
    other = next(item for item in blended["items"] if item["job_id"] == jobs[3].id)
    signals = {signal["signal"]: signal for signal in other["rank_explanation"]["signals"]}
    assert signals["job"]["raw"] == 0.0 and signals["job"]["points"] == 0.0
    assert signals["skill_category"]["raw"] > 0
    assert signals["skill_category"]["positive"] >= 1
    assert other["blended_score"] > other["final_score"]


def test_company_feedback_stays_within_that_company(client, dataset):
    resume, jobs = dataset
    items = calculate(client, resume)["items"]
    # `devops` is at the same company as job 0 but shares no skill category
    assert give(client, match_for(items, jobs[0])["id"], verdict="helpful").status_code == 201

    blended = listing(client, resume.id, "blended")
    devops = next(item for item in blended["items"] if item["job_id"] == jobs[2].id)
    signals = {signal["signal"]: signal for signal in devops["rank_explanation"]["signals"]}
    assert signals["company"]["raw"] > 0
    assert signals["company"]["positive"] >= 1
    # no category overlap -> no category signal leaking across domains
    assert signals["skill_category"]["raw"] == 0.0

    # and the unrelated company is not affected by Acme's feedback
    other = next(item for item in blended["items"] if item["job_id"] == jobs[3].id)
    other_signals = {s["signal"]: s for s in other["rank_explanation"]["signals"]}
    assert other_signals["company"]["raw"] == 0.0


def test_contradictory_feedback_cancels_out(client, dataset):
    resume, jobs = dataset
    items = calculate(client, resume)["items"]
    give(client, match_for(items, jobs[0])["id"], verdict="helpful")
    give(client, match_for(items, jobs[1])["id"], verdict="not_relevant")

    blended = listing(client, resume.id, "blended")
    # job 0 and job 1 are the feedback subjects; job 3 shares only categories
    other = next(item for item in blended["items"] if item["job_id"] == jobs[3].id)
    category = next(
        signal for signal in other["rank_explanation"]["signals"]
        if signal["signal"] == "skill_category"
    )
    # both jobs contribute the same categories in opposite directions
    assert category["positive"] == category["negative"] > 0
    assert category["raw"] == 0.0
    assert other["blended_score"] == other["final_score"]


def test_single_event_is_damped_and_explainable(client, dataset):
    resume, jobs = dataset
    items = calculate(client, resume)["items"]
    give(client, match_for(items, jobs[0])["id"], verdict="helpful")

    blended = listing(client, resume.id, "blended")
    item = next(i for i in blended["items"] if i["job_id"] == jobs[0].id)
    explanation = item["rank_explanation"]

    # one event cannot exhaust the cap (additive smoothing)
    assert 0 < explanation["adjustment"] < explanation["max_adjustment"]
    assert explanation["capped"] is False
    # points reconcile with the reported adjustment
    assert round(sum(s["points"] for s in explanation["signals"]), 2) == explanation["adjustment"]
    for signal in explanation["signals"]:
        assert round(signal["weight"] * signal["raw"] * explanation["max_adjustment"], 2) == signal["points"]
    assert explanation["notes"]


def test_adjustment_is_capped_and_bounded(client, db, dataset):
    resume, jobs = dataset
    items = calculate(client, resume)["items"]
    match = match_for(items, jobs[0])

    # many consistent events push the signal towards its limit. Each payload is
    # distinct, so these are real events rather than idempotent replays.
    for run in range(12):
        response = give(
            client, match["id"], verdict="helpful", relevance=5, reason=f"run {run}"
        )
        assert response.status_code == 201, response.text

    index = ranking.build_index(db, resume.id)
    settings = Settings(feedback_max_adjustment=2.0)
    result = ranking.adjust(match["final_score"], jobs[0], index, settings)
    assert abs(result.adjustment) <= settings.feedback_max_adjustment
    assert result.explanation["max_adjustment"] == 2.0
    assert 0 <= result.blended_score <= 100

    # the cap is a hard ceiling on the applied adjustment
    assert 0 < result.adjustment <= settings.feedback_max_adjustment
    assert result.explanation["max_adjustment"] == 2.0

    # score boundaries: a 100 can't exceed 100, a 0 can't go below 0
    top = ranking.adjust(100.0, jobs[0], index, settings)
    assert top.blended_score <= 100.0
    give(client, match["id"], verdict="not_relevant")
    bottom = ranking.adjust(0.0, jobs[0], ranking.build_index(db, resume.id), settings)
    assert bottom.blended_score >= 0.0


def test_feedback_ranking_can_be_disabled(client, db, dataset):
    resume, jobs = dataset
    items = calculate(client, resume)["items"]
    give(client, match_for(items, jobs[1])["id"], verdict="helpful")

    index = ranking.build_index(db, resume.id)
    disabled = Settings(feedback_enabled=False)
    result = ranking.adjust(match_for(items, jobs[1])["final_score"], jobs[1], index, disabled)
    assert result.adjustment == 0.0
    assert result.blended_score == result.engine_score
    assert result.explanation["enabled"] is False
    assert "disabled" in result.explanation["summary"].lower()


def test_invalid_rank_mode_is_rejected(client, dataset):
    resume, _jobs = dataset
    calculate(client, resume)
    response = client.get(
        f"/api/matches/resume/{resume.id}", params={"rank": "magic"}
    )
    assert response.status_code == 422


def test_blended_ranking_paginates_without_losing_matches(client, dataset):
    resume, _jobs = dataset
    calculate(client, resume)
    full = listing(client, resume.id, "blended")
    page_one = client.get(
        f"/api/matches/resume/{resume.id}", params={"rank": "blended", "page": 1, "page_size": 3}
    ).json()
    page_two = client.get(
        f"/api/matches/resume/{resume.id}", params={"rank": "blended", "page": 2, "page_size": 3}
    ).json()
    assert page_one["total"] == page_two["total"] == 4
    assert order_ids(page_one) + order_ids(page_two) == order_ids(full)


# --------------------------------------------------------------- summary
def test_feedback_summary_aggregates_events(client, dataset):
    resume, jobs = dataset
    items = calculate(client, resume)["items"]

    give(
        client,
        match_for(items, jobs[0])["id"],
        verdict="helpful",
        relevance=5,
        outcome="interview",
    )
    give(
        client,
        match_for(items, jobs[1])["id"],
        verdict="not_relevant",
        relevance=2,
        outcome="rejected",
    )

    summary = client.get(f"/api/matches/resume/{resume.id}/feedback-summary").json()
    assert summary["resume_id"] == resume.id
    assert summary["total_events"] == 2
    assert summary["helpful_count"] == 1
    assert summary["not_relevant_count"] == 1
    assert summary["average_relevance"] == 3.5
    assert summary["outcome_distribution"]["interview"] == 1
    assert summary["outcome_distribution"]["rejected"] == 1
    assert summary["matches_total"] == 4
    assert summary["matches_with_feedback"] == 2
    assert summary["feedback_coverage"] == 0.5
    # jobs 0/1 share Python + FastAPI, so both categories are counted each way
    helpful_categories = {row["category"] for row in summary["most_helpful_skill_categories"]}
    rejected_categories = {row["category"] for row in summary["most_rejected_skill_categories"]}
    assert {"programming_language", "framework"} <= helpful_categories
    assert {"programming_language", "framework"} <= rejected_categories
    assert summary["notes"]


def test_feedback_summary_for_unknown_resume_is_404(client):
    response = client.get("/api/matches/resume/99999/feedback-summary")
    assert response.status_code == 404


def test_empty_feedback_summary_is_all_zeroes(client, dataset):
    resume, _jobs = dataset
    calculate(client, resume)
    summary = client.get(f"/api/matches/resume/{resume.id}/feedback-summary").json()
    assert summary["total_events"] == 0
    assert summary["average_relevance"] is None
    assert summary["feedback_coverage"] == 0.0
    assert summary["most_helpful_skill_categories"] == []
