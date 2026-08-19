"""/api/jobs/submit — stage-4 real submit.

A single explicit entry point for real Seller Central submissions:

- ``POST /api/jobs/submit`` (reviewer+) runs the whitelist validation and
  the synchronous preflight, creates the ``submit`` job directly in
  ``queued`` state, and returns the job plus the preflight checks.
- The dispatcher picks the job up from the existing serial queue and runs
  ``cli.amazon5461 run --submit``.

The endpoint is hard-gated by ``settings.submit_enabled`` and every outcome
(success or rejection) is written to ``web_audit_events``.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request

from src.db import create_automation_job, record_web_audit
from src.jobs.preflight import run_submit_preflight
from src.web.api.jobs import _client_ip, _validate_job_options, _validate_job_request
from src.web.deps import get_settings, require_role
from src.web.schemas import AutomationJobOut, JobCreateRequest, SubmitJobResponse

router = APIRouter(prefix="/jobs", tags=["submit"])


def _audit(settings, action: str, user: dict, target_id: str, result: str, ip: str,
           detail: str | None = None) -> None:
    record_web_audit(
        str(settings.db_path),
        action=action,
        actor_id=int(user["id"]),
        target_type="automation_job",
        target_id=target_id,
        result=result,
        ip_address=ip,
        detail=detail,
    )


def _reject(settings, action: str, user: dict, target_id: str, reason: str, ip: str,
            status_code: int, detail) -> None:
    _audit(settings, action, user, target_id, f"rejected:{reason}", ip)
    raise HTTPException(status_code=status_code, detail=detail)


@router.post("/submit")
def create_submit_job(
    body: JobCreateRequest,
    request: Request,
    user: dict = Depends(require_role("reviewer")),  # noqa: B008 (FastAPI dependency idiom)
) -> SubmitJobResponse:
    settings = get_settings(request)
    db_path = str(settings.db_path)
    ip = _client_ip(request)
    target = f"submit:{body.account}"

    if not settings.submit_enabled:
        _reject(settings, "job_submit_created", user, target, "submit_disabled", ip,
                403, "submit_disabled")

    try:
        account_row, brands, site = _validate_job_request(settings, body)
        options = _validate_job_options(body, "submit")
    except HTTPException as exc:
        _audit(settings, "job_submit_created", user, target,
               f"rejected:{exc.detail}", ip)
        raise
    except ValueError as exc:
        reason = str(exc)
        _audit(settings, "job_submit_created", user, target,
               f"rejected:{reason}", ip)
        raise HTTPException(status_code=422, detail=reason) from exc

    if len(brands) > int(settings.submit_max_brands):
        _reject(settings, "job_submit_created", user, target, "too_many_brands", ip,
                422, {"error": "too_many_brands",
                      "max_brands": int(settings.submit_max_brands),
                      "brand_count": len(brands)})

    checks = run_submit_preflight(settings, account_row, brands, site)
    if any(c["level"] == "blocker" and not c["ok"] for c in checks):
        _reject(settings, "job_submit_created", user, target, "preflight_blocked", ip,
                422, {"error": "preflight_blocked", "checks": checks})

    job_id = f"job-{datetime.now():%Y%m%d}-{secrets.token_hex(4)}"
    job = create_automation_job(
        db_path,
        job_id=job_id,
        job_type="submit",
        created_by=str(user["username"]),
        account_id=body.account.strip(),
        marketplace=site,
        brands=brands,
        run_status="queued",
        options=options,
    )
    _audit(
        settings, "job_submit_created", user, job["id"], "ok", ip,
        detail=json.dumps(
            {
                "account": job["account_id"],
                "site": site,
                "brands": brands,
                **({"options": options} if options else {}),
            },
            ensure_ascii=False,
        ),
    )
    return SubmitJobResponse(job=AutomationJobOut(**job), preflight=checks)


# Re-exported for the dispatch layer and runbooks.
__all__ = ["router"]
