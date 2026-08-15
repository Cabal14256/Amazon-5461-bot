"""GET /api/users — web console user listing (admin only).

User creation/disable happens through ``scripts/manage_web_users.py`` so
passwords never traverse the API.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from src.db import list_web_users
from src.web.deps import get_settings
from src.web.schemas import WebUserOut

router = APIRouter(prefix="/users", tags=["users"])


@router.get("")
def get_users(request: Request):
    settings = get_settings(request)
    users = [WebUserOut(**row) for row in list_web_users(str(settings.db_path))]
    return {"users": users, "total": len(users)}
