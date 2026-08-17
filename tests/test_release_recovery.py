"""Crash cut-points and cross-process locking for repair Git operations."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.db import (
    begin_git_operation,
    get_conn,
    get_latest_git_operation,
    get_repair_job,
    init_db,
    now_str,
    record_incident,
)
from src.repair.release import ReleaseConflict, RepositoryReleaseLock, merge_repair_branch
from src.repair.release_recovery import reconcile_git_operations
from tests.test_repair_release import _release_fixture


def _env(tmp_path):
    repo, release_settings, job_data = _release_fixture(tmp_path)
    db_path = tmp_path / "runtime" / "state" / "ledger.db"
    init_db(str(db_path))
    incident, _ = record_incident(
        str(db_path), signature="release-recovery", scope_type="site", flow_type="5461",
        classification="selector_missing", marketplace="US",
    )
    conn = get_conn(str(db_path))
    cur = conn.execute(
        """INSERT INTO codex_repair_jobs(
               incident_id, stage, status, branch_name, baseline_sha, patch_sha, created_at)
           VALUES (?, 'patch', 'awaiting_release_approval', ?, ?, ?, ?)""",
        (
            int(incident["id"]), job_data["branch_name"], job_data["baseline_sha"],
            job_data["patch_sha"], now_str(),
        ),
    )
    repair_job_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    settings = SimpleNamespace(repo_root=release_settings.repo_root, db_path=db_path)
    return repo, settings, repair_job_id, job_data


def _begin(settings, job_id, job_data):
    begun = begin_git_operation(
        str(settings.db_path), repair_job_id=job_id, operation="release", actor_id=1,
        from_status="awaiting_release_approval", intent_status="releasing",
        expected_head_sha=job_data["baseline_sha"], target_sha=job_data["patch_sha"],
        note="approved test release",
    )
    assert begun is not None
    return begun


def test_reconcile_restores_approval_when_git_never_landed(tmp_path):
    _repo, settings, job_id, job_data = _env(tmp_path)
    operation, _job = _begin(settings, job_id, job_data)

    assert reconcile_git_operations(settings) == [
        {"operation_id": operation["id"], "outcome": "restored_pre_intent"}
    ]
    assert get_repair_job(str(settings.db_path), job_id)["status"] == "awaiting_release_approval"
    assert get_latest_git_operation(str(settings.db_path), job_id)["state"] == "failed"


def test_reconcile_adopts_merge_after_database_cut_point(tmp_path):
    _repo, settings, job_id, job_data = _env(tmp_path)
    operation, releasing_job = _begin(settings, job_id, job_data)
    merged = merge_repair_branch(settings, releasing_job, expected_patch_sha=job_data["patch_sha"])

    result = reconcile_git_operations(settings)

    assert result == [{"operation_id": operation["id"], "outcome": "adopted_release"}]
    repaired = get_repair_job(str(settings.db_path), job_id)
    assert repaired["status"] == "release_pending_restart"
    assert repaired["release_sha"] == merged["release_sha"]


def test_reconcile_stops_on_unrelated_head_and_lock_is_nonblocking(tmp_path):
    repo, settings, job_id, job_data = _env(tmp_path)
    operation, _job = _begin(settings, job_id, job_data)
    (repo / "README.md").write_text("unrelated\n", encoding="utf-8")
    from tests.test_repair_worktree import git

    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "unrelated head")
    result = reconcile_git_operations(settings)
    assert result == [{"operation_id": operation["id"], "outcome": "manual_head_diverged"}]
    assert get_repair_job(str(settings.db_path), job_id)["status"] == "release_reconciliation_required"

    with RepositoryReleaseLock(settings, job_id=job_id, operation="first"):
        with pytest.raises(ReleaseConflict, match="release_locked"):
            with RepositoryReleaseLock(settings, job_id=job_id, operation="second"):
                pass
