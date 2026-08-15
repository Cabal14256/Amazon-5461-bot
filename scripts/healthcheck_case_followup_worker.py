#!/usr/bin/env python3
"""Windows-safe health check that restarts the hidden Case worker if needed."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.case_followup import launch_case_followup_worker  # noqa: E402
from src.config_loader import load_yaml  # noqa: E402


def _pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _json_file_pid(path: Path) -> int | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        pid = int(payload.get("pid") or 0)
    except Exception:
        return None
    return pid or None


def main() -> int:
    settings = load_yaml(str(PROJECT_ROOT / "config" / "settings.yaml"))
    state_dir = PROJECT_ROOT / "runtime" / "state"
    # The worker lock is authoritative (written by the worker itself); the
    # meta file is only the launcher's record and can point at a racer that
    # lost the lock and exited. Either live PID means no restart is needed.
    for candidate in (
        state_dir / "case_followup_worker.lock",
        state_dir / "case_followup_worker.json",
    ):
        pid = _json_file_pid(candidate)
        if pid and _pid_running(pid):
            return 0
    launch_case_followup_worker(settings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
