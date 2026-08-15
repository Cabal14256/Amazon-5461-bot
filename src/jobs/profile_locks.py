"""Profile mutual exclusion on top of the ``profile_locks`` table.

The lock key is the first 16 hex chars of the SHA-256 of the AdsPower
profile id — an irreversible identifier, so the raw profile id never lands
in the database.  Lock acquisition, heartbeat and reclaim verification live
in :mod:`src.db`; this module only resolves account → profile key.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from src.db import (
    acquire_profile_lock,
    heartbeat_profile_lock,
    release_profile_lock,
)
from src.state_files import read_json_tolerant

LOCK_OWNER_TYPE = "automation_job"


def profile_key_for_profile_id(profile_id: str) -> str:
    return hashlib.sha256(str(profile_id).encode("utf-8")).hexdigest()[:16]


def profile_key_for_account(accounts_path: Path, account_id: str) -> str | None:
    """Resolve the lock key for one account, or None when it has no profile."""
    payload = read_json_tolerant(accounts_path)
    rows = payload.get("accounts") if isinstance(payload, dict) else []
    for row in rows or []:
        if not isinstance(row, dict) or row.get("account_id") != account_id:
            continue
        profile_id = str(row.get("adspower_profile_id") or "").strip()
        return profile_key_for_profile_id(profile_id) if profile_id else None
    return None


def acquire(
    db_path: str,
    profile_key: str,
    job_id: str,
    ttl_seconds: float = 120.0,
    pid_alive=None,
) -> bool:
    return acquire_profile_lock(
        db_path,
        profile_key,
        owner_type=LOCK_OWNER_TYPE,
        owner_id=job_id,
        ttl_seconds=ttl_seconds,
        pid_alive=pid_alive,
    )


def heartbeat(db_path: str, profile_key: str, job_id: str, ttl_seconds: float = 120.0) -> bool:
    return heartbeat_profile_lock(db_path, profile_key, job_id, ttl_seconds)


def release(db_path: str, profile_key: str, job_id: str) -> bool:
    return release_profile_lock(db_path, profile_key, owner_id=job_id)
