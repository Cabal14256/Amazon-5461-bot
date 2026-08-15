from datetime import datetime, timedelta

import pytest

from src.db import claim_due_case_followups, enqueue_case_followup, init_db, list_case_followups
from src.reapplication import (
    attach_case_followup,
    build_attempt_command,
    claim_due_attempt,
    create_campaign,
    get_attempt,
    handle_case_outcome,
    list_campaigns,
)


def _settings(tmp_path):
    return {
        "paths": {"db_path": str(tmp_path / "ledger.db")},
        "case_followup": {"delay_hours": 2.0},
        "reapplication": {
            "enabled": True,
            "decline_delay_hours": 2.0,
            "routes": {
                "NA": ["US", "MX"],
                "EU": ["UK", "BE", "DE", "SE", "NL", "FR"],
            },
        },
    }


def _task(campaign, *, case_id="19999999999"):
    attempt = campaign["attempt"]
    return {
        "id": 1,
        "account_id": campaign["account_id"],
        "brand_name": campaign["brand_name"],
        "marketplace": attempt["site"],
        "case_id": case_id,
        "reapplication_campaign_id": campaign["id"],
        "reapplication_attempt_id": attempt["id"],
    }


def test_declined_advances_eu_route_exactly_two_hours(tmp_path):
    settings = _settings(tmp_path)
    campaign = create_campaign(
        settings,
        "us_store_002",
        "DEMO_HOME",
        "EU",
        submit_authorized=True,
    )
    transition_time = datetime(2026, 8, 10, 10, 0, 0)

    result = handle_case_outcome(
        settings,
        _task(campaign),
        {"result": "declined", "decision_reason": "explicit rejection"},
        now=transition_time,
    )

    assert result["status"] == "next_scheduled"
    assert result["next_site"] == "BE"
    assert result["scheduled_at"] == "2026-08-10 12:00:00"
    campaigns = list_campaigns(settings)
    assert campaigns[0]["status"] == "next_scheduled"
    assert campaigns[0]["current_route_index"] == 1


def test_false_approved_advances_from_us_to_mx(tmp_path):
    settings = _settings(tmp_path)
    campaign = create_campaign(
        settings,
        "us_store_002",
        "DEMO_WILL",
        "NA",
        submit_authorized=True,
    )

    result = handle_case_outcome(
        settings,
        _task(campaign),
        {"result": "false_approved", "decision_reason": "not effective"},
        now=datetime(2026, 8, 10, 14, 0, 0),
    )

    assert result["status"] == "next_scheduled"
    assert result["next_site"] == "MX"


def test_last_route_rejection_stops_as_route_exhausted(tmp_path):
    settings = _settings(tmp_path)
    campaign = create_campaign(
        settings,
        "us_store_002",
        "DEMO_JUNO",
        "NA",
        start_site="MX",
        submit_authorized=True,
    )

    result = handle_case_outcome(
        settings,
        _task(campaign),
        {"result": "declined", "decision_reason": "explicit rejection"},
    )

    assert result["status"] == "route_exhausted"
    assert list_campaigns(settings)[0]["status"] == "route_exhausted"


def test_last_route_false_approval_also_stops_as_route_exhausted(tmp_path):
    settings = _settings(tmp_path)
    campaign = create_campaign(
        settings,
        "us_store_002",
        "DEMO_JUNO",
        "NA",
        start_site="MX",
        submit_authorized=True,
    )

    result = handle_case_outcome(
        settings,
        _task(campaign),
        {"result": "false_approved", "decision_reason": "not effective"},
    )

    assert result["status"] == "route_exhausted"


def test_approved_stops_campaign_as_passed(tmp_path):
    settings = _settings(tmp_path)
    campaign = create_campaign(
        settings,
        "us_store_002",
        "DEMO_JADE",
        "EU",
        submit_authorized=True,
    )

    result = handle_case_outcome(
        settings,
        _task(campaign),
        {"result": "approved", "decision_reason": "effective approval confirmed"},
    )

    assert result["status"] == "passed"
    assert list_campaigns(settings)[0]["status"] == "passed"


def test_manual_result_pauses_and_duplicate_result_is_idempotent(tmp_path):
    settings = _settings(tmp_path)
    campaign = create_campaign(
        settings,
        "us_store_002",
        "DEMO_SHIELD",
        "EU",
        submit_authorized=True,
    )
    task = _task(campaign)

    first = handle_case_outcome(
        settings,
        task,
        {"result": "action_required", "decision_reason": "documents requested"},
    )
    duplicate = handle_case_outcome(
        settings,
        task,
        {"result": "action_required", "decision_reason": "documents requested"},
    )

    assert first["status"] == "paused"
    assert duplicate["status"] == "already_handled"
    assert len(list_campaigns(settings)) == 1


def test_case_followup_and_attempt_are_linked_both_ways(tmp_path):
    settings = _settings(tmp_path)
    db_path = settings["paths"]["db_path"]
    campaign = create_campaign(
        settings,
        "us_store_002",
        "DEMO_HOME",
        "EU",
        submit_authorized=True,
    )
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    due = (datetime.now() + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    followup_id, _ = enqueue_case_followup(
        db_path,
        "us_store_002",
        "UK",
        "DEMO_HOME",
        "19999999998",
        now,
        due,
        reapplication_campaign_id=campaign["id"],
        reapplication_attempt_id=campaign["attempt"]["id"],
    )

    link = attach_case_followup(
        settings,
        campaign["id"],
        campaign["attempt"]["id"],
        followup_id,
        "19999999998",
        "UK",
    )

    assert link["status"] == "waiting_case"
    attempt = get_attempt(settings, campaign["attempt"]["id"])
    assert attempt["case_followup_id"] == followup_id
    task = next(row for row in list_case_followups(db_path) if row["id"] == followup_id)
    assert task["reapplication_campaign_id"] == campaign["id"]
    assert task["reapplication_attempt_id"] == campaign["attempt"]["id"]


def test_unauthorized_campaign_is_not_claimed_or_executable(tmp_path):
    settings = _settings(tmp_path)
    campaign = create_campaign(
        settings,
        "us_store_002",
        "DEMO_HOME",
        "EU",
        submit_authorized=False,
        scheduled_at="2020-01-01 00:00:00",
    )

    assert claim_due_attempt(settings, due_at="2030-01-01 00:00:00") is None
    attempt = get_attempt(settings, campaign["attempt"]["id"])
    with pytest.raises(ValueError, match="does not authorize"):
        build_attempt_command(settings, attempt)


def test_reapplication_only_case_claim_excludes_historical_tasks(tmp_path):
    settings = _settings(tmp_path)
    db_path = settings["paths"]["db_path"]
    init_db(db_path)
    due = "2026-08-10 08:00:00"
    enqueue_case_followup(
        db_path,
        "us_store_007",
        "BE",
        "DEMO_WILL",
        "19999999991",
        due,
        due,
    )
    linked_id, _ = enqueue_case_followup(
        db_path,
        "us_store_002",
        "UK",
        "DEMO_HOME",
        "19999999992",
        due,
        due,
        reapplication_campaign_id=100,
        reapplication_attempt_id=200,
    )

    claimed = claim_due_case_followups(
        db_path,
        due_at="2026-08-10 09:00:00",
        limit=10,
        reapplication_only=True,
    )

    assert [row["id"] for row in claimed] == [linked_id]


def test_attempt_command_is_scoped_and_uses_unique_state(tmp_path):
    settings = _settings(tmp_path)
    campaign = create_campaign(
        settings,
        "us_store_002",
        "DEMO_HOME",
        "EU",
        submit_authorized=True,
    )
    attempt = get_attempt(settings, campaign["attempt"]["id"])

    command, paths = build_attempt_command(settings, attempt, python_executable="python-test")

    assert command[0] == "python-test"
    assert command[command.index("--accounts") + 1] == "us_store_002"
    assert command[command.index("--brands") + 1] == "DEMO_HOME"
    assert command[command.index("--site") + 1] == "UK"
    assert "--dry-run" not in command
    assert "--yes" in command
    assert "--no-confirm" in command
    assert f"attempt_{attempt['id']}_UK.json" in str(paths["state"])
