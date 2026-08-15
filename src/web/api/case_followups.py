"""GET /api/case-followups — delayed Case check queue."""

from __future__ import annotations

from fastapi import APIRouter, Request

from src.db import list_case_followups
from src.web.deps import get_settings
from src.web.schemas import CaseFollowupOut

router = APIRouter(prefix="/case-followups", tags=["case_followups"])


@router.get("")
def get_case_followups(request: Request, status: str | None = None):
    settings = get_settings(request)
    statuses = [s.strip() for s in status.split(",") if s.strip()] if status else None
    rows = list_case_followups(str(settings.db_path), statuses)
    items = [CaseFollowupOut(**row) for row in rows]
    return {"case_followups": items, "total": len(items)}
