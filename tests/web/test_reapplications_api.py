"""Reviewer-authorized reapplication creation from an exact declined Case."""

from __future__ import annotations

from src.db import enqueue_case_followup, finish_case_followup
from src.web.api import reapplications as api
from tests.web.conftest import TEST_PASSWORD, create_user, login


def _declined(settings):
    followup_id, _ = enqueue_case_followup(
        str(settings.db_path), account_id="us_store_999", marketplace="UK",
        brand_name="TESTBRAND", case_id="19999999999",
        submitted_at="2026-08-10 08:00:00", scheduled_at="2026-08-10 09:00:00",
    )
    finish_case_followup(
        str(settings.db_path), followup_id, "completed", "declined", "declined",
        "explicit rejection", "runtime/evidence/case.json",
    )
    return followup_id


def test_reapplication_requires_reviewer_and_is_idempotent(client, web_settings, monkeypatch):
    followup_id = _declined(web_settings)
    create_user(web_settings, "operator-reapply", TEST_PASSWORD, "operator")
    login(client, "operator-reapply", TEST_PASSWORD)
    body = {
        "source_case_followup_id": followup_id,
        "confirmed_remaining_route": ["BE", "DE", "SE", "NL", "FR"],
        "authorize_submit": True,
    }
    assert client.post("/api/reapplications", json=body).status_code == 403

    create_user(web_settings, "reviewer-reapply", TEST_PASSWORD, "reviewer")
    login(client, "reviewer-reapply", TEST_PASSWORD)
    monkeypatch.setattr(api, "_validate_job_request", lambda settings, request: (
        {"account_id": request.account}, request.brands, request.site,
    ))
    monkeypatch.setattr(api, "run_submit_preflight", lambda *_args: [
        {"code": "fixture", "ok": True, "level": "info", "message": "ok"}
    ])
    monkeypatch.setattr(api, "launch_reapplication_worker", lambda _settings: {
        "started": False, "reason": "test",
    })

    first = client.post("/api/reapplications", json=body)
    assert first.status_code == 200, first.text
    payload = first.json()
    assert payload["created"] is True
    assert payload["reapplication"]["source_case_followup_id"] == followup_id
    assert [attempt["site"] for attempt in payload["reapplication"]["attempts"]] == ["UK", "BE"]

    second = client.post("/api/reapplications", json=body)
    assert second.status_code == 200
    assert second.json()["created"] is False
    assert second.json()["reapplication"]["id"] == payload["reapplication"]["id"]


def test_eligible_declines_excludes_last_route(client, web_settings):
    eligible_id = _declined(web_settings)
    last_id, _ = enqueue_case_followup(
        str(web_settings.db_path), account_id="us_store_999", marketplace="MX",
        brand_name="TESTBRAND-MX", case_id="18888888888",
        submitted_at="2026-08-10 08:00:00", scheduled_at="2026-08-10 09:00:00",
    )
    finish_case_followup(
        str(web_settings.db_path), last_id, "completed", "declined", "declined",
        "explicit rejection", "runtime/evidence/case-mx.json",
    )
    create_user(web_settings, "viewer-reapply", TEST_PASSWORD, "viewer")
    login(client, "viewer-reapply", TEST_PASSWORD)
    response = client.get("/api/reapplications/eligible-declines")
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["eligible_declines"]] == [eligible_id]
