"""Sanitized, allowlisted settings API for the internal console."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from src.db import record_web_audit
from src.web.deps import get_settings, require_role
from src.web.schemas import SettingsEffectiveOut, SettingsPatchOut, SettingsPatchRequest
from src.web.settings_registry import (
    SettingsUpdateError,
    apply_immediate_web_settings,
    build_settings_snapshot,
    update_settings_file,
)

router = APIRouter(prefix="/settings", tags=["settings"])


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


@router.get("/effective", response_model=SettingsEffectiveOut)
def get_effective_settings(request: Request):
    settings = get_settings(request)
    try:
        return build_settings_snapshot(Path(settings.settings_path))
    except SettingsUpdateError as exc:
        raise HTTPException(status_code=500, detail=exc.code) from exc


@router.patch("", response_model=SettingsPatchOut)
def patch_settings(
    body: SettingsPatchRequest,
    request: Request,
    user: dict = Depends(require_role("admin")),  # noqa: B008
):
    settings = get_settings(request)
    db_path = str(settings.db_path)
    keys = sorted(str(key) for key in body.changes)
    try:
        result = update_settings_file(
            Path(settings.settings_path),
            Path(settings.settings_backup_root),
            expected_revision=body.revision,
            changes=body.changes,
        )
    except SettingsUpdateError as exc:
        record_web_audit(
            db_path,
            action="settings_update",
            actor_id=int(user["id"]),
            target_type="settings",
            target_id="runtime_settings",
            result=f"rejected:{exc.code}",
            ip_address=_client_ip(request),
            detail=json.dumps({"keys": keys}, ensure_ascii=False),
        )
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc

    apply_immediate_web_settings(settings, result["snapshot"])
    record_web_audit(
        db_path,
        action="settings_update",
        actor_id=int(user["id"]),
        target_type="settings",
        target_id="runtime_settings",
        result="ok",
        ip_address=_client_ip(request),
        detail=json.dumps(
            {
                "changes": result["changed"],
                "restart_required": result["restart_required"],
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
    )
    return result


__all__ = ["router"]
