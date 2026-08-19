"""Persistent Seller Central authentication interruptions and safe resume."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from src.auth_recovery import (
    get_auth_block,
    list_auth_blocks,
    open_auth_profile,
    verify_auth_block,
)
from src.db import record_web_audit
from src.web.deps import get_current_user, get_settings, require_role
from src.web.schemas import AuthBlockActionOut, AuthBlockOut

router = APIRouter(prefix="/auth-blocks", tags=["auth_blocks"])


def _profile_hint(profile_key: str) -> str:
    # profile_key is already a one-way hash, but expose only a short hint so
    # it cannot become a stable cross-screen identifier.
    text = str(profile_key or "")
    return f"{text[:4]}…{text[-4:]}" if len(text) >= 10 else "已配置"


def _evidence_path(settings: Any, value: str | None) -> str | None:
    if not value:
        return None
    try:
        absolute = Path(value).resolve()
        root = Path(settings.evidence_root).resolve()
        return absolute.relative_to(root).as_posix()
    except (OSError, ValueError):
        return None


def _to_out(settings: Any, row: dict[str, Any], user: dict[str, Any]) -> AuthBlockOut:
    return AuthBlockOut(
        **{
            key: value
            for key, value in row.items()
            if key not in {"profile_key", "evidence_path", "submit_fenced"}
        },
        profile_hint=_profile_hint(str(row.get("profile_key") or "")),
        submit_fenced=bool(row.get("submit_fenced")),
        evidence_path=_evidence_path(settings, row.get("evidence_path")),
        can_open_profile=str(user.get("role")) in {"reviewer", "admin"},
        can_verify_and_resume=str(user.get("role")) in {"reviewer", "admin"},
    )


def _audit(
    request: Request,
    user: dict[str, Any],
    block_id: int,
    action: str,
    result: str,
    detail: dict[str, Any] | None = None,
) -> None:
    settings = get_settings(request)
    record_web_audit(
        str(settings.db_path),
        action=action,
        actor_id=int(user["id"]),
        target_type="auth_block",
        target_id=str(block_id),
        result=result,
        ip_address=request.client.host if request.client else "",
        detail=json.dumps(detail or {}, ensure_ascii=False),
    )


@router.get("")
def get_auth_blocks(
    request: Request,
    status: str | None = "open",
    user: dict = Depends(get_current_user),  # noqa: B008
):
    settings = get_settings(request)
    rows = list_auth_blocks(settings, status=status)
    items = [_to_out(settings, row, user) for row in rows]
    return {"auth_blocks": items, "total": len(items)}


@router.get("/{block_id}")
def get_auth_block_detail(
    block_id: int,
    request: Request,
    user: dict = Depends(get_current_user),  # noqa: B008
):
    settings = get_settings(request)
    row = get_auth_block(settings, block_id)
    if row is None:
        raise HTTPException(status_code=404, detail="auth_block_not_found")
    return {"auth_block": _to_out(settings, row, user)}


@router.post("/{block_id}/open-profile", response_model=AuthBlockActionOut)
def open_profile(
    block_id: int,
    request: Request,
    user: dict = Depends(require_role("reviewer")),  # noqa: B008
):
    settings = get_settings(request)
    if get_auth_block(settings, block_id) is None:
        _audit(request, user, block_id, "auth_block_open_profile", "not_found")
        raise HTTPException(status_code=404, detail="auth_block_not_found")
    try:
        result = open_auth_profile(settings, block_id)
    except Exception as exc:
        _audit(
            request,
            user,
            block_id,
            "auth_block_open_profile",
            "failed",
            {"error_class": type(exc).__name__},
        )
        raise HTTPException(status_code=502, detail="adspower_profile_open_failed") from None
    _audit(request, user, block_id, "auth_block_open_profile", str(result.get("status") or "ok"))
    return AuthBlockActionOut(**result)


@router.post("/{block_id}/verify-and-resume", response_model=AuthBlockActionOut)
def verify_and_resume(
    block_id: int,
    request: Request,
    user: dict = Depends(require_role("reviewer")),  # noqa: B008
):
    settings = get_settings(request)
    if get_auth_block(settings, block_id) is None:
        _audit(request, user, block_id, "auth_block_verify_resume", "not_found")
        raise HTTPException(status_code=404, detail="auth_block_not_found")
    try:
        result = verify_auth_block(settings, block_id, passive=False)
    except Exception as exc:
        _audit(
            request,
            user,
            block_id,
            "auth_block_verify_resume",
            "failed",
            {"error_class": type(exc).__name__},
        )
        raise HTTPException(status_code=502, detail="auth_verification_failed") from None
    _audit(
        request,
        user,
        block_id,
        "auth_block_verify_resume",
        str(result.get("status") or "unknown"),
        {"actions": result.get("actions") or [], "block_type": result.get("block_type")},
    )
    return AuthBlockActionOut(**result)
