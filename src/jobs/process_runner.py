"""Hidden subprocess spawning for automation jobs.

Commands are whitelisted to ``python -m cli.amazon5461 diagnose|dry-run|run``
with arguments passed as a list (never shell-joined).  Account/site/brand
validation happens at the API layer before a job row exists; this module
asserts the job-type whitelist as defense in depth.  ``submit`` maps onto
``run --submit`` and is only created through the explicit reviewer-gated
``POST /api/jobs/submit`` endpoint.  On Windows the child is
launched with ``CREATE_NO_WINDOW | DETACHED_PROCESS`` so no console window
pops up, and stdout/stderr go to separate per-job log files.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from src.db import now_str
from src.jobs.paths import JobPaths
from src.state_files import atomic_write_json
from src.windows_subprocess import no_window_kwargs

ALLOWED_JOB_TYPES = {"diagnose", "dry_run", "submit"}

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def build_job_command(job: dict[str, Any], paths: JobPaths) -> list[str]:
    job_type = str(job.get("job_type") or "")
    if job_type not in ALLOWED_JOB_TYPES:
        raise ValueError(f"非法任务类型: {job_type}")
    account = str(job["account_id"])
    brands = [str(b) for b in (job.get("brands") or [])]
    if not brands:
        raise ValueError("任务缺少品牌列表")
    site = str(job["marketplace"]) if job.get("marketplace") else None

    if job_type == "diagnose":
        cmd = [
            sys.executable, "-m", "cli.amazon5461", "diagnose",
            "--account", account,
            "--brand", ",".join(brands),
        ]
    elif job_type == "submit":
        # Reachable only for jobs created via the reviewer-gated
        # POST /api/jobs/submit endpoint (preflight + audit already done).
        cmd = [
            sys.executable, "-m", "cli.amazon5461", "run", "--submit",
            "--account", account,
            "--brand", ",".join(brands),
            "--state-file", str(paths.batch_state),
        ]
    else:
        cmd = [
            sys.executable, "-m", "cli.amazon5461", "dry-run",
            "--account", account,
            "--brand", ",".join(brands),
            "--state-file", str(paths.batch_state),
        ]
    if site:
        cmd.extend(["--site", site])
    return cmd


def build_job_env(paths: JobPaths, accounts_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    # Windows does not reliably interpret IANA ``TZ`` values loaded from
    # ``.env``; an inherited TZ can make the child start in the wrong timezone
    # while batch timestamps are stored as local naive values.
    # See case_followup.build_worker_subprocess_env.
    if os.name == "nt":
        env.pop("TZ", None)
    env.setdefault("PYTHONUTF8", "1")
    # The batch worker polls this sentinel between brands (safe stop).
    env["AMAZON5461_STOP_FILE"] = str(paths.stop_file)
    # Point the child at the same account catalog the web console validated.
    env["AMAZON5461_ACCOUNTS_PATH"] = str(accounts_path)
    return env


def spawn_hidden_process(
    cmd: list[str],
    *,
    stdout_path: Path,
    stderr_path: Path,
    env: dict[str, str],
    cwd: Path | None = None,
) -> subprocess.Popen:
    """Launch ``cmd`` detached with separated per-job stdout/stderr logs."""
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    popen_kwargs = no_window_kwargs()
    if os.name == "nt":
        popen_kwargs["creationflags"] = (
            int(popen_kwargs.get("creationflags") or 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        )
    stdout_handle = open(stdout_path, "ab", buffering=0)
    try:
        stderr_handle = open(stderr_path, "ab", buffering=0)
    except Exception:
        stdout_handle.close()
        raise
    try:
        return subprocess.Popen(
            list(cmd),
            stdout=stdout_handle,
            stderr=stderr_handle,
            stdin=subprocess.DEVNULL,
            cwd=str(cwd or PROJECT_ROOT),
            env=env,
            close_fds=True,
            **popen_kwargs,
        )
    finally:
        # Popen duplicates the handles; the parent copies may be closed.
        stdout_handle.close()
        stderr_handle.close()


def write_process_json(paths: JobPaths, job: dict[str, Any], cmd: list[str], pid: int) -> None:
    """Record non-secret spawn metadata atomically for post-restart diagnosis."""
    atomic_write_json(paths.process_json, {
        "job_id": paths.job_id,
        "pid": int(pid),
        "job_type": job.get("job_type"),
        "account_id": job.get("account_id"),
        "marketplace": job.get("marketplace"),
        "brands": job.get("brands") or [],
        "argv": [Path(cmd[0]).name, *cmd[1:]],
        "started_at": now_str(),
    })
