#!/usr/bin/env python3
"""Process delayed 5461 Case-detail checks from the persistent SQLite queue."""

from __future__ import annotations

import argparse
import atexit
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.case_followup import (  # noqa: E402
    get_case_followup_config,
    process_due_case_followups,
    seconds_until_next_followup,
)
from src.config_loader import load_yaml  # noqa: E402
from src.db import init_db, requeue_stale_case_followups  # noqa: E402


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
    parser.add_argument("--limit", type=int, default=1, help="Maximum tasks claimed per pass; checks remain sequential")
    parser.add_argument("--no-register", action="store_true", help="Do not update Excel/SQLite outcome records")
    parser.add_argument(
        "--followup-ids",
        default="",
        help="Comma-separated Case follow-up IDs; excludes every other queued task",
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

    lock_path = PROJECT_ROOT / "runtime" / "state" / "case_followup_worker.lock"
    if not _acquire_worker_lock(lock_path):
        print("[CaseFollowup] 已有 worker 在运行，本进程退出")
        return 0

    stale_before = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    requeued = requeue_stale_case_followups(db_path, stale_before)
    if requeued:
        print(f"[CaseFollowup] 已恢复 {requeued} 个中断任务")

    watch = bool(args.watch)
    poll_seconds = max(5, min(60, int(config.get("poll_interval_seconds") or 60)))
    while True:
        results = process_due_case_followups(
            settings=settings,
            limit=max(1, args.limit),
            update_records=not args.no_register,
            followup_ids=followup_ids or None,
        )
        if results:
            continue
        if not watch:
            return 0

        seconds = seconds_until_next_followup(settings, followup_ids=followup_ids or None)
        if seconds is None:
            print("[CaseFollowup] 队列已清空，worker 退出")
            return 0
        sleep_seconds = min(float(poll_seconds), max(1.0, seconds))
        time.sleep(sleep_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
