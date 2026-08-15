"""JobManager: serial dispatch, reaping, recovery, stop/terminate semantics."""

from src.db import (
    claim_due_case_followups,
    create_automation_job,
    enqueue_case_followup,
    get_automation_job,
    get_profile_lock,
    list_automation_job_items,
    now_str,
    update_automation_job,
)
from src.jobs.manager import JobManager
from src.jobs.paths import job_paths
from src.jobs.profile_locks import profile_key_for_profile_id

from .conftest import (
    ACTIVE_ACCOUNT,
    PROFILE_ID,
    dead_pid,
    make_fake_process_factory,
    wait_for,
)

BRANDS = ["TESTBRAND", "TESTBRAND2"]

FAKE_BATCH_STATE = {
    "created_at": "2026-08-10T12:00:00",
    "batches": [{
        "batch_no": 1,
        "items": [
            {"account_id": ACTIVE_ACCOUNT, "brand_name": "TESTBRAND", "site": "US",
             "status": "completed", "result": {"status": "dry_run"}},
            {"account_id": ACTIVE_ACCOUNT, "brand_name": "TESTBRAND2", "site": "US",
             "status": "skipped", "result": {}},
        ],
    }],
}


def _create_job(settings, job_id, **kwargs):
    return create_automation_job(
        str(settings.db_path),
        job_id=job_id,
        job_type=kwargs.pop("job_type", "dry_run"),
        created_by="operator1",
        account_id=kwargs.pop("account_id", ACTIVE_ACCOUNT),
        marketplace="US",
        brands=BRANDS,
        **kwargs,
    )


def _manager(settings, **factory_kwargs):
    return JobManager(
        settings,
        process_factory=make_fake_process_factory(**factory_kwargs),
        tick_seconds=0.05,
        lock_ttl_seconds=30,
    )


def test_serial_dispatch_and_reap_writes_items(job_env):
    settings = job_env
    job1 = _create_job(settings, "job-20260810-00000001")
    job2 = _create_job(settings, "job-20260810-00000002")
    manager = _manager(settings, batch_state=FAKE_BATCH_STATE)

    manager.tick()
    assert get_automation_job(str(settings.db_path), job1["id"])["run_status"] == "running"
    # Global serial: the second job stays queued while the first runs.
    assert get_automation_job(str(settings.db_path), job2["id"])["run_status"] == "queued"

    assert wait_for(lambda: manager._active["process"].poll() is not None)
    manager.tick()  # reap job1, then dispatch job2 in the same tick
    done1 = get_automation_job(str(settings.db_path), job1["id"])
    assert done1["run_status"] == "completed"
    assert done1["exit_code"] == 0
    assert get_automation_job(str(settings.db_path), job2["id"])["run_status"] == "running"

    # Items were parsed from the per-job batch state.
    items = list_automation_job_items(str(settings.db_path), job1["id"])
    assert {i["brand_name"] for i in items} == set(BRANDS)
    assert {i["run_status"] for i in items} == {"completed", "skipped"}

    assert wait_for(lambda: manager._active["process"].poll() is not None)
    manager.tick()
    assert get_automation_job(str(settings.db_path), job2["id"])["run_status"] == "completed"
    assert manager._active is None


def test_profile_lock_held_during_run_and_released_after(job_env):
    settings = job_env
    job = _create_job(settings, "job-20260810-00000003")
    manager = _manager(settings, delay=5.0)
    key = profile_key_for_profile_id(PROFILE_ID)

    manager.tick()
    lock = get_profile_lock(str(settings.db_path), key)
    assert lock is not None and lock["owner_id"] == job["id"]

    manager.force_terminate(job["id"])
    assert get_profile_lock(str(settings.db_path), key) is None
    terminated = get_automation_job(str(settings.db_path), job["id"])
    assert terminated["run_status"] == "terminated_unknown_state"
    assert terminated["error_class"] == "force_terminated"


def test_nonzero_exit_marks_failed(job_env):
    settings = job_env
    job = _create_job(settings, "job-20260810-00000004")
    manager = _manager(settings, exit_code=3)
    manager.tick()
    assert wait_for(lambda: manager._active["process"].poll() is not None)
    manager.tick()
    done = get_automation_job(str(settings.db_path), job["id"])
    assert done["run_status"] == "failed"
    assert done["error_class"] == "exit_nonzero"
    assert done["exit_code"] == 3


def test_recovery_running_dead_pid_and_queued_continues(job_env):
    settings = job_env
    crashed = _create_job(settings, "job-20260810-00000005")
    update_automation_job(
        str(settings.db_path), crashed["id"], run_status="running", pid=dead_pid()
    )
    stopped = _create_job(settings, "job-20260810-00000006")
    update_automation_job(
        str(settings.db_path), stopped["id"],
        run_status="stop_requested", pid=dead_pid(), stop_requested_at=now_str(),
    )
    queued = _create_job(settings, "job-20260810-00000007")

    manager = _manager(settings)
    manager.recover()

    assert get_automation_job(str(settings.db_path), crashed["id"])["run_status"] == (
        "terminated_unknown_state"
    )
    recovered_stop = get_automation_job(str(settings.db_path), stopped["id"])
    assert recovered_stop["run_status"] == "failed"
    assert recovered_stop["error_class"] == "cancelled"
    # Queued jobs are untouched by recovery and dispatch normally afterwards.
    assert get_automation_job(str(settings.db_path), queued["id"])["run_status"] == "queued"
    manager.tick()
    assert get_automation_job(str(settings.db_path), queued["id"])["run_status"] == "running"


def test_request_stop_does_not_kill_but_maps_cancelled(job_env):
    settings = job_env
    job = _create_job(settings, "job-20260810-00000008")
    manager = _manager(settings, delay=0.5)
    manager.tick()

    stopped = manager.request_stop(job["id"])
    assert stopped["run_status"] == "stop_requested"
    paths = job_paths(settings.state_root, settings.logs_root, settings.evidence_root, job["id"])
    assert paths.stop_file.exists()
    # Safe stop: the child process is NOT killed.
    assert manager._active["process"].poll() is None

    assert wait_for(lambda: manager._active["process"].poll() is not None)
    manager.tick()
    done = get_automation_job(str(settings.db_path), job["id"])
    assert done["run_status"] == "failed"
    assert done["error_class"] == "cancelled"


def test_active_case_followup_blocks_dispatch(job_env):
    settings = job_env
    db = str(settings.db_path)
    followup_id, _ = enqueue_case_followup(
        db, ACTIVE_ACCOUNT, "US", "TESTBRAND", "999000111",
        submitted_at=now_str(), scheduled_at=now_str(),
    )
    claimed = claim_due_case_followups(db, limit=1)
    assert claimed and claimed[0]["id"] == followup_id  # status now 'running'

    job = _create_job(settings, "job-20260810-00000009")
    manager = _manager(settings)
    manager.tick()
    # Conflict: job returns to queued, nothing dispatched.
    assert get_automation_job(db, job["id"])["run_status"] == "queued"
    assert manager._active is None
