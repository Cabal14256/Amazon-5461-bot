"""Persistent Stage-8 validation, canary and post-release workflow runner."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import sys
from pathlib import Path

from src.db import (
    AUTOMATION_JOB_TERMINAL_STATUSES,
    attach_repair_validation_process,
    claim_next_patch_ready_for_validation,
    create_repair_canary_job,
    fail_repair_canary,
    finish_post_release_check,
    finish_repair_validation,
    get_automation_job,
    get_profile_lock,
    get_repair_job,
    list_automation_jobs,
    list_repair_jobs_by_status,
    record_web_audit,
    requeue_stale_repair_validation,
    update_repair_canary_state,
)
from src.jobs.pid_check import pid_running
from src.jobs.preflight import run_submit_preflight
from src.jobs.process_runner import spawn_hidden_process
from src.jobs.profile_locks import profile_key_for_account
from src.state_files import atomic_write_json, read_json_tolerant

logger = logging.getLogger(__name__)


class RepairWorkflowRunner:
    """Single-machine serial validator plus resumable canary coordinator."""

    def __init__(self, settings, *, process_factory=None, tick_seconds: float = 2.0) -> None:
        self.settings = settings
        self.db_path = str(settings.db_path)
        self.process_factory = process_factory or spawn_hidden_process
        self.tick_seconds = float(tick_seconds)
        self._task: asyncio.Task | None = None
        self._validation_process = None
        self._validation_job_id: int | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run_loop(self) -> None:
        while True:
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - one broken job must not stop recovery
                logger.exception("[repair] workflow tick failed")
            await asyncio.sleep(self.tick_seconds)

    def recover(self) -> None:
        """Recover only validating rows whose recorded PID is no longer alive."""
        for job in list_repair_jobs_by_status(self.db_path, ("validating",)):
            if pid_running(job.get("validation_pid")):
                continue
            if self._finalize_validation_report(job):
                continue
            requeue_stale_repair_validation(self.db_path, int(job["id"]))
            self._audit(job, "validation_requeued", "dead_pid_without_result")
        # Canary/post-release states are resumed by tick() from their stored
        # automation job ids; recovery never creates a second job here.
        self._advance_canaries()
        self._advance_post_release_checks()

    def tick(self) -> None:
        self._reconcile_validation_processes()
        self._advance_canaries()
        self._advance_post_release_checks()
        if list_repair_jobs_by_status(self.db_path, ("validating",)):
            return
        claimed = claim_next_patch_ready_for_validation(self.db_path, os.getpid())
        if claimed is not None:
            self._spawn_validation(claimed)

    def _spawn_validation(self, job: dict) -> None:
        job_id = int(job["id"])
        state_dir = Path(self.settings.codex_state_root) / str(job_id)
        log_dir = Path(self.settings.codex_logs_root) / str(job_id) / "validation"
        state_dir.mkdir(parents=True, exist_ok=True)
        log_dir.mkdir(parents=True, exist_ok=True)
        validation_path = state_dir / "validation.json"
        request_path = state_dir / "validation-request.json"
        request = {
            "job_id": job_id,
            "db_path": self.db_path,
            "worktree_path": job.get("worktree_path"),
            "baseline_sha": job.get("baseline_sha"),
            "patch_sha": job.get("patch_sha"),
            "changed_files": job.get("changed_files") or [],
            "allowed_paths": list(self.settings.codex_patch_allowed_paths),
            "timeout_sec": int(self.settings.codex_validation_timeout_sec),
            "result_json_path": job.get("result_json_path"),
            "validation_json_path": str(validation_path),
            "validation_log_dir": str(log_dir),
            "python_executable": sys.executable,
            "codex_command": str(self.settings.codex_command),
            "codex_model": str(self.settings.codex_model or ""),
            "diff_review_enabled": bool(self.settings.codex_diff_review_enabled),
        }
        atomic_write_json(request_path, request)
        stdout_path = log_dir / "worker.out.log"
        stderr_path = log_dir / "worker.err.log"
        command = [
            sys.executable,
            "-m",
            "src.repair.validation_worker",
            "--request",
            str(request_path),
        ]
        try:
            proc = self.process_factory(
                command,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                env=dict(os.environ, PYTHONUTF8="1"),
                cwd=Path(self.settings.repo_root),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[repair] validation spawn failed job=%s: %s", job_id, type(exc).__name__)
            report = {
                "version": 1,
                "job_id": job_id,
                "status": "failed",
                "failure_reason": f"spawn_failed:{type(exc).__name__}",
                "steps": [],
            }
            atomic_write_json(validation_path, report)
            finish_repair_validation(
                self.db_path, job_id, passed=False, validation_json_path=str(validation_path)
            )
            self._audit(job, "validation_failed", report["failure_reason"])
            return
        attach_repair_validation_process(
            self.db_path,
            job_id,
            pid=int(proc.pid),
            validation_json_path=str(validation_path),
        )
        self._validation_process = proc
        self._validation_job_id = job_id
        self._audit(job, "validation_started", "")

    def _reconcile_validation_processes(self) -> None:
        if self._validation_process is not None:
            if self._validation_process.poll() is None:
                return
            job_id = self._validation_job_id
            self._validation_process = None
            self._validation_job_id = None
            if job_id is not None:
                job = get_repair_job(self.db_path, job_id)
                if job and job["status"] == "validating":
                    if not self._finalize_validation_report(job):
                        requeue_stale_repair_validation(self.db_path, job_id)
            return
        for job in list_repair_jobs_by_status(self.db_path, ("validating",)):
            if pid_running(job.get("validation_pid")):
                continue
            if not self._finalize_validation_report(job):
                requeue_stale_repair_validation(self.db_path, int(job["id"]))

    def _finalize_validation_report(self, job: dict) -> bool:
        raw_path = str(job.get("validation_json_path") or "").strip()
        if not raw_path:
            return False
        payload = read_json_tolerant(Path(raw_path), retries=1)
        if not isinstance(payload, dict) or payload.get("status") not in {"pass", "failed"}:
            return False
        passed = payload.get("status") == "pass"
        updated = finish_repair_validation(
            self.db_path,
            int(job["id"]),
            passed=passed,
            validation_json_path=raw_path,
        )
        if updated is None:
            return False
        self._audit(
            job,
            "validation_passed" if passed else "validation_failed",
            str(payload.get("failure_reason") or ""),
        )
        return True

    def _canary_target(self) -> tuple[dict | None, list[dict]]:
        target = self.settings.codex_canary or {}
        account_id = str(target.get("account_id") or "").strip()
        marketplace = str(target.get("marketplace") or "").strip().upper()
        brand_name = str(target.get("brand_name") or "").strip()
        checks: list[dict] = []
        payload = read_json_tolerant(self.settings.accounts_path)
        rows = payload.get("accounts") if isinstance(payload, dict) else []
        account = next(
            (row for row in rows or [] if isinstance(row, dict) and row.get("account_id") == account_id),
            None,
        )
        checks.append({"name": "configured_target", "ok": bool(account_id and marketplace and brand_name)})
        checks.append({"name": "active_account", "ok": bool(account and str(account.get("status") or "").lower() == "active")})
        if not account:
            return None, checks
        submit_checks = run_submit_preflight(self.settings, account, [brand_name], marketplace)
        for item in submit_checks:
            if item["name"] in {"recent_dry_run"}:
                continue
            checks.append({"name": str(item["name"]), "ok": bool(item["ok"])})
        profile_key = profile_key_for_account(self.settings.accounts_path, account_id)
        checks.append({"name": "profile_lock", "ok": not bool(profile_key and get_profile_lock(self.db_path, profile_key))})
        jobs, _total = list_automation_jobs(self.db_path, account_id=account_id, limit=200)
        active = [row for row in jobs if row["run_status"] not in AUTOMATION_JOB_TERMINAL_STATUSES]
        checks.append({"name": "conflicting_job", "ok": not active})
        return {
            "account_id": account_id,
            "marketplace": marketplace,
            "brand_name": brand_name,
        }, checks

    def _advance_canaries(self) -> None:
        for job in list_repair_jobs_by_status(self.db_path, ("canary",)):
            state = self._canary_state(job)
            if state.get("ready_for_review"):
                continue
            diagnose_id = str(state.get("diagnose") or "")
            if not diagnose_id:
                target, checks = self._canary_target()
                failed = [item["name"] for item in checks if not item["ok"]]
                update_repair_canary_state(
                    self.db_path, int(job["id"]), expected_status="canary",
                    values={"preflight": checks},
                )
                if target is None or failed:
                    fail_repair_canary(self.db_path, int(job["id"]), "preflight:" + ",".join(failed))
                    self._audit(job, "canary_failed", "preflight")
                    continue
                diagnose_id = self._create_canary_job(job, target, "diagnose") or ""
                if not diagnose_id:
                    continue
            diagnose = get_automation_job(self.db_path, diagnose_id)
            if not diagnose or diagnose["run_status"] in (
                AUTOMATION_JOB_TERMINAL_STATUSES - {"completed"}
            ):
                fail_repair_canary(self.db_path, int(job["id"]), "diagnose_failed")
                self._audit(job, "canary_failed", "diagnose")
                continue
            if diagnose["run_status"] != "completed":
                continue
            state = self._canary_state(get_repair_job(self.db_path, int(job["id"])) or job)
            dry_run_id = str(state.get("dry_run") or "")
            if not dry_run_id:
                target, checks = self._canary_target()
                if target is None or any(not item["ok"] for item in checks):
                    fail_repair_canary(self.db_path, int(job["id"]), "dry_run_preflight_failed")
                    continue
                dry_run_id = self._create_canary_job(job, target, "dry_run") or ""
                if not dry_run_id:
                    continue
            dry_run = get_automation_job(self.db_path, dry_run_id)
            if not dry_run or dry_run["run_status"] in (
                AUTOMATION_JOB_TERMINAL_STATUSES - {"completed"}
            ):
                fail_repair_canary(self.db_path, int(job["id"]), "dry_run_failed")
                self._audit(job, "canary_failed", "dry_run")
                continue
            if dry_run["run_status"] == "completed":
                update_repair_canary_state(
                    self.db_path, int(job["id"]), expected_status="canary",
                    values={"ready_for_review": True},
                )
                self._audit(job, "canary_ready_for_review", "")

    def _advance_post_release_checks(self) -> None:
        for job in list_repair_jobs_by_status(self.db_path, ("post_release_check",)):
            state = self._canary_state(job)
            automation_id = str(state.get("post_release") or "")
            if not automation_id:
                target, checks = self._canary_target()
                if target is None or any(not item["ok"] for item in checks):
                    finish_post_release_check(
                        self.db_path, int(job["id"]), passed=False, reason="preflight_failed"
                    )
                    continue
                automation_id = self._create_canary_job(job, target, "post_release") or ""
                if not automation_id:
                    continue
            automation = get_automation_job(self.db_path, automation_id)
            if automation and automation["run_status"] == "completed":
                finish_post_release_check(self.db_path, int(job["id"]), passed=True)
                self._audit(job, "released", "post_release_canary_passed")
            elif not automation or automation["run_status"] in (
                AUTOMATION_JOB_TERMINAL_STATUSES - {"completed"}
            ):
                finish_post_release_check(
                    self.db_path, int(job["id"]), passed=False, reason="diagnose_failed"
                )
                self._audit(job, "release_check_failed", "diagnose")

    def _create_canary_job(self, job: dict, target: dict, kind: str) -> str | None:
        suffix = secrets.token_hex(3)
        automation_id = f"repair-{int(job['id'])}-{kind}-{suffix}"
        return create_repair_canary_job(
            self.db_path,
            int(job["id"]),
            kind=kind,
            automation_job_id=automation_id,
            created_by="repair-workflow",
            account_id=target["account_id"],
            marketplace=target["marketplace"],
            brand_name=target["brand_name"],
        )

    @staticmethod
    def _canary_state(job: dict) -> dict:
        try:
            payload = json.loads(str(job.get("canary_job_ids_json") or "{}"))
        except ValueError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _audit(self, job: dict, result: str, reason: str) -> None:
        record_web_audit(
            self.db_path,
            action="repair_workflow",
            target_type="repair_job",
            target_id=str(job["id"]),
            result=str(result),
            detail=json.dumps({"reason": str(reason)[:200]}, ensure_ascii=False),
        )
