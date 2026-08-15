"""Stage-7 patch endpoints.

- ``POST /api/incidents/{id}/generate-patch`` (operator+): isolated patch
  generation.  Every path — including refusals — is audited as
  ``patch_generate`` inside ``run_patch_generation`` (the disabled master
  switch is audited here, mirroring the triage endpoint).
- ``GET /api/repair-jobs/{id}`` (viewer+): job row + result.json content.
- ``GET /api/repair-jobs/{id}/diff`` (viewer+): the stored ``patch.diff``
  text, only ever read from inside ``codex_state_root``.

Stage 7 implements no approve/release endpoints (stage 8).
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse

from src.codex_client.patch import run_patch_generation
from src.db import get_repair_job, record_web_audit
from src.web.deps import get_settings, require_role
from src.web.schemas import GeneratePatchRequest, IncidentOut, RepairJobOut

router = APIRouter(tags=["repair"])

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
    try:
        resolved = Path(raw_path).resolve()
        state_root = Path(settings.codex_state_root).resolve()
        if not resolved.is_file() or (resolved != state_root and state_root not in resolved.parents):
            return None
        return json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


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
    return {"job": RepairJobOut(**job), "result": _job_result_payload(settings, job)}


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
