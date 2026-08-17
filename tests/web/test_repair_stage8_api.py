"""Stage-8 approval API role matrix, state guards and enriched reads."""

from __future__ import annotations

import json
import os

from fastapi.testclient import TestClient

import src.web.api.repair_jobs as repair_jobs_api
from src.db import (
    create_automation_job,
    create_patch_job,
    finish_repair_job,
    get_repair_job,
    mark_incident_patch_ready,
    mark_incident_patching,
    mark_incident_triaged,
    mark_incident_validated,
    mark_incident_validating,
    record_incident,
    set_repair_job_status,
    update_automation_job,
)
from tests.web.conftest import TEST_PASSWORD, create_user, login


def _role_client(app, settings, username, role):
    client = TestClient(app)
    create_user(settings, username, TEST_PASSWORD, role)
    login(client, username, TEST_PASSWORD)
    return client


def _awaiting_validation(settings, signature="stage8-api"):
    settings.codex_state_root = settings.state_root / "repair"
    settings.codex_logs_root = settings.logs_root / "repair"
    incident, _ = record_incident(
        str(settings.db_path),
        signature=signature,
        scope_type="account",
        flow_type="5461",
        classification="selector_missing",
        confidence=0.9,
    )
    mark_incident_triaged(str(settings.db_path), incident["id"])
    mark_incident_patching(str(settings.db_path), incident["id"])
    job = create_patch_job(str(settings.db_path), incident["id"])
    finish_repair_job(
        str(settings.db_path),
        job["id"],
        "patch_ready",
        patch_sha="a" * 40,
        changed_files_json='["tests/test_contract.py"]',
        risk_level="R0",
        tests_passed=1,
    )
    mark_incident_patch_ready(str(settings.db_path), incident["id"])
    mark_incident_validating(str(settings.db_path), incident["id"])
    mark_incident_validated(str(settings.db_path), incident["id"])
    validation_path = settings.codex_state_root / str(job["id"]) / "validation.json"
    validation_path.parent.mkdir(parents=True, exist_ok=True)
    validation_log = settings.codex_logs_root / str(job["id"]) / "validation" / "01-allowed.log"
    validation_log.parent.mkdir(parents=True, exist_ok=True)
    validation_log.write_text("fixture validation log\n", encoding="utf-8")
    validation_path.write_text(
        json.dumps({
            "status": "pass",
            "steps": [{
                "name": "allowed_paths",
                "status": "passed",
                "log_path": str(validation_log),
            }],
            "failure_reason": "",
        }),
        encoding="utf-8",
    )
    set_repair_job_status(
        str(settings.db_path),
        job["id"],
        from_statuses=("patch_ready",),
        to_status="awaiting_validation_approval",
        extra_cols={"validation_json_path": str(validation_path)},
    )
    return incident, get_repair_job(str(settings.db_path), job["id"])


def test_validation_approval_requires_reviewer_and_is_idempotent(app, web_settings):
    _incident, job = _awaiting_validation(web_settings)
    viewer = _role_client(app, web_settings, "stage8viewer", "viewer")
    operator = _role_client(app, web_settings, "stage8operator", "operator")
    reviewer = _role_client(app, web_settings, "stage8reviewer", "reviewer")

    url = f"/api/repair-jobs/{job['id']}/approve-validation"
    assert viewer.post(url, json={}).status_code == 403
    assert operator.post(url, json={}).status_code == 403
    response = reviewer.post(url, json={"note": "diff and validation reviewed"})
    assert response.status_code == 200, response.text
    assert response.json()["job"]["status"] == "canary"
    assert reviewer.post(url, json={}).status_code == 409


def test_confirm_canary_requires_note_evidence_and_completed_jobs(app, web_settings):
    _incident, job = _awaiting_validation(web_settings, signature="stage8-canary-api")
    operator = _role_client(app, web_settings, "canaryoperator", "operator")
    reviewer = _role_client(app, web_settings, "canaryreviewer", "reviewer")
    db_path = str(web_settings.db_path)
    diagnose = create_automation_job(
        db_path, "canary-diagnose", "diagnose", "repair-workflow",
        "us_store_999", "US", ["TESTBRAND"],
    )
    dry_run = create_automation_job(
        db_path, "canary-dry-run", "dry_run", "repair-workflow",
        "us_store_999", "US", ["TESTBRAND"],
    )
    for automation in (diagnose, dry_run):
        update_automation_job(
            db_path, automation["id"], run_status="completed", exit_code=0
        )
    evidence = web_settings.evidence_root / "jobs" / diagnose["id"] / "final.png"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_bytes(b"fixture-image")
    log = web_settings.logs_root / "jobs" / dry_run["id"] / "stdout.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("fixture dry-run log\n", encoding="utf-8")
    set_repair_job_status(
        db_path,
        job["id"],
        from_statuses=("awaiting_validation_approval",),
        to_status="canary",
        extra_cols={
            "canary_job_ids_json": json.dumps({
                "diagnose": diagnose["id"],
                "dry_run": dry_run["id"],
                "ready_for_review": True,
            })
        },
    )
    url = f"/api/repair-jobs/{job['id']}/confirm-canary"
    assert operator.post(
        url, json={"note": "checked", "evidence_reviewed": True}
    ).status_code == 403
    assert reviewer.post(url, json={"note": "", "evidence_reviewed": True}).status_code == 422
    assert reviewer.post(url, json={"note": "checked", "evidence_reviewed": False}).status_code == 422
    response = reviewer.post(
        url, json={"note": "screenshots and logs match dry-run", "evidence_reviewed": True}
    )
    assert response.status_code == 200, response.text
    assert response.json()["job"]["status"] == "awaiting_release_approval"

    detail = reviewer.get(f"/api/repair-jobs/{job['id']}").json()
    assert detail["validation"]["status"] == "pass"
    assert detail["validation"]["steps"][0]["log_path"].startswith("repair/")
    assert not os.path.isabs(detail["validation"]["steps"][0]["log_path"])
    assert [row["decision"] for row in detail["approvals"]] == ["confirm_canary"]
    assert {row["kind"] for row in detail["canary_jobs"]} == {"diagnose", "dry_run"}
    assert detail["restart_required"] is False
    artifacts = [
        artifact
        for row in detail["canary_jobs"]
        for artifact in row["artifacts"]
    ]
    assert {artifact["root"] for artifact in artifacts} == {"evidence", "logs"}
    assert all(not os.path.isabs(artifact["path"]) for artifact in artifacts)


def test_release_switch_reject_note_and_restart_guards(app, web_settings):
    incident, job = _awaiting_validation(web_settings, signature="stage8-release-api")
    reviewer = _role_client(app, web_settings, "releasereviewer", "reviewer")
    admin = _role_client(app, web_settings, "releaseadmin", "admin")
    db_path = str(web_settings.db_path)
    set_repair_job_status(
        db_path,
        job["id"],
        from_statuses=("awaiting_validation_approval",),
        to_status="awaiting_release_approval",
    )

    release_url = f"/api/repair-jobs/{job['id']}/approve-release"
    assert reviewer.post(release_url, json={"patch_sha": "a" * 40}).status_code == 403
    response = admin.post(release_url, json={"patch_sha": "a" * 40})
    assert response.status_code == 403
    assert response.json()["detail"] == "release_disabled"

    # Rollback/reject notes are mandatory before any Git mutation.
    assert admin.post(f"/api/repair-jobs/{job['id']}/rollback", json={"note": ""}).status_code == 422
    reject = reviewer.post(
        f"/api/repair-jobs/{job['id']}/reject", json={"note": "risk not accepted"}
    )
    assert reject.status_code == 200
    assert reject.json()["job"]["status"] == "rejected"

    # A separate release-pending row cannot claim restart while served by the
    # same process that recorded the merge.
    _incident2, job2 = _awaiting_validation(web_settings, signature="stage8-restart-api")
    set_repair_job_status(
        db_path,
        job2["id"],
        from_statuses=("awaiting_validation_approval",),
        to_status="release_pending_restart",
        extra_cols={"release_process_pid": os.getpid(), "release_sha": "b" * 40},
    )
    restart = admin.post(f"/api/repair-jobs/{job2['id']}/post-release-check")
    assert restart.status_code == 409
    assert restart.json()["detail"] == "restart_required"
    assert incident["id"] > 0


def test_post_release_check_requires_admin_and_a_new_healthy_process(
    app, web_settings, monkeypatch
):
    _incident, job = _awaiting_validation(
        web_settings, signature="stage8-post-release-api"
    )
    reviewer = _role_client(app, web_settings, "postreviewer", "reviewer")
    admin = _role_client(app, web_settings, "postadmin", "admin")
    db_path = str(web_settings.db_path)
    set_repair_job_status(
        db_path,
        job["id"],
        from_statuses=("awaiting_validation_approval",),
        to_status="release_pending_restart",
        extra_cols={"release_process_pid": 111, "release_sha": "b" * 40},
    )
    url = f"/api/repair-jobs/{job['id']}/post-release-check"
    assert reviewer.post(url).status_code == 403
    assert reviewer.post(
        f"/api/repair-jobs/{job['id']}/rollback", json={"note": "reviewer cannot revert"}
    ).status_code == 403

    class _HealthyTask:
        @staticmethod
        def done():
            return False

    app.state.job_manager._task = _HealthyTask()
    monkeypatch.setattr(repair_jobs_api.os, "getpid", lambda: 222)
    monkeypatch.setattr(repair_jobs_api, "verify_release_head", lambda *_args: None)

    response = admin.post(url)

    assert response.status_code == 200, response.text
    assert response.json()["job"]["status"] == "post_release_check"
