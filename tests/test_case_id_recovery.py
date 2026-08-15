from datetime import datetime

from src import case_id_recovery
from src.case_id_recovery import (
    claim_due_case_id_recoveries,
    is_case_id_recovery_candidate,
    list_case_id_recoveries,
    process_claimed_recovery,
    schedule_case_id_recovery,
)
from src.reapplication import create_campaign, get_attempt, list_campaigns


def _settings(tmp_path):
    return {
        "paths": {
            "db_path": str(tmp_path / "ledger.db"),
            "evidence_root": str(tmp_path / "evidence"),
        },
        "case_followup": {"delay_hours": 2.0},
        "case_id_recovery": {
            "enabled": True,
            "initial_delay_minutes": 0,
            "retry_interval_minutes": 30,
            "max_attempts": 2,
            "auto_start_worker": False,
        },
        "reapplication": {
            "enabled": True,
            "decline_delay_hours": 2.0,
            "routes": {"NA": ["US", "MX"], "EU": ["UK", "BE", "DE", "SE", "NL", "FR"]},
        },
        "feishu_bitable": {"enabled": False},
    }


def test_missing_case_candidate_requires_submission_evidence():
    assert is_case_id_recovery_candidate({"status": "partial", "case_id": None})
    assert is_case_id_recovery_candidate({"status": "uncertain", "case_id": None})
    assert is_case_id_recovery_candidate(
        {
            "status": "failed",
            "note": "表单已提交但未获得 Case ID",
            "dashboard_check": {"status": "navigation_failed"},
        }
    )
    assert is_case_id_recovery_candidate(
        {"status": "failed", "note": "Connect brand 后提交未获得 Case ID"}
    )
    assert not is_case_id_recovery_candidate({"status": "failed", "note": "login expired"})
    assert not is_case_id_recovery_candidate(
        {"status": "partial", "dashboard_check": {"status": "draft"}}
    )
    assert not is_case_id_recovery_candidate({"status": "success", "case_id": "13153167372"})


def test_schedule_is_idempotent_and_holds_reapplication_at_waiting_case_id(tmp_path):
    settings = _settings(tmp_path)
    campaign = create_campaign(
        settings,
        "us_store_002",
        "DEMO_HOME",
        "EU",
        submit_authorized=True,
    )
    kwargs = {
        "account_id": "us_store_002",
        "site": "UK",
        "brand_name": "DEMO_HOME",
        "sku": "002-UK-DEMO_HOME-A1",
        "submitted_at": "2026-08-10 10:00:00",
        "reapplication_campaign_id": campaign["id"],
        "reapplication_attempt_id": campaign["attempt"]["id"],
    }

    first = schedule_case_id_recovery(settings, **kwargs)
    duplicate = schedule_case_id_recovery(settings, **kwargs)

    assert first["created"] is True
    assert duplicate["created"] is False
    assert len(list_case_id_recoveries(settings)) == 1
    assert get_attempt(settings, campaign["attempt"]["id"])["status"] == "waiting_case_id"
    assert list_campaigns(settings)[0]["status"] == "waiting_case_id"


def test_unique_exact_brand_case_id_hands_back_to_case_followup(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    scheduled = schedule_case_id_recovery(
        settings,
        account_id="us_store_002",
        site="DE",
        brand_name="DEMO_WILL",
        sku="002-DE-DEMO_WILL-A1",
        submitted_at="2026-08-10 10:00:00",
    )
    task = claim_due_case_id_recoveries(
        settings,
        due_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )[0]
    monkeypatch.setattr(
        case_id_recovery,
        "check_selling_applications",
        lambda *_args, **_kwargs: {
            "status": "under_review",
            "case_id": "13153167372",
            "case_ids": ["13153167372"],
            "evidence_dir": "runtime/evidence/case-id",
        },
    )
    captured = []
    from src import case_followup

    monkeypatch.setattr(
        case_followup,
        "schedule_case_followup",
        lambda **kwargs: captured.append(kwargs) or {"id": 99, "case_id": kwargs["case_id"]},
    )

    result = process_claimed_recovery(settings, task)

    assert result["status"] == "recovered"
    assert result["case_id"] == "13153167372"
    assert captured[0]["site"] == "DE"
    assert captured[0]["submitted_at"] == "2026-08-10 10:00:00"
    row = next(item for item in list_case_id_recoveries(settings) if item["id"] == scheduled["id"])
    assert row["status"] == "completed"


def test_multiple_case_ids_are_never_guessed(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    schedule_case_id_recovery(
        settings,
        account_id="us_store_002",
        site="BE",
        brand_name="DEMO_JADE",
        sku="002-BE-DEMO_JADE-A1",
        submitted_at="2026-08-10 10:00:00",
    )
    task = claim_due_case_id_recoveries(
        settings,
        due_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )[0]
    monkeypatch.setattr(
        case_id_recovery,
        "check_selling_applications",
        lambda *_args, **_kwargs: {
            "status": "under_review",
            "case_id": "13153167372",
            "case_ids": ["13153167372", "13153167373"],
            "evidence_dir": "runtime/evidence/case-id",
        },
    )

    result = process_claimed_recovery(settings, task)

    assert result["status"] == "retry"
    assert "multiple Case IDs" in result["reason"]


def test_recovery_exhaustion_pauses_campaign_instead_of_advancing(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    settings["case_id_recovery"]["max_attempts"] = 1
    campaign = create_campaign(
        settings,
        "us_store_002",
        "DEMO_JUNO",
        "EU",
        submit_authorized=True,
    )
    schedule_case_id_recovery(
        settings,
        account_id="us_store_002",
        site="UK",
        brand_name="DEMO_JUNO",
        sku="002-UK-DEMO_JUNO-A1",
        submitted_at="2026-08-10 10:00:00",
        reapplication_campaign_id=campaign["id"],
        reapplication_attempt_id=campaign["attempt"]["id"],
    )
    task = claim_due_case_id_recoveries(
        settings,
        due_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )[0]
    monkeypatch.setattr(
        case_id_recovery,
        "check_selling_applications",
        lambda *_args, **_kwargs: {
            "status": "not_found",
            "case_id": None,
            "case_ids": [],
            "evidence_dir": "runtime/evidence/case-id",
        },
    )

    result = process_claimed_recovery(settings, task)

    assert result["status"] == "manual_review"
    assert list_campaigns(settings)[0]["status"] == "paused"
    assert get_attempt(settings, campaign["attempt"]["id"])["status"] == "manual_review"
