"""Persistent Stage-8 runner claim, recovery and canary sequencing."""

from __future__ import annotations

import json
from pathlib import Path

import src.repair.runner as runner_module
from src.db import (
    claim_next_patch_ready_for_validation,
    create_patch_job,
    finish_repair_job,
    get_incident,
    get_repair_job,
    init_db,
    list_automation_jobs,
    mark_incident_patch_ready,
    mark_incident_patching,
    mark_incident_triaged,
    mark_incident_validated,
    mark_incident_validating,
    record_incident,
    set_repair_job_status,
    set_repair_job_worktree,
    update_automation_job,
)
from src.repair.runner import RepairWorkflowRunner
from src.web.config import WebSettings


def _settings(tmp_path):
    runtime = tmp_path / "runtime"
    settings = WebSettings(
        db_path=runtime / "state" / "ledger.db",
        repo_root=tmp_path / "repo",
        accounts_path=runtime / "private" / "accounts.json",
        marketplaces_dir=tmp_path / "config" / "marketplaces",
        brand_packs_root=tmp_path / "brand_packs",
        codex_state_root=runtime / "state" / "repair",
        codex_logs_root=runtime / "logs" / "repair",
        session_secret="0" * 64,
    )
    init_db(str(settings.db_path))
    return settings


def _patch_ready(settings, signature="runner-stage8"):
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
    set_repair_job_worktree(
        str(settings.db_path),
        job["id"],
        worktree_path=str(settings.repo_root),
        branch_name=f"codex/repair-{incident['id']}-fixture",
        baseline_sha="base",
    )
    finish_repair_job(
        str(settings.db_path),
        job["id"],
        "patch_ready",
        changed_files_json='["tests/test_contract.py"]',
        patch_sha="patch",
    )
    mark_incident_patch_ready(str(settings.db_path), incident["id"])
    return get_incident(str(settings.db_path), incident["id"]), get_repair_job(
        str(settings.db_path), job["id"]
    )


class _FakeProcess:
    pid = 987654

    def poll(self):
        return None


def test_runner_atomically_claims_only_one_validation_job(tmp_path):
    settings = _settings(tmp_path)
    _incident, job = _patch_ready(settings)
    _incident2, job2 = _patch_ready(settings, signature="runner-stage8-second")
    spawned = []

    def factory(*args, **kwargs):
        spawned.append((args, kwargs))
        return _FakeProcess()

    runner = RepairWorkflowRunner(settings, process_factory=factory)
    runner.tick()
    runner.tick()

    claimed = get_repair_job(str(settings.db_path), job["id"])
    assert claimed["status"] == "validating"
    assert claimed["validation_pid"] == _FakeProcess.pid
    assert get_repair_job(str(settings.db_path), job2["id"])["status"] == "patch_ready"
    assert len(spawned) == 1
    assert Path(settings.codex_state_root / str(job["id"]) / "validation-request.json").is_file()


def test_database_claim_enforces_global_serial_validation(tmp_path):
    settings = _settings(tmp_path)
    _incident, first = _patch_ready(settings, signature="runner-claim-first")
    _incident2, second = _patch_ready(settings, signature="runner-claim-second")

    claimed = claim_next_patch_ready_for_validation(str(settings.db_path), 1001)
    blocked = claim_next_patch_ready_for_validation(str(settings.db_path), 1002)

    assert claimed["id"] == first["id"]
    assert blocked is None
    assert get_repair_job(str(settings.db_path), second["id"])["status"] == "patch_ready"


def test_recovery_requeues_only_dead_validation_pid(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    incident, job = _patch_ready(settings)
    runner = RepairWorkflowRunner(settings)
    runner.tick = lambda: None
    claim_next_patch_ready_for_validation(str(settings.db_path), 123456)
    monkeypatch.setattr(runner_module, "pid_running", lambda _pid: False)

    runner.recover()

    assert get_repair_job(str(settings.db_path), job["id"])["status"] == "patch_ready"
    assert get_incident(str(settings.db_path), incident["id"])["status"] == "patch_ready"


def test_recovery_keeps_validation_with_live_pid(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    incident, job = _patch_ready(settings, signature="runner-live-pid")
    claim_next_patch_ready_for_validation(str(settings.db_path), 123456)
    monkeypatch.setattr(runner_module, "pid_running", lambda _pid: True)

    RepairWorkflowRunner(settings).recover()

    assert get_repair_job(str(settings.db_path), job["id"])["status"] == "validating"
    assert get_incident(str(settings.db_path), incident["id"])["status"] == "validating"


def test_recovery_finalizes_completed_validation_report(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    incident, job = _patch_ready(settings)
    from src.db import attach_repair_validation_process, claim_next_patch_ready_for_validation

    claim_next_patch_ready_for_validation(str(settings.db_path), 123456)
    validation_path = settings.codex_state_root / str(job["id"]) / "validation.json"
    validation_path.parent.mkdir(parents=True, exist_ok=True)
    validation_path.write_text(
        json.dumps({"status": "pass", "failure_reason": "", "steps": []}),
        encoding="utf-8",
    )
    attach_repair_validation_process(
        str(settings.db_path), job["id"], pid=123456,
        validation_json_path=str(validation_path),
    )
    monkeypatch.setattr(runner_module, "pid_running", lambda _pid: False)

    RepairWorkflowRunner(settings).recover()

    assert get_repair_job(str(settings.db_path), job["id"])["status"] == "awaiting_validation_approval"
    assert get_incident(str(settings.db_path), incident["id"])["status"] == "validated"


def test_canary_creates_diagnose_then_dry_run_once(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    incident, job = _patch_ready(settings, signature="runner-canary")
    mark_incident_validating(str(settings.db_path), incident["id"])
    mark_incident_validated(str(settings.db_path), incident["id"])
    set_repair_job_status(
        str(settings.db_path), job["id"],
        from_statuses=("patch_ready",), to_status="canary",
        extra_cols={"canary_job_ids_json": "{}"},
    )
    settings.codex_canary = {
        "account_id": "fixture_account",
        "marketplace": "US",
        "brand_name": "FIXTURE_BRAND",
    }
    settings.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    settings.accounts_path.write_text(
        json.dumps({
            "accounts": [{
                "account_id": "fixture_account",
                "status": "active",
                "adspower_profile_id": "fixture-profile",
            }]
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        runner_module,
        "run_submit_preflight",
        lambda *_args, **_kwargs: [{"name": "fixture", "ok": True}],
    )
    runner = RepairWorkflowRunner(settings)

    runner._advance_canaries()
    jobs, _ = list_automation_jobs(str(settings.db_path))
    assert [row["job_type"] for row in jobs] == ["diagnose"]
    update_automation_job(
        str(settings.db_path), jobs[0]["id"], run_status="completed", exit_code=0
    )

    runner._advance_canaries()
    jobs, _ = list_automation_jobs(str(settings.db_path))
    assert {row["job_type"] for row in jobs} == {"diagnose", "dry_run"}
    dry_run = next(row for row in jobs if row["job_type"] == "dry_run")
    update_automation_job(
        str(settings.db_path), dry_run["id"], run_status="completed", exit_code=0
    )

    runner._advance_canaries()
    runner._advance_canaries()
    refreshed = get_repair_job(str(settings.db_path), job["id"])
    state = json.loads(refreshed["canary_job_ids_json"])
    assert state["ready_for_review"] is True
    jobs, _ = list_automation_jobs(str(settings.db_path))
    assert len(jobs) == 2
    assert all(row["job_type"] != "submit" for row in jobs)


def test_canary_preflight_failure_closes_as_validation_failed(tmp_path):
    settings = _settings(tmp_path)
    incident, job = _patch_ready(settings, signature="runner-canary-preflight-fail")
    mark_incident_validating(str(settings.db_path), incident["id"])
    mark_incident_validated(str(settings.db_path), incident["id"])
    set_repair_job_status(
        str(settings.db_path), job["id"],
        from_statuses=("patch_ready",), to_status="canary",
        extra_cols={"canary_job_ids_json": "{}"},
    )

    RepairWorkflowRunner(settings)._advance_canaries()

    refreshed = get_repair_job(str(settings.db_path), job["id"])
    assert refreshed["status"] == "validation_failed"
    assert get_incident(str(settings.db_path), incident["id"])["status"] == "triaged"
    jobs, _ = list_automation_jobs(str(settings.db_path))
    assert jobs == []


def test_post_release_check_resumes_and_marks_released(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    incident, job = _patch_ready(settings, signature="runner-post-release")
    mark_incident_validating(str(settings.db_path), incident["id"])
    mark_incident_validated(str(settings.db_path), incident["id"])
    set_repair_job_status(
        str(settings.db_path), job["id"],
        from_statuses=("patch_ready",), to_status="post_release_check",
        extra_cols={"canary_job_ids_json": "{}"},
    )
    settings.codex_canary = {
        "account_id": "fixture_account",
        "marketplace": "US",
        "brand_name": "FIXTURE_BRAND",
    }
    settings.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    settings.accounts_path.write_text(
        json.dumps({
            "accounts": [{
                "account_id": "fixture_account",
                "status": "active",
                "adspower_profile_id": "fixture-profile",
            }]
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        runner_module,
        "run_submit_preflight",
        lambda *_args, **_kwargs: [{"name": "fixture", "ok": True}],
    )
    runner = RepairWorkflowRunner(settings)

    runner._advance_post_release_checks()
    jobs, _ = list_automation_jobs(str(settings.db_path))
    assert len(jobs) == 1
    assert jobs[0]["job_type"] == "diagnose"
    update_automation_job(
        str(settings.db_path), jobs[0]["id"], run_status="completed", exit_code=0
    )
    runner._advance_post_release_checks()

    assert get_repair_job(str(settings.db_path), job["id"])["status"] == "released"
    assert get_incident(str(settings.db_path), incident["id"])["status"] == "released"
