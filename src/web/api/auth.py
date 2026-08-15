"""Auth endpoints: login/logout/me. The only POST endpoints of the console."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from src.db import (
    get_web_user_by_username,
    record_web_audit,
    update_web_user_last_login,
)
from src.web.auth import make_session_token, verify_password
from src.web.deps import get_current_user, get_settings
from src.web.schemas import LoginRequest, WebUserOut

router = APIRouter(prefix="/auth", tags=["auth"])


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response):
    settings = get_settings(request)
    limiter = request.app.state.rate_limiter
    db_path = str(settings.db_path)
    username = body.username.strip()
    key = username.lower()
    ip = _client_ip(request)

    blocked_until = limiter.blocked_until(key)
    if blocked_until:
        retry_after = max(1, int(blocked_until - time.time()))
        record_web_audit(db_path, action="login", target_type="web_user",
                         target_id=username, result="rate_limited", ip_address=ip)
        raise HTTPException(
            status_code=429,
            detail="too_many_attempts",
            headers={"Retry-After": str(retry_after)},
        )

    user = get_web_user_by_username(db_path, username)
    if (
        not user
        or int(user.get("disabled") or 0)
        or not verify_password(body.password, user["password_hash"])
    ):
        blocked_until = limiter.record_failure(key)
        record_web_audit(db_path, action="login", target_type="web_user",
                         target_id=username, result="failed", ip_address=ip)
        headers = {}
        if blocked_until:
            headers["Retry-After"] = str(max(1, int(blocked_until - time.time())))
        raise HTTPException(status_code=401, detail="invalid_credentials", headers=headers)

    limiter.reset(key)
    update_web_user_last_login(db_path, int(user["id"]))
    token = make_session_token(
        settings.session_secret,
        int(user["id"]),
        username,
        str(user["role"]),
        ttl_hours=settings.session_ttl_hours,
    )
    response.set_cookie(
        key=settings.session_cookie,
        value=token,
        max_age=int(settings.session_ttl_hours * 3600),
        path="/",
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )
    record_web_audit(db_path, action="login", actor_id=int(user["id"]),
                     target_type="web_user", target_id=username,
                     result="ok", ip_address=ip)
    return {"user": WebUserOut(**user)}


@router.post("/logout")
def logout(request: Request, response: Response, user: dict = Depends(get_current_user)):  # noqa: B008 (FastAPI dependency idiom)
    settings = get_settings(request)
    response.delete_cookie(key=settings.session_cookie, path="/")
    record_web_audit(str(settings.db_path), action="logout", actor_id=int(user["id"]),
                     target_type="web_user", target_id=str(user["username"]),
                     result="ok", ip_address=_client_ip(request))
    return {"ok": True}


@router.get("/me")
def me(user: dict = Depends(get_current_user)):  # noqa: B008 (FastAPI dependency idiom)
    return {"user": WebUserOut(**user)}
