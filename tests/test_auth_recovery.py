from __future__ import annotations

import json
from datetime import datetime

from src import auth_recovery, case_id_recovery
from src.auth_guard import AuthState
from src.auth_recovery import (
    account_has_open_auth_block,
    create_auth_block,
    ensure_submission_checkpoint,
    finish_submission_reconciliation,
    get_auth_block,
    mark_submit_click_fenced,
    verify_auth_block,
)
from src.db import (
    claim_next_queued_automation_job,
    create_automation_job,
    enqueue_case_followup,
    finish_case_followup,
    get_automation_job,
    get_conn,
    init_db,
    list_automation_job_items,
    replace_automation_job_items,
    update_automation_job,
)


def _settings(tmp_path):
    accounts_path = tmp_path / "accounts.json"
    accounts_path.write_text(
        json.dumps(
            {
                "accounts": [
                    {
                        "account_id": "us_store_001",
                        "marketplace": "US",
                        "status": "active",
                        "adspower_profile_id": "fixture-profile",
                    },
                    {
                        "account_id": "us_store_alias",
                        "marketplace": "US",
                        "status": "active",
                        "adspower_profile_id": "fixture-profile",
                    },
                    {
                        "account_id": "us_store_other",
                        "marketplace": "US",
                        "status": "active",
                        "adspower_profile_id": "other-profile",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return {
        "paths": {
            "db_path": str(tmp_path / "ledger.db"),
            "accounts_path": str(accounts_path),
            "evidence_root": str(tmp_path / "evidence"),
        },
        "auth_recovery": {"poll_interval_seconds": 300},
        "case_id_recovery": {
            "enabled": True,
            "initial_delay_minutes": 10,
            "retry_interval_minutes": 30,
            "max_attempts": 12,
            "auto_start_worker": False,
        },
        "case_followup": {"auto_start_worker": False},
        "reapplication": {"auto_start_worker": False},
    }


class FakeManager:
    def __init__(self):
        self.pages = [object()]

    def get_profile_id(self, _account_id, config_path=None):
        return "fixture-profile"

    def connect(self, _profile_id):
        return self

    def disconnect(self, quiet=True):
        return None

    def new_tab(self):
        return FakeProbe()


class FakeProbe:
    url = "https://sellercentral.amazon.com/home"

    def goto(self, *_args, **_kwargs):
        return None

    def close(self):
        return None


def _authenticated(*_args, **_kwargs):
    return AuthState("authenticated", False, "ok", "https://sellercentral.amazon.com/home", True)


def _job_checkpoint(settings):
    db_path = settings["paths"]["db_path"]
    init_db(db_path)
    create_automation_job(
        db_path,
        job_id="job-auth-1",
        job_type="submit",
        created_by="fixture",
        account_id="us_store_001",
        marketplace="UK",
        brands=["TESTBRAND"],
    )
    return ensure_submission_checkpoint(
        db_path,
        owner_type="automation_job",
        owner_id="job-auth-1",
        account_id="us_store_001",
        marketplace="UK",
        brand_name="TESTBRAND",
    )


def test_pre_submit_login_resume_requeues_only_current_job(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    checkpoint = _job_checkpoint(settings)
    block = create_auth_block(
        settings,
        account_id="us_store_001",
        marketplace="UK",
        brand_name="TESTBRAND",
        block_type="login_required",
        phase="before_submit",
        source_type="automation_job",
        source_id="job-auth-1",
        checkpoint_id=checkpoint["id"],
    )
    assert get_automation_job(settings["paths"]["db_path"], "job-auth-1")["run_status"] == "waiting_human"

    monkeypatch.setattr(auth_recovery, "_browser_manager", lambda _settings: FakeManager())
    monkeypatch.setattr(auth_recovery, "detect_auth_state", _authenticated)
    result = verify_auth_block(settings, block["id"])

    assert result["status"] == "resolved"
    assert result["actions"] == ["submission_resume"]
    job = get_automation_job(settings["paths"]["db_path"], "job-auth-1")
    assert job["run_status"] == "queued"
    conn = get_conn(settings["paths"]["db_path"])
    saved = dict(conn.execute("SELECT * FROM submission_checkpoints WHERE id=?", (checkpoint["id"],)).fetchone())
    conn.close()
    assert saved["submit_click_fenced_at"] is None
    assert saved["phase"] == "resume_queued"


def test_auth_block_pauses_every_account_alias_on_same_profile(tmp_path):
    settings = _settings(tmp_path)
    db_path = settings["paths"]["db_path"]
    init_db(db_path)
    create_auth_block(
        settings,
        account_id="us_store_001",
        marketplace="UK",
        brand_name="TESTBRAND",
        block_type="login_required",
        phase="before_submit",
        source_type="direct_run",
        source_id=None,
    )
    create_automation_job(
        db_path,
        job_id="job-alias",
        job_type="submit",
        created_by="fixture",
        account_id="us_store_alias",
        marketplace="UK",
        brands=["ALIASBRAND"],
    )
    create_automation_job(
        db_path,
        job_id="job-other",
        job_type="submit",
        created_by="fixture",
        account_id="us_store_other",
        marketplace="UK",
        brands=["OTHERBRAND"],
    )

    assert account_has_open_auth_block(db_path, "us_store_alias") is True
    claimed = claim_next_queued_automation_job(db_path)
    assert claimed is not None
    assert claimed["id"] == "job-other"


def test_post_click_login_resume_never_requeues_submit_and_is_idempotent(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    checkpoint = _job_checkpoint(settings)
    fenced = mark_submit_click_fenced(
        settings["paths"]["db_path"], checkpoint["id"], phase="submit_click_dispatched"
    )
    block = create_auth_block(
        settings,
        account_id="us_store_001",
        marketplace="UK",
        brand_name="TESTBRAND",
        block_type="login_required",
        phase="after_submit",
        source_type="automation_job",
        source_id="job-auth-1",
        submit_fenced=True,
        checkpoint_id=checkpoint["id"],
    )
    monkeypatch.setattr(auth_recovery, "_browser_manager", lambda _settings: FakeManager())
    monkeypatch.setattr(auth_recovery, "detect_auth_state", _authenticated)
    monkeypatch.setattr(case_id_recovery, "launch_case_id_recovery_worker", lambda *_args, **_kwargs: {"started": False})

    first = verify_auth_block(settings, block["id"])
    second = verify_auth_block(settings, block["id"])

    assert first["status"] == "resolved"
    assert first["actions"] == ["reconciliation"]
    assert second["status"] == "already_resolved"
    job = get_automation_job(settings["paths"]["db_path"], "job-auth-1")
    assert job["run_status"] == "waiting_human"
    assert job["error_class"] == "waiting_reconciliation"
    conn = get_conn(settings["paths"]["db_path"])
    saved = dict(conn.execute("SELECT * FROM submission_checkpoints WHERE id=?", (checkpoint["id"],)).fetchone())
    recoveries = conn.execute("SELECT * FROM case_id_recoveries").fetchall()
    conn.close()
    assert saved["submit_click_fenced_at"] == fenced["submit_click_fenced_at"]
    assert saved["status"] == "waiting_reconciliation"
    assert len(recoveries) == 1
    scheduled = datetime.strptime(recoveries[0]["scheduled_at"], "%Y-%m-%d %H:%M:%S")
    assert 9 * 60 <= (scheduled - datetime.now()).total_seconds() <= 11 * 60
    assert get_auth_block(settings, block["id"])["status"] == "resolved"


def test_recovered_case_updates_one_brand_then_safely_requeues_remaining(tmp_path):
    settings = _settings(tmp_path)
    db_path = settings["paths"]["db_path"]
    init_db(db_path)
    job = create_automation_job(
        db_path,
        job_id="job-reconcile-resume",
        job_type="submit",
        created_by="fixture",
        account_id="us_store_001",
        marketplace="UK",
        brands=["FENCED", "FAILED_EARLIER", "NEXT"],
        run_status="waiting_human",
    )
    state_path = tmp_path / "job-reconcile-resume.json"
    state = {
        "created_at": "2026-08-18T10:00:00",
        "config": {},
        "batches": [{
            "batch_no": 1,
            "status": "waiting_human",
            "completed_at": None,
            "items": [
                {
                    "account_id": "us_store_001", "brand_name": "FENCED", "site": "UK",
                    "status": "waiting_human", "result": {"status": "waiting_reconciliation"},
                    "error": "waiting_reconciliation", "completed_at": None,
                },
                {
                    "account_id": "us_store_001", "brand_name": "FAILED_EARLIER", "site": "UK",
                    "status": "failed", "result": {"status": "error"}, "error": "fixture failure",
                },
                {
                    "account_id": "us_store_001", "brand_name": "NEXT", "site": "UK",
                    "status": "pending", "result": None, "error": None,
                },
            ],
        }],
        "summary": {"total": 3, "completed": 0, "failed": 1, "pending": 2},
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    update_automation_job(
        db_path,
        job["id"],
        state_file=str(state_path),
        error_class="waiting_reconciliation",
    )
    replace_automation_job_items(db_path, job["id"], [
        {"account_id": "us_store_001", "marketplace": "UK", "brand_name": "FENCED", "run_status": "waiting_human"},
        {"account_id": "us_store_001", "marketplace": "UK", "brand_name": "FAILED_EARLIER", "run_status": "failed"},
        {"account_id": "us_store_001", "marketplace": "UK", "brand_name": "NEXT", "run_status": "pending"},
    ])
    checkpoint = ensure_submission_checkpoint(
        db_path,
        owner_type="automation_job",
        owner_id=job["id"],
        account_id="us_store_001",
        marketplace="UK",
        brand_name="FENCED",
    )
    conn = get_conn(db_path)
    conn.execute(
        "UPDATE submission_checkpoints SET status='waiting_reconciliation' WHERE id=?",
        (checkpoint["id"],),
    )
    conn.commit()
    conn.close()

    finish_submission_reconciliation(
        settings,
        account_id="us_store_001",
        marketplace="UK",
        brand_name="FENCED",
        status="completed",
        case_id="fixture-case-id",
        detail="unique case recovered",
    )

    saved_state = json.loads(state_path.read_text(encoding="utf-8"))
    saved_items = saved_state["batches"][0]["items"]
    assert [item["status"] for item in saved_items] == ["completed", "failed", "pending"]
    assert saved_items[0]["result"]["case_id"] == "fixture-case-id"
    assert saved_state["summary"] == {"total": 3, "completed": 1, "failed": 1, "pending": 1}
    assert get_automation_job(db_path, job["id"])["run_status"] == "queued"
    rows = {row["brand_name"]: row for row in list_automation_job_items(db_path, job["id"])}
    assert rows["FENCED"]["run_status"] == "completed"
    assert rows["FAILED_EARLIER"]["run_status"] == "failed"
    assert rows["NEXT"]["run_status"] == "pending"


def test_case_followup_login_resume_only_requeues_original_case(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    db_path = settings["paths"]["db_path"]
    init_db(db_path)
    followup_id, _created = enqueue_case_followup(
        db_path,
        account_id="us_store_001",
        marketplace="UK",
        brand_name="TESTBRAND",
        case_id="10000000001",
        submitted_at="2026-08-18 09:00:00",
        scheduled_at="2026-08-18 10:00:00",
    )
    block = create_auth_block(
        settings,
        account_id="us_store_001",
        marketplace="UK",
        brand_name="TESTBRAND",
        block_type="two_factor_required",
        phase="case_followup",
        source_type="case_followup",
        source_id=followup_id,
        submit_fenced=True,
    )
    finish_case_followup(
        db_path,
        followup_id,
        "blocked",
        "blocked",
        "",
        "two_factor_required",
        "evidence/auth.png",
        error="two_factor_required",
    )
    monkeypatch.setattr(auth_recovery, "_browser_manager", lambda _settings: FakeManager())
    monkeypatch.setattr(auth_recovery, "detect_auth_state", _authenticated)
    from src import case_followup

    monkeypatch.setattr(case_followup, "launch_case_followup_worker", lambda *_args, **_kwargs: {"started": False})

    result = verify_auth_block(settings, block["id"])
    assert result["actions"] == ["case_followup"]
    conn = get_conn(db_path)
    followup = dict(conn.execute("SELECT * FROM case_followups WHERE id=?", (followup_id,)).fetchone())
    checkpoint_count = conn.execute("SELECT COUNT(*) FROM submission_checkpoints").fetchone()[0]
    recovery_count = conn.execute("SELECT COUNT(*) FROM case_id_recoveries").fetchone()[0]
    conn.close()
    assert followup["status"] == "pending"
    assert followup["case_id"] == "10000000001"
    assert followup["completed_at"] is None
    assert checkpoint_count == 0
    assert recovery_count == 0
