"""GET /api/case-id-recoveries — missing Case ID recovery queue."""

from __future__ import annotations

from fastapi import APIRouter, Request

from src.db import get_conn
from src.web.deps import get_settings
from src.web.schemas import CaseIdRecoveryOut

router = APIRouter(prefix="/case-id-recoveries", tags=["case_id_recoveries"])


@router.get("")
def get_case_id_recoveries(request: Request, status: str | None = None):
    settings = get_settings(request)
    sql = "SELECT * FROM case_id_recoveries"
    params: list = []
    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        if statuses:
            sql += " WHERE status IN (" + ",".join("?" for _ in statuses) + ")"
            params.extend(statuses)
    sql += " ORDER BY scheduled_at, id"
    conn = get_conn(str(settings.db_path))
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    items = [CaseIdRecoveryOut(**row) for row in rows]
    return {"case_id_recoveries": items, "total": len(items)}
