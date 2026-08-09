"""Unified result/status normalization for 5461 runs.

This module intentionally starts conservative: it normalizes status and produces a
single summary object that callers can write to batch_state / DB / ledger later.
It does not mutate Excel/SQLite by itself yet.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict


def normalize_submit_status(result: Dict[str, Any]) -> str:
    status = (result.get("submit_result") or result.get("status") or "unknown").lower()
    case_id = result.get("case_id")
    dashboard = result.get("dashboard_check") or {}
    dash_status = (dashboard.get("status") or "").lower() if isinstance(dashboard, dict) else ""

    if case_id and status in {"success", "submitted", "under_review", "approved"}:
        return "success"
    if dash_status in {"under_review", "approved"} and (case_id or dashboard.get("case_id")):
        return "success"
    if dash_status == "draft" or status == "draft":
        return "draft"
    if dash_status == "declined" or status == "declined":
        return "declined"
    if status == "already_approved":
        return "already_approved"
    if status == "success" and not case_id:
        return "submitted_no_case_id_pending_dashboard"
    if status in {"failed", "error", "partial"}:
        return status
    return status or "unknown"


def build_status_summary(
    account_id: str,
    marketplace: str,
    brand_name: str,
    result: Dict[str, Any],
) -> Dict[str, Any]:
    dashboard = result.get("dashboard_check") or {}
    health = result.get("health_classification") or {}
    status = normalize_submit_status(result)
    case_id = result.get("case_id") or (dashboard.get("case_id") if isinstance(dashboard, dict) else None)

    return {
        "account_id": account_id,
        "marketplace": marketplace,
        "brand_name": brand_name,
        "status": status,
        "case_id": case_id,
        "note": result.get("note") or result.get("error") or "",
        "dashboard_status": dashboard.get("status") if isinstance(dashboard, dict) else None,
        "dashboard_case_id": dashboard.get("case_id") if isinstance(dashboard, dict) else None,
        "draft_application_id": result.get("draft_application_id") or (dashboard.get("application_id") if isinstance(dashboard, dict) else None),
        "health_tags": health.get("tags", []),
        "health_severity": health.get("severity"),
        "evidence_files": result.get("evidence_files", []),
        "updated_at": datetime.now().isoformat(),
    }


def apply_dashboard_precedence(result: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy corrected by Dashboard status when available."""
    out = dict(result)
    dashboard = out.get("dashboard_check") or {}
    if not isinstance(dashboard, dict):
        return out
    dash_status = dashboard.get("status")
    dash_case_id = dashboard.get("case_id")
    if dash_status in ("under_review", "approved") and dash_case_id:
        out["status"] = out["submit_result"] = "success"
        out["case_id"] = dash_case_id
    elif dash_status == "draft":
        out["status"] = out["submit_result"] = "draft"
        if dashboard.get("application_id"):
            out["draft_application_id"] = dashboard.get("application_id")
    elif dash_status == "declined":
        out["status"] = out["submit_result"] = "declined"
        if dash_case_id and not out.get("case_id"):
            out["case_id"] = dash_case_id
    return out
