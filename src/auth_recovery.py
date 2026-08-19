"""Persistent, account-scoped recovery for Seller Central authentication stops.

The service is deliberately unable to enter credentials or solve CAPTCHA/2FA.
It may start/attach the exact AdsPower profile, inspect authentication state,
and resume only work whose durable submit fence makes the next action safe.
"""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .auth_guard import AuthState, detect_auth_state
from .browser_manager import BrowserManager
from .config_loader import load_yaml, resolve_accounts_path
from .db import get_conn, init_db, now_str
from .jobs.profile_locks import profile_key_for_account, profile_key_for_profile_id
from .marketplace_switcher import MARKETPLACE_CONFIG
from .state_files import atomic_write_json, read_json_tolerant

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OPEN_STATUS = "open"


class SubmissionReconciliationRequired(RuntimeError):
    """A durable click fence already exists, so submitting again is forbidden."""

    def __init__(self, checkpoint: Mapping[str, Any]):
        self.checkpoint = dict(checkpoint)
        super().__init__("submit_click_already_fenced")


def _db_path(settings: Mapping[str, Any] | Any) -> str:
    if isinstance(settings, Mapping):
        return str((settings.get("paths") or {}).get("db_path") or "./runtime/state/ledger.db")
    return str(settings.db_path)


def _accounts_path(settings: Mapping[str, Any] | Any) -> Path:
    if not isinstance(settings, Mapping) and getattr(settings, "accounts_path", None):
        return Path(settings.accounts_path)
    if isinstance(settings, Mapping):
        configured_path = (settings.get("paths") or {}).get("accounts_path")
        if configured_path:
            configured = Path(str(configured_path))
            return configured if configured.is_absolute() else PROJECT_ROOT / configured
    configured = os.getenv("AMAZON5461_ACCOUNTS_PATH", "").strip()
    return resolve_accounts_path(configured or PROJECT_ROOT / "config" / "accounts.json")


def _evidence_root(settings: Mapping[str, Any] | Any) -> Path:
    if isinstance(settings, Mapping):
        value = (settings.get("paths") or {}).get("evidence_root") or "./runtime/evidence"
        path = Path(str(value))
    else:
        path = Path(settings.evidence_root)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _poll_seconds(settings: Mapping[str, Any] | Any) -> int:
    if isinstance(settings, Mapping):
        value = (settings.get("auth_recovery") or {}).get("poll_interval_seconds", 300)
    else:
        value = getattr(settings, "auth_recovery_poll_seconds", 300)
    return max(60, int(value or 300))


def _runtime_settings(settings: Mapping[str, Any] | Any) -> dict[str, Any]:
    if isinstance(settings, Mapping):
        return dict(settings)
    runtime = load_yaml(str(PROJECT_ROOT / "config" / "settings.yaml")) or {}
    runtime.setdefault("paths", {})["db_path"] = str(settings.db_path)
    runtime["paths"]["accounts_path"] = str(settings.accounts_path)
    runtime["paths"]["evidence_root"] = str(settings.evidence_root)
    runtime["paths"]["logs_root"] = str(settings.logs_root)
    runtime.setdefault("adspower", {})["api_base_url"] = str(settings.adspower_api_base_url)
    runtime.setdefault("auth_recovery", {})["poll_interval_seconds"] = int(
        getattr(settings, "auth_recovery_poll_seconds", 300)
    )
    return runtime


def _safe_detail(value: Any, limit: int = 500) -> str:
    text = re.sub(
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        "[REDACTED_EMAIL]",
        str(value or ""),
    )
    return text[:limit]


def _plus_seconds(seconds: int) -> str:
    return (datetime.now() + timedelta(seconds=max(1, seconds))).replace(
        microsecond=0
    ).strftime("%Y-%m-%d %H:%M:%S")


def ensure_submission_checkpoint(
    db_path: str,
    *,
    owner_type: str,
    owner_id: str,
    account_id: str,
    marketplace: str,
    brand_name: str,
    phase: str = "started",
) -> dict[str, Any]:
    """Create/reuse the one checkpoint for this managed submission scope."""

    init_db(db_path)
    now = now_str()
    conn = get_conn(db_path)
    conn.execute(
        """INSERT OR IGNORE INTO submission_checkpoints(
               owner_type, owner_id, account_id, marketplace, brand_name,
               phase, status, created_at, updated_at
           ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
        (
            str(owner_type), str(owner_id), str(account_id),
            str(marketplace).upper(), str(brand_name), str(phase), now, now,
        ),
    )
    conn.execute(
        """UPDATE submission_checkpoints SET phase=?, updated_at=?
           WHERE owner_type=? AND owner_id=? AND marketplace=? AND brand_name=?""",
        (
            str(phase), now, str(owner_type), str(owner_id),
            str(marketplace).upper(), str(brand_name),
        ),
    )
    row = conn.execute(
        """SELECT * FROM submission_checkpoints
           WHERE owner_type=? AND owner_id=? AND marketplace=? AND brand_name=?""",
        (str(owner_type), str(owner_id), str(marketplace).upper(), str(brand_name)),
    ).fetchone()
    conn.commit()
    conn.close()
    return dict(row)


def mark_submit_intent(db_path: str, checkpoint_id: int, *, phase: str) -> dict[str, Any]:
    now = now_str()
    conn = get_conn(db_path)
    conn.execute(
        """UPDATE submission_checkpoints
           SET phase=?, submit_intent_at=COALESCE(submit_intent_at, ?),
               status='active', updated_at=? WHERE id=?""",
        (str(phase), now, now, int(checkpoint_id)),
    )
    row = conn.execute("SELECT * FROM submission_checkpoints WHERE id=?", (int(checkpoint_id),)).fetchone()
    conn.commit()
    conn.close()
    return dict(row)


def mark_submit_click_fenced(db_path: str, checkpoint_id: int, *, phase: str) -> dict[str, Any]:
    """Write the no-reclick fence before dispatching the browser click."""

    now = now_str()
    conn = get_conn(db_path)
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        """UPDATE submission_checkpoints
           SET phase=?, submit_intent_at=COALESCE(submit_intent_at, ?),
               submit_click_fenced_at=COALESCE(submit_click_fenced_at, ?),
               status='submitted_unknown', updated_at=? WHERE id=?""",
        (str(phase), now, now, now, int(checkpoint_id)),
    )
    row = conn.execute("SELECT * FROM submission_checkpoints WHERE id=?", (int(checkpoint_id),)).fetchone()
    conn.commit()
    conn.close()
    return dict(row)


def update_checkpoint_result(
    db_path: str,
    checkpoint_id: int,
    *,
    status: str,
    phase: str,
    case_id: str = "",
    detail: str = "",
) -> None:
    conn = get_conn(db_path)
    conn.execute(
        """UPDATE submission_checkpoints SET status=?, phase=?, case_id=?,
               detail=?, updated_at=? WHERE id=?""",
        (str(status), str(phase), str(case_id or ""), _safe_detail(detail), now_str(), int(checkpoint_id)),
    )
    conn.commit()
    conn.close()


def capture_auth_evidence(page: Any, settings: Mapping[str, Any] | Any, *, account_id: str, phase: str) -> str:
    """Capture one screenshot only; page text may contain private account data."""

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = _evidence_root(settings) / "auth_blocks" / datetime.now().strftime("%Y-%m-%d") / str(account_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{stamp}_{re.sub(r'[^a-zA-Z0-9_-]+', '_', phase)[:50]}.png"
    try:
        page.screenshot(path=str(path), full_page=True, timeout=20_000)
        return str(path)
    except Exception:
        return ""


def _link_source(conn, block_id: int, source_type: str, source_id: str | None, *, submit_fenced: bool) -> None:
    if not source_id:
        return
    now = now_str()
    if source_type == "automation_job":
        conn.execute(
            """UPDATE automation_jobs SET run_status='waiting_human', auth_block_id=?,
                   error_class=?, updated_at=? WHERE id=?""",
            (block_id, "waiting_reconciliation" if submit_fenced else "waiting_login", now, str(source_id)),
        )
    elif source_type == "reapplication_attempt":
        attempt_status = "waiting_reconciliation" if submit_fenced else "waiting_login"
        row = conn.execute(
            "SELECT campaign_id FROM reapplication_attempts WHERE id=?", (int(source_id),)
        ).fetchone()
        conn.execute(
            """UPDATE reapplication_attempts SET status=?, auth_block_id=?, error=?,
                   completed_at=NULL, updated_at=? WHERE id=?""",
            (attempt_status, block_id, attempt_status, now, int(source_id)),
        )
        if row:
            conn.execute(
                """UPDATE reapplication_campaigns SET status='blocked', stop_reason=?,
                       updated_at=? WHERE id=?""",
                (attempt_status, now, int(row["campaign_id"])),
            )
    elif source_type == "case_followup":
        conn.execute(
            "UPDATE case_followups SET status='blocked', auth_block_id=?, updated_at=? WHERE id=?",
            (block_id, now, int(source_id)),
        )
    elif source_type == "case_id_recovery":
        conn.execute(
            "UPDATE case_id_recoveries SET status='blocked', auth_block_id=?, updated_at=? WHERE id=?",
            (block_id, now, int(source_id)),
        )


def _sync_profile_bindings(settings: Mapping[str, Any] | Any, conn) -> None:
    """Persist only account ids and irreversible profile hashes.

    This makes the scheduler stop every account alias sharing the blocked
    AdsPower profile without copying raw private profile ids into SQLite.
    """

    payload = read_json_tolerant(_accounts_path(settings))
    rows = payload.get("accounts") if isinstance(payload, dict) else []
    now = now_str()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        account_id = str(row.get("account_id") or "").strip()
        profile_id = str(row.get("adspower_profile_id") or "").strip()
        if not account_id or not profile_id:
            continue
        conn.execute(
            """INSERT INTO account_profile_bindings(account_id, profile_key, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(account_id) DO UPDATE SET
                 profile_key=excluded.profile_key, updated_at=excluded.updated_at""",
            (account_id, profile_key_for_profile_id(profile_id), now),
        )


def create_auth_block(
    settings: Mapping[str, Any] | Any,
    *,
    account_id: str,
    marketplace: str,
    brand_name: str,
    block_type: str,
    phase: str,
    source_type: str,
    source_id: str | int | None,
    submit_fenced: bool = False,
    evidence_path: str = "",
    detail: str = "",
    checkpoint_id: int | None = None,
) -> dict[str, Any]:
    """Create one open block per profile and attach all affected work to it."""

    db_path = _db_path(settings)
    init_db(db_path)
    profile_key = profile_key_for_account(_accounts_path(settings), str(account_id))
    if not profile_key:
        raise ValueError("account_has_no_adspower_profile")
    now = now_str()
    next_check = _plus_seconds(_poll_seconds(settings))
    conn = get_conn(db_path)
    conn.execute("BEGIN IMMEDIATE")
    _sync_profile_bindings(settings, conn)
    row = conn.execute(
        "SELECT * FROM account_auth_blocks WHERE profile_key=? AND status='open'",
        (profile_key,),
    ).fetchone()
    if row:
        block_id = int(row["id"])
        conn.execute(
            """UPDATE account_auth_blocks SET block_type=?, phase=?,
                   marketplace=COALESCE(NULLIF(?, ''), marketplace),
                   brand_name=COALESCE(NULLIF(?, ''), brand_name),
                   submit_fenced=MAX(submit_fenced, ?),
                   evidence_path=COALESCE(NULLIF(?, ''), evidence_path),
                   detail=?, next_check_at=?, updated_at=? WHERE id=?""",
            (
                str(block_type), str(phase), str(marketplace).upper(), str(brand_name),
                1 if submit_fenced else 0, str(evidence_path), _safe_detail(detail),
                next_check, now, block_id,
            ),
        )
    else:
        cur = conn.execute(
            """INSERT INTO account_auth_blocks(
                   profile_key, account_id, marketplace, brand_name, block_type,
                   phase, source_type, source_id, status, submit_fenced,
                   evidence_path, detail, detected_at, next_check_at,
                   created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?)""",
            (
                profile_key, str(account_id), str(marketplace).upper(), str(brand_name),
                str(block_type), str(phase), str(source_type),
                str(source_id) if source_id is not None else None,
                1 if submit_fenced else 0, str(evidence_path), _safe_detail(detail),
                now, next_check, now, now,
            ),
        )
        block_id = int(cur.lastrowid)
    if checkpoint_id is not None:
        conn.execute(
            """UPDATE submission_checkpoints SET auth_block_id=?, status=?, phase=?,
                   detail=?, updated_at=? WHERE id=?""",
            (
                block_id,
                "waiting_reconciliation" if submit_fenced else "waiting_login",
                str(phase), _safe_detail(detail), now, int(checkpoint_id),
            ),
        )
    _link_source(
        conn, block_id, str(source_type),
        str(source_id) if source_id is not None else None,
        submit_fenced=bool(submit_fenced),
    )
    result = conn.execute("SELECT * FROM account_auth_blocks WHERE id=?", (block_id,)).fetchone()
    conn.commit()
    conn.close()
    return dict(result)


def account_has_open_auth_block(db_path: str, account_id: str) -> bool:
    init_db(db_path)
    conn = get_conn(db_path)
    row = conn.execute(
        """SELECT 1 FROM account_auth_blocks b
           LEFT JOIN account_profile_bindings p ON p.profile_key=b.profile_key
           WHERE b.status='open' AND (b.account_id=? OR p.account_id=?)
           LIMIT 1""",
        (str(account_id), str(account_id)),
    ).fetchone()
    conn.close()
    return bool(row)


def list_auth_blocks(settings: Mapping[str, Any] | Any, *, status: str | None = "open") -> list[dict[str, Any]]:
    db_path = _db_path(settings)
    init_db(db_path)
    conn = get_conn(db_path)
    sql = "SELECT * FROM account_auth_blocks"
    params: list[Any] = []
    if status:
        sql += " WHERE status=?"
        params.append(str(status))
    sql += " ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END, datetime(detected_at) DESC, id DESC"
    rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


def get_auth_block(settings: Mapping[str, Any] | Any, block_id: int) -> dict[str, Any] | None:
    db_path = _db_path(settings)
    init_db(db_path)
    conn = get_conn(db_path)
    row = conn.execute("SELECT * FROM account_auth_blocks WHERE id=?", (int(block_id),)).fetchone()
    conn.close()
    return dict(row) if row else None


def _mark_checked(settings: Mapping[str, Any] | Any, block_id: int, state: AuthState) -> None:
    now = now_str()
    conn = get_conn(_db_path(settings))
    conn.execute(
        """UPDATE account_auth_blocks SET block_type=?, last_checked_at=?,
               next_check_at=?, detail=?, updated_at=? WHERE id=? AND status='open'""",
        (
            state.state, now, _plus_seconds(_poll_seconds(settings)),
            _safe_detail(state.reason), now, int(block_id),
        ),
    )
    conn.commit()
    conn.close()


def open_auth_profile(settings: Mapping[str, Any] | Any, block_id: int) -> dict[str, Any]:
    block = get_auth_block(settings, block_id)
    if not block:
        return {"status": "not_found"}
    manager = _browser_manager(settings)
    try:
        _connect_exact_profile(manager, settings, str(block["account_id"]))
        return {"status": "opened", "block_id": int(block_id)}
    finally:
        manager.disconnect(quiet=True)


def _browser_manager(settings: Mapping[str, Any] | Any) -> BrowserManager:
    if isinstance(settings, Mapping):
        adspower = settings.get("adspower") or {}
        browser = settings.get("browser") or {}
        return BrowserManager(
            api_base_url=str(adspower.get("api_base_url") or "http://127.0.0.1:50325"),
            cdp_connect_timeout_ms=int(browser.get("cdp_connect_timeout_ms") or 30_000),
            cdp_health_timeout_sec=float(browser.get("cdp_health_timeout_sec") or 5.0),
            cdp_ready_timeout_sec=float(browser.get("cdp_ready_timeout_sec") or 30.0),
            cdp_restart_attempts=int(browser.get("cdp_restart_attempts", 1)),
            profile_restart_wait_sec=float(browser.get("profile_restart_wait_sec") or 5.0),
        )
    return BrowserManager(api_base_url=str(settings.adspower_api_base_url))


def _connect_exact_profile(
    manager: BrowserManager,
    settings: Mapping[str, Any] | Any,
    account_id: str,
) -> None:
    profile_id = manager.get_profile_id(str(account_id), config_path=_accounts_path(settings))
    manager.connect(profile_id)


def _sellercentral_home(site: str) -> str:
    info = MARKETPLACE_CONFIG.get(str(site or "US").upper()) or MARKETPLACE_CONFIG["US"]
    return f"https://sellercentral.{info.domain}/home"


def _resume_auth_block(settings: Mapping[str, Any] | Any, block_id: int) -> dict[str, Any]:
    """Resolve one block and requeue only operations safe under their fence."""

    db_path = _db_path(settings)
    now = now_str()
    conn = get_conn(db_path)
    conn.execute("BEGIN IMMEDIATE")
    block = conn.execute(
        "SELECT * FROM account_auth_blocks WHERE id=?", (int(block_id),)
    ).fetchone()
    if not block:
        conn.rollback()
        conn.close()
        return {"status": "not_found"}
    if block["status"] != "open":
        conn.commit()
        conn.close()
        return {"status": "already_resolved", "block_id": int(block_id)}

    checkpoints = [
        dict(row) for row in conn.execute(
            "SELECT * FROM submission_checkpoints WHERE auth_block_id=?", (int(block_id),)
        ).fetchall()
    ]
    actions: list[str] = []
    for checkpoint in checkpoints:
        fenced = bool(checkpoint.get("submit_click_fenced_at"))
        owner_type = str(checkpoint["owner_type"])
        owner_id = str(checkpoint["owner_id"])
        if fenced:
            conn.execute(
                """UPDATE submission_checkpoints SET status='waiting_reconciliation',
                       phase='reconciliation', updated_at=? WHERE id=?""",
                (now, int(checkpoint["id"])),
            )
            if owner_type == "automation_job":
                conn.execute(
                    """UPDATE automation_jobs SET run_status='waiting_human',
                           error_class='waiting_reconciliation', auth_block_id=NULL,
                           updated_at=? WHERE id=?""",
                    (now, owner_id),
                )
            elif owner_type == "reapplication_attempt":
                row = conn.execute(
                    "SELECT campaign_id FROM reapplication_attempts WHERE id=?", (int(owner_id),)
                ).fetchone()
                conn.execute(
                    """UPDATE reapplication_attempts SET status='waiting_case_id',
                           auth_block_id=NULL, error=NULL, updated_at=? WHERE id=?""",
                    (now, int(owner_id)),
                )
                if row:
                    conn.execute(
                        """UPDATE reapplication_campaigns SET status='waiting_case_id',
                               stop_reason=NULL, updated_at=? WHERE id=?""",
                        (now, int(row["campaign_id"])),
                    )
            actions.append("reconciliation")
        else:
            conn.execute(
                """UPDATE submission_checkpoints SET status='active', phase='resume_queued',
                       auth_block_id=NULL, resumed_at=?, updated_at=? WHERE id=?""",
                (now, now, int(checkpoint["id"])),
            )
            if owner_type == "automation_job":
                conn.execute(
                    """UPDATE automation_jobs SET run_status='queued', pid=NULL,
                           exit_code=NULL, error_class=NULL, auth_block_id=NULL,
                           finished_at=NULL, stop_requested_at=NULL, updated_at=? WHERE id=?""",
                    (now, owner_id),
                )
            elif owner_type == "reapplication_attempt":
                row = conn.execute(
                    "SELECT campaign_id FROM reapplication_attempts WHERE id=?", (int(owner_id),)
                ).fetchone()
                conn.execute(
                    """UPDATE reapplication_attempts SET status='scheduled', scheduled_at=?,
                           pid=NULL, auth_block_id=NULL, error=NULL, completed_at=NULL,
                           updated_at=? WHERE id=?""",
                    (now, now, int(owner_id)),
                )
                if row:
                    conn.execute(
                        """UPDATE reapplication_campaigns SET status='next_scheduled',
                               stop_reason=NULL, updated_at=? WHERE id=?""",
                        (now, int(row["campaign_id"])),
                    )
            actions.append("submission_resume")

    followups = conn.execute(
        "SELECT id FROM case_followups WHERE auth_block_id=? AND status='blocked'", (int(block_id),)
    ).fetchall()
    for row in followups:
        conn.execute(
            """UPDATE case_followups SET status='pending', scheduled_at=?,
                   auth_block_id=NULL, claimed_pid=NULL, error=NULL,
                   completed_at=NULL, final_result=NULL, decision_reason=NULL,
                   updated_at=? WHERE id=?""",
            (now, now, int(row["id"])),
        )
        actions.append("case_followup")

    recoveries = conn.execute(
        """SELECT id, reapplication_campaign_id, reapplication_attempt_id
           FROM case_id_recoveries WHERE auth_block_id=? AND status='blocked'""",
        (int(block_id),)
    ).fetchall()
    for row in recoveries:
        conn.execute(
            """UPDATE case_id_recoveries SET status='retry', scheduled_at=?,
                   auth_block_id=NULL, error=NULL, completed_at=NULL,
                   decision_reason=NULL, updated_at=? WHERE id=?""",
            (now, now, int(row["id"])),
        )
        if row["reapplication_attempt_id"] is not None:
            conn.execute(
                """UPDATE reapplication_attempts SET status='waiting_case_id',
                       auth_block_id=NULL, error=NULL, updated_at=? WHERE id=?""",
                (now, int(row["reapplication_attempt_id"])),
            )
        if row["reapplication_campaign_id"] is not None:
            conn.execute(
                """UPDATE reapplication_campaigns SET status='waiting_case_id',
                       stop_reason=NULL, updated_at=? WHERE id=?""",
                (now, int(row["reapplication_campaign_id"])),
            )
        actions.append("case_id_recovery")

    conn.execute(
        """UPDATE account_auth_blocks SET status='resolved', resolved_at=?,
               last_checked_at=?, next_check_at=NULL, updated_at=? WHERE id=?""",
        (now, now, now, int(block_id)),
    )
    conn.commit()
    conn.close()

    runtime_settings = _runtime_settings(settings)
    for checkpoint in checkpoints:
        if not checkpoint.get("submit_click_fenced_at"):
            continue
        try:
            from .case_id_recovery import schedule_case_id_recovery

            reapplication_attempt_id = (
                int(checkpoint["owner_id"])
                if checkpoint["owner_type"] == "reapplication_attempt"
                else None
            )
            reapplication_campaign_id = None
            if reapplication_attempt_id is not None:
                conn = get_conn(db_path)
                attempt = conn.execute(
                    "SELECT campaign_id FROM reapplication_attempts WHERE id=?",
                    (reapplication_attempt_id,),
                ).fetchone()
                conn.close()
                reapplication_campaign_id = int(attempt["campaign_id"]) if attempt else None
            schedule_case_id_recovery(
                runtime_settings,
                account_id=str(checkpoint["account_id"]),
                site=str(checkpoint["marketplace"]),
                brand_name=str(checkpoint["brand_name"]),
                sku="",
                submitted_at=str(checkpoint["submit_click_fenced_at"]),
                reapplication_campaign_id=reapplication_campaign_id,
                reapplication_attempt_id=reapplication_attempt_id,
            )
        except Exception:
            pass

    # Launchers are idempotent; failures leave durable due rows for the next
    # web start / five-minute health check.
    if "submission_resume" in actions:
        try:
            from .reapplication import launch_reapplication_worker

            launch_reapplication_worker(runtime_settings)
        except Exception:
            pass
    if "case_followup" in actions:
        try:
            from .case_followup import launch_case_followup_worker

            launch_case_followup_worker(runtime_settings)
        except Exception:
            pass
    if "case_id_recovery" in actions or "reconciliation" in actions:
        try:
            from .case_id_recovery import launch_case_id_recovery_worker

            launch_case_id_recovery_worker(runtime_settings)
        except Exception:
            pass
    return {"status": "resolved", "block_id": int(block_id), "actions": actions}


def finish_submission_reconciliation(
    settings: Mapping[str, Any] | Any,
    *,
    account_id: str,
    marketplace: str,
    brand_name: str,
    status: str,
    case_id: str = "",
    detail: str = "",
) -> None:
    """Reflect Case-ID recovery and safely resume untouched job items.

    A recovered Case ID closes only the matching brand.  The per-job state
    file is updated before the job is requeued, so a resumed worker skips the
    fenced brand and can never click Submit for it again.  Ambiguous/manual
    outcomes remain paused and are not automatically resumed.
    """

    db_path = _db_path(settings)
    now = now_str()
    conn = get_conn(db_path)
    conn.execute("BEGIN IMMEDIATE")
    checkpoints = conn.execute(
        """SELECT * FROM submission_checkpoints
           WHERE account_id=? AND UPPER(marketplace)=UPPER(?)
             AND brand_name=? COLLATE NOCASE
             AND status='waiting_reconciliation'
           ORDER BY datetime(submit_click_fenced_at) DESC, id DESC""",
        (str(account_id), str(marketplace), str(brand_name)),
    ).fetchall()
    for checkpoint in checkpoints:
        checkpoint_status = "waiting_case" if case_id else str(status)
        conn.execute(
            """UPDATE submission_checkpoints SET status=?, phase=?, case_id=?,
                   detail=?, updated_at=? WHERE id=?""",
            (
                checkpoint_status,
                "case_followup" if case_id else "manual_review",
                str(case_id or ""), _safe_detail(detail), now, int(checkpoint["id"]),
            ),
        )
        if checkpoint["owner_type"] == "automation_job":
            job_id = str(checkpoint["owner_id"])
            business_status = "under_review" if case_id else "manual_review"
            conn.execute(
                """UPDATE automation_job_items SET run_status=?, business_status=?,
                       case_id=COALESCE(NULLIF(?, ''), case_id),
                       finished_at=?, note=?
                   WHERE job_id=? AND UPPER(COALESCE(marketplace, ''))=UPPER(?)
                     AND brand_name=? COLLATE NOCASE""",
                (
                    "completed" if case_id else "waiting_human",
                    business_status,
                    str(case_id or ""),
                    now if case_id else None,
                    _safe_detail(detail),
                    job_id,
                    str(marketplace),
                    str(brand_name),
                ),
            )
            if not case_id:
                conn.execute(
                    """UPDATE automation_jobs SET run_status='waiting_human',
                           error_class='reconciliation_manual_review',
                           finished_at=NULL, updated_at=? WHERE id=?""",
                    (now, job_id),
                )
                continue

            job = conn.execute(
                "SELECT state_file FROM automation_jobs WHERE id=?", (job_id,)
            ).fetchone()
            state_path = Path(str(job["state_file"] or "")) if job else Path()
            if state_path and not state_path.is_absolute():
                state_path = PROJECT_ROOT / state_path
            state = read_json_tolerant(state_path, retries=1) if state_path.is_file() else None
            target_found = False
            has_pending = False
            has_waiting_human = False
            if isinstance(state, dict):
                for batch in state.get("batches") or []:
                    for item in (batch or {}).get("items") or []:
                        same_target = (
                            str(item.get("account_id") or "") == str(account_id)
                            and str(item.get("site") or item.get("marketplace") or "").upper()
                            == str(marketplace).upper()
                            and str(item.get("brand_name") or "").casefold()
                            == str(brand_name).casefold()
                        )
                        if same_target:
                            target_found = True
                            result = item.get("result") if isinstance(item.get("result"), dict) else {}
                            result.update({
                                "status": "under_review",
                                "case_id": str(case_id),
                                "note": _safe_detail(detail),
                            })
                            item["result"] = result
                            item["status"] = "completed"
                            item["error"] = None
                            item["completed_at"] = now

                all_items = [
                    item
                    for batch in state.get("batches") or []
                    for item in (batch or {}).get("items") or []
                    if isinstance(item, dict)
                ]
                has_pending = any(str(item.get("status") or "") == "pending" for item in all_items)
                has_waiting_human = any(
                    str(item.get("status") or "") == "waiting_human" for item in all_items
                )
                state["summary"] = {
                    "total": len(all_items),
                    "completed": sum(str(item.get("status") or "") == "completed" for item in all_items),
                    "failed": sum(str(item.get("status") or "") == "failed" for item in all_items),
                    "pending": sum(
                        str(item.get("status") or "") not in {"completed", "failed", "skipped"}
                        for item in all_items
                    ),
                }
                for batch in state.get("batches") or []:
                    batch_items = [item for item in (batch or {}).get("items") or [] if isinstance(item, dict)]
                    if any(str(item.get("status") or "") == "waiting_human" for item in batch_items):
                        batch["status"] = "waiting_human"
                        batch["completed_at"] = None
                    elif any(str(item.get("status") or "") == "pending" for item in batch_items):
                        batch["status"] = "pending"
                        batch["completed_at"] = None
                    else:
                        batch["status"] = "completed"
                        batch["completed_at"] = batch.get("completed_at") or now
                if target_found:
                    atomic_write_json(state_path, state)

            if target_found and has_pending and not has_waiting_human:
                conn.execute(
                    """UPDATE automation_jobs SET run_status='queued', pid=NULL,
                           exit_code=NULL, error_class=NULL, finished_at=NULL,
                           stop_requested_at=NULL, updated_at=? WHERE id=?""",
                    (now, job_id),
                )
            elif target_found and not has_pending and not has_waiting_human:
                conn.execute(
                    """UPDATE automation_jobs SET run_status='completed', pid=NULL,
                           exit_code=0, error_class=NULL, finished_at=?, updated_at=?
                       WHERE id=?""",
                    (now, now, job_id),
                )
            else:
                # Fail closed if the durable state could not be reconciled or
                # another human pause still exists. Never blindly rerun.
                conn.execute(
                    """UPDATE automation_jobs SET run_status='waiting_human',
                           error_class=?, finished_at=NULL, updated_at=? WHERE id=?""",
                    (
                        "waiting_reconciliation" if has_waiting_human else "reconciliation_state_missing",
                        now,
                        job_id,
                    ),
                )
    conn.commit()
    conn.close()


def verify_auth_block(
    settings: Mapping[str, Any] | Any,
    block_id: int,
    *,
    passive: bool = False,
) -> dict[str, Any]:
    """Live-check the exact profile and resume only after deterministic auth."""

    block = get_auth_block(settings, block_id)
    if not block:
        return {"status": "not_found"}
    if block["status"] != "open":
        return {"status": "already_resolved", "block_id": int(block_id)}
    manager = _browser_manager(settings)
    states: list[AuthState] = []
    probe_page = None
    try:
        _connect_exact_profile(manager, settings, str(block["account_id"]))
        # Existing tabs are useful evidence of the current blocker, but an old
        # Seller Central DOM is not proof that the session is still valid.
        # Always verify through a fresh read-only navigation in the same
        # profile before resuming work.
        for page in manager.pages:
            states.append(detect_auth_state(page))
        probe_page = manager.new_tab()
        probe_completed = False
        try:
            probe_page.goto(
                _sellercentral_home(str(block.get("marketplace") or "US")),
                wait_until="domcontentloaded",
                timeout=45_000,
            )
            probe_completed = True
        except Exception:
            pass
        state = detect_auth_state(probe_page)
        states.append(state)
        if state.state == "authenticated" and probe_completed:
            try:
                probe_page.close()
            except Exception:
                pass
            probe_page = None
            return _resume_auth_block(settings, block_id)
        if passive:
            # The original challenge tab stays open.  Close only the periodic
            # probe so five-minute checks do not accumulate browser tabs.
            try:
                probe_page.close()
            except Exception:
                pass
            probe_page = None
        blocked_state = next((state for state in reversed(states) if state.blocked), None)
        if blocked_state is None:
            blocked_state = AuthState(
                "unknown_auth_state", True,
                "No authenticated Seller Central page is currently visible", "", False,
            )
        _mark_checked(settings, block_id, blocked_state)
        return {
            "status": "still_blocked",
            "block_id": int(block_id),
            "block_type": blocked_state.state,
            "reason": blocked_state.reason,
        }
    finally:
        manager.disconnect(quiet=True)


def process_due_auth_blocks(
    settings: Mapping[str, Any] | Any,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    now = now_str()
    rows = [
        row for row in list_auth_blocks(settings, status="open")
        if not row.get("next_check_at") or str(row["next_check_at"]) <= now
    ][: max(1, int(limit))]
    results = []
    for row in rows:
        try:
            results.append(verify_auth_block(settings, int(row["id"]), passive=True))
        except Exception as exc:
            state = AuthState(
                "unknown_auth_state", True,
                f"Authentication check unavailable: {type(exc).__name__}", "", False,
            )
            _mark_checked(settings, int(row["id"]), state)
            results.append({"status": "check_failed", "block_id": int(row["id"])})
    return results


class AuthRecoveryRunner:
    """Five-minute passive verifier used by the web console lifespan."""

    def __init__(self, settings: Any, *, poll_seconds: int = 300):
        self.settings = settings
        self.poll_seconds = max(60, int(poll_seconds))
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await asyncio.to_thread(process_due_auth_blocks, self.settings)
            except Exception:
                pass
            await asyncio.sleep(self.poll_seconds)
