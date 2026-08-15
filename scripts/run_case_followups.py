#!/usr/bin/env python3
"""Process delayed 5461 Case-detail checks from the persistent SQLite queue."""

from __future__ import annotations

import argparse
import atexit
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.case_followup import (  # noqa: E402
    get_case_followup_config,
    process_due_case_followups,
    seconds_until_next_followup,
)
from src.config_loader import load_yaml, resolve_accounts_path  # noqa: E402
from src.db import (  # noqa: E402
    acquire_profile_lock,
    init_db,
    list_due_case_followup_groups,
    release_profile_lock,
    requeue_stale_case_followups,
)
from src.jobs.profile_locks import profile_key_for_account  # noqa: E402


def _pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run delayed Amazon 5461 Case follow-up checks")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="Process currently due tasks and exit (default)")
    mode.add_argument("--watch", action="store_true", help="Wait for future due tasks until the queue is empty")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum tasks claimed from one oldest account/site group",
    )
    parser.add_argument("--no-register", action="store_true", help="Do not update Excel/SQLite outcome records")
    parser.add_argument(
        "--followup-ids",
        default="",
        help="Comma-separated Case follow-up IDs; excludes every other queued task",
    )
    parser.add_argument(
        "--reapplication-only",
        action="store_true",
        help="Process only Case tasks linked to a reapplication campaign",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_yaml(str(PROJECT_ROOT / "config" / "settings.yaml"))
    config = get_case_followup_config(settings)
    followup_ids = [
        int(value.strip())
        for value in str(args.followup_ids or "").split(",")
        if value.strip()
    ]
    db_path = str(settings.get("paths", {}).get("db_path") or "./runtime/state/ledger.db")
    init_db(db_path)

    if followup_ids:
        target_key = hashlib.sha256(
            ",".join(str(value) for value in sorted(followup_ids)).encode("utf-8")
        ).hexdigest()[:12]
        lock_name = f"case_followup_targeted_{target_key}.lock"
    elif args.reapplication_only:
        lock_name = "case_followup_reapplication_worker.lock"
    else:
        lock_name = "case_followup_worker.lock"
    lock_path = PROJECT_ROOT / "runtime" / "state" / lock_name
    if not _acquire_worker_lock(lock_path):
        print("[CaseFollowup] 已有 worker 在运行，本进程退出")
        return 0

    stale_before = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    requeued = requeue_stale_case_followups(db_path, stale_before, pid_alive=_pid_running)
    if requeued:
        print(f"[CaseFollowup] 已恢复 {requeued} 个中断任务")

    watch = bool(args.watch)
    poll_seconds = max(5, min(60, int(config.get("poll_interval_seconds") or 60)))
    group_limit = max(1, int(args.limit or config.get("claim_group_limit") or 20))
    max_parallel = max(1, min(3, int(config.get("max_parallel_profiles") or 1)))
    backlog_threshold = max(2, int(config.get("parallel_backlog_threshold") or 8))
    accounts_path = resolve_accounts_path(
        str(settings.get("paths", {}).get("accounts_path") or "config/accounts.json")
    )

    def process_group(group: dict, worker_no: int) -> list[dict]:
        account_id = str(group["account_id"])
        marketplace = str(group["marketplace"] or "").upper()
        profile_key = profile_key_for_account(accounts_path, account_id)
        if profile_key is None:
            # Account-scoped fallback preserves serial execution even when the
            # private config is incomplete; the browser check will classify it.
            from src.jobs.profile_locks import profile_key_for_profile_id

            profile_key = profile_key_for_profile_id(f"account:{account_id}")
        owner_id = f"case-followup:{os.getpid()}:{worker_no}:{account_id}:{marketplace}"
        acquired = acquire_profile_lock(
            db_path,
            profile_key,
            owner_type="case_followup_worker",
            owner_id=owner_id,
            ttl_seconds=7200,
            pid_alive=_pid_running,
        )
        if not acquired:
            print(
                f"[CaseFollowup] profile 正被其他任务使用，跳过本轮: "
                f"{account_id}/{marketplace}"
            )
            return []
        try:
            return process_due_case_followups(
                settings=settings,
                limit=group_limit,
                update_records=not args.no_register,
                followup_ids=followup_ids or None,
                reapplication_only=args.reapplication_only,
                account_id=account_id,
                marketplace=marketplace,
            )
        finally:
            release_profile_lock(db_path, profile_key, owner_id=owner_id)

    while True:
        groups = list_due_case_followup_groups(
            db_path,
            followup_ids=followup_ids or None,
            reapplication_only=args.reapplication_only,
        )
        due_count = sum(int(group.get("task_count") or 0) for group in groups)
        parallel = max_parallel if due_count >= backlog_threshold else 1
        selected = []
        selected_profiles: set[str] = set()
        for group in groups:
            profile_key = profile_key_for_account(
                accounts_path, str(group["account_id"])
            ) or f"account:{group['account_id']}"
            if profile_key in selected_profiles:
                continue
            selected.append(group)
            selected_profiles.add(profile_key)
            if len(selected) >= parallel:
                break

        results: list[dict] = []
        if len(selected) == 1:
            results.extend(process_group(selected[0], 0))
        elif selected:
            print(
                f"[CaseFollowup] 积压 {due_count} 条，启动 "
                f"{len(selected)} 个不同 profile 的有限并发槽"
            )
            with ThreadPoolExecutor(max_workers=len(selected)) as pool:
                futures = {
                    pool.submit(process_group, group, index): group
                    for index, group in enumerate(selected)
                }
                for future in as_completed(futures):
                    results.extend(future.result())
        if results:
            continue
        if not watch:
            return 0

        seconds = seconds_until_next_followup(
            settings,
            followup_ids=followup_ids or None,
            reapplication_only=args.reapplication_only,
        )
        if seconds is None:
            print("[CaseFollowup] 队列已清空，worker 退出")
            return 0
        sleep_seconds = min(float(poll_seconds), max(1.0, seconds))
        time.sleep(sleep_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
