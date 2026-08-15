"""Profile lock semantics: acquire / conflict / expiry reclaim / dead-PID reap."""

from datetime import datetime, timedelta

from src.db import (
    acquire_profile_lock,
    get_profile_lock,
    heartbeat_profile_lock,
    reap_stale_profile_locks,
    release_profile_lock,
)
from src.jobs.profile_locks import (
    profile_key_for_account,
    profile_key_for_profile_id,
)

from .conftest import ACTIVE_ACCOUNT, PROFILE_ID

T0 = datetime(2026, 8, 10, 12, 0, 0)


def test_profile_key_is_irreversible(job_env):
    key = profile_key_for_profile_id(PROFILE_ID)
    assert len(key) == 16
    assert key != PROFILE_ID
    assert PROFILE_ID not in key
    assert profile_key_for_account(job_env.accounts_path, ACTIVE_ACCOUNT) == key
    assert profile_key_for_account(job_env.accounts_path, "no_such_account") is None


def test_acquire_conflict_and_release(job_env):
    db = str(job_env.db_path)
    key = profile_key_for_profile_id(PROFILE_ID)
    assert acquire_profile_lock(db, key, "automation_job", "job-a", now=T0)
    # Second owner conflicts while the lock is fresh.
    assert not acquire_profile_lock(db, key, "automation_job", "job-b", now=T0)
    lock = get_profile_lock(db, key)
    assert lock["owner_id"] == "job-a"
    assert PROFILE_ID not in str(dict(lock))
    assert release_profile_lock(db, key, owner_id="job-a")
    assert acquire_profile_lock(db, key, "automation_job", "job-b", now=T0)


def test_expired_lock_reclaimed_only_when_pid_dead(job_env):
    db = str(job_env.db_path)
    key = profile_key_for_profile_id(PROFILE_ID)
    later = T0 + timedelta(seconds=3600)
    assert acquire_profile_lock(db, key, "automation_job", "job-a", ttl_seconds=30, now=T0)
    # Expired but owner PID verified alive -> still held.
    assert not acquire_profile_lock(
        db, key, "automation_job", "job-b", ttl_seconds=30, now=later,
        pid_alive=lambda pid: True,
    )
    # Expired and owner PID gone -> reclaimed.
    assert acquire_profile_lock(
        db, key, "automation_job", "job-b", ttl_seconds=30, now=later,
        pid_alive=lambda pid: False,
    )
    assert get_profile_lock(db, key)["owner_id"] == "job-b"


def test_heartbeat_extends_expiry(job_env):
    db = str(job_env.db_path)
    key = profile_key_for_profile_id(PROFILE_ID)
    assert acquire_profile_lock(db, key, "automation_job", "job-a", ttl_seconds=30, now=T0)
    later = T0 + timedelta(seconds=25)
    assert heartbeat_profile_lock(db, key, "job-a", ttl_seconds=60, now=later)
    lock = get_profile_lock(db, key)
    assert lock["expires_at"] > lock["acquired_at"]
    # Wrong owner cannot heartbeat.
    assert not heartbeat_profile_lock(db, key, "job-b", ttl_seconds=60, now=later)


def test_reap_stale_locks_respects_pid_liveness(job_env):
    db = str(job_env.db_path)
    dead_key = profile_key_for_profile_id("profile-dead")
    live_key = profile_key_for_profile_id("profile-live")
    fresh_key = profile_key_for_profile_id("profile-fresh")
    assert acquire_profile_lock(db, dead_key, "automation_job", "job-dead", ttl_seconds=1, now=T0)
    assert acquire_profile_lock(db, live_key, "automation_job", "job-live", ttl_seconds=1, now=T0)
    assert acquire_profile_lock(db, fresh_key, "automation_job", "job-fresh", ttl_seconds=9999, now=T0)

    later = T0 + timedelta(seconds=3600)
    reaped = reap_stale_profile_locks(db, pid_alive=lambda pid: False, now=later)
    # owner rows have no automation_jobs entry -> pid None -> treated dead.
    assert reaped == 2
    assert get_profile_lock(db, dead_key) is None
    assert get_profile_lock(db, fresh_key) is not None

    # Live-PID locks survive reaping.
    assert acquire_profile_lock(db, live_key, "automation_job", "job-x", ttl_seconds=1, now=T0)
    # (already held by job-x; simulate expiry with a live owner)
    expired = T0 + timedelta(seconds=3600)
    reaped = reap_stale_profile_locks(db, pid_alive=lambda pid: True, now=expired)
    assert reaped == 0
    assert get_profile_lock(db, live_key) is not None


def test_case_followup_lock_uses_owner_pid_for_reclaim(job_env):
    db = str(job_env.db_path)
    key = profile_key_for_profile_id("profile-followup")
    later = T0 + timedelta(seconds=3600)
    owner = "case-followup:43210:0:us_store_004:BE"
    assert acquire_profile_lock(
        db, key, "case_followup_worker", owner, ttl_seconds=1, now=T0
    )
    assert not acquire_profile_lock(
        db,
        key,
        "case_followup_worker",
        "case-followup:55555:0:us_store_005:BE",
        ttl_seconds=30,
        now=later,
        pid_alive=lambda pid: pid == 43210,
    )
    assert acquire_profile_lock(
        db,
        key,
        "case_followup_worker",
        "case-followup:55555:0:us_store_005:BE",
        ttl_seconds=30,
        now=later,
        pid_alive=lambda _pid: False,
    )
