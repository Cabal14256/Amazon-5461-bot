"""GET /api/reapplications — reapplication campaigns + per-site attempts."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from src.db import get_conn
from src.web.deps import get_settings
from src.web.schemas import ReapplicationAttemptOut, ReapplicationCampaignOut

router = APIRouter(prefix="/reapplications", tags=["reapplications"])


def _campaign_to_out(row: dict[str, Any], attempts: list[dict[str, Any]]) -> ReapplicationCampaignOut:
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
