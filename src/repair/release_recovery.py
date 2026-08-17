"""Crash reconciliation for persisted Stage-8 Git operation intents."""

from __future__ import annotations

import logging

from src.db import (
    abort_git_operation,
    complete_git_operation,
    get_repair_job,
    list_pending_git_operations,
    mark_incident_rolled_back,
    now_str,
    record_web_audit,
    require_git_operation_reconciliation,
)
from src.repair.release import (
    ReleaseConflict,
    RepositoryReleaseLock,
    current_head,
    is_expected_release_merge,
    is_expected_release_revert,
    is_worktree_clean,
)

logger = logging.getLogger(__name__)


def _audit(settings, operation: dict, result: str) -> bool:
    try:
        record_web_audit(
            str(settings.db_path),
            action="repair_git_reconcile",
            actor_id=int(operation["actor_id"]),
            target_type="repair_job",
            target_id=str(operation["repair_job_id"]),
            result=str(result),
        )
    except Exception:
        logger.exception(
            "Failed to audit Git reconciliation operation_id=%s",
            operation.get("id"),
        )
        return False
    return True


def _require_manual(
    db_path: str,
    operation: dict,
    *,
    intent_status: str,
    manual_status: str,
    error_code: str,
) -> bool:
    return require_git_operation_reconciliation(
        db_path,
        int(operation["id"]),
        intent_status=intent_status,
        reconciliation_status=manual_status,
        error_code=error_code,
    ) is not None


def reconcile_git_operations(settings) -> list[dict]:
    """Reconcile every prepared operation without resetting Git history."""
    db_path = str(settings.db_path)
    results: list[dict] = []
    for operation in list_pending_git_operations(db_path):
        op_name = str(operation["operation"])
        intent_status = "releasing" if op_name == "release" else "rolling_back"
        manual_status = (
            "release_reconciliation_required"
            if op_name == "release"
            else "rollback_reconciliation_required"
        )
        try:
            with RepositoryReleaseLock(
                settings,
                job_id=int(operation["repair_job_id"]),
                operation=f"reconcile_{op_name}",
            ):
                job = get_repair_job(db_path, int(operation["repair_job_id"]))
                if job is None or job.get("status") != intent_status:
                    transitioned = _require_manual(
                        db_path,
                        operation,
                        intent_status=intent_status,
                        manual_status=manual_status,
                        error_code="job_state_mismatch",
                    )
                    outcome = (
                        "manual_job_state_mismatch"
                        if transitioned
                        else "manual_finalize_conflict"
                    )
                else:
                    head = current_head(settings)
                    if head == str(operation["expected_head_sha"]):
                        restored = abort_git_operation(
                            db_path,
                            int(operation["id"]),
                            intent_status=intent_status,
                            error_code="interrupted_before_git",
                        )
                        if restored is not None:
                            outcome = "restored_pre_intent"
                        else:
                            _require_manual(
                                db_path,
                                operation,
                                intent_status=intent_status,
                                manual_status=manual_status,
                                error_code="abort_state_conflict",
                            )
                            outcome = "manual_finalize_conflict"
                    elif op_name == "release" and is_expected_release_merge(
                        settings,
                        expected_head=str(operation["expected_head_sha"]),
                        target_sha=str(operation["target_sha"]),
                    ):
                        if not is_worktree_clean(settings):
                            transitioned = _require_manual(
                                db_path,
                                operation,
                                intent_status=intent_status,
                                manual_status=manual_status,
                                error_code="dirty_worktree_after_git",
                            )
                            outcome = (
                                "manual_dirty_worktree"
                                if transitioned
                                else "manual_finalize_conflict"
                            )
                        else:
                            completed = complete_git_operation(
                                db_path,
                                int(operation["id"]),
                                intent_status=intent_status,
                                final_status="release_pending_restart",
                                result_sha=head,
                                extra_cols={
                                    "pre_release_sha": str(operation["expected_head_sha"]),
                                    "release_sha": head,
                                    "release_process_pid": operation.get("process_pid"),
                                    "finished_at": None,
                                },
                            )
                            if completed is not None:
                                outcome = "adopted_release"
                            else:
                                _require_manual(
                                    db_path,
                                    operation,
                                    intent_status=intent_status,
                                    manual_status=manual_status,
                                    error_code="finalize_state_conflict",
                                )
                                outcome = "manual_finalize_conflict"
                    elif op_name == "rollback" and is_expected_release_revert(
                        settings,
                        expected_head=str(operation["expected_head_sha"]),
                        pre_release_sha=str(job.get("pre_release_sha") or ""),
                    ):
                        if not is_worktree_clean(settings):
                            transitioned = _require_manual(
                                db_path,
                                operation,
                                intent_status=intent_status,
                                manual_status=manual_status,
                                error_code="dirty_worktree_after_git",
                            )
                            outcome = (
                                "manual_dirty_worktree"
                                if transitioned
                                else "manual_finalize_conflict"
                            )
                        else:
                            completed = complete_git_operation(
                                db_path,
                                int(operation["id"]),
                                intent_status=intent_status,
                                final_status="rolled_back",
                                result_sha=head,
                                extra_cols={"rollback_sha": head, "finished_at": now_str()},
                            )
                            if completed:
                                mark_incident_rolled_back(db_path, int(job["incident_id"]))
                            if completed is not None:
                                outcome = "adopted_rollback"
                            else:
                                _require_manual(
                                    db_path,
                                    operation,
                                    intent_status=intent_status,
                                    manual_status=manual_status,
                                    error_code="finalize_state_conflict",
                                )
                                outcome = "manual_finalize_conflict"
                    else:
                        transitioned = _require_manual(
                            db_path,
                            operation,
                            intent_status=intent_status,
                            manual_status=manual_status,
                            error_code="head_diverged",
                        )
                        outcome = (
                            "manual_head_diverged"
                            if transitioned
                            else "manual_finalize_conflict"
                        )
        except ReleaseConflict as exc:
            outcome = exc.code
        result = {"operation_id": int(operation["id"]), "outcome": outcome}
        if not _audit(settings, operation, outcome):
            result["audit_error"] = True
        results.append(result)
    return results
