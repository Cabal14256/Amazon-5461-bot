"""Reviewer-authorized reapplication creation from an exact declined Case."""

from __future__ import annotations

from src.db import enqueue_case_followup, finish_case_followup
from src.reapplication import get_campaign_by_source_case
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
    preflight_calls = []
    worker_calls = []

    def preflight(*args):
        preflight_calls.append(args)
        return [{"code": "fixture", "ok": True, "level": "info", "message": "ok"}]

    def launch_worker(settings):
        worker_calls.append(settings)
        return {"started": False, "reason": "test"}

    monkeypatch.setattr(api, "run_submit_preflight", preflight)
    monkeypatch.setattr(api, "launch_reapplication_worker", launch_worker)

    first = client.post("/api/reapplications", json=body)
    assert first.status_code == 200, first.text
    payload = first.json()
    assert payload["created"] is True
    assert payload["reapplication"]["source_case_followup_id"] == followup_id
    assert [attempt["site"] for attempt in payload["reapplication"]["attempts"]] == ["UK", "BE"]

    second_body = dict(body, confirmed_remaining_route=["XX"])
    second = client.post("/api/reapplications", json=second_body)
    assert second.status_code == 200
    assert second.json()["created"] is False
    assert second.json()["reapplication"]["id"] == payload["reapplication"]["id"]
    assert second.json()["reapplication"]["route"] == ["UK", "BE", "DE", "SE", "NL", "FR"]
    assert second.json()["preflight"] == []
    assert second.json()["worker"] == {"started": False, "reason": "existing_campaign"}
    assert len(preflight_calls) == 1
    assert len(worker_calls) == 1


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


def test_first_reapplication_preflight_blocker_creates_nothing(
    client, web_settings, monkeypatch
):
    followup_id = _declined(web_settings)
    create_user(web_settings, "reviewer-blocked", TEST_PASSWORD, "reviewer")
    login(client, "reviewer-blocked", TEST_PASSWORD)
    monkeypatch.setattr(
        api,
        "_validate_job_request",
        lambda settings, request: (
            {"account_id": request.account},
            request.brands,
            request.site,
        ),
    )
    monkeypatch.setattr(
        api,
        "run_submit_preflight",
        lambda *_args: [
            {"code": "fixture", "ok": False, "level": "blocker", "message": "blocked"}
        ],
    )

    response = client.post(
        "/api/reapplications",
        json={
            "source_case_followup_id": followup_id,
            "confirmed_remaining_route": ["BE", "DE", "SE", "NL", "FR"],
            "authorize_submit": True,
        },
    )

    assert response.status_code == 422
    assert get_campaign_by_source_case(api._runtime_settings(web_settings), followup_id) is None
