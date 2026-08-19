from datetime import datetime, timedelta

import pytest

from src import case_followup, reapplication
from src.auth_recovery import ensure_submission_checkpoint, mark_submit_click_fenced
from src.db import (
    claim_due_case_followups,
    enqueue_case_followup,
    finish_case_followup,
    get_conn,
    init_db,
    list_case_followups,
)
from src.reapplication import (
    CONFIRMED_DRAFT_REASON,
    ReapplicationStartError,
    attach_case_followup,
    auto_schedule_declined_case,
    auto_schedule_declined_cases,
    build_attempt_command,
    claim_due_attempt,
    confirmed_draft_confirmation,
    create_campaign,
    create_campaign_from_declined_case,
    declined_case_candidate,
    get_attempt,
    handle_case_outcome,
    list_campaigns,
    mark_confirmed_draft_attempt,
    rearm_confirmed_draft_attempt,
)
from src.state_files import atomic_write_json


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


def _declined_followup(
    settings,
    *,
    site="UK",
    brand="DEMO_HOME",
    case_id="19999999999",
):
    db_path = settings["paths"]["db_path"]
    init_db(db_path)
    followup_id, _ = enqueue_case_followup(
        db_path,
        account_id="us_store_002",
        marketplace=site,
        brand_name=brand,
        case_id=case_id,
        submitted_at="2026-08-10 08:00:00",
        scheduled_at="2026-08-10 09:00:00",
    )
    finish_case_followup(
        db_path,
        followup_id,
        "completed",
        "declined",
        "declined",
        "explicit rejection",
        "runtime/evidence/case.json",
    )
    return followup_id


def test_declined_case_authorization_preserves_source_and_schedules_next(tmp_path):
    settings = _settings(tmp_path)
    followup_id = _declined_followup(settings)
    candidate = declined_case_candidate(settings, followup_id)
    assert candidate["remaining_route"] == ["BE", "DE", "SE", "NL", "FR"]

    campaign = create_campaign_from_declined_case(
        settings,
        followup_id,
        confirmed_remaining_route=candidate["remaining_route"],
        authorize_submit=True,
    )

    assert campaign["created"] is True
    assert campaign["current_route_index"] == 1
    assert [attempt["site"] for attempt in campaign["attempts"]] == ["UK", "BE"]
    assert campaign["attempts"][0]["status"] == "completed"
    assert campaign["attempts"][0]["final_result"] == "declined"
    assert campaign["attempts"][1]["status"] == "scheduled"
    source = next(row for row in list_case_followups(settings["paths"]["db_path"]) if row["id"] == followup_id)
    assert source["reapplication_campaign_id"] == campaign["id"]

    repeated = create_campaign_from_declined_case(
        settings,
        followup_id,
        confirmed_remaining_route=candidate["remaining_route"],
        authorize_submit=True,
    )
    assert repeated["created"] is False
    assert repeated["id"] == campaign["id"]


def test_explicit_decline_auto_authorizes_finite_route_idempotently(tmp_path):
    settings = _settings(tmp_path)
    settings["reapplication"].update(
        {
            "auto_authorize_declined_cases": True,
            "auto_backfill_declined_cases": True,
        }
    )
    followup_id = _declined_followup(settings, brand="DEMO_AUTO")

    first = auto_schedule_declined_case(
        settings,
        followup_id,
        launch_worker=False,
    )
    repeated = auto_schedule_declined_case(
        settings,
        followup_id,
        launch_worker=False,
    )

    assert first["created"] is True
    assert first["next_site"] == "BE"
    assert repeated["created"] is False
    campaigns = list_campaigns(settings)
    assert len(campaigns) == 1
    assert campaigns[0]["submit_authorized"] == 1
    assert campaigns[0]["authorization_source"] == "automatic_decline"
    source = next(
        row
        for row in list_case_followups(settings["paths"]["db_path"])
        if row["id"] == followup_id
    )
    assert source["reapplication_campaign_id"] == campaigns[0]["id"]


def test_case_outcome_hook_auto_schedules_new_explicit_decline(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    settings["reapplication"]["auto_authorize_declined_cases"] = True
    followup_id = _declined_followup(settings, brand="DEMO_HOOK")
    task = next(
        row
        for row in list_case_followups(settings["paths"]["db_path"])
        if row["id"] == followup_id
    )
    monkeypatch.setattr(
        reapplication,
        "launch_reapplication_worker",
        lambda _settings: {"started": False, "reason": "test"},
    )
    result = {"result": "declined", "decision_reason": "explicit rejection"}

    case_followup._handle_reapplication_transition(settings, task, result)

    assert result["reapplication_transition"]["created"] is True
    assert result["reapplication_transition"]["next_site"] == "BE"
    assert result["reapplication_worker"] == {"started": False, "reason": "test"}


def test_auto_backfill_enrolls_only_eligible_explicit_declines(tmp_path):
    settings = _settings(tmp_path)
    settings["reapplication"].update(
        {
            "auto_authorize_declined_cases": True,
            "auto_backfill_declined_cases": True,
        }
    )
    _declined_followup(settings, site="UK", brand="DEMO_EU_AUTO")
    _declined_followup(
        settings,
        site="UK",
        brand="DEMO_EU_AUTO",
        case_id="18888888887",
    )
    _declined_followup(settings, site="US", brand="DEMO_NA_AUTO")
    _declined_followup(settings, site="FR", brand="DEMO_EU_LAST")

    first = auto_schedule_declined_cases(settings, launch_worker=False)
    repeated = auto_schedule_declined_cases(settings, launch_worker=False)

    assert first == {
        "status": "completed",
        "created": 2,
        "skipped": 1,
        "errors": 0,
        "worker": {"started": False, "reason": "not_requested"},
    }
    assert repeated["created"] == 0
    assert repeated["errors"] == 0
    campaigns = list_campaigns(settings)
    assert {(c["region"], c["brand_name"]) for c in campaigns} == {
        ("EU", "DEMO_EU_AUTO"),
        ("NA", "DEMO_NA_AUTO"),
    }


def test_auto_decline_cutoff_is_inclusive_and_ignores_older_history(tmp_path):
    settings = _settings(tmp_path)
    settings["reapplication"].update(
        {
            "auto_authorize_declined_cases": True,
            "auto_backfill_declined_cases": True,
        }
    )
    older_id = _declined_followup(settings, brand="DEMO_BEFORE_CUTOFF")
    cutoff_id = _declined_followup(
        settings,
        brand="DEMO_AT_CUTOFF",
        case_id="18888888886",
    )
    newer_id = _declined_followup(
        settings,
        brand="DEMO_AFTER_CUTOFF",
        case_id="18888888885",
    )
    settings["reapplication"]["auto_backfill_min_followup_id"] = cutoff_id

    ignored = auto_schedule_declined_case(
        settings,
        older_id,
        launch_worker=False,
    )
    result = auto_schedule_declined_cases(settings, launch_worker=False)

    assert ignored == {
        "status": "before_cutoff",
        "created": False,
        "min_followup_id": cutoff_id,
    }
    assert result["created"] == 2
    assert result["errors"] == 0
    campaigns = list_campaigns(settings)
    assert {campaign["source_case_followup_id"] for campaign in campaigns} == {
        cutoff_id,
        newer_id,
    }


def test_auto_decline_policy_disabled_creates_nothing(tmp_path):
    settings = _settings(tmp_path)
    followup_id = _declined_followup(settings, brand="DEMO_DISABLED")

    result = auto_schedule_declined_case(
        settings,
        followup_id,
        launch_worker=False,
    )

    assert result == {"status": "disabled", "created": False}
    assert list_campaigns(settings) == []


def test_declined_case_authorization_rejects_route_mismatch_and_last_site(tmp_path):
    settings = _settings(tmp_path)
    followup_id = _declined_followup(settings)
    with pytest.raises(ReapplicationStartError, match="route_confirmation_mismatch"):
        create_campaign_from_declined_case(
            settings,
            followup_id,
            confirmed_remaining_route=["DE", "BE"],
            authorize_submit=True,
        )

    mx_followup = _declined_followup(settings, site="MX", brand="DEMO_MX")
    with pytest.raises(ReapplicationStartError, match="route_exhausted"):
        declined_case_candidate(settings, mx_followup)


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


def test_false_approved_keeps_same_case_followup_active(tmp_path):
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

    assert result["status"] == "waiting_case"
    assert result["site"] == "US"
    campaigns = list_campaigns(settings)
    assert campaigns[0]["status"] == "waiting_case"
    assert campaigns[0]["current_route_index"] == 0
    attempt = get_attempt(settings, campaign["attempt"]["id"])
    assert attempt["status"] == "waiting_case"
    assert attempt["final_result"] is None


def test_false_approved_followup_exhaustion_pauses_campaign(tmp_path):
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
        {
            "result": "false_approved",
            "decision_reason": "six automatic checks exhausted",
            "automatic_followup_exhausted": True,
        },
        now=datetime(2026, 8, 10, 15, 0, 0),
    )

    assert result["status"] == "paused"
    assert result["reason"] == "case_followup_attempt_limit_reached:false_approved"
    campaigns = list_campaigns(settings)
    assert campaigns[0]["status"] == "paused"
    assert campaigns[0]["stop_reason"] == result["reason"]
    attempt = get_attempt(settings, campaign["attempt"]["id"])
    assert attempt["status"] == "manual_review"
    assert attempt["final_result"] == "false_approved"


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


def test_last_route_false_approval_keeps_same_case_followup_active(tmp_path):
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

    assert result["status"] == "waiting_case"
    assert result["site"] == "MX"
    assert list_campaigns(settings)[0]["status"] == "waiting_case"


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
    conn = get_conn(db_path)
    conn.execute(
        """UPDATE reapplication_attempts
           SET decision_reason='stale retry detail', error='stale error'
           WHERE id=?""",
        (int(campaign["attempt"]["id"]),),
    )
    conn.execute(
        "UPDATE reapplication_campaigns SET stop_reason='stale retry detail' WHERE id=?",
        (int(campaign["id"]),),
    )
    conn.commit()
    conn.close()

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
    assert attempt["decision_reason"] is None
    assert attempt["error"] is None
    assert list_campaigns(settings)[0]["stop_reason"] is None
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


def test_confirmed_dashboard_draft_can_be_explicitly_rearmed(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    campaign = create_campaign(
        settings,
        "us_store_682",
        "JavoYion",
        "EU",
        start_site="DE",
        submit_authorized=True,
    )
    attempt_id = int(campaign["attempt"]["id"])
    db_path = settings["paths"]["db_path"]
    conn = get_conn(db_path)
    conn.execute(
        "UPDATE reapplication_attempts SET status='manual_review' WHERE id=?",
        (attempt_id,),
    )
    conn.execute(
        "UPDATE reapplication_campaigns SET status='paused' WHERE id=?",
        (int(campaign["id"]),),
    )
    now = "2026-08-19 00:25:26"
    conn.execute(
        """INSERT INTO case_id_recoveries(
               account_id, marketplace, brand_name, submitted_at, scheduled_at,
               status, dashboard_status, reapplication_campaign_id,
               reapplication_attempt_id, created_at, updated_at
           ) VALUES (?, ?, ?, ?, ?, 'manual_review', 'draft', ?, ?, ?, ?)""",
        (
            "us_store_682",
            "DE",
            "JavoYion",
            now,
            now,
            int(campaign["id"]),
            attempt_id,
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()
    checkpoint = ensure_submission_checkpoint(
        db_path,
        owner_type="reapplication_attempt",
        owner_id=str(attempt_id),
        account_id="us_store_682",
        marketplace="DE",
        brand_name="JavoYion",
    )
    mark_submit_click_fenced(db_path, int(checkpoint["id"]), phase="submit")

    monkeypatch.setattr(reapplication, "PROJECT_ROOT", tmp_path)
    paths = reapplication.build_attempt_paths(get_attempt(settings, attempt_id))
    atomic_write_json(
        paths["state"],
        {"batches": [{"items": [{"status": "failed", "brand_name": "JavoYion"}]}]},
    )

    result = rearm_confirmed_draft_attempt(
        settings,
        attempt_id,
        authorized_by="user_request",
        scheduled_at="2026-08-19 01:00:00",
    )

    assert result["status"] == "scheduled"
    assert not paths["state"].exists()
    assert result["archived_state"]
    assert get_attempt(settings, attempt_id)["status"] == "scheduled"
    conn = get_conn(db_path)
    saved = conn.execute(
        "SELECT * FROM submission_checkpoints WHERE id=?", (int(checkpoint["id"]),)
    ).fetchone()
    campaign_row = conn.execute(
        "SELECT status FROM reapplication_campaigns WHERE id=?", (int(campaign["id"]),)
    ).fetchone()
    conn.close()
    assert saved["status"] == "active"
    assert saved["submit_click_fenced_at"] is None
    assert campaign_row["status"] == "next_scheduled"


def test_immediate_dashboard_draft_is_marked_and_rearmed_without_recovery_row(
    monkeypatch, tmp_path
):
    settings = _settings(tmp_path)
    campaign = create_campaign(
        settings,
        "us_store_682",
        "JavoYion",
        "EU",
        start_site="NL",
        submit_authorized=True,
    )
    attempt_id = int(campaign["attempt"]["id"])
    db_path = settings["paths"]["db_path"]
    monkeypatch.setattr(reapplication, "PROJECT_ROOT", tmp_path)
    attempt = get_attempt(settings, attempt_id)
    paths = reapplication.build_attempt_paths(attempt)
    evidence = tmp_path / "runtime" / "evidence" / "dashboard.png"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_bytes(b"dashboard evidence")
    atomic_write_json(
        paths["state"],
        {
            "batches": [
                {
                    "items": [
                        {
                            "account_id": "us_store_682",
                            "brand_name": "JavoYion",
                            "site": "NL",
                            "reapplication_attempt_id": attempt_id,
                            "status": "failed",
                            "result": {
                                "status": "draft",
                                "case_id": None,
                                "dashboard_check": {
                                    "checked": True,
                                    "status": "draft",
                                    "case_id": None,
                                    "brand_mentions": 1,
                                    "matched_text": (
                                        "JavoYion [catalogue authorisation] → draft"
                                    ),
                                    "dashboard_navigation": {"ok": True},
                                    "evidence_files": [str(evidence)],
                                },
                            },
                        }
                    ]
                }
            ]
        },
    )
    conn = get_conn(db_path)
    conn.execute(
        """UPDATE reapplication_attempts
           SET status='failed', state_path=?, error='generic failure' WHERE id=?""",
        (str(paths["state"]), attempt_id),
    )
    conn.execute(
        "UPDATE reapplication_campaigns SET status='paused' WHERE id=?",
        (int(campaign["id"]),),
    )
    conn.commit()
    conn.close()
    checkpoint = ensure_submission_checkpoint(
        db_path,
        owner_type="reapplication_attempt",
        owner_id=str(attempt_id),
        account_id="us_store_682",
        marketplace="NL",
        brand_name="JavoYion",
    )
    mark_submit_click_fenced(db_path, int(checkpoint["id"]), phase="submit")
    conn = get_conn(db_path)
    conn.execute(
        """UPDATE submission_checkpoints
           SET status='manual_review', phase='reconciliation' WHERE id=?""",
        (int(checkpoint["id"]),),
    )
    conn.commit()
    conn.close()

    confirmation = confirmed_draft_confirmation(settings, attempt_id)
    marked = mark_confirmed_draft_attempt(settings, attempt_id)

    assert confirmation["source"] == "immediate_dashboard"
    assert marked["status"] == "draft"
    saved_attempt = get_attempt(settings, attempt_id)
    assert saved_attempt["status"] == "manual_review"
    assert saved_attempt["final_result"] == "draft"
    assert saved_attempt["decision_reason"] == CONFIRMED_DRAFT_REASON
    assert saved_attempt["error"] is None
    conn = get_conn(db_path)
    fenced = conn.execute(
        "SELECT * FROM submission_checkpoints WHERE id=?", (int(checkpoint["id"]),)
    ).fetchone()
    conn.close()
    assert fenced["submit_click_fenced_at"] is not None
    assert fenced["status"] == "manual_review"

    resumed = rearm_confirmed_draft_attempt(
        settings,
        attempt_id,
        authorized_by="user_request",
        scheduled_at="2026-08-19 12:00:00",
    )

    assert resumed["confirmation_source"] == "immediate_dashboard"
    assert resumed["archived_state"]
    assert not paths["state"].exists()
    assert get_attempt(settings, attempt_id)["status"] == "scheduled"
    conn = get_conn(db_path)
    cleared = conn.execute(
        "SELECT * FROM submission_checkpoints WHERE id=?", (int(checkpoint["id"]),)
    ).fetchone()
    conn.close()
    assert cleared["submit_click_fenced_at"] is None
    assert cleared["phase"] == "draft_retry_authorized"


def test_immediate_draft_confirmation_fails_closed_on_navigation_failure(
    monkeypatch, tmp_path
):
    settings = _settings(tmp_path)
    campaign = create_campaign(
        settings,
        "us_store_682",
        "JavoYion",
        "EU",
        start_site="NL",
        submit_authorized=True,
    )
    attempt_id = int(campaign["attempt"]["id"])
    db_path = settings["paths"]["db_path"]
    monkeypatch.setattr(reapplication, "PROJECT_ROOT", tmp_path)
    attempt = get_attempt(settings, attempt_id)
    paths = reapplication.build_attempt_paths(attempt)
    evidence = tmp_path / "runtime" / "evidence" / "dashboard.png"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_bytes(b"not trusted after navigation failure")
    atomic_write_json(
        paths["state"],
        {
            "batches": [
                {
                    "items": [
                        {
                            "account_id": "us_store_682",
                            "brand_name": "JavoYion",
                            "site": "NL",
                            "reapplication_attempt_id": attempt_id,
                            "result": {
                                "status": "draft",
                                "dashboard_check": {
                                    "checked": True,
                                    "status": "draft",
                                    "brand_mentions": 1,
                                    "matched_text": (
                                        "JavoYion [catalogue authorisation] → draft"
                                    ),
                                    "dashboard_navigation": {"ok": False},
                                    "evidence_files": [str(evidence)],
                                },
                            },
                        }
                    ]
                }
            ]
        },
    )
    conn = get_conn(db_path)
    conn.execute(
        "UPDATE reapplication_attempts SET status='failed', state_path=? WHERE id=?",
        (str(paths["state"]), attempt_id),
    )
    conn.execute(
        "UPDATE reapplication_campaigns SET status='paused' WHERE id=?",
        (int(campaign["id"]),),
    )
    conn.commit()
    conn.close()
    checkpoint = ensure_submission_checkpoint(
        db_path,
        owner_type="reapplication_attempt",
        owner_id=str(attempt_id),
        account_id="us_store_682",
        marketplace="NL",
        brand_name="JavoYion",
    )
    mark_submit_click_fenced(db_path, int(checkpoint["id"]), phase="submit")
    conn = get_conn(db_path)
    conn.execute(
        """UPDATE submission_checkpoints
           SET status='manual_review', phase='reconciliation' WHERE id=?""",
        (int(checkpoint["id"]),),
    )
    conn.commit()
    conn.close()

    assert confirmed_draft_confirmation(settings, attempt_id) is None
    assert mark_confirmed_draft_attempt(settings, attempt_id) is None
    assert get_attempt(settings, attempt_id)["status"] == "failed"
