#!/usr/bin/env python3
"""Stage-7 repair worktree retention cleanup.

Removes ``git worktree`` checkouts of terminal patch jobs (released /
rejected / failed / …) whose ``finished_at`` is older than
``codex.worktree_retention_days``, then runs ``git worktree prune`` to drop
stale metadata.  Active jobs (running / patch_ready / validating) are never
touched.  Read-only with ``--dry-run``.

Examples:
    python scripts/cleanup_repair_worktrees.py --dry-run
    python scripts/cleanup_repair_worktrees.py
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.db import init_db, list_patch_jobs_past_retention  # noqa: E402
from src.repair.worktree import cleanup_expired_worktrees  # noqa: E402
from src.web.config import load_settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="只列出将清理的 worktree，不执行")
    args = parser.parse_args()

    settings = load_settings()
    init_db(str(settings.db_path))
    cutoff = (
        datetime.now() - timedelta(days=int(settings.codex_worktree_retention_days))
    ).strftime("%Y-%m-%d %H:%M:%S")
    expired = list_patch_jobs_past_retention(str(settings.db_path), cutoff)

    if args.dry_run:
        print(json.dumps({"dry_run": True, "expired_jobs": [
            {"id": job["id"], "incident_id": job["incident_id"],
             "status": job["status"], "worktree_path": job["worktree_path"],
             "finished_at": job["finished_at"]}
            for job in expired
        ]}, ensure_ascii=False, indent=2))
        return 0

    removed = cleanup_expired_worktrees(settings)
    print(json.dumps({"removed_job_ids": removed}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
