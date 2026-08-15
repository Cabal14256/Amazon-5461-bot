"""Per-job resource layout.

Every automation job gets isolated directories so concurrent views, logs and
evidence never mix with the global ``data/batch_state.json`` legacy file:

    runtime/state/jobs/<job_id>/batch_state.json   (dry-run worker state)
    runtime/state/jobs/<job_id>/process.json       (spawn metadata, atomic)
    runtime/state/jobs/<job_id>/STOP_REQUESTED     (safe-stop sentinel)
    runtime/logs/jobs/<job_id>/stdout.log|stderr.log
    runtime/evidence/jobs/<job_id>/
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class JobPaths:
    job_id: str
    state_dir: Path
    batch_state: Path
    process_json: Path
    stop_file: Path
    log_dir: Path
    stdout_log: Path
    stderr_log: Path
    evidence_dir: Path


def job_paths(state_root: Path, logs_root: Path, evidence_root: Path, job_id: str) -> JobPaths:
    state_dir = Path(state_root) / "jobs" / job_id
    log_dir = Path(logs_root) / "jobs" / job_id
    return JobPaths(
        job_id=job_id,
        state_dir=state_dir,
        batch_state=state_dir / "batch_state.json",
        process_json=state_dir / "process.json",
        stop_file=state_dir / "STOP_REQUESTED",
        log_dir=log_dir,
        stdout_log=log_dir / "stdout.log",
        stderr_log=log_dir / "stderr.log",
        evidence_dir=Path(evidence_root) / "jobs" / job_id,
    )


def ensure_job_paths(state_root: Path, logs_root: Path, evidence_root: Path, job_id: str) -> JobPaths:
    paths = job_paths(state_root, logs_root, evidence_root, job_id)
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    paths.log_dir.mkdir(parents=True, exist_ok=True)
    paths.evidence_dir.mkdir(parents=True, exist_ok=True)
    return paths


def request_stop_file(paths: JobPaths) -> None:
    """Drop the STOP_REQUESTED sentinel the batch worker polls between brands."""
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    paths.stop_file.touch()


def stop_requested(paths: JobPaths) -> bool:
    return paths.stop_file.exists()
