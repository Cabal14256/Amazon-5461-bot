"""GET /api/reapplications — reapplication campaigns + per-site attempts."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from src.config_loader import load_yaml
from src.db import get_conn, record_web_audit
from src.jobs.preflight import run_submit_preflight
from src.reapplication import (
    ReapplicationStartError,
    create_campaign_from_declined_case,
    declined_case_candidate,
    get_campaign_by_source_case,
    launch_reapplication_worker,
    list_eligible_declined_cases,
)
from src.web.api.jobs import _validate_job_request
from src.web.config import DEFAULT_SETTINGS_PATH
from src.web.deps import get_settings, require_role
from src.web.schemas import (
    JobCreateRequest,
    ReapplicationAttemptOut,
    ReapplicationCampaignOut,
    ReapplicationStartRequest,
)

router = APIRouter(prefix="/reapplications", tags=["reapplications"])


def _campaign_to_out(row: dict[str, Any], attempts: list[dict[str, Any]]) -> ReapplicationCampaignOut:
    route = row.get("route")
    if not isinstance(route, list):
        try:
            route = json.loads(row.get("route_json") or "[]")
        except (TypeError, json.JSONDecodeError):
            route = []
    return ReapplicationCampaignOut(
        id=row["id"],
        account_id=row["account_id"],
        brand_name=row["brand_name"],
        region=row["region"],
        route=[str(s) for s in route] if isinstance(route, list) else [],
        current_route_index=int(row.get("current_route_index") or 0),
        status=row["status"],
        source_case_followup_id=row.get("source_case_followup_id"),
        source_marketplace=row.get("source_marketplace"),
        stop_reason=row.get("stop_reason"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
        completed_at=row.get("completed_at"),
        attempts=[ReapplicationAttemptOut(**a) for a in attempts],
    )


def _load_attempts(conn, campaign_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT id, campaign_id, route_index, site, status, scheduled_at, started_at,
                  submitted_at, completed_at, case_id, final_result, decision_reason, error
           FROM reapplication_attempts WHERE campaign_id=? ORDER BY route_index""",
        (campaign_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _runtime_settings(web_settings) -> dict:
    raw = load_yaml(str(DEFAULT_SETTINGS_PATH)) or {}
    raw.setdefault("paths", {})["db_path"] = str(web_settings.db_path)
    return raw


def _audit(request: Request, user: dict, followup_id: int, result: str, detail: dict | None = None) -> None:
    settings = get_settings(request)
    record_web_audit(
        str(settings.db_path),
        action="reapplication_authorize",
        actor_id=int(user["id"]),
        target_type="case_followup",
        target_id=str(followup_id),
        result=result,
        ip_address=request.client.host if request.client else "",
        detail=json.dumps(detail or {}, ensure_ascii=False),
    )


@router.get("")
def list_reapplications(request: Request, status: str | None = None):
    settings = get_settings(request)
    sql = "SELECT * FROM reapplication_campaigns"
    params: list = []
    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        if statuses:
            sql += " WHERE status IN (" + ",".join("?" for _ in statuses) + ")"
            params.extend(statuses)
    sql += " ORDER BY updated_at DESC, id DESC"
    conn = get_conn(str(settings.db_path))
    campaigns = [dict(r) for r in conn.execute(sql, params).fetchall()]
    items = [_campaign_to_out(c, _load_attempts(conn, int(c["id"]))) for c in campaigns]
    conn.close()
    return {"reapplications": items, "total": len(items)}


@router.get("/eligible-declines")
def eligible_declines(request: Request):
    settings = get_settings(request)
    items = list_eligible_declined_cases(_runtime_settings(settings))
    return {"eligible_declines": items, "total": len(items)}


@router.post("")
def authorize_reapplication(
    body: ReapplicationStartRequest,
    request: Request,
    user: dict = Depends(require_role("reviewer")),  # noqa: B008
):
    settings = get_settings(request)
    runtime_settings = _runtime_settings(settings)
    followup_id = int(body.source_case_followup_id)
    if not body.authorize_submit:
        _audit(request, user, followup_id, "rejected:submit_authorization_required")
        raise HTTPException(status_code=422, detail="submit_authorization_required")

    existing = get_campaign_by_source_case(runtime_settings, followup_id)
    if existing is not None:
        attempts = existing.get("attempts") or []
        _audit(
            request,
            user,
            followup_id,
            "idempotent_existing",
            {"campaign_id": existing["id"], "route": existing.get("route") or []},
        )
        return {
            "reapplication": _campaign_to_out(existing, attempts),
            "created": False,
            "preflight": [],
            "worker": {"started": False, "reason": "existing_campaign"},
        }
    try:
        candidate = declined_case_candidate(runtime_settings, followup_id)
        account, brands, site = _validate_job_request(
            settings,
            JobCreateRequest(
                account=candidate["account_id"],
                brands=[candidate["brand_name"]],
                site=candidate["next_site"],
            ),
        )
        checks = run_submit_preflight(settings, account, brands, site)
        if any(check["level"] == "blocker" and not check["ok"] for check in checks):
            _audit(request, user, followup_id, "rejected:preflight_blocked")
            raise HTTPException(
                status_code=422,
                detail={"error": "preflight_blocked", "checks": checks},
            )
        campaign = create_campaign_from_declined_case(
            runtime_settings,
            followup_id,
            confirmed_remaining_route=body.confirmed_remaining_route,
            authorize_submit=body.authorize_submit,
        )
    except ReapplicationStartError as exc:
        status = 404 if exc.code == "case_followup_not_found" else 403 if exc.code == "reapplication_disabled" else 409
        if exc.code in {"submit_authorization_required", "route_confirmation_mismatch"}:
            status = 422
        _audit(request, user, followup_id, f"rejected:{exc.code}")
        raise HTTPException(status_code=status, detail=exc.code) from None
    except HTTPException as exc:
        if exc.status_code != 422 or not isinstance(exc.detail, dict):
            _audit(request, user, followup_id, f"rejected:{exc.detail}")
        raise

    attempts = campaign.get("attempts") or []
    worker = {"started": False, "reason": "existing_campaign"}
    if campaign.get("created"):
        worker = launch_reapplication_worker(runtime_settings)
    _audit(
        request,
        user,
        followup_id,
        "ok" if campaign.get("created") else "idempotent_existing",
        {"campaign_id": campaign["id"], "remaining_route": candidate["remaining_route"]},
    )
    return {
        "reapplication": _campaign_to_out(campaign, attempts),
        "created": bool(campaign.get("created")),
        "preflight": checks,
        "worker": {"started": bool(worker.get("started")), "reason": worker.get("reason")},
    }


@router.get("/{campaign_id}")
def get_reapplication(campaign_id: int, request: Request):
    settings = get_settings(request)
    conn = get_conn(str(settings.db_path))
    row = conn.execute(
        "SELECT * FROM reapplication_campaigns WHERE id=?", (campaign_id,)
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="campaign_not_found")
    item = _campaign_to_out(dict(row), _load_attempts(conn, campaign_id))
    conn.close()
    return {"reapplication": item}
