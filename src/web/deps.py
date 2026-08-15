"""FastAPI dependencies: current user extraction and role enforcement."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, HTTPException, Request

from src.db import get_web_user_by_username
from src.web.auth import verify_session_token
from src.web.config import WebSettings

ROLE_ORDER = {"viewer": 0, "operator": 1, "reviewer": 2, "admin": 3}


def get_settings(request: Request) -> WebSettings:
    return request.app.state.settings


def get_current_user(request: Request) -> dict:
    settings = get_settings(request)
    token = request.cookies.get(settings.session_cookie)
    payload = verify_session_token(settings.session_secret, token)
    if not payload:
        raise HTTPException(status_code=401, detail="not_authenticated")
    user = get_web_user_by_username(str(settings.db_path), str(payload.get("u") or ""))
    if not user or int(user.get("disabled") or 0):
        raise HTTPException(status_code=401, detail="not_authenticated")
    if int(user["id"]) != int(payload.get("uid") or -1):
        raise HTTPException(status_code=401, detail="not_authenticated")
    # Never expose the password hash through the request context.
    user.pop("password_hash", None)
    return user


def require_role(min_role: str) -> Callable:
    min_level = ROLE_ORDER[min_role]

    def dependency(user: dict = Depends(get_current_user)) -> dict:  # noqa: B008 (FastAPI dependency idiom)
        if ROLE_ORDER.get(str(user.get("role") or ""), -1) < min_level:
            raise HTTPException(status_code=403, detail="forbidden")
        return user

    return dependency
