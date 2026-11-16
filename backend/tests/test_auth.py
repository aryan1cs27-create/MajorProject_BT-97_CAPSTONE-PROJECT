"""Phase 5.1 — local API-token authentication.

The suite runs with ``API_TOKEN`` configured (see ``conftest``), so every
resume/job/match request must carry the token. Health checks stay open so a
monitoring probe or the dashboard connection indicator keeps working.
"""

from __future__ import annotations

import pytest

from app.core.config import get_settings


def test_health_stays_open_without_a_token(anon_client):
    assert anon_client.get("/api/health").status_code == 200


def test_protected_routes_reject_missing_tokens(anon_client):
    for path in (
        "/api/resumes",
        "/api/jobs",
        "/api/jobs/providers",
        "/api/matches/resume/1",
        "/api/matches/job/1",
    ):
        response = anon_client.get(path)
        assert response.status_code == 401, path
        assert response.json()["error"]["code"] == "unauthorized", path
        # the message explains how to authenticate, and never leaks the token
        assert "API token" in response.json()["error"]["message"]
        assert "test-token" not in response.text


def test_writing_routes_are_protected_too(anon_client):
    response = anon_client.post("/api/jobs", json={"title": "x", "company": "y", "description": "z"})
    assert response.status_code == 401
    assert anon_client.delete("/api/resumes/1").status_code == 401


def test_invalid_token_is_rejected(client):
    client.token = "not-the-token"
    response = client.get("/api/resumes")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


def test_bearer_token_is_accepted(client):
    assert client.get("/api/resumes").status_code == 200


def test_x_api_token_header_is_accepted(anon_client):
    response = anon_client.get("/api/resumes", headers={"X-API-Token": "test-token"})
    assert response.status_code == 200


def test_bearer_scheme_is_case_insensitive(anon_client):
    response = anon_client.get(
        "/api/jobs", headers={"Authorization": "bearer test-token"}
    )
    assert response.status_code == 200


def test_authorized_calls_behave_exactly_as_before(client, db):
    from app.models.resume import STATUS_PROCESSED, Resume

    db.add(
        Resume(
            candidate_name="Authed Candidate",
            original_filename="cv.pdf",
            stored_filename="authed-test.pdf",
            file_type="pdf",
            file_size=10,
            extracted_text="Python developer",
            skills=[],
            processing_status=STATUS_PROCESSED,
        )
    )
    db.commit()

    listing = client.get("/api/resumes")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert client.get("/api/jobs").status_code == 200


def test_auth_can_be_switched_off_when_no_token_is_configured(monkeypatch, anon_client):
    """Local development mode: unset token => routes stay open (documented)."""
    monkeypatch.delenv("API_TOKEN", raising=False)
    get_settings.cache_clear()
    try:
        assert anon_client.get("/api/resumes").status_code == 200
    finally:
        get_settings.cache_clear()  # restore the configured token for other tests


def test_missing_token_error_is_consistent_json(anon_client):
    body = anon_client.get("/api/jobs").json()
    assert set(body) == {"error"}
    assert body["error"]["status"] == 401
