"""Stage-7 patch generation and Stage-8 approval/release endpoints.

- ``POST /api/incidents/{id}/generate-patch`` (operator+): isolated patch
  generation.  Every path — including refusals — is audited as
  ``patch_generate`` inside ``run_patch_generation`` (the disabled master
  switch is audited here, mirroring the triage endpoint).
- ``GET /api/repair-jobs/{id}`` (viewer+): job row + result.json content.
- ``GET /api/repair-jobs/{id}/diff`` (viewer+): the stored ``patch.diff``
  text, only ever read from inside ``codex_state_root``.

"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse

from src.codex_client.patch import run_patch_generation
from src.db import (
    AUTOMATION_JOB_TERMINAL_STATUSES,
    get_automation_job,
    get_repair_job,
    list_approvals,
    list_automation_job_items,
    mark_incident_rejected,
    mark_incident_rolled_back,
    now_str,
    record_approval,
    record_approval_transition,
    record_web_audit,
    set_repair_job_status,
)
from src.repair.release import (
    ReleaseConflict,
    merge_repair_branch,
    revert_release,
    verify_release_head,
)
from src.web.deps import get_settings, require_role
from src.web.schemas import (
    AutomationJobItemOut,
    AutomationJobOut,
    CanaryConfirmationRequest,
    GeneratePatchRequest,
    IncidentOut,
    ReleaseApprovalRequest,
    RepairApprovalRequest,
    RepairJobOut,
    RepairRejectRequest,
    RepairRollbackRequest,
)
from src.web.services.evidence_index import classify_file

router = APIRouter(tags=["repair"])
_RELEASE_LOCK = threading.Lock()

# Refusal outcomes that never started generation -> HTTP error mapping.
_REFUSAL_STATUS = {
    "incident_not_triaged": 409,
    "incident_closed": 409,
    "active_patch_conflict": 409,
    "unsafe_to_patch": 422,
    "r3_refused": 422,
}


def _job_result_payload(settings, job: dict) -> dict | None:
    """result.json content, confined to the codex state root (same allowlist
    mechanism as the stage-6 triage reads)."""
    raw_path = str(job.get("result_json_path") or "").strip()
    if not raw_path:
        return None
    return _state_json_payload(settings, raw_path)


def _state_json_payload(settings, raw_path: object) -> dict | None:
    """Read one JSON artifact only from codex_state_root."""
    text = str(raw_path or "").strip()
    if not text:
        return None
    try:
        resolved = Path(text).resolve()
        state_root = Path(settings.codex_state_root).resolve()
        if not resolved.is_file() or (resolved != state_root and state_root not in resolved.parents):
            return None
        payload = json.loads(resolved.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except (OSError, ValueError):
        return None


def _validation_payload(settings, job: dict) -> dict | None:
    """Return validation summary with log paths relative to the logs allowlist."""
    payload = _state_json_payload(settings, job.get("validation_json_path"))
    if payload is None:
        return None
    logs_root = Path(settings.logs_root).resolve()
    steps = payload.get("steps")
    if not isinstance(steps, list):
        return payload
    for step in steps:
        if not isinstance(step, dict) or not step.get("log_path"):
            continue
        try:
            resolved = Path(str(step["log_path"])).resolve()
            if logs_root not in resolved.parents or not resolved.is_file():
                step["log_path"] = None
            else:
                step["log_path"] = resolved.relative_to(logs_root).as_posix()
        except (OSError, ValueError):
            step["log_path"] = None
    return payload


def _canary_state(job: dict) -> dict:
    try:
        payload = json.loads(str(job.get("canary_job_ids_json") or "{}"))
    except ValueError:
        return {}
    if not isinstance(payload, dict):
        return {}
    allowed = {
        "diagnose", "dry_run", "post_release", "ready_for_review",
        "failure_reason", "post_release_result", "post_release_reason", "preflight",
    }
    return {key: value for key, value in payload.items() if key in allowed}


def _canary_artifacts(settings, job_id: str) -> list[dict]:
    """List only job-scoped evidence/log files using allowlisted relative paths."""
    artifacts: list[dict] = []
    for root_name, configured_root in (
        ("evidence", settings.evidence_root),
        ("logs", settings.logs_root),
    ):
        root = Path(configured_root).resolve()
        job_root = (root / "jobs" / job_id).resolve()
        if root not in job_root.parents or not job_root.is_dir():
            continue
        for path in sorted(job_root.rglob("*")):
            if len(artifacts) >= 100:
                return artifacts
            if not path.is_file() or path.is_symlink():
                continue
            resolved = path.resolve()
            if root not in resolved.parents:
                continue
            try:
                artifacts.append({
                    "root": root_name,
                    "path": resolved.relative_to(root).as_posix(),
                    "type": classify_file(resolved),
                    "size": int(resolved.stat().st_size),
                })
            except OSError:
                continue
    return artifacts


def _canary_jobs(settings, state: dict) -> list[dict]:
    db_path = str(settings.db_path)
    rows = []
    for kind in ("diagnose", "dry_run", "post_release"):
        job_id = str(state.get(kind) or "").strip()
        if not job_id:
            continue
        job = get_automation_job(db_path, job_id)
        if not job:
            continue
        items = list_automation_job_items(db_path, job_id)
        rows.append({
            "kind": kind,
            "job": AutomationJobOut(**job),
            "items": [AutomationJobItemOut(**item) for item in items],
            "artifacts": _canary_artifacts(settings, job_id),
        })
    return rows


def _job_or_404(settings, job_id: int) -> dict:
    job = get_repair_job(str(settings.db_path), job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown_job")
    if job.get("stage") != "patch":
        raise HTTPException(status_code=409, detail="not_patch_job")
    return job


def _audit(request: Request, user: dict, job_id: int, action: str, result: str) -> None:
    settings = get_settings(request)
    record_web_audit(
        str(settings.db_path),
        action=action,
        actor_id=int(user["id"]),
        target_type="repair_job",
        target_id=str(job_id),
        result=result,
        ip_address=request.client.host if request.client else "",
    )


@router.post("/incidents/{incident_id}/generate-patch")
def generate_patch_endpoint(
    incident_id: int,
    request: Request,
    body: GeneratePatchRequest | None = None,
    user: dict = Depends(require_role("operator")),  # noqa: B008 (FastAPI dependency idiom)
):
    settings = get_settings(request)
    db_path = str(settings.db_path)
    ip_address = request.client.host if request.client else ""
    allow_r2 = bool(body.allow_r2) if body else False

    if not settings.codex_enabled:
        record_web_audit(
            db_path, action="patch_generate", actor_id=int(user["id"]),
            target_type="repair_incident", target_id=str(incident_id),
            result="unavailable", ip_address=ip_address,
            detail=json.dumps({"trigger": "manual", "allow_r2": allow_r2}, ensure_ascii=False),
        )
        raise HTTPException(status_code=503, detail="codex_disabled")

    outcome = run_patch_generation(
        settings,
        incident_id,
        allow_r2=allow_r2,
        trigger="manual",
        actor_id=int(user["id"]),
        ip_address=ip_address,
    )
    name = outcome["outcome"]
    if name == "unknown_incident":
        raise HTTPException(status_code=404, detail="unknown_incident")
    if name == "disabled":  # race with the check above
        raise HTTPException(status_code=503, detail="codex_disabled")
    if name in _REFUSAL_STATUS:
        raise HTTPException(status_code=_REFUSAL_STATUS[name], detail=name)
    return {
        "outcome": name,
        "job": RepairJobOut(**outcome["job"]) if outcome["job"] else None,
        "result": outcome["result"],
        "incident": IncidentOut(**outcome["incident"]) if outcome["incident"] else None,
        "violations": outcome["violations"],
    }


@router.get("/repair-jobs/{job_id}")
def get_repair_job_endpoint(job_id: int, request: Request):
    settings = get_settings(request)
    job = get_repair_job(str(settings.db_path), job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown_job")
    db_path = str(settings.db_path)
    canary = _canary_state(job)
    return {
        "job": RepairJobOut(**job),
        "result": _job_result_payload(settings, job),
        "validation": _validation_payload(settings, job),
        "approvals": list_approvals(db_path, job_id),
        "canary": canary,
        "canary_jobs": _canary_jobs(settings, canary),
        "restart_required": job["status"] == "release_pending_restart",
        "release_enabled": bool(settings.codex_release_enabled),
    }


@router.post("/repair-jobs/{job_id}/approve-validation")
def approve_validation_endpoint(
    job_id: int,
    body: RepairApprovalRequest,
    request: Request,
    user: dict = Depends(require_role("reviewer")),  # noqa: B008
):
    settings = get_settings(request)
    job = _job_or_404(settings, job_id)
    if job["status"] != "awaiting_validation_approval":
        raise HTTPException(status_code=409, detail="invalid_repair_job_state")
    validation = _validation_payload(settings, job)
    if not validation or validation.get("status") != "pass":
        raise HTTPException(status_code=409, detail="validation_not_passed")
    updated = record_approval_transition(
        str(settings.db_path),
        repair_job_id=job_id,
        decision="approve_validation",
        actor_id=int(user["id"]),
        from_status="awaiting_validation_approval",
        to_status="canary",
        note=body.note.strip() or None,
        extra_cols={"canary_job_ids_json": "{}", "finished_at": None},
    )
    if updated is None:
        raise HTTPException(status_code=409, detail="invalid_repair_job_state")
    _audit(request, user, job_id, "repair_approve_validation", "ok")
    return {"job": RepairJobOut(**updated)}


@router.post("/repair-jobs/{job_id}/confirm-canary")
def confirm_canary_endpoint(
    job_id: int,
    body: CanaryConfirmationRequest,
    request: Request,
    user: dict = Depends(require_role("reviewer")),  # noqa: B008
):
    settings = get_settings(request)
    job = _job_or_404(settings, job_id)
    note = body.note.strip()
    if not note or not body.evidence_reviewed:
        raise HTTPException(status_code=422, detail="canary_evidence_confirmation_required")
    if job["status"] != "canary":
        raise HTTPException(status_code=409, detail="invalid_repair_job_state")
    state = _canary_state(job)
    ids = [str(state.get(name) or "") for name in ("diagnose", "dry_run")]
    if not state.get("ready_for_review") or not all(ids):
        raise HTTPException(status_code=409, detail="canary_not_ready")
    if any(
        (get_automation_job(str(settings.db_path), automation_id) or {}).get("run_status")
        != "completed"
        for automation_id in ids
    ):
        raise HTTPException(status_code=409, detail="canary_not_ready")
    updated = record_approval_transition(
        str(settings.db_path),
        repair_job_id=job_id,
        decision="confirm_canary",
        actor_id=int(user["id"]),
        from_status="canary",
        to_status="awaiting_release_approval",
        note=note,
    )
    if updated is None:
        raise HTTPException(status_code=409, detail="invalid_repair_job_state")
    _audit(request, user, job_id, "repair_confirm_canary", "ok")
    return {"job": RepairJobOut(**updated)}


@router.post("/repair-jobs/{job_id}/approve-release")
def approve_release_endpoint(
    job_id: int,
    body: ReleaseApprovalRequest,
    request: Request,
    user: dict = Depends(require_role("admin")),  # noqa: B008
):
    settings = get_settings(request)
    if not settings.codex_release_enabled:
        raise HTTPException(status_code=403, detail="release_disabled")
    with _RELEASE_LOCK:
        job = _job_or_404(settings, job_id)
        if job["status"] != "awaiting_release_approval":
            raise HTTPException(status_code=409, detail="invalid_repair_job_state")
        try:
            merged = merge_repair_branch(
                settings, job, expected_patch_sha=body.patch_sha.strip()
            )
        except ReleaseConflict as exc:
            _audit(request, user, job_id, "repair_approve_release", exc.code)
            raise HTTPException(status_code=409, detail=exc.code) from None
        updated = record_approval_transition(
            str(settings.db_path),
            repair_job_id=job_id,
            decision="approve_release",
            actor_id=int(user["id"]),
            from_status="awaiting_release_approval",
            to_status="release_pending_restart",
            note=body.note.strip() or None,
            extra_cols={
                **merged,
                "release_process_pid": os.getpid(),
                "finished_at": None,
            },
        )
        if updated is None:
            raise HTTPException(status_code=409, detail="release_state_changed")
    _audit(request, user, job_id, "repair_approve_release", "restart_required")
    return {"job": RepairJobOut(**updated), "restart_required": True}


@router.post("/repair-jobs/{job_id}/reject")
def reject_repair_endpoint(
    job_id: int,
    body: RepairRejectRequest,
    request: Request,
    user: dict = Depends(require_role("reviewer")),  # noqa: B008
):
    settings = get_settings(request)
    note = body.note.strip()
    if not note:
        raise HTTPException(status_code=422, detail="note_required")
    job = _job_or_404(settings, job_id)
    allowed = {"awaiting_validation_approval", "canary", "awaiting_release_approval"}
    if job["status"] not in allowed:
        raise HTTPException(status_code=409, detail="invalid_repair_job_state")
    state = _canary_state(job)
    for key in ("diagnose", "dry_run"):
        automation_id = str(state.get(key) or "")
        automation = get_automation_job(str(settings.db_path), automation_id) if automation_id else None
        if automation and automation["run_status"] not in AUTOMATION_JOB_TERMINAL_STATUSES:
            request.app.state.job_manager.request_stop(automation_id)
    updated = record_approval_transition(
        str(settings.db_path),
        repair_job_id=job_id,
        decision="reject",
        actor_id=int(user["id"]),
        from_status=str(job["status"]),
        to_status="rejected",
        note=note,
        extra_cols={"finished_at": now_str()},
    )
    if updated is None:
        raise HTTPException(status_code=409, detail="invalid_repair_job_state")
    mark_incident_rejected(str(settings.db_path), int(job["incident_id"]))
    _audit(request, user, job_id, "repair_reject", "ok")
    return {"job": RepairJobOut(**updated)}


@router.post("/repair-jobs/{job_id}/post-release-check")
def post_release_check_endpoint(
    job_id: int,
    request: Request,
    user: dict = Depends(require_role("admin")),  # noqa: B008
):
    settings = get_settings(request)
    job = _job_or_404(settings, job_id)
    if job["status"] != "release_pending_restart":
        raise HTTPException(status_code=409, detail="invalid_repair_job_state")
    if int(job.get("release_process_pid") or 0) == os.getpid():
        raise HTTPException(status_code=409, detail="restart_required")
    manager_task = getattr(request.app.state.job_manager, "_task", None)
    if manager_task is None or manager_task.done():
        raise HTTPException(status_code=409, detail="service_not_healthy")
    try:
        verify_release_head(settings, job)
    except ReleaseConflict as exc:
        raise HTTPException(status_code=409, detail=exc.code) from None
    updated = set_repair_job_status(
        str(settings.db_path),
        job_id,
        from_statuses=("release_pending_restart",),
        to_status="post_release_check",
        extra_cols={"finished_at": None},
    )
    if updated is None:
        raise HTTPException(status_code=409, detail="invalid_repair_job_state")
    _audit(request, user, job_id, "repair_post_release_check", "started")
    return {"job": RepairJobOut(**updated)}


@router.post("/repair-jobs/{job_id}/rollback")
def rollback_repair_endpoint(
    job_id: int,
    body: RepairRollbackRequest,
    request: Request,
    user: dict = Depends(require_role("admin")),  # noqa: B008
):
    settings = get_settings(request)
    note = body.note.strip()
    if not note:
        raise HTTPException(status_code=422, detail="note_required")
    with _RELEASE_LOCK:
        job = _job_or_404(settings, job_id)
        allowed = {
            "release_pending_restart", "post_release_check",
            "release_check_failed", "released",
        }
        if job["status"] not in allowed:
            raise HTTPException(status_code=409, detail="invalid_repair_job_state")
        state = _canary_state(job)
        post_id = str(state.get("post_release") or "")
        post_job = get_automation_job(str(settings.db_path), post_id) if post_id else None
        if post_job and post_job["run_status"] not in AUTOMATION_JOB_TERMINAL_STATUSES:
            raise HTTPException(status_code=409, detail="post_release_check_active")
        try:
            rollback_sha = revert_release(settings, job)
        except ReleaseConflict as exc:
            _audit(request, user, job_id, "repair_rollback", exc.code)
            raise HTTPException(status_code=409, detail=exc.code) from None
        updated = set_repair_job_status(
            str(settings.db_path),
            job_id,
            from_statuses=(str(job["status"]),),
            to_status="rolled_back",
            extra_cols={"rollback_sha": rollback_sha, "finished_at": now_str()},
        )
        if updated is None:
            raise HTTPException(status_code=409, detail="rollback_state_changed")
        record_approval(
            str(settings.db_path),
            repair_job_id=job_id,
            decision="rollback",
            actor_id=int(user["id"]),
            note=note,
        )
        mark_incident_rolled_back(str(settings.db_path), int(job["incident_id"]))
    _audit(request, user, job_id, "repair_rollback", "ok")
    return {"job": RepairJobOut(**updated)}


@router.get("/repair-jobs/{job_id}/diff")
def get_repair_job_diff_endpoint(job_id: int, request: Request):
    settings = get_settings(request)
    job = get_repair_job(str(settings.db_path), job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown_job")
    # The diff path is reconstructed from the state root, never taken from
    # the job row — reads stay confined to codex_state_root.
    try:
        state_root = Path(settings.codex_state_root).resolve()
        resolved = (state_root / str(int(job["id"])) / "patch.diff").resolve()
    except (OSError, ValueError):
        raise HTTPException(status_code=404, detail="diff_not_found") from None
    if resolved != state_root and state_root not in resolved.parents:
        raise HTTPException(status_code=403, detail="path_not_allowed")
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="diff_not_found")
    return PlainTextResponse(resolved.read_text(encoding="utf-8", errors="replace"))
