"""GET /api/applications — submission records with authoritative status."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request

from src.web.deps import get_settings
from src.web.services.read_model import list_applications

router = APIRouter(prefix="/applications", tags=["applications"])


@router.get("")
def get_applications(
    request: Request,
    account_id: str | None = None,
    marketplace: str | None = None,
    brand_name: str | None = None,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 200,
):
    settings = get_settings(request)
    items = list_applications(
        str(settings.db_path),
        data_root=settings.data_root,
        account_id=account_id,
        marketplace=marketplace,
        brand_name=brand_name,
        status=status,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )
    evidence_root = Path(settings.evidence_root).resolve()
    for item in items:
        automation = item.get("automation")
        if not isinstance(automation, dict) or not automation.get("evidence_path"):
            continue
        try:
            automation["evidence_path"] = (
                Path(str(automation["evidence_path"])).resolve()
                .relative_to(evidence_root)
                .as_posix()
            )
        except (OSError, ValueError):
            automation["evidence_path"] = None
    return {"applications": items, "total": len(items)}
