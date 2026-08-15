"""Deterministic local handoff signals for Codex-assisted diagnosis."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from src.state_files import atomic_write_json

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SIGNAL_PATH = PROJECT_ROOT / "runtime" / "codex_signal.json"


def _local_now() -> str:
    # 与 src/db.py 的业务记录一致：本地时间、不带时区（"%Y-%m-%d %H:%M:%S"）。
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def write_pending_signal(
    *,
    reason: str,
    brand_data: dict[str, Any],
    evidence: dict[str, Any] | None = None,
    step: int | None = None,
    signal_path: Path = DEFAULT_SIGNAL_PATH,
    db_path: str | None = None,
) -> dict[str, Any]:
    """Write a minimal non-secret failure signal for later Codex inspection.

    When ``db_path`` is given, the failure is additionally persisted as a
    stage-5 repair incident (deduplicated by signature) with a redacted
    evidence bundle.  Incident persistence never breaks signal writing.
    """
    evidence = evidence or {}
    payload: dict[str, Any] = {
        "version": 1,
        "signal_id": str(uuid.uuid4()),
        "status": "pending",
        "created_at": _local_now(),
        "reason": str(reason),
        "account_id": str(brand_data.get("account_id") or ""),
        "brand_name": str(brand_data.get("brand_name") or ""),
        "site": str(brand_data.get("site") or brand_data.get("marketplace") or ""),
        "step": step,
        "page_url": str(evidence.get("url") or ""),
        "page_title": str(evidence.get("title") or ""),
        "screenshot_path": str(evidence.get("screenshot_path") or ""),
    }
    atomic_write_json(Path(signal_path), payload)
    if db_path is not None:
        try:
            _record_signal_incident(db_path, payload, evidence)
        except Exception as exc:
            print(f"[WARN] failed to record repair incident for signal: {exc}")
    return payload


def _record_signal_incident(
    db_path: str, payload: dict[str, Any], evidence: dict[str, Any]
) -> None:
    from src.db import record_incident, set_incident_evidence_bundle
    from src.incidents import (
        build_evidence_bundle,
        classify_failure,
        compute_signature,
        initial_confidence,
    )

    reason = str(payload.get("reason") or "")
    flow_type = "5461"
    if reason.startswith("stuck_at_") or reason == "max_steps_exceeded":
        classification = "flow_loop_exhausted"
    else:
        classification = classify_failure(reason=reason)
    signature = compute_signature(
        flow_type=flow_type,
        url_pattern=str(payload.get("page_url") or ""),
        state_loop_reason=reason,
        error_class=classification,
    )
    incident, created = record_incident(
        db_path,
        signature=signature,
        scope_type="account",
        flow_type=flow_type,
        account_id=str(payload.get("account_id") or ""),
        marketplace=str(payload.get("site") or ""),
        brand_name=str(payload.get("brand_name") or ""),
        detector_type="codex_signal",
        classification=classification,
        confidence=initial_confidence(classification),
    )
    if created:
        bundle_dir = build_evidence_bundle(
            int(incident["id"]),
            PROJECT_ROOT / "runtime" / "evidence",
            page_evidence=evidence,
            run_context={
                "account_id": payload.get("account_id") or "",
                "site": payload.get("site") or "",
                "brand_name": payload.get("brand_name") or "",
                "detector_type": "codex_signal",
                "reason": reason,
                "step": payload.get("step"),
            },
            screenshot_path=payload.get("screenshot_path") or None,
        )
        set_incident_evidence_bundle(db_path, int(incident["id"]), str(bundle_dir))


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
    payload["handled_at"] = _local_now()
    if note:
        payload["note"] = str(note)[:500]
    atomic_write_json(Path(signal_path), payload)
    return payload
