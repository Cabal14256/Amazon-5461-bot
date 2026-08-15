"""GET /api/health — local infrastructure health snapshot."""

from __future__ import annotations

import shutil
import socket
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Request

from src.db import get_conn
from src.state_files import read_json_tolerant
from src.web.deps import get_settings

router = APIRouter(tags=["health"])


def _check_db(db_path: Path) -> dict:
    try:
        conn = get_conn(str(db_path))
        conn.execute("SELECT 1")
        conn.execute("BEGIN IMMEDIATE")
        conn.rollback()
        journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        return {"ok": True, "detail": {"path": str(db_path), "journal_mode": journal}}
    except Exception as exc:
        return {"ok": False, "detail": {"error": exc.__class__.__name__}}


def _check_disk(path: Path) -> dict:
    try:
        usage = shutil.disk_usage(str(path))
        free_gb = round(usage.free / (1024 ** 3), 2)
        return {"ok": free_gb >= 1.0, "detail": {"free_gb": free_gb}}
    except Exception as exc:
        return {"ok": False, "detail": {"error": exc.__class__.__name__}}


def _check_dir_writable(path: Path) -> dict:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".web_health_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return {"ok": True, "detail": {"path": str(path)}}
    except Exception as exc:
        return {"ok": False, "detail": {"path": str(path), "error": exc.__class__.__name__}}


def _check_adspower(api_base_url: str) -> dict:
    try:
        parsed = urlparse(api_base_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 50325
        with socket.create_connection((host, port), timeout=2.0):
            pass
        return {"ok": True, "detail": {"reachable": True}}
    except Exception as exc:
        return {"ok": False, "detail": {"reachable": False, "error": exc.__class__.__name__}}


def _check_codex_signal(signal_path: Path) -> dict:
    payload = read_json_tolerant(signal_path)
    pending = bool(isinstance(payload, dict) and payload.get("status") == "pending")
    return {"ok": not pending, "detail": {"pending": pending}}


@router.get("/health")
def health(request: Request):
    settings = get_settings(request)
    checks = {
        "db": _check_db(settings.db_path),
        "disk": _check_disk(settings.db_path.parent),
        "evidence_dir": _check_dir_writable(settings.evidence_root),
        "logs_dir": _check_dir_writable(settings.logs_root),
        "adspower": _check_adspower(settings.adspower_api_base_url),
        "codex_signal": _check_codex_signal(settings.codex_signal_path),
    }
    ok = all(c["ok"] for c in checks.values())
    return {
        "ok": ok,
        "checks": checks,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
