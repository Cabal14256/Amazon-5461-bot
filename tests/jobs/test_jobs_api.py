"""API integration: whitelist validation, roles, full chain, SSE, audit."""

import json
import os

import pytest
from fastapi.testclient import TestClient

from src.auth_recovery import ensure_submission_checkpoint
from src.db import (
    add_web_user,
    create_automation_job,
    get_automation_job,
    get_conn,
    replace_automation_job_items,
    update_automation_job,
    upsert_case_outcome_status,
)
from src.jobs.manager import JobManager
from src.web.app import create_app
from src.web.auth import hash_password

from .conftest import (
    ACTIVE_ACCOUNT,
    PAUSED_ACCOUNT,
    TEST_PASSWORD,
    make_fake_process_factory,
    wait_for,
)

FAKE_BATCH_STATE = {
    "created_at": "2026-08-10T12:00:00",
    "batches": [{
        "batch_no": 1,
        "items": [
            {"account_id": ACTIVE_ACCOUNT, "brand_name": "TESTBRAND", "site": "US",
             "status": "completed", "result": {"status": "dry_run"}},
        ],
    }],
}


@pytest.fixture()
def app(job_env):
    manager = JobManager(
        job_env,
        process_factory=make_fake_process_factory(batch_state=FAKE_BATCH_STATE),
        tick_seconds=0.05,
        lock_ttl_seconds=30,
    )
    return create_app(job_env, job_manager=manager)


@pytest.fixture()
def client(app):
    with TestClient(app) as test_client:
        yield test_client


def _create_user(settings, username, role):
    return add_web_user(str(settings.db_path), username, hash_password(TEST_PASSWORD), role=role)


def _login(client, username):
    response = client.post("/api/auth/login", json={"username": username, "password": TEST_PASSWORD})
    assert response.status_code == 200, response.text


@pytest.fixture()
def operator_client(client, job_env):
    _create_user(job_env, "operator1", "operator")
    _login(client, "operator1")
    return client


@pytest.fixture()
def viewer_client(client, job_env):
    _create_user(job_env, "viewer1", "viewer")
    _login(client, "viewer1")
    return client


@pytest.fixture()
def admin_client(client, job_env):
    _create_user(job_env, "admin1", "admin")
    _login(client, "admin1")
    return client


def _audit_rows(settings, action=None):
    conn = get_conn(str(settings.db_path))
    sql = "SELECT * FROM web_audit_events"
    params = ()
    if action:
        sql += " WHERE action=?"
        params = (action,)
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


# -- whitelist validation ----------------------------------------------------

VALID_BODY = {"account": ACTIVE_ACCOUNT, "brands": ["TESTBRAND"], "site": "US"}


@pytest.mark.parametrize("body,detail", [
    ({"account": "no_such", "brands": ["TESTBRAND"]}, "unknown_account"),
    ({"account": PAUSED_ACCOUNT, "brands": ["TESTBRAND"]}, "account_not_enabled"),
    ({"account": ACTIVE_ACCOUNT, "brands": []}, "no_brands"),
    ({"account": ACTIVE_ACCOUNT, "brands": ["NO_SUCH_BRAND"]}, "unknown_brand:NO_SUCH_BRAND"),
    ({"account": ACTIVE_ACCOUNT, "brands": ["TESTBRAND"] * 21}, "too_many_brands"),
    ({"account": ACTIVE_ACCOUNT, "brands": ["TESTBRAND"], "site": "XX"}, "unknown_site"),
])
def test_create_validation_422(operator_client, job_env, body, detail):
    for endpoint in ("/api/jobs/diagnose", "/api/jobs/dry-run"):
        response = operator_client.post(endpoint, json=body)
        assert response.status_code == 422, response.text
        assert response.json()["detail"] == detail
    rejected = [r for r in _audit_rows(job_env, "job_create") if r["result"].startswith("rejected")]
    assert len(rejected) == 2


# -- role enforcement ---------------------------------------------------------


def test_viewer_cannot_create(viewer_client):
    response = viewer_client.post("/api/jobs/dry-run", json=VALID_BODY)
    assert response.status_code == 403


def test_anonymous_cannot_create_or_read(client):
    assert client.post("/api/jobs/dry-run", json=VALID_BODY).status_code == 401
    assert client.get("/api/jobs").status_code == 401
    assert client.get("/api/jobs/job-x/events").status_code == 401


def test_force_terminate_requires_admin(operator_client, admin_client, job_env):
    # Hold the profile lock so the dispatcher cannot drain the queued job
    # before the assertions run.
    from src.db import acquire_profile_lock, release_profile_lock
    from src.jobs.profile_locks import profile_key_for_account

    db = str(job_env.db_path)
    key = profile_key_for_account(job_env.accounts_path, ACTIVE_ACCOUNT)
    assert acquire_profile_lock(db, key, "automation_job", "job-blocker", ttl_seconds=600)
    try:
        job = create_automation_job(
            db, "job-20260810-ft000001", "dry_run", "operator1",
            ACTIVE_ACCOUNT, "US", ["TESTBRAND"],
        )
        # operator_client and admin_client share one cookie jar (last login
        # wins), so re-login before each role's assertions.
        _login(operator_client, "operator1")
        assert operator_client.post(f"/api/jobs/{job['id']}/force-terminate").status_code == 403

        _login(admin_client, "admin1")
        response = admin_client.post(f"/api/jobs/{job['id']}/force-terminate")
        assert response.status_code == 200, response.text
        assert response.json()["job"]["run_status"] == "terminated_unknown_state"
        assert _audit_rows(job_env, "job_force_terminate")

        # Already terminal -> 409.
        assert admin_client.post(f"/api/jobs/{job['id']}/force-terminate").status_code == 409
        assert admin_client.post("/api/jobs/job-nope/force-terminate").status_code == 404
    finally:
        release_profile_lock(db, key, owner_id="job-blocker")


# -- full chain: create -> dispatcher lifecycle -> completed -------------------


def test_create_dispatch_complete_full_chain(operator_client, job_env):
    response = operator_client.post("/api/jobs/dry-run", json=VALID_BODY)
    assert response.status_code == 200, response.text
    job = response.json()["job"]
    # The API returns the live database row. The background dispatcher may
    # advance it before the response is serialized, including completing this
    # deliberately tiny fixture. Failure/unknown terminal states remain invalid.
    assert job["run_status"] in {"queued", "starting", "running", "completed"}
    assert job["job_type"] == "dry_run"
    assert job["brands"] == ["TESTBRAND"]
    # No server-side filesystem paths leak into the API.
    assert "stdout_log" not in job and "state_file" not in job

    job_id = job["id"]
    assert wait_for(
        lambda: get_automation_job(str(job_env.db_path), job_id)["run_status"] == "completed",
        timeout=15.0,
    )
    detail = operator_client.get(f"/api/jobs/{job_id}")
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["job"]["run_status"] == "completed"
    assert payload["job"]["exit_code"] == 0
    assert [item["brand_name"] for item in payload["items"]] == ["TESTBRAND"]

    listed = operator_client.get("/api/jobs", params={"run_status": "completed"})
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert _audit_rows(job_env, "job_create")


def test_job_list_ignores_legacy_state_files(operator_client, job_env):
    # Legacy runtime/state/*.json and data/batch_state.json must not appear.
    (job_env.state_root / "669_nl_legacy_worker.json").write_text(
        json.dumps({"pid": 1234, "started": "2026-08-01"}), encoding="utf-8"
    )
    job_env.data_root.mkdir(parents=True, exist_ok=True)
    (job_env.data_root / "batch_state.json").write_text(
        json.dumps({"created_at": "2026-08-01", "batches": []}), encoding="utf-8"
    )
    response = operator_client.get("/api/jobs")
    assert response.status_code == 200
    assert response.json()["jobs"] == []
    assert response.json()["total"] == 0


def test_job_detail_corrects_stale_waiting_login_reason(operator_client, job_env):
    db = str(job_env.db_path)
    job = create_automation_job(
        db,
        "job-20260818-stale-reason",
        "submit",
        "operator1",
        ACTIVE_ACCOUNT,
        "US",
        ["TESTBRAND"],
        run_status="waiting_human",
    )
    update_automation_job(db, job["id"], error_class="waiting_login")
    checkpoint = ensure_submission_checkpoint(
        db,
        owner_type="automation_job",
        owner_id=job["id"],
        account_id=ACTIVE_ACCOUNT,
        marketplace="US",
        brand_name="TESTBRAND",
    )
    conn = get_conn(db)
    conn.execute(
        "UPDATE submission_checkpoints SET status='waiting_reconciliation' WHERE id=?",
        (checkpoint["id"],),
    )
    conn.commit()
    conn.close()

    response = operator_client.get(f"/api/jobs/{job['id']}")

    assert response.status_code == 200
    assert response.json()["job"]["error_class"] == "waiting_reconciliation"


def test_job_item_includes_authoritative_business_status(operator_client, job_env):
    db = str(job_env.db_path)
    job = create_automation_job(
        db,
        "job-20260818-authoritative",
        "dry_run",
        "operator1",
        ACTIVE_ACCOUNT,
        "US",
        ["TESTBRAND"],
    )
    update_automation_job(db, job["id"], run_status="completed", exit_code=0)
    replace_automation_job_items(db, job["id"], [{
        "account_id": ACTIVE_ACCOUNT,
        "marketplace": "US",
        "brand_name": "TESTBRAND",
        "run_status": "pending",
        "business_status": "draft",
    }])
    upsert_case_outcome_status(
        db,
        ACTIVE_ACCOUNT,
        "US",
        "TESTBRAND",
        "approved",
        method="dashboard",
    )

    response = operator_client.get(f"/api/jobs/{job['id']}")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["run_status"] == "pending"
    assert item["business_status"] == "draft"
    assert item["authoritative"]["status"] == "approved"
    assert item["authoritative"]["source"] == "dashboard_today"


def test_request_stop_queued_job(operator_client, job_env, app):
    # Hold the profile lock so the running dispatcher cannot pick the job up;
    # the job stays queued deterministically.
    from src.db import acquire_profile_lock, release_profile_lock
    from src.jobs.profile_locks import profile_key_for_account

    db = str(job_env.db_path)
    key = profile_key_for_account(job_env.accounts_path, ACTIVE_ACCOUNT)
    assert acquire_profile_lock(db, key, "automation_job", "job-blocker", ttl_seconds=600)
    try:
        job = create_automation_job(
            db, "job-20260810-rs000001", "dry_run", "operator1",
            ACTIVE_ACCOUNT, "US", ["TESTBRAND"],
        )
        # A queued job transitions straight to cancelled_before_start.
        response = operator_client.post(f"/api/jobs/{job['id']}/request-stop")
        assert response.status_code == 200, response.text
        assert response.json()["job"]["run_status"] == "cancelled_before_start"
        assert _audit_rows(job_env, "job_request_stop")
        # Terminal now -> 409.
        assert operator_client.post(f"/api/jobs/{job['id']}/request-stop").status_code == 409
        assert operator_client.post("/api/jobs/job-nope/request-stop").status_code == 404
    finally:
        release_profile_lock(db, key, owner_id="job-blocker")


# -- SSE and logs --------------------------------------------------------------


def _make_terminal_job_with_log(settings, job_id="job-20260810-sse00001"):
    from src.jobs.paths import ensure_job_paths

    paths = ensure_job_paths(settings.state_root, settings.logs_root, settings.evidence_root, job_id)
    paths.stdout_log.write_text(
        "line one\ncontact fixture-secret-user@example.com for details\nline three\n",
        encoding="utf-8",
    )
    job = create_automation_job(
        str(settings.db_path), job_id, "dry_run", "operator1",
        ACTIVE_ACCOUNT, "US", ["TESTBRAND"],
    )
    update_automation_job(
        str(settings.db_path), job_id,
        run_status="completed", exit_code=0,
        stdout_log=str(paths.stdout_log), stderr_log=str(paths.stderr_log),
    )
    return job, paths


def test_sse_streams_job_and_redacted_log_events(operator_client, job_env):
    job, _paths = _make_terminal_job_with_log(job_env)
    events = []
    with operator_client.stream("GET", f"/api/jobs/{job['id']}/events") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line:
                events.append(line)
    text = "\n".join(events)
    assert "event: job" in text
    assert '"run_status": "completed"' in text
    assert "event: log" in text
    assert "line one" in text
    # Redaction applied to streamed log lines.
    assert "fixture-secret-user@example.com" not in text
    assert "[REDACTED_EMAIL]" in text


def test_logs_endpoint_returns_redacted_tail(operator_client, job_env):
    job, _paths = _make_terminal_job_with_log(job_env)
    response = operator_client.get(f"/api/jobs/{job['id']}/logs", params={"lines": 2})
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_lines"] == 3
    assert len(payload["lines"]) == 2
    assert "example.com" not in payload["lines"][0]


def test_events_and_logs_404_for_unknown_job(operator_client):
    assert operator_client.get("/api/jobs/job-nope/events").status_code == 404
    assert operator_client.get("/api/jobs/job-nope/logs").status_code == 404
    assert operator_client.get("/api/jobs/job-nope").status_code == 404


# -- queue reason annotation ----------------------------------------------------


def test_queued_job_reports_case_followup_wait(operator_client, job_env):
    from src.db import claim_due_case_followups, enqueue_case_followup

    db = str(job_env.db_path)
    due = "2020-01-01 00:00:00"
    enqueue_case_followup(db, ACTIVE_ACCOUNT, "US", "TESTBRAND", "19999999901", due, due)
    # status=running -> the account has an active follow-up, so the
    # dispatcher keeps deferring the web job and the reason must say so.
    assert claim_due_case_followups(db, limit=1)

    response = operator_client.post("/api/jobs/dry-run", json=VALID_BODY)
    assert response.status_code == 200, response.text
    job_id = response.json()["job"]["id"]

    listed = operator_client.get("/api/jobs").json()["jobs"]
    row = next(j for j in listed if j["id"] == job_id)
    assert row["run_status"] == "queued"
    assert row["queue_reason"] == "waiting_case_followup"

    detail = operator_client.get(f"/api/jobs/{job_id}").json()["job"]
    assert detail["queue_reason"] == "waiting_case_followup"


def test_queued_job_reports_profile_lock_wait(operator_client, job_env):
    from src.db import acquire_profile_lock, release_profile_lock
    from src.jobs.profile_locks import profile_key_for_account

    db = str(job_env.db_path)
    key = profile_key_for_account(job_env.accounts_path, ACTIVE_ACCOUNT)
    owner = f"case-followup:{os.getpid()}:0:us_store_999:US"
    assert acquire_profile_lock(db, key, "case_followup_worker", owner, ttl_seconds=600)
    try:
        response = operator_client.post("/api/jobs/dry-run", json=VALID_BODY)
        assert response.status_code == 200, response.text
        job_id = response.json()["job"]["id"]

        detail = operator_client.get(f"/api/jobs/{job_id}").json()["job"]
        assert detail["run_status"] == "queued"
        assert detail["queue_reason"] == "waiting_profile_lock"
    finally:
        release_profile_lock(db, key, owner_id=owner)


def test_queue_reason_absent_for_terminal_job(operator_client, job_env):
    job, _paths = _make_terminal_job_with_log(job_env, job_id="job-20260810-qr000001")
    detail = operator_client.get(f"/api/jobs/{job['id']}").json()["job"]
    assert detail["queue_reason"] is None


def test_non_submit_job_rejects_submit_only_options(operator_client):
    body = dict(VALID_BODY)
    body["options"] = {"case_followup_enabled": False}
    response = operator_client.post("/api/jobs/dry-run", json=body)
    assert response.status_code == 422
    assert response.json()["detail"] == "job_options_not_supported"
