"""GET/POST /api/incidents/* — stage-5 persisted anomaly queue.

Read endpoints are viewer-level (router dependency); close requires the
operator role and is audited as ``incident_close`` on both success and
refusal.  Evidence bundle files are served through the existing
/api/evidence/file allowlist — this module only lists them, and only when
the bundle directory resolves inside the allowlist roots.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from src.codex_client.triage import run_triage
from src.db import (
    close_incident,
    get_incident,
    get_latest_triage_job,
    list_incidents,
    list_repair_jobs,
    record_web_audit,
)
from src.web.deps import get_settings, require_role
from src.web.schemas import IncidentCloseRequest, IncidentOut, RepairJobOut
from src.web.services.evidence_index import classify_file

router = APIRouter(prefix="/incidents", tags=["incidents"])


def _inside_allowed_roots(settings, resolved: Path) -> bool:
    for root in settings.allowed_roots:
        root_resolved = Path(root).resolve()
        if resolved == root_resolved or root_resolved in resolved.parents:
            return True
    return False


def _bundle_files(settings, incident: dict) -> list[dict]:
    raw = str(incident.get("evidence_bundle_path") or "").strip()
    if not raw:
        return []
    try:
        bundle_dir = Path(raw).resolve()
    except (OSError, ValueError):
        return []
    if not _inside_allowed_roots(settings, bundle_dir) or not bundle_dir.is_dir():
        return []
    files: list[dict] = []
    for child in sorted(bundle_dir.iterdir()):
        if not child.is_file():
            continue
        try:
            resolved = child.resolve()
        except (OSError, ValueError):
            continue
        if not _inside_allowed_roots(settings, resolved):
            continue
        files.append({
            "name": child.name,
            "path": str(resolved),
            "size": int(resolved.stat().st_size),
            "kind": classify_file(resolved),
        })
    return files


@router.get("")
def get_incidents(
    request: Request,
    status: str | None = None,
    classification: str | None = None,
    account_id: str | None = None,
    marketplace: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    settings = get_settings(request)
    rows, total = list_incidents(
        str(settings.db_path),
        status=status,
        classification=classification,
        account_id=account_id,
        marketplace=marketplace,
        limit=limit,
        offset=offset,
    )
    return {"incidents": [IncidentOut(**row) for row in rows], "total": total}


def _latest_triage_payload(settings, incident: dict) -> dict | None:
    """Latest triage job summary plus its validated result.json content."""
    job = get_latest_triage_job(str(settings.db_path), int(incident["id"]))
    if job is None:
        return None
    result = None
    raw_path = str(job.get("result_json_path") or "").strip()
    if raw_path:
        try:
            resolved = Path(raw_path).resolve()
            state_root = Path(settings.codex_state_root).resolve()
            if resolved.is_file() and (resolved == state_root or state_root in resolved.parents):
                result = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            result = None
    return {"job": RepairJobOut(**job), "result": result}


@router.get("/{incident_id}")
def get_incident_detail(incident_id: int, request: Request):
    settings = get_settings(request)
    incident = get_incident(str(settings.db_path), incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="unknown_incident")
    return {
        "incident": IncidentOut(**incident),
        "bundle_files": _bundle_files(settings, incident),
        "latest_triage": _latest_triage_payload(settings, incident),
    }


@router.get("/{incident_id}/repair-jobs")
def get_incident_repair_jobs(incident_id: int, request: Request):
    settings = get_settings(request)
    incident = get_incident(str(settings.db_path), incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="unknown_incident")
    jobs = list_repair_jobs(str(settings.db_path), incident_id)
    return {"jobs": [RepairJobOut(**job) for job in jobs]}


@router.post("/{incident_id}/triage")
def triage_incident_endpoint(
    incident_id: int,
    request: Request,
    user: dict = Depends(require_role("operator")),  # noqa: B008 (FastAPI dependency idiom)
):
    """Manual trigger/retry of a read-only Codex triage (stage 6).

    Synchronous by design (triage is bounded by codex.timeout_sec).  Every
    path — including refusals — is audited as ``incident_triage`` inside
    run_triage; the disabled master switch is audited here.
    """
    settings = get_settings(request)
    db_path = str(settings.db_path)
    ip_address = request.client.host if request.client else ""

    if not settings.codex_enabled:
        record_web_audit(
            db_path, action="incident_triage", actor_id=int(user["id"]),
            target_type="repair_incident", target_id=str(incident_id),
            result="unavailable", ip_address=ip_address,
            detail=json.dumps({"trigger": "manual"}, ensure_ascii=False),
        )
        raise HTTPException(status_code=503, detail="codex_disabled")

    outcome = run_triage(
        settings,
        incident_id,
        trigger="manual",
        actor_id=int(user["id"]),
        ip_address=ip_address,
    )
    if outcome["outcome"] == "unknown_incident":
        raise HTTPException(status_code=404, detail="unknown_incident")
    if outcome["outcome"] == "disabled":  # race with the check above
        raise HTTPException(status_code=503, detail="codex_disabled")
    return {
        "outcome": outcome["outcome"],
        "job": RepairJobOut(**outcome["job"]) if outcome["job"] else None,
        "triage": outcome["triage"],
        "incident": IncidentOut(**outcome["incident"]) if outcome["incident"] else None,
    }


@router.post("/{incident_id}/close")
def close_incident_endpoint(
    incident_id: int,
    body: IncidentCloseRequest,
    request: Request,
    user: dict = Depends(require_role("operator")),  # noqa: B008 (FastAPI dependency idiom)
):
    settings = get_settings(request)
    db_path = str(settings.db_path)
    ip_address = request.client.host if request.client else ""

    existing = get_incident(db_path, incident_id)
    if existing is None:
        record_web_audit(
            db_path, action="incident_close", actor_id=int(user["id"]),
            target_type="repair_incident", target_id=str(incident_id),
            result="unknown_incident", ip_address=ip_address,
        )
        raise HTTPException(status_code=404, detail="unknown_incident")

    closed = close_incident(db_path, incident_id, body.note)
    if closed is None:
        record_web_audit(
            db_path, action="incident_close", actor_id=int(user["id"]),
            target_type="repair_incident", target_id=str(incident_id),
            result="incident_already_closed", ip_address=ip_address,
        )
        raise HTTPException(status_code=409, detail="incident_already_closed")

    record_web_audit(
        db_path, action="incident_close", actor_id=int(user["id"]),
        target_type="repair_incident", target_id=str(incident_id),
        result="ok", ip_address=ip_address,
    )
    return {"incident": IncidentOut(**closed)}
