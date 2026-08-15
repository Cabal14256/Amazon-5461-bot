"""Batch state parsing shared by the web console and the job manager.

Extracted from stage-2 ``src/web/api/jobs.py::_batch_state_summary`` so the
manager can turn a per-job ``batch_state.json`` into ``automation_job_items``
rows with the exact same semantics the read API used.
"""

from __future__ import annotations

from typing import Any


def batch_state_summary(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    items: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for batch in payload.get("batches") or []:
        for item in (batch or {}).get("items") or []:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "unknown")
            counts[status] = counts.get(status, 0) + 1
            result = item.get("result") if isinstance(item.get("result"), dict) else {}
            items.append({
                "account_id": item.get("account_id"),
                "brand_name": item.get("brand_name"),
                "site": item.get("site") or item.get("marketplace"),
                "status": item.get("status"),
                "case_id": result.get("case_id"),
                "result_status": result.get("status"),
            })
    return {
        "created_at": payload.get("created_at"),
        "item_count": len(items),
        "status_counts": counts,
        "items": items[:500],
    }


def job_items_from_batch_state(payload: Any) -> list[dict[str, Any]]:
    """Map a batch-state payload onto ``automation_job_items`` row dicts."""
    rows: list[dict[str, Any]] = []
    for item in batch_state_summary(payload).get("items") or []:
        rows.append({
            "account_id": item.get("account_id"),
            "marketplace": item.get("site"),
            "brand_name": item.get("brand_name"),
            "run_status": item.get("status"),
            "business_status": item.get("result_status"),
            "case_id": item.get("case_id"),
            "dashboard_status": None,
            "evidence_root": None,
            "started_at": None,
            "finished_at": None,
            "note": None,
        })
    return rows
