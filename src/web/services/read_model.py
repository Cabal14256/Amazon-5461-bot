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

_AUTOMATION_VISIBLE_STATUSES = {
    "waiting_login": "waiting_login",
    "waiting_reconciliation": "waiting_reconciliation",
    "manual_review": "manual_review",
}


def _resolution(status: str, source: str, detail: str | None = None,
                checked_at: str | None = None) -> dict[str, Any]:
    return {
        "status": status,
        "source": source,
        "detail": detail,
        "checked_at": checked_at,
    }


def _latest_automation(
    db_path: str,
    account_id: str,
    marketplace: str,
    brand_name: str,
) -> dict[str, Any] | None:
    """Return the durable submit boundary and linked login interruption."""

    conn = get_conn(db_path)
    row = conn.execute(
        """SELECT c.*, b.block_type, b.status AS auth_block_status,
                  b.detected_at, b.last_checked_at, b.next_check_at,
                  b.evidence_path
           FROM submission_checkpoints c
           LEFT JOIN account_auth_blocks b ON b.id=c.auth_block_id
           WHERE c.account_id=? AND UPPER(c.marketplace)=UPPER(?)
             AND c.brand_name=? COLLATE NOCASE
           ORDER BY datetime(c.updated_at) DESC, c.id DESC LIMIT 1""",
        (account_id, marketplace, brand_name),
    ).fetchone()
    if row is None:
        conn.close()
        return None
    data = dict(row)
    timeline: list[dict[str, Any]] = []
    if data.get("owner_type") == "reapplication_attempt":
        attempt = conn.execute(
            """SELECT a.id, a.campaign_id, a.route_index, a.site, a.status,
                      a.scheduled_at, a.started_at, a.submitted_at, a.completed_at,
                      a.case_id, a.final_result, a.decision_reason, a.error
               FROM reapplication_attempts a WHERE a.id=?""",
            (int(data["owner_id"]),),
        ).fetchone()
        if attempt:
            campaign_id = int(attempt["campaign_id"])
            timeline = [
                dict(item)
                for item in conn.execute(
                    """SELECT route_index, site, status, scheduled_at, started_at,
                              submitted_at, completed_at, case_id, final_result,
                              decision_reason, error
                       FROM reapplication_attempts WHERE campaign_id=?
                       ORDER BY route_index""",
                    (campaign_id,),
                ).fetchall()
            ]
            data["campaign_id"] = campaign_id
            data["attempt"] = dict(attempt)
    conn.close()

    status = str(data.get("status") or "")
    phase = str(data.get("phase") or "")
    if status == "active" and data.get("resumed_at"):
        public_status = "resolved"
    else:
        public_status = _AUTOMATION_VISIBLE_STATUSES.get(status, status)
    fenced = bool(data.get("submit_click_fenced_at"))
    if public_status == "waiting_login":
        next_action = "登录恢复后继续当前站点，不推进下一站"
    elif public_status == "waiting_reconciliation":
        next_action = "不会重复提交；核对 Selling Applications/Case"
    elif public_status == "manual_review":
        next_action = "结果存在歧义，需要人工确认"
    elif public_status == "resolved":
        next_action = "登录已恢复，已安排自动处理"
    elif public_status == "completed" and phase == "dashboard_approved":
        next_action = "已通过 Selling Applications 对账确认"
    elif fenced:
        next_action = "等待 Case 或控制面板确认结果"
    else:
        next_action = "继续当前站点表单处理"
    return {
        "checkpoint_id": int(data["id"]),
        "owner_type": data.get("owner_type"),
        "owner_id": data.get("owner_id"),
        "status": public_status,
        "phase": phase,
        "current_site": str(data.get("marketplace") or "").upper(),
        "submit_intent_at": data.get("submit_intent_at"),
        "submit_click_fenced_at": data.get("submit_click_fenced_at"),
        "resumed_at": data.get("resumed_at"),
        "submit_fenced": fenced,
        "auth_block_id": data.get("auth_block_id"),
        "block_type": data.get("block_type"),
        "detected_at": data.get("detected_at"),
        "last_checked_at": data.get("last_checked_at"),
        "next_check_at": data.get("next_check_at"),
        "evidence_path": data.get("evidence_path"),
        "next_action": next_action,
        "campaign_id": data.get("campaign_id"),
        "attempt": data.get("attempt"),
        "timeline": timeline,
        "case_id": data.get("case_id"),
        "updated_at": data.get("updated_at"),
        "detail": data.get("detail"),
    }


def _latest_case_reply(db_path: str, account_id: str, marketplace: str,
                       brand_name: str, *, case_id: str | None = None) -> dict[str, Any] | None:
    conn = get_conn(db_path)
    sql = """SELECT final_result, case_status, decision_reason, updated_at, status
             FROM case_followups
             WHERE account_id=? AND UPPER(marketplace)=UPPER(?) AND brand_name=? COLLATE NOCASE
               AND final_result IS NOT NULL AND final_result != ''"""
    params: list[Any] = [account_id, marketplace, brand_name]
    if case_id:
        sql += " AND case_id=?"
        params.append(str(case_id))
    sql += " ORDER BY datetime(updated_at) DESC, id DESC LIMIT 1"
    row = conn.execute(sql, params).fetchone()
    conn.close()
    if not row:
        return None
    final_result = str(row["final_result"])
    status = _CASE_OUTCOME_RANK.get(final_result, "under_review")
    # A worker/browser failure is authoritative for the local run state, but it
    # is not text returned by Amazon.  Keep it out of the UI's Case-reply field.
    source = "case_followup_error" if final_result in {"error", "blocked"} else "case_reply"
    return _resolution(
        status,
        source,
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
        source = "dashboard_reconciliation"
    elif row["case_id"]:
        status = "under_review"
        source = "case_id_recovery"
    else:
        return None
    return _resolution(
        status,
        source,
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

    automation = _latest_automation(db_path, account_id, marketplace, brand_name)
    if automation and automation["status"] in {
        "waiting_login", "waiting_reconciliation", "manual_review", "resolved"
    }:
        return _resolution(
            str(automation["status"]),
            "automation_checkpoint",
            detail=str(automation.get("next_action") or ""),
            checked_at=str(automation.get("updated_at") or "") or None,
        )

    for probe in (
        lambda: _latest_case_reply(
            db_path,
            account_id,
            marketplace,
            brand_name,
            case_id=str(automation.get("case_id") or "") if automation else None,
        ),
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
    checkpoint_rows = [
        dict(r)
        for r in conn.execute(
            """SELECT account_id, marketplace, brand_name,
                      COALESCE(submit_click_fenced_at, submit_intent_at, created_at) AS submitted_at,
                      case_id, status AS submit_result
               FROM submission_checkpoints
               ORDER BY datetime(updated_at) DESC, id DESC"""
        ).fetchall()
    ]
    conn.close()

    existing_keys = {
        (str(row["account_id"]), str(row["marketplace"]).upper(), str(row["brand_name"]).casefold())
        for row in rows
    }
    for checkpoint_row in checkpoint_rows:
        key = (
            str(checkpoint_row["account_id"]),
            str(checkpoint_row["marketplace"]).upper(),
            str(checkpoint_row["brand_name"]).casefold(),
        )
        if key in existing_keys:
            continue
        if account_id and checkpoint_row["account_id"] != account_id:
            continue
        if marketplace and str(checkpoint_row["marketplace"]).upper() != marketplace.upper():
            continue
        if brand_name and str(checkpoint_row["brand_name"]).casefold() != brand_name.casefold():
            continue
        checkpoint_date = str(checkpoint_row.get("submitted_at") or "")
        if date_from and checkpoint_date < date_from:
            continue
        date_to_bound = date_to + " 23:59:59" if date_to and len(date_to) == 10 else date_to
        if date_to_bound and checkpoint_date > date_to_bound:
            continue
        rows.append(checkpoint_row)
        existing_keys.add(key)
    rows.sort(key=lambda item: str(item.get("submitted_at") or ""), reverse=True)
    rows = rows[: max(1, min(int(limit), 1000))]

    results: list[dict[str, Any]] = []
    automation_attached: set[tuple[str, str, str]] = set()
    automation_cache: dict[tuple[str, str, str], dict[str, Any] | None] = {}
    for row in rows:
        key = (
            str(row["account_id"]),
            str(row["marketplace"]).upper(),
            str(row["brand_name"]).casefold(),
        )
        if key not in automation_cache:
            automation_cache[key] = _latest_automation(
                db_path, row["account_id"], row["marketplace"], row["brand_name"]
            )
        automation = automation_cache[key]
        row_case_id = str(row.get("case_id") or "")
        current_case_id = str((automation or {}).get("case_id") or "")
        if row_case_id and current_case_id and row_case_id != current_case_id:
            # A historical Case row keeps its own outcome.  Never attach the
            # latest brand checkpoint/result to a different Case ID.
            resolution = _latest_case_reply(
                db_path,
                row["account_id"],
                row["marketplace"],
                row["brand_name"],
                case_id=row_case_id,
            )
            if resolution is None:
                resolution = _resolution(
                    _SUBMIT_RESULT_MAP.get(str(row.get("submit_result") or ""), "under_review"),
                    "submission_case_id",
                    checked_at=str(row.get("submitted_at") or "") or None,
                )
        else:
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
        if key not in automation_attached:
            row["automation"] = automation
            automation_attached.add(key)
        else:
            row["automation"] = None
        results.append(row)
    return results
