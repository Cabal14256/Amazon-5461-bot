#!/usr/bin/env python3
"""Process persistent missing-Case-ID lookups sequentially."""

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

from src.case_id_recovery import (  # noqa: E402
    get_case_id_recovery_config,
    process_due_case_id_recoveries,
    requeue_stale_case_id_recoveries,
    seconds_until_next_case_id_recovery,
)
from src.config_loader import load_yaml  # noqa: E402
from src.db import init_db  # noqa: E402


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
    parser = argparse.ArgumentParser(description="Recover missing 5461 Case IDs")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--watch", action="store_true")
    parser.add_argument("--limit", type=int, default=1)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_yaml(str(PROJECT_ROOT / "config" / "settings.yaml"))
    db_path = str((settings.get("paths") or {}).get("db_path") or "./runtime/state/ledger.db")
    init_db(db_path)
    lock_path = PROJECT_ROOT / "runtime" / "state" / "case_id_recovery_worker.lock"
    if not _acquire_worker_lock(lock_path):
        print("[CaseIdRecovery] 已有 worker 在运行，本进程退出")
        return 0
    stale_before = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    requeued = requeue_stale_case_id_recoveries(settings, stale_before)
    if requeued:
        print(f"[CaseIdRecovery] 已恢复 {requeued} 个中断任务")
    watch = bool(args.watch)
    poll_seconds = int(get_case_id_recovery_config(settings)["poll_interval_seconds"])
    while True:
        results = process_due_case_id_recoveries(settings, limit=max(1, args.limit))
        if results:
            for result in results:
                print("[CaseIdRecovery] " + json.dumps(result, ensure_ascii=False))
            if not watch:
                return 0
            continue
        if not watch:
            return 0
        seconds = seconds_until_next_case_id_recovery(settings)
        if seconds is None:
            print("[CaseIdRecovery] 队列已清空，worker 退出")
            return 0
        time.sleep(min(float(poll_seconds), max(1.0, seconds)))


if __name__ == "__main__":
    raise SystemExit(main())
