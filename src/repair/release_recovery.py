"""Crash reconciliation for persisted Stage-8 Git operation intents."""

from __future__ import annotations

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
)


def _audit(settings, operation: dict, result: str) -> None:
    record_web_audit(
        str(settings.db_path),
        action="repair_git_reconcile",
        actor_id=int(operation["actor_id"]),
        target_type="repair_job",
        target_id=str(operation["repair_job_id"]),
        result=str(result),
    )


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
                    require_git_operation_reconciliation(
                        db_path,
                        int(operation["id"]),
                        intent_status=intent_status,
                        reconciliation_status=manual_status,
                        error_code="job_state_mismatch",
                    )
                    outcome = "manual_job_state_mismatch"
                else:
                    head = current_head(settings)
                    if head == str(operation["expected_head_sha"]):
                        abort_git_operation(
                            db_path,
                            int(operation["id"]),
                            intent_status=intent_status,
                            error_code="interrupted_before_git",
                        )
                        outcome = "restored_pre_intent"
                    elif op_name == "release" and is_expected_release_merge(
                        settings,
                        expected_head=str(operation["expected_head_sha"]),
                        target_sha=str(operation["target_sha"]),
                    ):
                        complete_git_operation(
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
                        outcome = "adopted_release"
                    elif op_name == "rollback" and is_expected_release_revert(
                        settings,
                        expected_head=str(operation["expected_head_sha"]),
                        pre_release_sha=str(job.get("pre_release_sha") or ""),
                    ):
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
                        outcome = "adopted_rollback"
                    else:
                        require_git_operation_reconciliation(
                            db_path,
                            int(operation["id"]),
                            intent_status=intent_status,
                            reconciliation_status=manual_status,
                            error_code="head_diverged",
                        )
                        outcome = "manual_head_diverged"
        except ReleaseConflict as exc:
            outcome = exc.code
        _audit(settings, operation, outcome)
        results.append({"operation_id": int(operation["id"]), "outcome": outcome})
    return results
