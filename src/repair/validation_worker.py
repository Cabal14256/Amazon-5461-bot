"""Subprocess entry point for Stage-8 repair validation."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from src.db import heartbeat_repair_validation
from src.repair.validation import run_validation_request
from src.state_files import atomic_write_json


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    args = parser.parse_args()
    request_path = Path(args.request).resolve()
    request = json.loads(request_path.read_text(encoding="utf-8"))
    db_path = str(request["db_path"])
    job_id = int(request["job_id"])
    pid = os.getpid()

    def heartbeat() -> None:
        heartbeat_repair_validation(db_path, job_id, pid)

    try:
        report = run_validation_request(request, heartbeat=heartbeat)
    except Exception as exc:  # noqa: BLE001 - worker must leave a durable result
        output_path = Path(str(request["validation_json_path"]))
        report = {
            "version": 1,
            "job_id": job_id,
            "status": "failed",
            "failure_reason": f"worker_exception:{type(exc).__name__}",
            "steps": [],
        }
        atomic_write_json(output_path, report)
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
