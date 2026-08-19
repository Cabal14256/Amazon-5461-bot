"""Asyncio job dispatcher: global-serial diagnose / dry-run execution.

One tick every ``tick_seconds`` (default 2s, registered as a FastAPI startup
task): reap the active child process, then — when idle — claim the oldest
queued job, verify the profile lock and case-follow-up conflict, and spawn a
hidden subprocess.

Semantics pinned by the stage-3 plan:

- Global serial: at most one job dispatched at a time, across all profiles.
- Safe stop: ``request_stop`` only records the DB flag and drops the
  STOP_REQUESTED sentinel; the batch worker exits at the next brand
  boundary.  The manager never kills on a stop request.
- Force terminate (admin only): ``terminate()`` the child and record
  ``terminated_unknown_state`` — never auto-mark as failed.
- Web restart recovery: starting/running with a dead PID becomes
  ``terminated_unknown_state``; stop_requested with a dead PID becomes
  ``failed`` (cancelled); queued jobs simply stay queued.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from collections.abc import Callable
from typing import Any

from src.auth_recovery import account_has_open_auth_block
from src.db import (
    account_has_active_case_followup,
    claim_next_queued_automation_job,
    get_automation_job,
    list_active_automation_jobs,
    reap_stale_profile_locks,
    release_automation_job_claim,
    replace_automation_job_items,
    request_automation_job_stop,
    update_automation_job,
)
from src.jobs import profile_locks
from src.jobs.paths import ensure_job_paths, job_paths, request_stop_file
from src.jobs.pid_check import pid_running
from src.jobs.process_runner import (
    build_job_command,
    build_job_env,
    spawn_hidden_process,
    write_process_json,
)
from src.jobs.state_reader import job_items_from_batch_state
from src.state_files import read_json_tolerant
from src.web.config import WebSettings

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = {"completed", "failed", "cancelled_before_start", "terminated_unknown_state"}
WAITING_HUMAN_ERROR_CLASSES = (
    "waiting_reconciliation",
    "waiting_login",
    "manual_review",
)


def _waiting_human_error_class(job: dict[str, Any], state: Any) -> str:
    """Return the exact pause reason persisted by the batch item.

    ``waiting_human`` is a run-state umbrella, not proof that Seller Central
    logged out.  Prefer the fenced reconciliation state over login and manual
    review, then preserve a known job-level value.  Unknown pauses remain
    generic instead of being mislabeled as a login failure.
    """

    found: set[str] = set()
    if isinstance(state, dict):
        for batch in state.get("batches") or []:
            for item in (batch or {}).get("items") or []:
                result = item.get("result") if isinstance(item.get("result"), dict) else {}
                if (
                    str((item or {}).get("status") or "") != "waiting_human"
                    and str(result.get("status") or "") != "manual_review"
                ):
                    continue
                for value in (
                    result.get("status"),
                    result.get("error"),
                    result.get("note"),
                    item.get("error"),
                ):
                    normalized = str(value or "").strip().lower()
                    if normalized in WAITING_HUMAN_ERROR_CLASSES:
                        found.add(normalized)
    for candidate in WAITING_HUMAN_ERROR_CLASSES:
        if candidate in found:
            return candidate
    existing = str(job.get("error_class") or "").strip().lower()
    if existing in WAITING_HUMAN_ERROR_CLASSES:
        return existing
    return existing or "waiting_human"


class JobManager:
    def __init__(
        self,
        settings: WebSettings,
        *,
        process_factory: Callable[..., Any] | None = None,
        tick_seconds: float = 2.0,
        lock_ttl_seconds: float = 120.0,
    ) -> None:
        self.settings = settings
        self.db_path = str(settings.db_path)
        self.process_factory = process_factory or spawn_hidden_process
        self.tick_seconds = float(tick_seconds)
        self.lock_ttl_seconds = float(lock_ttl_seconds)
        self._task: asyncio.Task | None = None
        self._active: dict[str, Any] | None = None

    # -- lifecycle ---------------------------------------------------------

    def recover(self) -> None:
        """Startup recovery: reconcile DB state with actual process liveness."""
        reaped = reap_stale_profile_locks(self.db_path, pid_running)
        if reaped:
            logger.info("[jobs] 回收了 %d 个过期 profile 锁", reaped)
        for job in list_active_automation_jobs(self.db_path):
            alive = pid_running(job.get("pid"))
            if alive:
                continue
            if job["run_status"] in ("starting", "running"):
                update_automation_job(
                    self.db_path,
                    job["id"],
                    run_status="terminated_unknown_state",
                    error_class="process_lost_on_restart",
                    finished_at=_now(),
                )
            elif job["run_status"] == "stop_requested":
                update_automation_job(
                    self.db_path,
                    job["id"],
                    run_status="failed",
                    error_class="cancelled",
                    finished_at=_now(),
                )
            profile_key = _lock_key_for(self.settings, job)
            if profile_key:
                profile_locks.release(self.db_path, profile_key, job["id"])

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """Cancel the dispatch loop. Running child processes are left alone."""
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
            except Exception:  # keep the loop alive; next tick retries
                logger.exception("[jobs] 调度 tick 失败")
            await asyncio.sleep(self.tick_seconds)

    # -- dispatch loop -----------------------------------------------------

    def tick(self) -> None:
        if self._active is not None:
            self._reap_active()
            if self._active is not None:
                return
        self._dispatch_next()

    def _reap_active(self) -> None:
        active = self._active
        assert active is not None
        job_id = active["job_id"]
        proc = active["process"]

        profile_locks.heartbeat(
            self.db_path, active["profile_key"], job_id, self.lock_ttl_seconds
        )
        self._sync_items(job_id, active["paths"])

        exit_code = proc.poll()
        if exit_code is None:
            return

        job = get_automation_job(self.db_path, job_id) or {}
        cancelled = bool(job.get("stop_requested_at"))
        state = read_json_tolerant(active["paths"].batch_state, retries=1)
        waiting_human = bool(
            job.get("run_status") == "waiting_human"
            or any(
                str((batch or {}).get("status") or "") == "waiting_human"
                or str(item.get("status") or "") == "waiting_human"
                for batch in (state.get("batches") or [])
                for item in ((batch or {}).get("items") or [])
            )
            if isinstance(state, dict)
            else job.get("run_status") == "waiting_human"
        )
        if waiting_human:
            run_status = "waiting_human"
            error_class = _waiting_human_error_class(job, state)
        elif exit_code == 0 and not cancelled:
            run_status, error_class = "completed", None
        elif cancelled:
            # The worker saw STOP_REQUESTED and wound down cleanly; the plan
            # maps this onto the failed/cancelled terminal semantics.
            run_status, error_class = "failed", "cancelled"
        else:
            run_status, error_class = "failed", "exit_nonzero"
        update_automation_job(
            self.db_path,
            job_id,
            run_status=run_status,
            exit_code=int(exit_code),
            error_class=error_class,
            finished_at=None if waiting_human else _now(),
        )
        self._sync_items(job_id, active["paths"])
        profile_locks.release(self.db_path, active["profile_key"], job_id)
        self._active = None

    def _sync_items(self, job_id: str, paths) -> None:
        payload = read_json_tolerant(paths.batch_state, retries=1)
        if isinstance(payload, dict):
            try:
                replace_automation_job_items(
                    self.db_path, job_id, job_items_from_batch_state(payload)
                )
            except Exception:
                logger.exception("[jobs] 写入任务明细失败 job=%s", job_id)

    def _dispatch_next(self) -> None:
        job = claim_next_queued_automation_job(self.db_path)
        if job is None:
            return
        job_id = job["id"]

        if account_has_active_case_followup(self.db_path, job["account_id"]):
            release_automation_job_claim(self.db_path, job_id)
            return

        if account_has_open_auth_block(self.db_path, str(job["account_id"])):
            release_automation_job_claim(self.db_path, job_id)
            return

        profile_key = _lock_key_for(self.settings, job)
        if profile_key is None:
            update_automation_job(
                self.db_path,
                job_id,
                run_status="failed",
                error_class="missing_adspower_profile",
                finished_at=_now(),
            )
            return

        if not profile_locks.acquire(
            self.db_path, profile_key, job_id, self.lock_ttl_seconds, pid_alive=pid_running
        ):
            release_automation_job_claim(self.db_path, job_id)
            return

        paths = ensure_job_paths(
            self.settings.state_root, self.settings.logs_root,
            self.settings.evidence_root, job_id,
        )
        try:
            cmd = build_job_command(job, paths)
            env = build_job_env(
                paths,
                self.settings.accounts_path,
                db_path=self.settings.db_path,
            )
            proc = self.process_factory(
                cmd,
                stdout_path=paths.stdout_log,
                stderr_path=paths.stderr_log,
                env=env,
                cwd=None,
            )
        except Exception as exc:
            logger.exception("[jobs] 启动子进程失败 job=%s", job_id)
            profile_locks.release(self.db_path, profile_key, job_id)
            update_automation_job(
                self.db_path,
                job_id,
                run_status="failed",
                error_class=f"spawn_failed:{type(exc).__name__}",
                finished_at=_now(),
            )
            return

        write_process_json(paths, job, cmd, proc.pid)
        update_automation_job(
            self.db_path,
            job_id,
            run_status="running",
            pid=int(proc.pid),
            started_at=_now(),
            state_file=str(paths.batch_state),
            stdout_log=str(paths.stdout_log),
            stderr_log=str(paths.stderr_log),
        )
        self._active = {"job_id": job_id, "process": proc, "paths": paths, "profile_key": profile_key}

    # -- operator actions --------------------------------------------------

    def request_stop(self, job_id: str) -> dict[str, Any] | None:
        """Record a safe-stop request; never signals the process."""
        job = request_automation_job_stop(self.db_path, job_id)
        if job and job["run_status"] == "stop_requested":
            paths = job_paths(
                self.settings.state_root, self.settings.logs_root,
                self.settings.evidence_root, job_id,
            )
            request_stop_file(paths)
        return job

    def force_terminate(self, job_id: str) -> dict[str, Any] | None:
        """Admin emergency stop: terminate the child, mark unknown state."""
        job = get_automation_job(self.db_path, job_id)
        if not job or job["run_status"] in TERMINAL_STATUSES:
            return None

        if self._active is not None and self._active["job_id"] == job_id:
            try:
                self._active["process"].terminate()
            except Exception:
                logger.exception("[jobs] terminate() 失败 job=%s", job_id)
            profile_locks.release(self.db_path, self._active["profile_key"], job_id)
            self._active = None
        elif pid_running(job.get("pid")):
            # Child outlived a previous web process: best-effort kill by PID.
            try:
                os.kill(int(job["pid"]), signal.SIGTERM)
                profile_key = _lock_key_for(self.settings, job)
                if profile_key:
                    profile_locks.release(self.db_path, profile_key, job_id)
            except Exception:
                logger.exception("[jobs] 按 PID 终止失败 job=%s pid=%s", job_id, job.get("pid"))

        return update_automation_job(
            self.db_path,
            job_id,
            run_status="terminated_unknown_state",
            error_class="force_terminated",
            finished_at=_now(),
        )


def _now() -> str:
    from src.db import now_str

    return now_str()


def _lock_key_for(settings: WebSettings, job: dict[str, Any]) -> str | None:
    return profile_locks.profile_key_for_account(settings.accounts_path, job["account_id"])
