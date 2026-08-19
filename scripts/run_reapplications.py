#!/usr/bin/env python3
"""Run authorized, finite 5461 reapplication attempts from SQLite."""

from __future__ import annotations

import argparse
import atexit
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.config_loader import load_yaml, resolve_accounts_path  # noqa: E402
from src.db import (  # noqa: E402
    acquire_profile_lock,
    get_conn,
    init_db,
    release_profile_lock,
)
from src.jobs.profile_locks import profile_key_for_account  # noqa: E402
from src.reapplication import (  # noqa: E402
    build_attempt_command,
    claim_due_attempt,
    get_attempt,
    get_reapplication_config,
    mark_confirmed_draft_attempt,
    pause_attempt,
    reschedule_running_attempt,
    seconds_until_next_attempt,
    set_attempt_process_metadata,
)


def _pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (OSError, SystemError):
        return False
    return True


def _acquire_worker_lock(lock_path: Path) -> bool:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                payload = json.loads(lock_path.read_text(encoding="utf-8"))
                if _pid_running(int(payload.get("pid") or 0)):
                    return False
            except Exception:
                pass
            try:
                lock_path.unlink()
            except OSError:
                return False
            continue
        else:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"pid": os.getpid(), "started_at": datetime.now().isoformat()}, stream)

            def cleanup() -> None:
                try:
                    payload = json.loads(lock_path.read_text(encoding="utf-8"))
                    if int(payload.get("pid") or 0) == os.getpid():
                        lock_path.unlink()
                except Exception:
                    pass

            atexit.register(cleanup)
            return True
    return False


def _validate_prerequisites(attempt: dict) -> str:
    accounts_path = resolve_accounts_path(PROJECT_ROOT / "config" / "accounts.json")
    if not accounts_path.exists():
        return "private account configuration is missing"
    try:
        payload = json.loads(accounts_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "private account configuration is unreadable"
    account = next(
        (
            row
            for row in payload.get("accounts", [])
            if str(row.get("account_id") or "") == str(attempt["account_id"])
        ),
        None,
    )
    if not account:
        return "account is not onboarded; run account onboarding first"
    if not str(account.get("adspower_profile_id") or "").strip():
        return "account has no AdsPower profile ID"
    manifest = PROJECT_ROOT / "brand_packs" / str(attempt["brand_name"]) / "manifest.json"
    if not manifest.exists():
        return "brand pack manifest is missing"
    return ""


def _account_has_running_case(settings: dict, account_id: str) -> bool:
    db_path = str((settings.get("paths") or {}).get("db_path") or "./runtime/state/ledger.db")
    conn = get_conn(db_path)
    row = conn.execute(
        "SELECT 1 FROM case_followups WHERE account_id=? AND status='running' LIMIT 1",
        (str(account_id),),
    ).fetchone()
    conn.close()
    return bool(row)


def _launch_targeted_case_worker(followup_id: int, attempt: dict) -> dict:
    from src.case_followup import build_worker_subprocess_env

    log_dir = (
        PROJECT_ROOT
        / "runtime"
        / "logs"
        / "reapplications"
        / f"campaign_{int(attempt['campaign_id'])}"
    )
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / f"case_followup_{int(followup_id)}.out.log"
    stderr_path = log_dir / f"case_followup_{int(followup_id)}.err.log"
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "run_case_followups.py"),
        "--watch",
        "--reapplication-only",
    ]
    kwargs: dict = {
        "cwd": str(PROJECT_ROOT),
        "stdin": subprocess.DEVNULL,
        "env": build_worker_subprocess_env(),
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    else:
        kwargs["start_new_session"] = True
    with stdout_path.open("a", encoding="utf-8") as stdout_file, stderr_path.open(
        "a", encoding="utf-8"
    ) as stderr_file:
        proc = subprocess.Popen(
            command,
            stdout=stdout_file,
            stderr=stderr_file,
            **kwargs,
        )
    return {"started": True, "pid": proc.pid}


def process_one(settings: dict) -> dict | None:
    attempt = claim_due_attempt(settings)
    if not attempt:
        return None
    attempt_id = int(attempt["id"])
    config = get_reapplication_config(settings)
    prerequisite_error = _validate_prerequisites(attempt)
    if prerequisite_error:
        return pause_attempt(settings, attempt_id, prerequisite_error, status="blocked")

    db_path = str((settings.get("paths") or {}).get("db_path") or "./runtime/state/ledger.db")
    accounts_path = resolve_accounts_path(
        str((settings.get("paths") or {}).get("accounts_path") or "config/accounts.json")
    )
    profile_key = profile_key_for_account(accounts_path, str(attempt["account_id"]))
    if not profile_key:
        return pause_attempt(
            settings,
            attempt_id,
            "account has no resolvable AdsPower profile lock key",
            status="blocked",
        )
    owner_id = f"reapplication:{os.getpid()}:{attempt_id}:{attempt['account_id']}"
    acquired = acquire_profile_lock(
        db_path,
        profile_key,
        owner_type="reapplication_worker",
        owner_id=owner_id,
        ttl_seconds=7200,
        pid_alive=_pid_running,
    )
    if not acquired:
        next_run = (
            datetime.now() + timedelta(minutes=int(config["busy_retry_minutes"]))
        ).strftime("%Y-%m-%d %H:%M:%S")
        reschedule_running_attempt(
            settings,
            attempt_id,
            next_run,
            "AdsPower profile is held by another worker",
        )
        return {"status": "rescheduled_busy", "attempt_id": attempt_id, "scheduled_at": next_run}

    try:
        command, paths = build_attempt_command(settings, attempt)
        for path in paths.values():
            path.parent.mkdir(parents=True, exist_ok=True)
        kwargs: dict = {"cwd": str(PROJECT_ROOT), "stdin": subprocess.DEVNULL}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        with paths["stdout"].open("a", encoding="utf-8") as stdout_file, paths[
            "stderr"
        ].open("a", encoding="utf-8") as stderr_file:
            proc = subprocess.Popen(
                command,
                stdout=stdout_file,
                stderr=stderr_file,
                **kwargs,
            )
            set_attempt_process_metadata(
                settings,
                attempt_id,
                pid=proc.pid,
                state_path=str(paths["state"]),
                stdout_path=str(paths["stdout"]),
                stderr_path=str(paths["stderr"]),
            )
            return_code = proc.wait()
    except Exception as exc:
        return pause_attempt(
            settings,
            attempt_id,
            f"attempt process launch failed: {exc.__class__.__name__}",
        )
    finally:
        release_profile_lock(db_path, profile_key, owner_id=owner_id)

    refreshed = get_attempt(settings, attempt_id)
    if refreshed and refreshed.get("status") == "waiting_login":
        return {
            "status": "waiting_login",
            "attempt_id": attempt_id,
            "auth_block_id": refreshed.get("auth_block_id"),
        }
    if refreshed and refreshed.get("status") == "waiting_reconciliation":
        from src.case_id_recovery import launch_case_id_recovery_worker

        worker = launch_case_id_recovery_worker(settings)
        return {
            "status": "waiting_reconciliation",
            "attempt_id": attempt_id,
            "auth_block_id": refreshed.get("auth_block_id"),
            "case_id_worker": worker,
        }
    if refreshed and refreshed.get("status") == "waiting_case_id":
        from src.case_id_recovery import launch_case_id_recovery_worker

        worker = launch_case_id_recovery_worker(settings)
        return {
            "status": "waiting_case_id",
            "attempt_id": attempt_id,
            "case_id_worker": worker,
        }
    if refreshed and refreshed.get("status") == "waiting_case" and refreshed.get("case_followup_id"):
        worker = _launch_targeted_case_worker(int(refreshed["case_followup_id"]), refreshed)
        return {
            "status": "waiting_case",
            "attempt_id": attempt_id,
            "case_followup_id": int(refreshed["case_followup_id"]),
            "case_worker": worker,
        }
    draft = mark_confirmed_draft_attempt(settings, attempt_id)
    if draft:
        return draft
    reason = "batch ended without a linked Case follow-up"
    if return_code:
        reason += f" (exit_code={return_code})"
    return pause_attempt(settings, attempt_id, reason)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run authorized finite 5461 reapplication campaigns")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="Process one currently due attempt and exit")
    mode.add_argument("--watch", action="store_true", help="Wait until all scheduled attempts are dispatched")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_yaml(str(PROJECT_ROOT / "config" / "settings.yaml"))
    db_path = str((settings.get("paths") or {}).get("db_path") or "./runtime/state/ledger.db")
    init_db(db_path)
    lock_path = PROJECT_ROOT / "runtime" / "state" / "reapplication_worker.lock"
    if not _acquire_worker_lock(lock_path):
        print("[Reapplication] 已有 worker 在运行，本进程退出")
        return 0
    watch = bool(args.watch)
    poll_seconds = int(get_reapplication_config(settings)["poll_interval_seconds"])
    while True:
        result = process_one(settings)
        if result:
            print("[Reapplication] " + json.dumps(result, ensure_ascii=False))
            if not watch:
                return 0
            continue
        if not watch:
            return 0
        seconds = seconds_until_next_attempt(settings)
        if seconds is None:
            print("[Reapplication] 当前没有待调度尝试，worker 退出")
            return 0
        time.sleep(min(float(poll_seconds), max(1.0, seconds)))


if __name__ == "__main__":
    raise SystemExit(main())
