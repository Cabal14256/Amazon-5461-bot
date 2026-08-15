"""Unified read model for the authoritative business status of one
account/site/brand application.

Priority (plan doc §0.9)::

    Case latest Amazon reply (terminal case_followups / case_id_recoveries)
      > Dashboard same-day check (verifications / brand_status_snapshot)
      > explicit Case ID + submission context (submissions)
      > local batch_state files

Every resolution carries a ``source`` tag so the UI can show where the
status came from.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from src.db import get_conn
from src.state_files import read_json_tolerant

# Terminal/manual case outcomes that outrank any dashboard or local state.
_CASE_OUTCOME_RANK = {
    "approved": "approved",
    "declined": "declined",
    "false_approved": "false_approved",
    "action_required": "action_required",
    "answered_unknown": "under_review",
    "pending": "under_review",
    "verification_pending": "under_review",
    "error": "error",
    "blocked": "blocked",
}

_RECOVERY_TERMINAL_STATUSES = {"completed", "failed", "manual_review"}

_SUBMIT_RESULT_MAP = {
    "success": "submitted",
    "partial": "submitted",
    "approved": "approved",
    "declined": "declined",
    "false_approved": "false_approved",
    "failed": "error",
    "error": "error",
}

_BATCH_ITEM_STATUS_MAP = {
    "completed": "submitted",
    "success": "submitted",
    "approved": "approved",
    "declined": "declined",
    "failed": "error",
    "error": "error",
    "skipped": "skipped",
    "pending": "draft",
    "running": "under_review",
}


def _resolution(status: str, source: str, detail: str | None = None,
                checked_at: str | None = None) -> dict[str, Any]:
    return {
        "status": status,
        "source": source,
        "detail": detail,
        "checked_at": checked_at,
    }


def _latest_case_reply(db_path: str, account_id: str, marketplace: str,
                       brand_name: str) -> dict[str, Any] | None:
    conn = get_conn(db_path)
    row = conn.execute(
        """SELECT final_result, case_status, decision_reason, updated_at, status
           FROM case_followups
           WHERE account_id=? AND UPPER(marketplace)=UPPER(?) AND brand_name=? COLLATE NOCASE
             AND final_result IS NOT NULL AND final_result != ''
           ORDER BY datetime(updated_at) DESC, id DESC LIMIT 1""",
        (account_id, marketplace, brand_name),
    ).fetchone()
    conn.close()
    if not row:
        return None
    status = _CASE_OUTCOME_RANK.get(str(row["final_result"]), "under_review")
    return _resolution(
        status,
        "case_reply",
        detail=str(row["decision_reason"] or "")[:300] or None,
        checked_at=str(row["updated_at"] or "") or None,
    )


def _latest_case_id_recovery(db_path: str, account_id: str, marketplace: str,
                             brand_name: str) -> dict[str, Any] | None:
    conn = get_conn(db_path)
    row = conn.execute(
        """SELECT status, case_id, dashboard_status, decision_reason, updated_at
           FROM case_id_recoveries
           WHERE account_id=? AND UPPER(marketplace)=UPPER(?) AND brand_name=? COLLATE NOCASE
           ORDER BY datetime(updated_at) DESC, id DESC LIMIT 1""",
        (account_id, marketplace, brand_name),
    ).fetchone()
    conn.close()
    if not row or str(row["status"]) not in _RECOVERY_TERMINAL_STATUSES:
        return None
    dashboard = str(row["dashboard_status"] or "").lower()
    if dashboard in {"approved", "declined", "false_approved"}:
        status = dashboard
    elif row["case_id"]:
        status = "under_review"
    else:
        return None
    return _resolution(
        status,
        "case_reply",
        detail=str(row["decision_reason"] or "")[:300] or None,
        checked_at=str(row["updated_at"] or "") or None,
    )


def _dashboard_today(db_path: str, account_id: str, marketplace: str,
                     brand_name: str, today: str) -> dict[str, Any] | None:
    conn = get_conn(db_path)
    row = conn.execute(
        """SELECT current_status, last_verified_at, last_method, updated_at
           FROM brand_status_snapshot
           WHERE account_id=? AND UPPER(marketplace)=UPPER(?) AND brand_name=? COLLATE NOCASE
             AND substr(COALESCE(last_verified_at, updated_at), 1, 10)=?
           LIMIT 1""",
        (account_id, marketplace, brand_name, today),
    ).fetchone()
    if not row:
        row = conn.execute(
            """SELECT result, verified_at FROM verifications
               WHERE account_id=? AND UPPER(marketplace)=UPPER(?) AND brand_name=? COLLATE NOCASE
                 AND substr(verified_at, 1, 10)=?
               ORDER BY datetime(verified_at) DESC, id DESC LIMIT 1""",
            (account_id, marketplace, brand_name, today),
        ).fetchone()
        conn.close()
        if not row:
            return None
        status = {"pass": "approved", "fail": "pending"}.get(str(row["result"]), "unknown")
        return _resolution(status, "dashboard_today", checked_at=str(row["verified_at"] or ""))
    conn.close()
    return _resolution(
        str(row["current_status"] or "unknown"),
        "dashboard_today",
        detail=str(row["last_method"] or "") or None,
        checked_at=str(row["last_verified_at"] or row["updated_at"] or "") or None,
    )


def _submission_with_case(db_path: str, account_id: str, marketplace: str,
                          brand_name: str) -> dict[str, Any] | None:
    conn = get_conn(db_path)
    row = conn.execute(
        """SELECT case_id, submit_result, submitted_at, note
           FROM submissions
           WHERE account_id=? AND UPPER(marketplace)=UPPER(?) AND brand_name=? COLLATE NOCASE
             AND case_id IS NOT NULL AND case_id != ''
           ORDER BY datetime(submitted_at) DESC, id DESC LIMIT 1""",
        (account_id, marketplace, brand_name),
    ).fetchone()
    conn.close()
    if not row:
        return None
    mapped = _SUBMIT_RESULT_MAP.get(str(row["submit_result"]), "under_review")
    status = "under_review" if mapped == "submitted" else mapped
    return _resolution(
        status,
        "submission_case_id",
        detail=f"case_id={row['case_id']}",
        checked_at=str(row["submitted_at"] or "") or None,
    )


def _scan_batch_state_file(payload: Any, account_id: str, marketplace: str,
                           brand_name: str) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    for batch in payload.get("batches") or []:
        for item in (batch or {}).get("items") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("account_id") or "") != account_id:
                continue
            if str(item.get("brand_name") or "").lower() != brand_name.lower():
                continue
            site = str(item.get("site") or item.get("marketplace") or "")
            if site and site.upper() != marketplace.upper():
                continue
            result = item.get("result") if isinstance(item.get("result"), dict) else {}
            raw_status = str(result.get("status") or item.get("status") or "")
            status = _BATCH_ITEM_STATUS_MAP.get(raw_status, "unknown")
            case_id = result.get("case_id")
            detail = f"case_id={case_id}" if case_id else None
            return _resolution(
                status, "batch_state", detail=detail,
                checked_at=str(payload.get("created_at") or "") or None,
            )
    return None


def _from_batch_state(data_root: Path, account_id: str, marketplace: str,
                      brand_name: str) -> dict[str, Any] | None:
    data_root = Path(data_root)
    candidates = sorted(
        data_root.glob("*batch*state*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:20]
    for path in candidates:
        payload = read_json_tolerant(path)
        found = _scan_batch_state_file(payload, account_id, marketplace, brand_name)
        if found:
            return found
    return None


def resolve_brand_status(
    db_path: str,
    account_id: str,
    marketplace: str,
    brand_name: str,
    *,
    data_root: Path | None = None,
    today: str | None = None,
) -> dict[str, Any]:
    """Return the authoritative status + source for one account/site/brand."""
    today = today or datetime.now().strftime("%Y-%m-%d")

    for probe in (
        lambda: _latest_case_reply(db_path, account_id, marketplace, brand_name),
        lambda: _latest_case_id_recovery(db_path, account_id, marketplace, brand_name),
        lambda: _dashboard_today(db_path, account_id, marketplace, brand_name, today),
        lambda: _submission_with_case(db_path, account_id, marketplace, brand_name),
    ):
        result = probe()
        if result:
            return result

    if data_root is not None:
        result = _from_batch_state(data_root, account_id, marketplace, brand_name)
        if result:
            return result

    return _resolution("unknown", "none")


def list_applications(
    db_path: str,
    *,
    data_root: Path | None = None,
    account_id: str | None = None,
    marketplace: str | None = None,
    brand_name: str | None = None,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """List submission records enriched with the authoritative read model."""
    sql = """SELECT account_id, marketplace, brand_name, submitted_at, case_id, submit_result
             FROM submissions WHERE 1=1"""
    params: list[Any] = []
    if account_id:
        sql += " AND account_id=?"
        params.append(account_id)
    if marketplace:
        sql += " AND UPPER(marketplace)=UPPER(?)"
        params.append(marketplace)
    if brand_name:
        sql += " AND brand_name=? COLLATE NOCASE"
        params.append(brand_name)
    if date_from:
        sql += " AND submitted_at >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND submitted_at <= ?"
        params.append(date_to + " 23:59:59" if len(date_to) == 10 else date_to)
    sql += " ORDER BY datetime(submitted_at) DESC, id DESC LIMIT ?"
    params.append(max(1, min(int(limit), 1000)))

    conn = get_conn(db_path)
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()

    results: list[dict[str, Any]] = []
    for row in rows:
        resolution = resolve_brand_status(
            db_path,
            row["account_id"],
            row["marketplace"],
            row["brand_name"],
            data_root=data_root,
        )
        if status and resolution["status"] != status:
            continue
        row["authoritative"] = resolution
        results.append(row)
    return results
