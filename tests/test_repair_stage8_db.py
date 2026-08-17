"""Stage-8 DB migration + approval/state-transition helpers (src/db.py).

Covers the repair_approvals table, the widened codex_repair_jobs columns and
active-patch partial unique index (drop + recreate migration), the
set_repair_job_status conditional transition helper, and the stage-8
incident status transitions (validating/validated/released/rejected).
"""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db import (  # noqa: E402
    create_patch_job,
    get_repair_job,
    init_db,
    list_approvals,
    mark_incident_patch_ready,
    mark_incident_patching,
    mark_incident_rejected,
    mark_incident_released,
    mark_incident_triaged,
    mark_incident_validated,
    mark_incident_validating,
    mark_incident_validation_failed,
    record_approval,
    record_incident,
    set_repair_job_status,
)

OLD_INDEX_SQL = """CREATE UNIQUE INDEX idx_codex_repair_jobs_active_patch
ON codex_repair_jobs(incident_id)
WHERE stage='patch' AND status IN ('running','patch_ready','validating')"""

NEW_ACTIVE_STATUSES = (
    "awaiting_validation_approval",
    "canary",
    "awaiting_release_approval",
    "release_pending_restart",
    "post_release_check",
    "release_check_failed",
)


@pytest.fixture()
def db_path(tmp_path):
    path = tmp_path / "state" / "ledger.db"
    init_db(str(path))
    return str(path)


def _seed_incident(db_path, signature="sig-stage8"):
    incident, _ = record_incident(
        db_path,
        signature=signature,
        scope_type="account",
        flow_type="5461",
        account_id="us_store_999",
        marketplace="US",
        brand_name="ACME",
        classification="selector_missing",
        confidence=0.9,
    )
    return int(incident["id"])


def _seed_patch_ready_incident(db_path, signature="sig-stage8"):
    """Incident walked to patch_ready with its patch job attached."""
    incident_id = _seed_incident(db_path, signature)
    assert mark_incident_triaged(db_path, incident_id) is not None
    assert mark_incident_patching(db_path, incident_id) is not None
    job = create_patch_job(db_path, incident_id)
    assert job is not None
    assert mark_incident_patch_ready(db_path, incident_id) is not None
    return incident_id, int(job["id"])


def _index_sql(db_path, name):
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (name,)
    ).fetchone()
    conn.close()
    return row[0] if row else None


def test_migration_idempotent(tmp_path):
    """init_db twice: no error; new table, columns and index present."""
    path = str(tmp_path / "state" / "ledger.db")
    init_db(path)
    init_db(path)

    conn = sqlite3.connect(path)
    tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "repair_approvals" in tables
    job_cols = {r[1] for r in conn.execute("PRAGMA table_info(codex_repair_jobs)")}
    approval_cols = {r[1] for r in conn.execute("PRAGMA table_info(repair_approvals)")}
    conn.close()

    for col in (
        "pre_release_sha",
        "release_sha",
        "rollback_sha",
        "validation_json_path",
        "validation_pid",
        "validation_started_at",
        "validation_heartbeat_at",
        "canary_job_ids_json",
        "release_process_pid",
    ):
        assert col in job_cols
    assert approval_cols == {
        "id", "repair_job_id", "decision", "actor_id", "note", "created_at"
    }
    assert _index_sql(path, "idx_repair_approvals_job") is not None


def test_active_patch_index_rebuilt_from_old_db(db_path):
    """An old-predicate partial unique index is rebuilt with the widened
    stage-8 status list, and the new statuses hold the exclusive slot."""
    # Simulate a stage-7 database: old predicate on the same index name.
    conn = sqlite3.connect(db_path)
    conn.execute("DROP INDEX idx_codex_repair_jobs_active_patch")
    conn.execute(OLD_INDEX_SQL)
    conn.commit()
    conn.close()
    assert "awaiting_validation_approval" not in _index_sql(
        db_path, "idx_codex_repair_jobs_active_patch"
    )

    # Re-running the migration rebuilds the index with the new predicate.
    init_db(db_path)
    sql = _index_sql(db_path, "idx_codex_repair_jobs_active_patch")
    for status in NEW_ACTIVE_STATUSES:
        assert status in sql

    # A patch job parked in awaiting_validation_approval blocks a second
    # active patch job for the same incident (index-level backstop).
    incident_id, job_id = _seed_patch_ready_incident(db_path)
    assert set_repair_job_status(
        db_path, job_id,
        from_statuses=("running", "patch_ready"),
        to_status="awaiting_validation_approval",
    ) is not None

    conn = sqlite3.connect(db_path)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO codex_repair_jobs(incident_id, stage, status, created_at)
               VALUES (?, 'patch', 'running', '2026-08-13 00:00:00')""",
            (incident_id,),
        )
    conn.close()
    # The app-level check-and-insert reports a conflict instead of raising.
    assert create_patch_job(db_path, incident_id) is None


def test_set_repair_job_status_legal_and_illegal(db_path):
    incident_id, job_id = _seed_patch_ready_incident(db_path)
    assert incident_id > 0

    # Legal: running -> validating with extra columns in one statement.
    job = set_repair_job_status(
        db_path, job_id,
        from_statuses=("running", "patch_ready"),
        to_status="validating",
        extra_cols={"validation_json_path": "runtime/logs/repair/v1.json"},
    )
    assert job is not None
    assert job["status"] == "validating"
    assert job["validation_json_path"] == "runtime/logs/repair/v1.json"

    # Illegal: current status not in from_statuses -> None, row untouched.
    assert set_repair_job_status(
        db_path, job_id, from_statuses=("running",), to_status="released"
    ) is None
    assert get_repair_job(db_path, job_id)["status"] == "validating"

    # Illegal: unknown job id -> None.
    assert set_repair_job_status(
        db_path, 99999, from_statuses=("validating",), to_status="released"
    ) is None


def test_record_and_list_approvals(db_path):
    _, job_id = _seed_patch_ready_incident(db_path)

    first = record_approval(
        db_path, repair_job_id=job_id, decision="approve_validation", actor_id=7,
        note="diff reviewed",
    )
    second = record_approval(
        db_path, repair_job_id=job_id, decision="approve_release", actor_id=8,
    )
    assert second > first

    approvals = list_approvals(db_path, job_id)
    assert [a["id"] for a in approvals] == [first, second]
    assert approvals[0]["decision"] == "approve_validation"
    assert approvals[0]["actor_id"] == 7
    assert approvals[0]["note"] == "diff reviewed"
    assert approvals[1]["decision"] == "approve_release"
    assert approvals[1]["note"] is None
    assert all(a["repair_job_id"] == job_id for a in approvals)

    # Other jobs have an empty trail; unknown decisions are refused.
    assert list_approvals(db_path, job_id + 100) == []
    with pytest.raises(ValueError):
        record_approval(
            db_path, repair_job_id=job_id, decision="ship_it", actor_id=7
        )


def test_incident_stage8_happy_path(db_path):
    incident_id, _ = _seed_patch_ready_incident(db_path)

    assert mark_incident_validating(db_path, incident_id) is not None
    assert mark_incident_validated(db_path, incident_id) is not None
    released = mark_incident_released(db_path, incident_id)
    assert released is not None
    assert released["status"] == "released"


def test_incident_stage8_illegal_transitions(db_path):
    # open incident: validating is illegal.
    incident_id = _seed_incident(db_path)
    assert mark_incident_validating(db_path, incident_id) is None

    # patch_ready: validated/released are illegal without validating first.
    incident_id, _ = _seed_patch_ready_incident(db_path, signature="sig-stage8-b")
    assert mark_incident_validated(db_path, incident_id) is None
    assert mark_incident_released(db_path, incident_id) is None

    # validating: released is illegal without validated first.
    assert mark_incident_validating(db_path, incident_id) is not None
    assert mark_incident_released(db_path, incident_id) is None


def test_incident_rejected_from_each_gate(db_path):
    for signature, reach in (
        ("sig-rej-ready", "patch_ready"),
        ("sig-rej-validating", "validating"),
        ("sig-rej-validated", "validated"),
    ):
        incident_id, _ = _seed_patch_ready_incident(db_path, signature=signature)
        if reach in ("validating", "validated"):
            assert mark_incident_validating(db_path, incident_id) is not None
        if reach == "validated":
            assert mark_incident_validated(db_path, incident_id) is not None
        rejected = mark_incident_rejected(db_path, incident_id)
        assert rejected is not None
        assert rejected["status"] == "rejected"

    # rejected is terminal: no further transitions.
    assert mark_incident_validating(db_path, incident_id) is None
    assert mark_incident_rejected(db_path, incident_id) is None

    # open incident cannot be rejected through this helper.
    open_id = _seed_incident(db_path, signature="sig-rej-open")
    assert mark_incident_rejected(db_path, open_id) is None


def test_incident_validation_failed_returns_to_triaged(db_path):
    incident_id, _ = _seed_patch_ready_incident(db_path)
    assert mark_incident_validation_failed(db_path, incident_id) is None

    assert mark_incident_validating(db_path, incident_id) is not None
    failed = mark_incident_validation_failed(db_path, incident_id)
    assert failed is not None
    assert failed["status"] == "triaged"
