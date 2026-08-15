"""Stage-4 submit: single-step POST /api/jobs/submit contract tests.

Everything runs against tmp_path fixtures (temporary DB, accounts.json,
brand packs).  The AdsPower API check is pointed at a dead port; the
manager tests use a recording fake process factory, so no real browser,
AdsPower profile, or Seller Central submission is ever touched.
"""

import json
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient

from src.db import create_automation_job, get_automation_job, get_conn
from src.jobs.manager import JobManager

from .conftest import TEST_PASSWORD, create_user, login

ACCOUNT = "us_store_999"
BRANDS = ["TESTBRAND"]
SUBMIT_BODY = {"account": ACCOUNT, "brands": BRANDS, "site": "US"}


def _make_brand_pack_ok(settings, brand: str = "TESTBRAND") -> None:
    brand_dir = settings.brand_packs_root / brand
    (brand_dir / "images").mkdir(parents=True, exist_ok=True)
    (brand_dir / "images" / "a.jpg").write_bytes(b"fake-image")
    (brand_dir / "manifest.json").write_text(json.dumps({
        "brand_name": brand,
        "5461": {"upload_files": ["images/a.jpg"]},
    }, ensure_ascii=False), encoding="utf-8")


@pytest.fixture()
def submit_settings(web_settings):
    """Submit enabled, valid TESTBRAND pack, AdsPower check on a dead port."""
    web_settings.submit_enabled = True
    web_settings.adspower_api_base_url = "http://127.0.0.1:9"
    _make_brand_pack_ok(web_settings, "TESTBRAND")
    return web_settings


def _client_for(app, settings, username: str, role: str) -> TestClient:
    create_user(settings, username, TEST_PASSWORD, role)
    client = TestClient(app)
    login(client, username, TEST_PASSWORD)
    return client


def _submit(client, **overrides):
    body = dict(SUBMIT_BODY)
    body.update(overrides)
    return client.post("/api/jobs/submit", json=body)


def _add_account(settings, account_id: str, **fields) -> None:
    payload = json.loads(settings.accounts_path.read_text(encoding="utf-8"))
    row = {"account_id": account_id, "marketplace": "US", "status": "active"}
    row.update(fields)
    payload["accounts"].append(row)
    settings.accounts_path.write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


# 1. Master switch off -> 403 submit_disabled.
def test_submit_disabled_gate(app, web_settings):
    web_settings.submit_enabled = False
    reviewer = _client_for(app, web_settings, "reviewer1", "reviewer")

    response = _submit(reviewer)
    assert response.status_code == 403
    assert response.json()["detail"] == "submit_disabled"

    conn = get_conn(str(web_settings.db_path))
    count = conn.execute("SELECT COUNT(*) FROM automation_jobs").fetchone()[0]
    conn.close()
    assert count == 0


def test_real_submit_is_enabled_by_default():
    from src.web.config import WebSettings

    assert WebSettings().submit_enabled is True


# 2. viewer cannot submit.
def test_viewer_cannot_submit(app, submit_settings):
    viewer = _client_for(app, submit_settings, "viewer1", "viewer")
    response = _submit(viewer)
    assert response.status_code == 403
    assert response.json()["detail"] == "forbidden"


# 3. operator cannot submit (reviewer+ required).
def test_operator_cannot_submit(app, submit_settings):
    operator = _client_for(app, submit_settings, "operator1", "operator")
    response = _submit(operator)
    assert response.status_code == 403
    assert response.json()["detail"] == "forbidden"


# 4. A single reviewer creates the submit job directly -> 200, queued.
def test_reviewer_submit_creates_queued_job(app, submit_settings):
    reviewer = _client_for(app, submit_settings, "reviewer1", "reviewer")

    response = _submit(reviewer)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload.keys()) == {"job", "preflight"}
    job = payload["job"]
    assert job["job_type"] == "submit"
    assert job["run_status"] == "queued"
    assert job["created_by"] == "reviewer1"
    assert job["account_id"] == ACCOUNT
    assert job["marketplace"] == "US"
    assert job["brands"] == BRANDS
    assert isinstance(payload["preflight"], list) and payload["preflight"]

    stored = get_automation_job(str(submit_settings.db_path), job["id"])
    assert stored["run_status"] == "queued"


# 5. Preflight blocker (no adspower_profile_id) -> 422 preflight_blocked, no job.
def test_preflight_blocked_no_profile(app, submit_settings):
    _add_account(submit_settings, "no_profile_store", entry_url="https://fixture.example.com/login")
    reviewer = _client_for(app, submit_settings, "reviewer1", "reviewer")

    response = _submit(reviewer, account="no_profile_store")
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["error"] == "preflight_blocked"
    blockers = [c for c in detail["checks"] if c["level"] == "blocker" and not c["ok"]]
    assert any(c["name"] == "adspower_profile" for c in blockers)

    conn = get_conn(str(submit_settings.db_path))
    count = conn.execute("SELECT COUNT(*) FROM automation_jobs").fetchone()[0]
    conn.close()
    assert count == 0


# 6. More brands than submit_max_brands -> 422 too_many_brands.
def test_too_many_brands(app, submit_settings):
    submit_settings.submit_max_brands = 1
    _make_brand_pack_ok(submit_settings, "TESTBRAND2")
    reviewer = _client_for(app, submit_settings, "reviewer1", "reviewer")

    response = _submit(reviewer, brands=["TESTBRAND", "TESTBRAND2"])
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["error"] == "too_many_brands"
    assert detail["max_brands"] == 1
    assert detail["brand_count"] == 2

    conn = get_conn(str(submit_settings.db_path))
    count = conn.execute("SELECT COUNT(*) FROM automation_jobs").fetchone()[0]
    conn.close()
    assert count == 0


def _recording_factory(calls: list, delay: float = 0.0):
    """Fake process factory: records the command, runs a trivial child."""

    def factory(cmd, *, stdout_path, stderr_path, env, cwd=None):
        calls.append(list(cmd))
        stdout_handle = open(stdout_path, "ab", buffering=0)
        stderr_handle = open(stderr_path, "ab", buffering=0)
        try:
            proc = subprocess.Popen(
                [sys.executable, "-c", f"import time; time.sleep({delay!r})"],
                stdout=stdout_handle,
                stderr=stderr_handle,
                stdin=subprocess.DEVNULL,
                env=env,
            )
        finally:
            stdout_handle.close()
            stderr_handle.close()
        return proc

    return factory


# 7. The dispatcher spawns a queued submit job directly (`run --submit`).
def test_dispatch_runs_queued_submit(submit_settings):
    settings = submit_settings
    db_path = str(settings.db_path)
    calls: list = []
    manager = JobManager(
        settings,
        process_factory=_recording_factory(calls),
        tick_seconds=0.05,
        lock_ttl_seconds=30,
    )

    job = create_automation_job(
        db_path,
        job_id="job-20260812-aaa00001",
        job_type="submit",
        created_by="reviewer1",
        account_id=ACCOUNT,
        marketplace="US",
        brands=BRANDS,
        run_status="queued",
    )
    manager.tick()
    assert len(calls) == 1
    cmd = calls[0]
    assert "run" in cmd
    assert "--submit" in cmd
    assert get_automation_job(db_path, job["id"])["run_status"] == "running"

    # Reap the fake child so no process outlives the test.
    deadline = time.time() + 10
    while manager._active["process"].poll() is None and time.time() < deadline:
        time.sleep(0.05)
    manager.tick()
    assert get_automation_job(db_path, job["id"])["run_status"] == "completed"


# 8. Every submit outcome lands in web_audit_events with actor/params/job id.
def test_audit_trail(app, submit_settings):
    reviewer = _client_for(app, submit_settings, "reviewer1", "reviewer")

    submit_settings.submit_enabled = False
    assert _submit(reviewer).status_code == 403
    submit_settings.submit_enabled = True

    assert _submit(reviewer, account="ghost_store").status_code == 422
    created = _submit(reviewer)
    assert created.status_code == 200, created.text
    job_id = created.json()["job"]["id"]

    conn = get_conn(str(submit_settings.db_path))
    rows = conn.execute(
        "SELECT actor_id, action, target_id, result, detail FROM web_audit_events"
        " WHERE action=?",
        ("job_submit_created",),
    ).fetchall()
    user_row = conn.execute(
        "SELECT id FROM web_users WHERE username=?", ("reviewer1",)
    ).fetchone()
    conn.close()

    triples = {(row["action"], row["target_id"], row["result"]) for row in rows}
    assert ("job_submit_created", "submit:us_store_999", "rejected:submit_disabled") in triples
    assert ("job_submit_created", "submit:ghost_store", "rejected:unknown_account") in triples
    assert ("job_submit_created", job_id, "ok") in triples

    ok_row = next(r for r in rows if r["result"] == "ok")
    assert ok_row["actor_id"] == user_row["id"]
    detail = json.loads(ok_row["detail"])
    assert detail == {"account": ACCOUNT, "site": "US", "brands": BRANDS}
