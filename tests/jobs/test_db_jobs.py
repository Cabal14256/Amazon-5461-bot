"""DB layer: automation_jobs / automation_job_items helpers."""

import pytest

from src.db import (
    claim_next_queued_automation_job,
    count_running_automation_jobs,
    create_automation_job,
    get_automation_job,
    get_conn,
    list_active_automation_jobs,
    list_automation_job_items,
    list_automation_jobs,
    release_automation_job_claim,
    replace_automation_job_items,
    request_automation_job_stop,
    update_automation_job,
)

from .conftest import ACTIVE_ACCOUNT


def _create(db_path, job_id="job-20260810-aaaa0001", **kwargs):
    return create_automation_job(
        str(db_path),
        job_id=job_id,
        job_type=kwargs.pop("job_type", "dry_run"),
        created_by=kwargs.pop("created_by", "operator1"),
        account_id=kwargs.pop("account_id", ACTIVE_ACCOUNT),
        marketplace=kwargs.pop("marketplace", "US"),
        brands=kwargs.pop("brands", ["TESTBRAND"]),
        **kwargs,
    )


def test_create_rejects_unknown_job_type(job_env):
    with pytest.raises(ValueError):
        _create(str(job_env.db_path), job_type="run")


def test_create_submit_job_is_queued(job_env):
    # Stage 4: submit is a real job type, born queued like any other job;
    # the explicit reviewer-gated API entry is the only creation path.
    job = _create(str(job_env.db_path), job_type="submit")
    assert job["job_type"] == "submit"
    assert job["run_status"] == "queued"


def test_create_and_get_roundtrip(job_env):
    db = str(job_env.db_path)
    job = _create(db, brands=["TESTBRAND", "TESTBRAND2"])
    assert job["run_status"] == "queued"
    fetched = get_automation_job(db, job["id"])
    assert fetched["brands"] == ["TESTBRAND", "TESTBRAND2"]
    assert fetched["job_type"] == "dry_run"
    assert fetched["account_id"] == ACTIVE_ACCOUNT


def test_submit_options_roundtrip_and_old_jobs_default_empty(job_env):
    db = str(job_env.db_path)
    submit = _create(
        db,
        job_type="submit",
        options={
            "case_followup_delay_hours": 3.5,
            "case_followup_enabled": False,
        },
    )
    assert submit["options"] == {
        "case_followup_delay_hours": 3.5,
        "case_followup_enabled": False,
    }

    legacy_shape = _create(db, job_id="job-20260810-nooptions")
    assert legacy_shape["options"] == {}


def test_db_rejects_unknown_job_options(job_env):
    with pytest.raises(ValueError, match="unknown_job_options"):
        _create(
            str(job_env.db_path),
            job_type="submit",
            options={"unsafe_option": "nope"},
        )


def test_claim_is_atomic_and_fifo(job_env):
    db = str(job_env.db_path)
    first = _create(db, job_id="job-20260810-00000001")
    _create(db, job_id="job-20260810-00000002")
    claimed = claim_next_queued_automation_job(db)
    assert claimed["id"] == first["id"]
    assert claimed["run_status"] == "starting"
    # The claimed row is no longer queued; the second job claims next.
    second_claim = claim_next_queued_automation_job(db)
    assert second_claim is not None and second_claim["id"] != first["id"]
    assert claim_next_queued_automation_job(db) is None
    release_automation_job_claim(db, first["id"])
    assert get_automation_job(db, first["id"])["run_status"] == "queued"


def test_claim_skips_auth_blocked_account_without_starving_other_account(job_env):
    db = str(job_env.db_path)
    blocked = _create(db, job_id="job-20260810-blocked")
    other = _create(
        db,
        job_id="job-20260810-other",
        account_id="us_store_other",
    )
    conn = get_conn(db)
    conn.execute(
        """INSERT INTO account_auth_blocks(
               profile_key, account_id, block_type, phase, source_type,
               status, submit_fenced, detected_at, created_at, updated_at
           ) VALUES ('fixture-blocked', ?, 'login_required', 'before_submit',
                     'automation_job', 'open', 0, ?, ?, ?)""",
        (blocked["account_id"], "2026-08-18 10:00:00", "2026-08-18 10:00:00", "2026-08-18 10:00:00"),
    )
    conn.commit()
    conn.close()

    claimed = claim_next_queued_automation_job(db)
    assert claimed["id"] == other["id"]
    assert get_automation_job(db, blocked["id"])["run_status"] == "queued"


def test_update_whitelists_columns(job_env):
    db = str(job_env.db_path)
    job = _create(db)
    update_automation_job(db, job["id"], run_status="running", pid=1234)
    assert get_automation_job(db, job["id"])["pid"] == 1234
    with pytest.raises(ValueError):
        update_automation_job(db, job["id"], brands_json="[]")


def test_request_stop_transitions(job_env):
    db = str(job_env.db_path)
    queued = _create(db, job_id="job-20260810-0000000a")
    stopped = request_automation_job_stop(db, queued["id"])
    assert stopped["run_status"] == "cancelled_before_start"
    assert stopped["stop_requested_at"]

    running = _create(db, job_id="job-20260810-0000000b")
    update_automation_job(db, running["id"], run_status="running", pid=4321)
    stopped = request_automation_job_stop(db, running["id"])
    assert stopped["run_status"] == "stop_requested"

    update_automation_job(db, running["id"], run_status="completed", exit_code=0)
    assert request_automation_job_stop(db, running["id"]) is None
    assert request_automation_job_stop(db, "job-does-not-exist") is None


def test_replace_items_is_idempotent(job_env):
    db = str(job_env.db_path)
    job = _create(db)
    items = [
        {"account_id": ACTIVE_ACCOUNT, "marketplace": "US", "brand_name": "TESTBRAND",
         "run_status": "completed", "business_status": "dry_run", "case_id": None,
         "dashboard_status": None, "evidence_root": None, "started_at": None,
         "finished_at": None, "note": None},
    ]
    replace_automation_job_items(db, job["id"], items)
    replace_automation_job_items(db, job["id"], items)
    rows = list_automation_job_items(db, job["id"])
    assert len(rows) == 1
    assert rows[0]["brand_name"] == "TESTBRAND"


def test_list_filters_pagination_and_counts(job_env):
    db = str(job_env.db_path)
    _create(db, job_id="job-20260810-00000001", job_type="diagnose")
    job2 = _create(db, job_id="job-20260810-00000002", job_type="dry_run")
    update_automation_job(db, job2["id"], run_status="running", pid=1)

    rows, total = list_automation_jobs(db, job_type="dry_run")
    assert total == 1 and rows[0]["job_type"] == "dry_run"
    rows, total = list_automation_jobs(db, run_status="running")
    assert total == 1 and rows[0]["id"] == job2["id"]
    rows, total = list_automation_jobs(db, limit=1, offset=1)
    assert total == 2 and len(rows) == 1
    assert count_running_automation_jobs(db) == 2  # queued + running
    assert {j["id"] for j in list_active_automation_jobs(db)} == {job2["id"]}
