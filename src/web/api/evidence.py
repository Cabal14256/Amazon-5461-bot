"""GET /api/evidence/* — allowlisted evidence listing and file access.

All paths are resolved through ``resolve_allowed_path``; text payloads are
redacted a second time via ``src/capture/redact.py`` before serving.  Every
file download is recorded in ``web_audit_events``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse

from src.capture.redact import redact_text
from src.db import record_web_audit
from src.web.deps import get_current_user, get_settings
from src.web.schemas import EvidenceItemOut
from src.web.services.evidence_index import (
    MAX_TEXT_BYTES,
    PathNotAllowed,
    classify_file,
    list_evidence,
    resolve_allowed_path,
)

router = APIRouter(prefix="/evidence", tags=["evidence"])


@router.get("/list")
def get_evidence_list(
    request: Request,
    date: str | None = None,
    account_id: str | None = None,
):
    settings = get_settings(request)
    items = list_evidence(
        str(settings.db_path),
        settings.evidence_root,
        settings.logs_root,
        date=date,
        account_id=account_id,
    )
    return {"evidence": [EvidenceItemOut(**item) for item in items], "total": len(items)}


@router.get("/file")
def get_evidence_file(path: str, request: Request,
                      user: dict = Depends(get_current_user)):  # noqa: B008 (FastAPI dependency idiom)
    settings = get_settings(request)
    try:
        resolved, root = resolve_allowed_path(settings.allowed_roots, path)
    except PathNotAllowed:
        raise HTTPException(status_code=403, detail="path_not_allowed") from None
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="file_not_found") from None

    relative = resolved.relative_to(root).as_posix()
    record_web_audit(
        str(settings.db_path),
        action="evidence_download",
        actor_id=int(user["id"]),
        target_type="evidence_file",
        target_id=relative,
        result="ok",
        ip_address=request.client.host if request.client else "",
    )

    if classify_file(resolved) == "text":
        raw = resolved.read_bytes()[:MAX_TEXT_BYTES]
        text = raw.decode("utf-8", errors="replace")
        return PlainTextResponse(redact_text(text))
    return FileResponse(resolved)
