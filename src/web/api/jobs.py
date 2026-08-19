"""/api/jobs — DB-backed automation job queue (stage 3).

Creation is whitelisted to ``diagnose`` and ``dry_run`` job types; there is
intentionally no endpoint that can reach a real submission.  Reads are served
from ``automation_jobs`` / ``automation_job_items`` — legacy
``runtime/state/*.json`` files and ``data/batch_state.json`` are no longer
listed here to avoid dual-source confusion.

Stop semantics: ``request-stop`` only records the request (the batch worker
exits at the next brand boundary); ``force-terminate`` (admin) kills the
child and records ``terminated_unknown_state``.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from src.auth_recovery import account_has_open_auth_block
from src.capture.redact import redact_text
from src.db import (
    AUTOMATION_JOB_TERMINAL_STATUSES,
    account_has_active_case_followup,
    create_automation_job,
    get_automation_job,
    get_conn,
    get_profile_lock,
    list_active_automation_jobs,
    list_automation_job_items,
    list_automation_jobs,
    record_web_audit,
)
from src.jobs import profile_locks
from src.jobs.options import NON_SUBMIT_BRAND_LIMIT, normalize_job_options
from src.jobs.paths import job_paths
from src.state_files import read_json_tolerant
from src.web.deps import get_settings, require_role
from src.web.schemas import (
    AutomationJobItemOut,
    AutomationJobOut,
    JobCreateRequest,
)
from src.web.services.read_model import resolve_brand_status

router = APIRouter(prefix="/jobs", tags=["jobs"])

MAX_SSE_LOG_LINES = 200


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _load_account_rows(settings) -> dict[str, dict]:
    payload = read_json_tolerant(settings.accounts_path)
    rows = payload.get("accounts") if isinstance(payload, dict) else []
    return {
        str(row["account_id"]): row
        for row in rows or []
        if isinstance(row, dict) and row.get("account_id")
    }


def _validate_job_request(settings, body: JobCreateRequest) -> tuple[dict, list[str], str | None]:
    """Whitelist validation: catalog account (enabled), brand packs, site."""
    accounts = _load_account_rows(settings)
    account = accounts.get(body.account.strip())
    if account is None:
        raise HTTPException(status_code=422, detail="unknown_account")
    if str(account.get("status") or "").lower() != "active":
        raise HTTPException(status_code=422, detail="account_not_enabled")

    brands = [b.strip() for b in body.brands if b and b.strip()]
    if not brands:
        raise HTTPException(status_code=422, detail="no_brands")
    if len(brands) > NON_SUBMIT_BRAND_LIMIT:
        raise HTTPException(status_code=422, detail="too_many_brands")
    root = settings.brand_packs_root
    for brand in brands:
        if not (root / brand).is_dir():
            raise HTTPException(status_code=422, detail=f"unknown_brand:{brand}")

    site = (body.site or "").strip().upper() or None
    if site:
        known = {p.stem.upper() for p in settings.marketplaces_dir.glob("*.yaml")}
        if site not in known:
            raise HTTPException(status_code=422, detail="unknown_site")
    return account, brands, site


def _create_job(request: Request, user: dict, body: JobCreateRequest, job_type: str):
    settings = get_settings(request)
    db_path = str(settings.db_path)
    ip = _client_ip(request)
    try:
        _account, brands, site = _validate_job_request(settings, body)
        options = _validate_job_options(body, job_type)
    except HTTPException as exc:
        record_web_audit(
            db_path, action="job_create", actor_id=int(user["id"]),
            target_type="automation_job", target_id=f"{job_type}:{body.account}",
            result=f"rejected:{exc.detail}", ip_address=ip,
        )
        raise
    except ValueError as exc:
        reason = str(exc)
        record_web_audit(
            db_path, action="job_create", actor_id=int(user["id"]),
            target_type="automation_job", target_id=f"{job_type}:{body.account}",
            result=f"rejected:{reason}", ip_address=ip,
        )
        raise HTTPException(status_code=422, detail=reason) from exc

    job_id = f"job-{datetime.now():%Y%m%d}-{secrets.token_hex(4)}"
    job = create_automation_job(
        db_path,
        job_id=job_id,
        job_type=job_type,
        created_by=str(user["username"]),
        account_id=body.account.strip(),
        marketplace=site,
        brands=brands,
        options=options,
    )
    record_web_audit(
        db_path, action="job_create", actor_id=int(user["id"]),
        target_type="automation_job", target_id=job_id,
        result="ok", ip_address=ip,
    )
    return {"job": AutomationJobOut(**job)}


def _validate_job_options(body: JobCreateRequest, job_type: str) -> dict[str, Any]:
    raw = body.options.model_dump(exclude_none=True) if body.options else {}
    return normalize_job_options(raw, job_type=job_type)


@router.post("/diagnose")
def create_diagnose_job(
    body: JobCreateRequest,
    request: Request,
    user: dict = Depends(require_role("operator")),  # noqa: B008 (FastAPI dependency idiom)
):
    return _create_job(request, user, body, "diagnose")


@router.post("/dry-run")
def create_dry_run_job(
    body: JobCreateRequest,
    request: Request,
    user: dict = Depends(require_role("operator")),  # noqa: B008 (FastAPI dependency idiom)
):
    return _create_job(request, user, body, "dry_run")


@router.post("/{job_id}/request-stop")
def request_stop_job(
    job_id: str,
    request: Request,
    user: dict = Depends(require_role("operator")),  # noqa: B008 (FastAPI dependency idiom)
):
    settings = get_settings(request)
    db_path = str(settings.db_path)
    existing = get_automation_job(db_path, job_id)
    if not existing:
        raise HTTPException(status_code=404, detail="job_not_found")
    if existing["run_status"] in AUTOMATION_JOB_TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail="job_already_terminal")
    job = request.app.state.job_manager.request_stop(job_id)
    record_web_audit(
        db_path, action="job_request_stop", actor_id=int(user["id"]),
        target_type="automation_job", target_id=job_id,
        result="ok" if job else "noop", ip_address=_client_ip(request),
    )
    return {"job": AutomationJobOut(**job)} if job else {"job": AutomationJobOut(**existing)}


@router.post("/{job_id}/force-terminate")
def force_terminate_job(
    job_id: str,
    request: Request,
    user: dict = Depends(require_role("admin")),  # noqa: B008 (FastAPI dependency idiom)
):
    settings = get_settings(request)
    db_path = str(settings.db_path)
    existing = get_automation_job(db_path, job_id)
    if not existing:
        raise HTTPException(status_code=404, detail="job_not_found")
    job = request.app.state.job_manager.force_terminate(job_id)
    if job is None:
        raise HTTPException(status_code=409, detail="job_already_terminal")
    record_web_audit(
        db_path, action="job_force_terminate", actor_id=int(user["id"]),
        target_type="automation_job", target_id=job_id,
        result="ok", ip_address=_client_ip(request),
    )
    return {"job": AutomationJobOut(**job)}


def _queue_reason(settings, job: dict) -> str | None:
    """Read-time annotation: why a queued job is not dispatching right now.

    Mirrors the dispatcher's own checks so the console can explain the wait
    instead of showing a bare ``queued`` status.
    """
    if job.get("run_status") != "queued":
        return None
    db_path = str(settings.db_path)
    if account_has_open_auth_block(db_path, str(job["account_id"])):
        return "waiting_login"
    if account_has_active_case_followup(db_path, str(job["account_id"])):
        return "waiting_case_followup"
    profile_key = profile_locks.profile_key_for_account(
        settings.accounts_path, str(job["account_id"])
    )
    if profile_key and get_profile_lock(db_path, profile_key):
        return "waiting_profile_lock"
    if any(row["id"] != job["id"] for row in list_active_automation_jobs(db_path)):
        return "waiting_serial_queue"
    return None


def _effective_waiting_error_class(settings, job: dict) -> str | None:
    """Correct stale umbrella reasons without mutating historical job rows."""

    existing = str(job.get("error_class") or "").strip() or None
    if job.get("run_status") != "waiting_human":
        return existing

    conn = get_conn(str(settings.db_path))
    checkpoints = conn.execute(
        """SELECT status FROM submission_checkpoints
           WHERE owner_type='automation_job' AND owner_id=?
           ORDER BY datetime(updated_at) DESC, id DESC""",
        (str(job["id"]),),
    ).fetchall()
    conn.close()
    statuses = {str(row["status"] or "") for row in checkpoints}
    if "waiting_reconciliation" in statuses:
        return "waiting_reconciliation"
    if account_has_open_auth_block(str(settings.db_path), str(job["account_id"])):
        return "waiting_login"
    if "manual_review" in statuses:
        return "manual_review"
    if existing in {"waiting_reconciliation", "waiting_login", "manual_review"}:
        return existing
    return existing or "waiting_human"


def _job_out(settings, job: dict) -> AutomationJobOut:
    payload = dict(job)
    payload["error_class"] = _effective_waiting_error_class(settings, job)
    return AutomationJobOut(**payload, queue_reason=_queue_reason(settings, payload))


@router.get("")
def list_jobs(
    request: Request,
    run_status: str | None = None,
    job_type: str | None = None,
    account_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    settings = get_settings(request)
    rows, total = list_automation_jobs(
        str(settings.db_path),
        run_status=run_status,
        job_type=job_type,
        account_id=account_id,
        limit=limit,
        offset=offset,
    )
    return {
        "jobs": [_job_out(settings, row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def _get_job_or_404(settings, job_id: str) -> dict[str, Any]:
    job = get_automation_job(str(settings.db_path), job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job_not_found")
    return job


@router.get("/{job_id}")
def get_job(job_id: str, request: Request):
    settings = get_settings(request)
    job = _get_job_or_404(settings, job_id)
    items = list_automation_job_items(str(settings.db_path), job_id)
    enriched_items = []
    for item in items:
        authoritative = None
        account_id = str(item.get("account_id") or job.get("account_id") or "")
        marketplace = str(item.get("marketplace") or job.get("marketplace") or "")
        brand_name = str(item.get("brand_name") or "")
        if account_id and marketplace and brand_name:
            authoritative = resolve_brand_status(
                str(settings.db_path),
                account_id,
                marketplace,
                brand_name,
                data_root=settings.data_root,
            )
        enriched_items.append(AutomationJobItemOut(**item, authoritative=authoritative))
    return {
        "job": _job_out(settings, job),
        "items": enriched_items,
    }


def _read_log_lines(path_text: str | None) -> list[str]:
    if not path_text:
        return []
    from pathlib import Path

    path = Path(path_text)
    if not path.is_file():
        return []
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def _sse_event(event_id: int, event: str, data: Any) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"id: {event_id}\nevent: {event}\ndata: {payload}\n\n"


@router.get("/{job_id}/events")
async def job_events(job_id: str, request: Request):
    """SSE: job status changes + redacted stdout increments (2s poll).

    Log events carry their 1-based line number as the event id, so a client
    reconnecting with ``Last-Event-ID`` resumes at the right offset.  At most
    ``MAX_SSE_LOG_LINES`` log lines are sent per connection.
    """
    settings = get_settings(request)
    db_path = str(settings.db_path)
    _get_job_or_404(settings, job_id)

    try:
        log_offset = max(0, int(request.headers.get("last-event-id") or 0))
    except ValueError:
        log_offset = 0
    poll_seconds = max(0.05, float(settings.sse_poll_seconds))

    async def stream():
        offset = log_offset
        sent_log_lines = 0
        last_job_payload: str | None = None
        while True:
            job = get_automation_job(db_path, job_id)
            if not job:
                break
            public = _job_out(settings, job).model_dump(mode="json")
            encoded = json.dumps(public, ensure_ascii=False, sort_keys=True)
            if encoded != last_job_payload:
                last_job_payload = encoded
                yield _sse_event(offset, "job", public)

            if sent_log_lines < MAX_SSE_LOG_LINES:
                lines = _read_log_lines(job.get("stdout_log"))
                new_lines = lines[offset:]
                budget = MAX_SSE_LOG_LINES - sent_log_lines
                for line in new_lines[:budget]:
                    offset += 1
                    sent_log_lines += 1
                    yield _sse_event(offset, "log", {"line": redact_text(line)})
                if len(new_lines) > budget:
                    offset = len(lines)

            if job["run_status"] in AUTOMATION_JOB_TERMINAL_STATUSES:
                break
            if await request.is_disconnected():
                break
            await asyncio.sleep(poll_seconds)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{job_id}/logs")
def get_job_logs(
    job_id: str,
    request: Request,
    stream: str = Query(default="stdout", pattern="^(stdout|stderr)$"),
    lines: int = Query(default=200, ge=1, le=1000),
):
    settings = get_settings(request)
    job = _get_job_or_404(settings, job_id)
    path_text = job.get("stdout_log") if stream == "stdout" else job.get("stderr_log")
    all_lines = _read_log_lines(path_text)
    tail = all_lines[-lines:]
    return {
        "job_id": job_id,
        "stream": stream,
        "total_lines": len(all_lines),
        "lines": [redact_text(line) for line in tail],
    }


# ``job_paths`` re-exported for callers that map a job id to its resource dir
# without touching the DB (e.g. runbook tooling).
__all__ = ["router", "job_paths"]
