"""Deterministic local handoff signals for Codex-assisted diagnosis."""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SIGNAL_PATH = PROJECT_ROOT / "runtime" / "codex_signal.json"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_pending_signal(
    *,
    reason: str,
    brand_data: dict[str, Any],
    evidence: dict[str, Any] | None = None,
    step: int | None = None,
    signal_path: Path = DEFAULT_SIGNAL_PATH,
) -> dict[str, Any]:
    """Write a minimal non-secret failure signal for later Codex inspection."""
    evidence = evidence or {}
    payload: dict[str, Any] = {
        "version": 1,
        "signal_id": str(uuid.uuid4()),
        "status": "pending",
        "created_at": _utc_now(),
        "reason": str(reason),
        "account_id": str(brand_data.get("account_id") or ""),
        "brand_name": str(brand_data.get("brand_name") or ""),
        "site": str(brand_data.get("site") or brand_data.get("marketplace") or ""),
        "step": step,
        "page_url": str(evidence.get("url") or ""),
        "page_title": str(evidence.get("title") or ""),
        "screenshot_path": str(evidence.get("screenshot_path") or ""),
    }
    _atomic_write_json(Path(signal_path), payload)
    return payload


def read_signal(signal_path: Path = DEFAULT_SIGNAL_PATH) -> dict[str, Any] | None:
    """Return the current signal, or ``None`` when no signal exists."""
    path = Path(signal_path)
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Signal must contain a JSON object: {path}")
    return payload


def mark_signal_handled(
    *,
    note: str = "",
    signal_path: Path = DEFAULT_SIGNAL_PATH,
) -> dict[str, Any] | None:
    """Mark the current signal handled while retaining it as an audit record."""
    payload = read_signal(signal_path)
    if payload is None:
        return None
    payload["status"] = "handled"
    payload["handled_at"] = _utc_now()
    if note:
        payload["note"] = str(note)[:500]
    _atomic_write_json(Path(signal_path), payload)
    return payload
