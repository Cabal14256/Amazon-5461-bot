"""Recover missing 5461 Case IDs from View Selling Applications.

The recovery is read-only on Seller Central. It accepts a Case ID only from an
exact-brand dashboard row with one unique 10-12 digit identifier, then hands
the task back to the normal delayed Case-detail follow-up pipeline.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .browser_manager import BrowserManager
from .case_dashboard_checker import check_case_dashboard_for_brand
from .config_loader import load_yaml, resolve_accounts_path
from .db import (
    acquire_profile_lock,
    get_conn,
    init_db,
    now_str,
    release_profile_lock,
)
from .jobs.profile_locks import profile_key_for_account, profile_key_for_profile_id
from .marketplace_switcher import switch_marketplace

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _pid_running(pid: int | None) -> bool:
    if not pid or int(pid) <= 0:
        return False
    try:
        os.kill(int(pid), 0)
    except (OSError, SystemError):
        return False
    return True


def _db_path(settings: Mapping[str, Any]) -> str:
    return str((settings.get("paths") or {}).get("db_path") or "./runtime/state/ledger.db")


def _db_datetime(value: datetime) -> str:
    return value.replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")


def _parse_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.now()
    normalized = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        parsed = datetime.strptime(normalized, "%Y-%m-%d %H:%M:%S")
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def _safe_reason(value: Any, limit: int = 1000) -> str:
    text = re.sub(
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        "[REDACTED_EMAIL]",
        str(value or ""),
    )
    return text[:limit]


def get_case_id_recovery_config(settings: Mapping[str, Any] | None = None) -> dict[str, Any]:
    settings = settings or load_yaml(str(PROJECT_ROOT / "config" / "settings.yaml"))
    configured = dict(settings.get("case_id_recovery") or {})
    return {
        "enabled": bool(configured.get("enabled", True)),
        "initial_delay_minutes": max(0.0, float(configured.get("initial_delay_minutes", 10.0))),
        "retry_interval_minutes": max(1.0, float(configured.get("retry_interval_minutes", 30.0))),
        "max_attempts": max(1, int(configured.get("max_attempts", 12))),
        "poll_interval_seconds": max(5, min(60, int(configured.get("poll_interval_seconds", 60)))),
        "auto_start_worker": bool(configured.get("auto_start_worker", True)),
    }


def is_case_id_recovery_candidate(result: Mapping[str, Any]) -> bool:
    """Return True only when a submission may exist but has no Case ID."""

    dashboard = result.get("dashboard_check") if isinstance(result.get("dashboard_check"), dict) else {}
    if result.get("case_id") or dashboard.get("case_id"):
        return False
    status = str(result.get("status") or result.get("submit_result") or "").strip().lower()
    dashboard_status = str(dashboard.get("status") or "").strip().lower()
    if dashboard_status == "draft":
        return False
    if status in {
        "success",
        "partial",
        "uncertain",
        "under_review",
        "submitted_no_case_id_pending_dashboard",
        "waiting_reconciliation",
        "unknown",
    }:
        return True
    if dashboard_status in {
        "under_review",
        "approved",
        "declined",
        "navigation_failed",
        "unknown",
    }:
        note = str(result.get("note") or "").casefold()
        return status != "failed" or any(
            marker in note
            for marker in ("submitted", "已提交", "未获得 case id", "未提取到 case id")
        )
    note = str(result.get("note") or "").casefold()
    if any(
        marker in note
        for marker in (
            "已提交",
            "submitted",
            "未获得 case id",
            "未提取到 case id",
            "no case id",
            "without case id",
        )
    ):
        return True
    return False


def schedule_case_id_recovery(
    settings: Mapping[str, Any],
    *,
    account_id: str,
    site: str,
    brand_name: str,
    sku: str,
    submitted_at: str | None = None,
    feishu_country_option: str = "",
    submission_title: str = "",
    submission_content: str = "",
    uk_sku: str = "",
    uk_title: str = "",
    uk_content: str = "",
    us_sku: str = "",
    us_title: str = "",
    us_content: str = "",
    reapplication_campaign_id: int | None = None,
    reapplication_attempt_id: int | None = None,
) -> dict[str, Any]:
    """Persist one idempotent missing-Case lookup and prepare Feishu rows."""

    config = get_case_id_recovery_config(settings)
    if not config["enabled"]:
        return {"status": "disabled"}
    if (reapplication_campaign_id is None) != (reapplication_attempt_id is None):
        raise ValueError("Both reapplication campaign and attempt IDs are required")
    submitted_dt = _parse_datetime(submitted_at)
    submitted_db = _db_datetime(submitted_dt)
    scheduled_db = _db_datetime(
        datetime.now() + timedelta(minutes=float(config["initial_delay_minutes"]))
    )
    db_path = _db_path(settings)
    init_db(db_path)
    now = now_str()
    conn = get_conn(db_path)
    existing = conn.execute(
        """SELECT * FROM case_id_recoveries
           WHERE account_id=? AND marketplace=? AND brand_name=? AND submitted_at=?""",
        (account_id, site.upper(), brand_name, submitted_db),
    ).fetchone()
    if existing:
        recovery_id = int(existing["id"])
        created = False
    else:
        cur = conn.execute(
            """INSERT INTO case_id_recoveries(
                   account_id, marketplace, brand_name, sku, submitted_at,
                   scheduled_at, status, feishu_country_option,
                   reapplication_campaign_id, reapplication_attempt_id,
                   created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)""",
            (
                account_id,
                site.upper(),
                brand_name,
                sku,
                submitted_db,
                scheduled_db,
                feishu_country_option,
                reapplication_campaign_id,
                reapplication_attempt_id,
                now,
                now,
            ),
        )
        recovery_id = int(cur.lastrowid)
        created = True
    conn.commit()
    conn.close()

    try:
        from .feishu_bitable import ensure_submission_records

        feishu_ensure = ensure_submission_records(
            settings,
            account_id=account_id,
            site=site.upper(),
            brand_name=brand_name,
            sku=sku,
            title=submission_title,
            content=submission_content,
            country_option=feishu_country_option,
            uk_sku=uk_sku,
            uk_title=uk_title,
            uk_content=uk_content,
            us_sku=us_sku,
            us_title=us_title,
            us_content=us_content,
        )
    except Exception as exc:
        feishu_ensure = {
            "status": "error",
            "reason": f"unexpected record ensure error: {exc.__class__.__name__}",
        }

    if reapplication_campaign_id is not None and reapplication_attempt_id is not None:
        from .reapplication import attach_case_id_recovery

        reapplication_link = attach_case_id_recovery(
            settings,
            int(reapplication_campaign_id),
            int(reapplication_attempt_id),
            recovery_id,
            site.upper(),
        )
    else:
        reapplication_link = {"status": "not_linked"}
    return {
        "id": recovery_id,
        "created": created,
        "status": "pending",
        "scheduled_at": scheduled_db if created else str(existing["scheduled_at"]),
        "feishu_record_ensure": feishu_ensure,
        "reapplication_link": reapplication_link,
    }


def claim_due_case_id_recoveries(
    settings: Mapping[str, Any],
    *,
    limit: int = 1,
    due_at: str | None = None,
) -> list[dict[str, Any]]:
    db_path = _db_path(settings)
    init_db(db_path)
    due = due_at or now_str()
    conn = get_conn(db_path)
    conn.execute("BEGIN IMMEDIATE")
    rows = conn.execute(
        """SELECT r.* FROM case_id_recoveries r
           WHERE r.status IN ('pending', 'retry') AND r.scheduled_at <= ?
             AND NOT EXISTS (
               SELECT 1 FROM account_auth_blocks b
               LEFT JOIN account_profile_bindings p ON p.profile_key=b.profile_key
               WHERE b.status='open'
                 AND (b.account_id=r.account_id OR p.account_id=r.account_id)
             )
           ORDER BY scheduled_at, id LIMIT ?""",
        (due, max(1, int(limit))),
    ).fetchall()
    claimed = []
    for row in rows:
        conn.execute(
            """UPDATE case_id_recoveries
               SET status='running', attempt_count=attempt_count+1,
                   last_checked_at=?, error=NULL, updated_at=?
               WHERE id=? AND status IN ('pending', 'retry')""",
            (due, due, int(row["id"])),
        )
        payload = dict(row)
        payload["status"] = "running"
        payload["attempt_count"] = int(row["attempt_count"]) + 1
        payload["last_checked_at"] = due
        claimed.append(payload)
    conn.commit()
    conn.close()
    return claimed


def requeue_stale_case_id_recoveries(
    settings: Mapping[str, Any],
    stale_before: str,
) -> int:
    conn = get_conn(_db_path(settings))
    now = now_str()
    cur = conn.execute(
        """UPDATE case_id_recoveries
           SET status='retry', scheduled_at=?, error='stale_running_requeued', updated_at=?
           WHERE status='running' AND last_checked_at IS NOT NULL AND last_checked_at < ?""",
        (now, now, stale_before),
    )
    count = cur.rowcount
    conn.commit()
    conn.close()
    return count


def _finish_recovery(
    settings: Mapping[str, Any],
    recovery_id: int,
    status: str,
    *,
    case_id: str = "",
    dashboard_status: str = "",
    reason: str = "",
    evidence_path: str = "",
    error: str = "",
) -> None:
    now = now_str()
    conn = get_conn(_db_path(settings))
    conn.execute(
        """UPDATE case_id_recoveries
           SET status=?, case_id=?, dashboard_status=?, decision_reason=?,
               evidence_path=?, error=?, completed_at=?, updated_at=? WHERE id=?""",
        (
            status,
            case_id,
            dashboard_status,
            _safe_reason(reason),
            evidence_path,
            _safe_reason(error),
            now,
            now,
            int(recovery_id),
        ),
    )
    conn.commit()
    conn.close()


def _reschedule_recovery(
    settings: Mapping[str, Any],
    recovery_id: int,
    dashboard_status: str,
    reason: str,
    evidence_path: str,
) -> str:
    config = get_case_id_recovery_config(settings)
    scheduled_at = _db_datetime(
        datetime.now() + timedelta(minutes=float(config["retry_interval_minutes"]))
    )
    conn = get_conn(_db_path(settings))
    conn.execute(
        """UPDATE case_id_recoveries
           SET status='retry', scheduled_at=?, dashboard_status=?,
               decision_reason=?, evidence_path=?, updated_at=? WHERE id=?""",
        (
            scheduled_at,
            dashboard_status,
            _safe_reason(reason),
            evidence_path,
            now_str(),
            int(recovery_id),
        ),
    )
    conn.commit()
    conn.close()
    return scheduled_at


def _reschedule_profile_busy(
    settings: Mapping[str, Any],
    recovery_id: int,
) -> str:
    """Yield a claimed lookup without consuming a Dashboard-check attempt."""

    config = get_case_id_recovery_config(settings)
    retry_minutes = min(5.0, float(config["retry_interval_minutes"]))
    scheduled_at = _db_datetime(datetime.now() + timedelta(minutes=retry_minutes))
    conn = get_conn(_db_path(settings))
    conn.execute(
        """UPDATE case_id_recoveries
           SET status='retry', scheduled_at=?, dashboard_status='profile_busy',
               decision_reason='AdsPower profile is held by another worker',
               attempt_count=CASE WHEN attempt_count > 0 THEN attempt_count - 1 ELSE 0 END,
               updated_at=? WHERE id=?""",
        (scheduled_at, now_str(), int(recovery_id)),
    )
    conn.commit()
    conn.close()
    return scheduled_at


def check_selling_applications(
    settings: Mapping[str, Any],
    account_id: str,
    site: str,
    brand_name: str,
    submitted_at: str | None = None,
) -> dict[str, Any]:
    """Open the correct regional dashboard in a fresh AdsPower tab."""

    from .case_followup import CaseFollowupBlocked, _auth_block_reason, _sellercentral_home_url

    site_code = str(site).upper()
    evidence_root = Path((settings.get("paths") or {}).get("evidence_root") or "./runtime/evidence")
    if not evidence_root.is_absolute():
        evidence_root = PROJECT_ROOT / evidence_root
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = (
        evidence_root
        / "case_id_recovery"
        / datetime.now().strftime("%Y-%m-%d")
        / account_id
        / site_code
        / brand_name
        / stamp
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    adspower = settings.get("adspower") or {}
    browser = settings.get("browser") or {}
    manager = BrowserManager(
        api_base_url=str(adspower.get("api_base_url") or "http://127.0.0.1:50325"),
        cdp_connect_timeout_ms=int(browser.get("cdp_connect_timeout_ms") or 30_000),
        cdp_health_timeout_sec=float(browser.get("cdp_health_timeout_sec") or 5.0),
        cdp_ready_timeout_sec=float(browser.get("cdp_ready_timeout_sec") or 30.0),
        cdp_restart_attempts=int(browser.get("cdp_restart_attempts", 1)),
        profile_restart_wait_sec=float(browser.get("profile_restart_wait_sec") or 5.0),
    )
    page = None
    leave_page_open = False
    try:
        _, account = manager.connect_by_account(account_id)
        available_sites = account.get("marketplace_configs") or {}
        if site_code not in available_sites and site_code != str(account.get("marketplace") or "").upper():
            raise ValueError(f"账号 {account_id} 缺少 {site_code} 站点配置")
        page = manager.new_tab()
        page.set_default_timeout(int(browser.get("action_timeout_ms") or 20_000))
        page.goto(
            _sellercentral_home_url(site_code),
            wait_until="domcontentloaded",
            timeout=int(browser.get("page_timeout_ms") or 45_000),
        )
        block_reason = _auth_block_reason(page)
        if block_reason:
            raise CaseFollowupBlocked(block_reason)
        switched, actual_site = switch_marketplace(
            page,
            site_code,
            evidence_dir=str(out_dir),
            max_retries=3,
        )
        if not switched or actual_site != site_code:
            raise RuntimeError(
                f"marketplace_switch_failed: expected={site_code} actual={actual_site}"
            )
        dashboard = check_case_dashboard_for_brand(
            page=page,
            account_id=account_id,
            marketplace=site_code,
            brand_name=brand_name,
            evidence_dir=out_dir,
            submitted_at=submitted_at,
        )
        dashboard["evidence_dir"] = str(out_dir)
        return dashboard
    except CaseFollowupBlocked as exc:
        leave_page_open = True
        return {
            "checked": False,
            "status": "blocked",
            "case_id": None,
            "case_ids": [],
            "error": str(exc),
            "block_type": str(exc),
            "evidence_dir": str(out_dir),
        }
    except Exception as exc:
        return {
            "checked": False,
            "status": "error",
            "case_id": None,
            "case_ids": [],
            "error": f"{exc.__class__.__name__}: {exc}",
            "evidence_dir": str(out_dir),
        }
    finally:
        try:
            if page and not leave_page_open:
                page.close()
        except Exception:
            pass
        try:
            manager.close()
        except Exception:
            pass


def _process_claimed_recovery_unlocked(
    settings: Mapping[str, Any],
    task: Mapping[str, Any],
) -> dict[str, Any]:
    dashboard = check_selling_applications(
        settings,
        str(task["account_id"]),
        str(task["marketplace"]),
        str(task["brand_name"]),
        str(task["submitted_at"]),
    )
    dashboard_status = str(dashboard.get("status") or "unknown")
    evidence_path = str(dashboard.get("evidence_dir") or "")
    case_ids = []
    for value in [*(dashboard.get("case_ids") or []), dashboard.get("case_id")]:
        normalized = str(value or "")
        if re.fullmatch(r"\d{10,12}", normalized) and normalized not in case_ids:
            case_ids.append(normalized)
    if len(case_ids) == 1:
        case_id = case_ids[0]
        from .case_followup import schedule_case_followup

        followup = schedule_case_followup(
            settings=dict(settings),
            account_id=str(task["account_id"]),
            site=str(task["marketplace"]),
            brand_name=str(task["brand_name"]),
            case_id=case_id,
            sku=str(task.get("sku") or ""),
            submitted_at=str(task["submitted_at"]),
            feishu_country_option=str(task.get("feishu_country_option") or ""),
            reapplication_campaign_id=task.get("reapplication_campaign_id"),
            reapplication_attempt_id=task.get("reapplication_attempt_id"),
        )
        _finish_recovery(
            settings,
            int(task["id"]),
            "completed",
            case_id=case_id,
            dashboard_status=dashboard_status,
            reason="unique exact-brand Case ID recovered from View Selling Applications",
            evidence_path=evidence_path,
        )
        from .auth_recovery import finish_submission_reconciliation

        finish_submission_reconciliation(
            settings,
            account_id=str(task["account_id"]),
            marketplace=str(task["marketplace"]),
            brand_name=str(task["brand_name"]),
            status="waiting_case",
            case_id=case_id,
            detail="Unique exact-brand Case ID recovered",
        )
        return {
            "status": "recovered",
            "case_id": case_id,
            "dashboard_status": dashboard_status,
            "case_followup": followup,
        }

    config = get_case_id_recovery_config(settings)
    reason = (
        "multiple Case IDs matched the exact brand"
        if len(case_ids) > 1
        else str(dashboard.get("error") or "exact-brand row has no Case ID yet")
    )
    if dashboard_status == "blocked":
        _finish_recovery(
            settings,
            int(task["id"]),
            "blocked",
            dashboard_status=dashboard_status,
            reason=reason,
            evidence_path=evidence_path,
            error=reason,
        )
        from .auth_recovery import create_auth_block
        from .reapplication import pause_missing_case_id_attempt

        auth_block = create_auth_block(
            settings,
            account_id=str(task["account_id"]),
            marketplace=str(task["marketplace"]),
            brand_name=str(task["brand_name"]),
            block_type=str(dashboard.get("block_type") or "login_required"),
            phase="case_id_recovery",
            source_type="case_id_recovery",
            source_id=int(task["id"]),
            submit_fenced=True,
            evidence_path=evidence_path,
            detail=reason,
        )

        pause_missing_case_id_attempt(
            settings,
            task.get("reapplication_campaign_id"),
            task.get("reapplication_attempt_id"),
            "Case-ID recovery blocked by login/CAPTCHA/2FA",
            blocked=True,
        )
        return {"status": "blocked", "reason": reason, "auth_block_id": auth_block["id"]}

    if dashboard_status == "draft" or int(task.get("attempt_count") or 0) >= int(
        config["max_attempts"]
    ):
        final_reason = "Selling Applications shows Draft" if dashboard_status == "draft" else reason
        _finish_recovery(
            settings,
            int(task["id"]),
            "manual_review",
            dashboard_status=dashboard_status,
            reason=final_reason,
            evidence_path=evidence_path,
            error=reason,
        )
        from .auth_recovery import finish_submission_reconciliation

        finish_submission_reconciliation(
            settings,
            account_id=str(task["account_id"]),
            marketplace=str(task["marketplace"]),
            brand_name=str(task["brand_name"]),
            status="manual_review",
            detail=final_reason,
        )
        from .reapplication import pause_missing_case_id_attempt

        pause_missing_case_id_attempt(
            settings,
            task.get("reapplication_campaign_id"),
            task.get("reapplication_attempt_id"),
            final_reason,
        )
        return {"status": "manual_review", "reason": final_reason}

    scheduled_at = _reschedule_recovery(
        settings,
        int(task["id"]),
        dashboard_status,
        reason,
        evidence_path,
    )
    return {
        "status": "retry",
        "dashboard_status": dashboard_status,
        "reason": reason,
        "scheduled_at": scheduled_at,
    }


def process_claimed_recovery(
    settings: Mapping[str, Any],
    task: Mapping[str, Any],
) -> dict[str, Any]:
    """Run one Dashboard lookup while exclusively owning its AdsPower profile."""

    db_path = _db_path(settings)
    init_db(db_path)
    accounts_path = resolve_accounts_path(
        str((settings.get("paths") or {}).get("accounts_path") or "config/accounts.json")
    )
    account_id = str(task["account_id"])
    profile_key = profile_key_for_account(accounts_path, account_id)
    if not profile_key:
        profile_key = profile_key_for_profile_id(f"account:{account_id}")
    owner_id = f"case-id-recovery:{os.getpid()}:{int(task['id'])}:{account_id}"
    acquired = acquire_profile_lock(
        db_path,
        profile_key,
        owner_type="case_id_recovery_worker",
        owner_id=owner_id,
        ttl_seconds=1800,
        pid_alive=_pid_running,
    )
    if not acquired:
        scheduled_at = _reschedule_profile_busy(settings, int(task["id"]))
        return {
            "status": "retry",
            "dashboard_status": "profile_busy",
            "reason": "AdsPower profile is held by another worker",
            "scheduled_at": scheduled_at,
        }
    try:
        return _process_claimed_recovery_unlocked(settings, task)
    finally:
        release_profile_lock(db_path, profile_key, owner_id=owner_id)


def process_due_case_id_recoveries(
    settings: Mapping[str, Any] | None = None,
    *,
    limit: int = 1,
) -> list[dict[str, Any]]:
    settings = settings or load_yaml(str(PROJECT_ROOT / "config" / "settings.yaml"))
    tasks = claim_due_case_id_recoveries(settings, limit=limit)
    return [process_claimed_recovery(settings, task) for task in tasks]


def get_next_case_id_recovery_due(settings: Mapping[str, Any]) -> str | None:
    db_path = _db_path(settings)
    init_db(db_path)
    conn = get_conn(db_path)
    row = conn.execute(
        """SELECT MIN(scheduled_at) AS scheduled_at FROM case_id_recoveries
           WHERE status IN ('pending', 'retry')
             AND NOT EXISTS (
               SELECT 1 FROM account_auth_blocks b
               LEFT JOIN account_profile_bindings p ON p.profile_key=b.profile_key
               WHERE b.status='open'
                 AND (b.account_id=case_id_recoveries.account_id
                      OR p.account_id=case_id_recoveries.account_id)
             )"""
    ).fetchone()
    conn.close()
    return str(row["scheduled_at"]) if row and row["scheduled_at"] else None


def seconds_until_next_case_id_recovery(settings: Mapping[str, Any]) -> float | None:
    value = get_next_case_id_recovery_due(settings)
    if not value:
        return None
    return max(0.0, (_parse_datetime(value) - datetime.now()).total_seconds())


def list_case_id_recoveries(settings: Mapping[str, Any]) -> list[dict[str, Any]]:
    db_path = _db_path(settings)
    init_db(db_path)
    conn = get_conn(db_path)
    rows = [dict(row) for row in conn.execute("SELECT * FROM case_id_recoveries ORDER BY id")]
    conn.close()
    return rows


def launch_case_id_recovery_worker(
    settings: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    settings = settings or load_yaml(str(PROJECT_ROOT / "config" / "settings.yaml"))
    config = get_case_id_recovery_config(settings)
    if not config["enabled"] or not config["auto_start_worker"]:
        return {"started": False, "reason": "disabled"}
    if get_next_case_id_recovery_due(settings) is None:
        return {"started": False, "reason": "no_pending_recoveries"}
    state_dir = PROJECT_ROOT / "runtime" / "state"
    logs_dir = PROJECT_ROOT / "runtime" / "logs"
    state_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    meta_path = state_dir / "case_id_recovery_worker.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            pid = int(meta.get("pid") or 0)
            if pid > 0:
                os.kill(pid, 0)
                return {"started": False, "reason": "already_running", **meta}
        except Exception:
            pass
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stdout_path = logs_dir / f"case_id_recovery_worker_{stamp}.out.log"
    stderr_path = logs_dir / f"case_id_recovery_worker_{stamp}.err.log"
    command = [sys.executable, str(PROJECT_ROOT / "scripts" / "run_case_id_recoveries.py"), "--watch"]
    kwargs: dict[str, Any] = {"cwd": str(PROJECT_ROOT), "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    else:
        kwargs["start_new_session"] = True
    with stdout_path.open("a", encoding="utf-8") as stdout_file, stderr_path.open(
        "a", encoding="utf-8"
    ) as stderr_file:
        proc = subprocess.Popen(
            command,
            stdout=stdout_file,
            stderr=stderr_file,
            **kwargs,
        )
    meta = {
        "pid": proc.pid,
        "started_at": datetime.now().isoformat(),
        "stdout_log": str(stdout_path),
        "stderr_log": str(stderr_path),
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"started": True, **meta}
