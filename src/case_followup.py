"""Delayed, read-only Case detail checks for completed 5461 submissions.

The module deliberately keeps Seller Central's transport state separate from the
business outcome.  For example, ``Answered`` means Amazon replied; it does not
mean that the application was approved.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .approved_case_verification import verify_approved_case
from .browser_manager import BrowserConnectionError, BrowserManager
from .config_loader import load_yaml
from .db import (
    claim_due_case_followups,
    defer_due_case_followups_for_account,
    enqueue_case_followup,
    finish_case_followup,
    get_next_case_followup_due,
    init_db,
    insert_verification,
    mark_brand_status_pending_after_submission,
    reschedule_case_followup,
    update_case_followup_feishu_binding,
    update_submission_case_outcome,
    upsert_case_outcome_status,
    upsert_submission_by_case_id,
)
from .marketplace_switcher import MARKETPLACE_CONFIG, switch_marketplace
from .registration import RegistrationManager

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TERMINAL_RESULTS = {"approved", "declined", "false_approved"}
MANUAL_RESULTS = {"action_required", "answered_unknown"}
PENDING_RESULTS = {"pending", "verification_pending"}


class CaseFollowupBlocked(RuntimeError):
    """Raised when login, CAPTCHA, 2FA, or another human control blocks the check."""


CONNECTION_CIRCUIT_ERROR_CODES = {
    "adspower_profile_unavailable",
    "cdp_connect_failed",
    "proxy_connection_failed",
}


def get_case_followup_config(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = settings or load_yaml(str(PROJECT_ROOT / "config" / "settings.yaml"))
    configured = dict(settings.get("case_followup") or {})
    defaults = {
        "enabled": True,
        "delay_hours": 24.0,
        "retry_interval_hours": 6.0,
        "error_retry_interval_hours": 1.0,
        "max_attempts": 12,
        "poll_interval_seconds": 60,
        "connection_circuit_breaker": True,
        "auto_start_worker": True,
        "registration_path": "./data/5461申请登记表.xlsx",
        "approved_verification": {"enabled": True},
    }
    defaults.update(configured)
    return defaults


def _db_path(settings: dict[str, Any]) -> str:
    return str(settings.get("paths", {}).get("db_path") or "./runtime/state/ledger.db")


def _parse_local_datetime(value: str | None) -> datetime:
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


def _db_datetime(value: datetime) -> str:
    return value.replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")


def _redact_emails(value: str) -> str:
    return re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[REDACTED_EMAIL]", value or "")


def _technical_error(exc: Exception) -> tuple[str, str]:
    """Return a stable error code and a sanitized diagnostic reason."""
    if isinstance(exc, BrowserConnectionError):
        return exc.error_code, str(exc)
    message = str(exc)
    if "ERR_SOCKS_CONNECTION_FAILED" in message:
        return (
            "proxy_connection_failed",
            "Seller Central navigation failed through the AdsPower SOCKS proxy",
        )
    return "automation_error", f"{type(exc).__name__}: {message}"


def case_detail_url(site: str, case_id: str) -> str:
    site_code = (site or "").upper()
    info = MARKETPLACE_CONFIG.get(site_code)
    if not info:
        raise ValueError(f"未知站点: {site}")
    return f"https://sellercentral.{info.domain}/cu/case-dashboard/view-case/?ie=UTF8&caseID={case_id}"


def _sellercentral_home_url(site: str) -> str:
    site_code = (site or "").upper()
    info = MARKETPLACE_CONFIG.get(site_code)
    if not info:
        raise ValueError(f"未知站点: {site}")
    return f"https://sellercentral.{info.domain}/home"


def _auth_block_reason(page) -> str | None:
    url = (getattr(page, "url", "") or "").lower()
    try:
        text = (page.locator("body").inner_text(timeout=5_000) or "").lower()
    except Exception:
        text = ""
    if "/ap/signin" in url or "sign in to your account" in text:
        return "seller_central_login_required"
    if "captcha" in url or "enter the characters you see" in text:
        return "captcha_required"
    if any(token in text for token in ("two-step verification", "two factor authentication", "verification code")):
        return "two_factor_authentication_required"
    return None


def _save_page_evidence(page, out_dir: Path, prefix: str) -> list[str]:
    evidence_files: list[str] = []
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        url_path = out_dir / f"{prefix}.url.txt"
        url_path.write_text(page.url, encoding="utf-8")
        evidence_files.append(str(url_path))
    except Exception:
        pass
    try:
        text_path = out_dir / f"{prefix}.text.txt"
        text_path.write_text(page.locator("body").inner_text(timeout=10_000), encoding="utf-8")
        evidence_files.append(str(text_path))
    except Exception:
        pass
    try:
        html_path = out_dir / f"{prefix}.html"
        html_path.write_text(page.content(), encoding="utf-8")
        evidence_files.append(str(html_path))
    except Exception:
        pass
    try:
        shot_path = out_dir / f"{prefix}.png"
        page.screenshot(path=str(shot_path), full_page=True, timeout=20_000)
        evidence_files.append(str(shot_path))
    except Exception:
        pass
    return evidence_files


def extract_case_detail(page, expected_case_id: str) -> dict[str, Any]:
    """Extract Case Summary and all visible message cards from a detail page."""
    payload = page.evaluate(
        r"""expectedCaseId => {
          const compact = value => (value || '').trim().replace(/\s+/g, ' ');
          const bodyText = document.body ? (document.body.innerText || '') : '';
          const cards = Array.from(document.querySelectorAll('div.view-case kat-box, kat-box'))
            .map((box, index) => {
              const senderNode = box.querySelector('[data-testid="sender-label"]');
              const receiverNode = box.querySelector('[data-testid="reciever"]');
              const rawText = compact(box.innerText || box.textContent || '');
              const sender = compact(senderNode && (senderNode.innerText || senderNode.textContent));
              const receiver = compact(receiverNode && (receiverNode.innerText || receiverNode.textContent));
              const bodyParts = Array.from(box.querySelectorAll('p, span'))
                .map(node => compact(node.innerText || node.textContent || ''))
                .filter(text => text.length >= 20 && text !== sender && text !== receiver);
              const uniqueBodyParts = bodyParts.filter((text, pos) => bodyParts.indexOf(text) === pos);
              const timestampMatch = rawText.match(/\b\d{1,2}\s+[A-Za-z]{3}\s+\d{4}\s+\d{1,2}:\d{2}\s+[A-Z]{2,5}\b/);
              return {
                index,
                sender,
                receiver,
                timestamp_text: timestampMatch ? timestampMatch[0] : '',
                body: uniqueBodyParts.join('\n') || rawText,
                raw_text: rawText,
              };
            })
            .filter(item => item.sender || /Selling Partner support|Error 5461/i.test(item.raw_text));
          return {
            url: location.href,
            title: document.title || '',
            body_text: bodyText,
            expected_case_visible: bodyText.includes(expectedCaseId),
            messages: cards,
          };
        }""",
        str(expected_case_id),
    )
    body_text = payload.get("body_text") or ""
    case_match = re.search(r"\bID:\s*(\d{10,12})\b", body_text, re.IGNORECASE)
    status_match = re.search(r"\bStatus:\s*([^\r\n]+)", body_text, re.IGNORECASE)
    payload["case_id"] = case_match.group(1) if case_match else str(expected_case_id)
    payload["case_status"] = status_match.group(1).strip() if status_match else ""
    payload.pop("body_text", None)
    return payload


APPROVED_PATTERNS = [
    r"\b(?:your|the) application (?:has been|was|is) approved\b",
    r"\b(?:your|the) request (?:has been|was|is) approved\b",
    r"\bwe (?:have )?approved your (?:application|request)\b",
    r"\bwe (?:have )?completed our review and accepted your application\b",
    r"\byou (?:are|have been) approved to (?:create|sell|list)\b",
    r"\byou may now (?:create|sell|list)\b",
    r"\bapplication approuv[ée]e\b",
    r"\bantrag (?:wurde|ist) genehmigt\b",
    r"\bwir (?:haben )?unsere pr[üu]fung abgeschlossen und (?:ihren|den) antrag akzeptiert(?: haben)?\b",
    r"\bsolicitud (?:ha sido|fue) aprobada\b",
    r"\brichiesta (?:è stata|e stata) approvata\b",
]

DECLINED_PATTERNS = [
    r"\b(?:your|the) application (?:has been|was|is) declined\b",
    r"\b(?:your|the) request (?:has been|was|is) (?:declined|rejected|denied)\b",
    r"\bwe (?:had to|have to|must) decline your (?:application|request)\b",
    r"\bwe (?:had to|have to|must) decline your brand(?: and associated gtin exemption request)?\b",
    r"\bwe (?:are )?unable to approve your (?:application|request)\b",
    r"\bwe cannot approve your (?:application|request)\b",
    r"\bapplication (?:a été|a ete) refus[ée]e\b",
    r"\bnous avons d[ûu] rejeter (?:votre|la) demande\b",
    r"\bantrag (?:wurde|ist) abgelehnt\b",
    r"\bmussten wir (?:ihren|den) antrag ablehnen\b",
    r"\bsolicitud (?:ha sido|fue) rechazada\b",
    r"\bhemos tenido que rechazar (?:tu|su) solicitud\b",
    r"\brichiesta (?:è stata|e stata) rifiutata\b",
]

ACTION_REQUIRED_PATTERNS = [
    r"\bplease (?:reply|respond|provide|submit|send|upload)\b",
    r"\badditional (?:information|documents?|evidence) (?:is|are) required\b",
    r"\bwe need (?:more|additional) (?:information|documents?|evidence)\b",
    r"\baction is required\b",
]


def classify_case_reply(case_status: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Classify the newest Amazon-authored message using explicit wording."""
    amazon_messages = [
        message for message in messages
        if (message.get("sender") or "").strip().casefold() == "amazon"
    ]
    if not amazon_messages:
        return {
            "result": "pending",
            "is_success": None,
            "has_amazon_reply": False,
            "decision_reason": "Case 详情中尚未发现 Amazon 回复",
            "latest_amazon_reply": "",
        }

    latest = amazon_messages[0]
    reply = (latest.get("body") or latest.get("raw_text") or "").strip()
    reply_lower = reply.casefold()

    if any(re.search(pattern, reply_lower, re.IGNORECASE) for pattern in APPROVED_PATTERNS):
        result = "approved"
        is_success: bool | None = True
        reason = "Amazon 最新回复明确表示申请已通过"
    elif any(re.search(pattern, reply_lower, re.IGNORECASE) for pattern in DECLINED_PATTERNS):
        result = "declined"
        is_success = False
        reason = "Amazon 最新回复明确表示申请被拒绝"
    elif any(re.search(pattern, reply_lower, re.IGNORECASE) for pattern in ACTION_REQUIRED_PATTERNS):
        result = "action_required"
        is_success = None
        reason = "Amazon 已回复并要求补充信息或执行操作"
    else:
        result = "answered_unknown"
        is_success = None
        reason = f"Amazon 已回复，但正文未命中明确通过/拒绝规则（Case 状态: {case_status or 'unknown'}）"

    return {
        "result": result,
        "is_success": is_success,
        "has_amazon_reply": True,
        "decision_reason": reason,
        "latest_amazon_reply": reply,
        "latest_amazon_reply_at": latest.get("timestamp_text") or "",
    }


def check_case_detail(
    account_id: str,
    site: str,
    brand_name: str,
    case_id: str,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Open the matching AdsPower profile, switch marketplace, and read a Case."""
    settings = settings or load_yaml(str(PROJECT_ROOT / "config" / "settings.yaml"))
    site_code = (site or "").upper()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    date_part = datetime.now().strftime("%Y-%m-%d")
    evidence_root = Path(settings.get("paths", {}).get("evidence_root") or "./runtime/evidence")
    if not evidence_root.is_absolute():
        evidence_root = PROJECT_ROOT / evidence_root
    out_dir = evidence_root / "case_followups" / date_part / account_id / site_code / brand_name / str(case_id) / stamp
    out_dir.mkdir(parents=True, exist_ok=True)

    adspower_config = settings.get("adspower", {}) or {}
    browser_config = settings.get("browser", {}) or {}
    manager = BrowserManager(
        api_base_url=str(adspower_config.get("api_base_url") or "http://127.0.0.1:50325"),
        cdp_connect_timeout_ms=int(browser_config.get("cdp_connect_timeout_ms") or 30_000),
        cdp_health_timeout_sec=float(browser_config.get("cdp_health_timeout_sec") or 5.0),
        cdp_ready_timeout_sec=float(browser_config.get("cdp_ready_timeout_sec") or 30.0),
        cdp_restart_attempts=int(browser_config.get("cdp_restart_attempts", 1)),
        profile_restart_wait_sec=float(browser_config.get("profile_restart_wait_sec") or 5.0),
    )
    page = None
    evidence_files: list[str] = []
    started_at = datetime.now().isoformat()
    try:
        _, account = manager.connect_by_account(account_id)
        available_sites = account.get("marketplace_configs") or {}
        if site_code not in available_sites and site_code != str(account.get("marketplace") or "").upper():
            raise ValueError(f"账号 {account_id} 缺少 {site_code} 站点配置")

        page = manager.new_tab()
        page.set_default_timeout(int(settings.get("browser", {}).get("action_timeout_ms") or 20_000))
        page.goto(
            _sellercentral_home_url(site_code),
            wait_until="domcontentloaded",
            timeout=int(settings.get("browser", {}).get("page_timeout_ms") or 45_000),
        )
        block_reason = _auth_block_reason(page)
        if block_reason:
            raise CaseFollowupBlocked(block_reason)

        switched, actual_site = switch_marketplace(page, site_code, evidence_dir=str(out_dir), max_retries=3)
        if not switched or actual_site != site_code:
            raise RuntimeError(f"marketplace_switch_failed: expected={site_code} actual={actual_site}")

        detail_url = case_detail_url(site_code, str(case_id))
        page.goto(
            detail_url,
            wait_until="domcontentloaded",
            timeout=int(settings.get("browser", {}).get("page_timeout_ms") or 45_000),
        )
        try:
            page.wait_for_load_state("networkidle", timeout=8_000)
        except Exception:
            pass
        time.sleep(2)

        block_reason = _auth_block_reason(page)
        if block_reason:
            raise CaseFollowupBlocked(block_reason)

        evidence_files.extend(_save_page_evidence(page, out_dir, "case_detail"))
        detail = extract_case_detail(page, str(case_id))
        if not detail.get("expected_case_visible"):
            raise RuntimeError(f"case_detail_not_found: {case_id}")
        decision = classify_case_reply(detail.get("case_status") or "", detail.get("messages") or [])
        case_reply_result = str(decision.get("result") or "pending")
        if case_reply_result == "approved":
            case_decision_reason = str(decision.get("decision_reason") or "")
            try:
                approval_verification = verify_approved_case(
                    page,
                    account,
                    site_code,
                    brand_name,
                    _sellercentral_home_url(site_code),
                    settings,
                    out_dir,
                )
            except Exception as exc:
                approval_verification = {
                    "result": "verification_pending",
                    "is_success": None,
                    "decision_reason": (
                        "批准后二次验证异常，保留待复核: "
                        f"{type(exc).__name__}: {exc}"
                    ),
                    "manage_brand": {"result": "not_run"},
                    "add_product": {"result": "not_run"},
                    "evidence_files": [],
                }
            decision["case_reply_result"] = case_reply_result
            decision["case_decision_reason"] = case_decision_reason
            decision["approval_verification"] = approval_verification
            decision["result"] = approval_verification["result"]
            decision["is_success"] = approval_verification["is_success"]
            decision["decision_reason"] = (
                f"{case_decision_reason}；批准后二次验证: "
                f"{approval_verification['decision_reason']}"
            )
            evidence_files.extend(approval_verification.get("evidence_files") or [])
        result = {
            "account_id": account_id,
            "site": site_code,
            "brand_name": brand_name,
            "case_id": str(case_id),
            "detail_url": detail.get("url") or detail_url,
            "case_status": detail.get("case_status") or "",
            "messages": detail.get("messages") or [],
            **decision,
            "evidence_dir": str(out_dir),
            "evidence_files": evidence_files,
            "started_at": started_at,
            "completed_at": datetime.now().isoformat(),
        }
        result_path = out_dir / "case_result.json"
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result["evidence_files"].append(str(result_path))
        return result
    except CaseFollowupBlocked as exc:
        if page is not None:
            evidence_files.extend(_save_page_evidence(page, out_dir, "blocked"))
        result = {
            "account_id": account_id,
            "site": site_code,
            "brand_name": brand_name,
            "case_id": str(case_id),
            "result": "blocked",
            "is_success": None,
            "case_status": "",
            "has_amazon_reply": False,
            "decision_reason": str(exc),
            "latest_amazon_reply": "",
            "evidence_dir": str(out_dir),
            "evidence_files": evidence_files,
            "started_at": started_at,
            "completed_at": datetime.now().isoformat(),
        }
        (out_dir / "case_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result
    except Exception as exc:
        technical_error_code, technical_reason = _technical_error(exc)
        if page is not None:
            evidence_files.extend(_save_page_evidence(page, out_dir, "error"))
        result = {
            "account_id": account_id,
            "site": site_code,
            "brand_name": brand_name,
            "case_id": str(case_id),
            "result": "error",
            "is_success": None,
            "case_status": "",
            "has_amazon_reply": False,
            "decision_reason": technical_reason,
            "technical_error_code": technical_error_code,
            "latest_amazon_reply": "",
            "evidence_dir": str(out_dir),
            "evidence_files": evidence_files,
            "started_at": started_at,
            "completed_at": datetime.now().isoformat(),
        }
        (out_dir / "case_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result
    finally:
        if page is not None:
            try:
                page.close()
            except Exception:
                pass
        try:
            manager.close()
        except Exception:
            pass


def _registration_path(settings: dict[str, Any], config: dict[str, Any]) -> Path:
    path = Path(str(config.get("registration_path") or "./data/5461申请登记表.xlsx"))
    return path if path.is_absolute() else PROJECT_ROOT / path


def register_pending_case(
    settings: dict[str, Any],
    account_id: str,
    site: str,
    brand_name: str,
    case_id: str,
    sku: str,
    submitted_at: str,
) -> None:
    db_path = _db_path(settings)
    init_db(db_path)
    upsert_submission_by_case_id(
        db_path,
        account_id,
        site,
        brand_name,
        case_id,
        "under_review",
        note="5461 submitted; delayed Case follow-up scheduled",
        submitted_at=submitted_at,
    )
    mark_brand_status_pending_after_submission(db_path, account_id, site, brand_name)

    config = get_case_followup_config(settings)
    registry = RegistrationManager(str(_registration_path(settings, config)))
    if not registry.find_record(case_id=case_id):
        registry.add_record(
            {
                "site": site,
                "account": account_id,
                "brand": brand_name,
                "sku": sku,
                "case_id": case_id,
                "status": "申请中",
                "notes": "已提交；等待 Case 自动复核",
            }
        )


def schedule_case_followup(
    settings: dict[str, Any],
    account_id: str,
    site: str,
    brand_name: str,
    case_id: str,
    sku: str = "",
    submitted_at: str | None = None,
    delay_hours: float | None = None,
    feishu_country_option: str = "",
    submission_title: str = "",
    submission_content: str = "",
    uk_sku: str = "",
    uk_title: str = "",
    uk_content: str = "",
) -> dict[str, Any]:
    config = get_case_followup_config(settings)
    submitted_dt = _parse_local_datetime(submitted_at)
    delay = float(config["delay_hours"] if delay_hours is None else delay_hours)
    scheduled_dt = submitted_dt + timedelta(hours=max(0.0, delay))
    submitted_db = _db_datetime(submitted_dt)
    scheduled_db = _db_datetime(scheduled_dt)
    db_path = _db_path(settings)
    init_db(db_path)
    followup_id, created = enqueue_case_followup(
        db_path,
        account_id,
        site.upper(),
        brand_name,
        str(case_id),
        submitted_db,
        scheduled_db,
    )
    register_pending_case(
        settings,
        account_id,
        site.upper(),
        brand_name,
        str(case_id),
        sku,
        submitted_db,
    )
    try:
        from .feishu_bitable import bind_case_to_record, ensure_submission_records

        feishu_record_ensure = ensure_submission_records(
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
        )

        feishu_binding = bind_case_to_record(
            settings,
            account_id=account_id,
            site=site.upper(),
            brand_name=brand_name,
            sku=sku,
            country_option=feishu_country_option,
        )
    except Exception as exc:  # Downstream bookkeeping must not downgrade a submission.
        feishu_record_ensure = {
            "status": "error",
            "reason": f"unexpected record ensure error: {exc.__class__.__name__}",
        }
        feishu_binding = {
            "status": "error",
            "reason": f"unexpected binding error: {exc.__class__.__name__}",
            "record_id": "",
            "country_option": feishu_country_option,
            "candidate_count": 0,
        }
    update_case_followup_feishu_binding(
        db_path,
        followup_id,
        str(feishu_binding.get("status") or "error"),
        record_id=str(feishu_binding.get("record_id") or ""),
        country_option=str(feishu_binding.get("country_option") or feishu_country_option or ""),
        reason=str(feishu_binding.get("reason") or ""),
    )
    if feishu_binding.get("status") == "bound" and feishu_binding.get("record_id"):
        try:
            from .feishu_bitable import update_bound_progress

            feishu_progress_update = update_bound_progress(
                settings,
                str(feishu_binding["record_id"]),
                "申请中",
            )
        except Exception as exc:  # A Feishu write must never downgrade an Amazon submission.
            feishu_progress_update = {
                "status": "error",
                "reason": f"unexpected progress update error: {exc.__class__.__name__}",
            }
    else:
        feishu_progress_update = {
            "status": "skipped",
            "reason": "Case task has no unique Feishu binding",
        }
    return {
        "id": followup_id,
        "created": created,
        "scheduled_at": scheduled_db,
        "delay_hours": delay,
        "case_id": str(case_id),
        "feishu_record_ensure": feishu_record_ensure,
        "feishu_binding": feishu_binding,
        "feishu_progress_update": feishu_progress_update,
    }


def record_case_outcome(settings: dict[str, Any], task: dict[str, Any], result: dict[str, Any]) -> None:
    outcome = result.get("result") or "answered_unknown"
    reason = _redact_emails(result.get("decision_reason") or "")[:500]
    db_path = _db_path(settings)
    update_submission_case_outcome(
        db_path,
        task["account_id"],
        task["marketplace"],
        task["brand_name"],
        task["case_id"],
        outcome,
        note=reason,
    )
    insert_verification(
        db_path,
        task["account_id"],
        task["marketplace"],
        task["brand_name"],
        method=(
            "case_reply_and_effective_approval"
            if outcome in {"approved", "false_approved"}
            else "case_reply"
        ),
        result=outcome,
        summary_text=reason,
        confidence_points=100 if outcome in TERMINAL_RESULTS else 50,
    )
    upsert_case_outcome_status(
        db_path,
        task["account_id"],
        task["marketplace"],
        task["brand_name"],
        outcome,
        method=(
            "case_reply_and_effective_approval"
            if outcome in {"approved", "false_approved"}
            else "case_reply"
        ),
    )

    config = get_case_followup_config(settings)
    registry = RegistrationManager(str(_registration_path(settings, config)))
    excel_status = {
        "approved": "已通过",
        "false_approved": "假过",
        "declined": "已拒绝",
        "action_required": "待补充材料",
        "answered_unknown": "待人工复核",
    }.get(outcome)
    if excel_status:
        registry.update_status(task["case_id"], excel_status, reason)

    try:
        from .feishu_bitable import CASE_RESULT_PROGRESS_MAP, update_bound_progress

        progress = CASE_RESULT_PROGRESS_MAP.get(str(outcome))
        if (
            progress
            and task.get("feishu_binding_status") == "bound"
            and task.get("feishu_record_id")
        ):
            result["feishu_progress_update"] = update_bound_progress(
                settings,
                str(task["feishu_record_id"]),
                progress,
            )
    except Exception as exc:  # Outcome persistence remains authoritative if Feishu is unavailable.
        result["feishu_progress_update"] = {
            "status": "error",
            "reason": f"unexpected progress update error: {exc.__class__.__name__}",
        }


def process_claimed_followup(
    settings: dict[str, Any],
    task: dict[str, Any],
    update_records: bool = True,
) -> dict[str, Any]:
    config = get_case_followup_config(settings)
    result = check_case_detail(
        task["account_id"],
        task["marketplace"],
        task["brand_name"],
        task["case_id"],
        settings=settings,
    )
    outcome = result.get("result") or "error"
    db_path = _db_path(settings)
    evidence_path = result.get("evidence_dir")
    reason = _redact_emails(result.get("decision_reason") or "")[:1000]
    case_status = result.get("case_status") or ""

    if outcome in TERMINAL_RESULTS:
        finish_case_followup(
            db_path, task["id"], "completed", outcome, case_status, reason, evidence_path
        )
        if update_records:
            record_case_outcome(settings, task, result)
    elif outcome in MANUAL_RESULTS:
        finish_case_followup(
            db_path, task["id"], "manual_review", outcome, case_status, reason, evidence_path
        )
        if update_records:
            record_case_outcome(settings, task, result)
    elif outcome == "blocked":
        finish_case_followup(
            db_path, task["id"], "blocked", outcome, case_status, reason, evidence_path, error=reason
        )
    else:
        attempts = int(task.get("attempt_count") or 1)
        max_attempts = max(1, int(config["max_attempts"]))
        if attempts >= max_attempts:
            finish_case_followup(
                db_path,
                task["id"],
                "failed",
                outcome,
                case_status,
                reason,
                evidence_path,
                error=reason,
            )
        else:
            retry_hours = (
                float(config["retry_interval_hours"])
                if outcome in PENDING_RESULTS
                else float(config["error_retry_interval_hours"])
            )
            next_run = _db_datetime(datetime.now() + timedelta(hours=max(0.1, retry_hours)))
            reschedule_case_followup(
                db_path,
                task["id"],
                next_run,
                outcome,
                case_status,
                reason,
                evidence_path,
                error=reason if outcome == "error" else None,
            )
            result["rescheduled_at"] = next_run
    return result


def process_due_case_followups(
    settings: dict[str, Any] | None = None,
    limit: int = 1,
    update_records: bool = True,
    followup_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    settings = settings or load_yaml(str(PROJECT_ROOT / "config" / "settings.yaml"))
    db_path = _db_path(settings)
    init_db(db_path)
    claimed = claim_due_case_followups(db_path, limit=limit, followup_ids=followup_ids)
    results = []
    deferred_claimed_ids: set[int] = set()
    config = get_case_followup_config(settings)
    for index, task in enumerate(claimed):
        if int(task["id"]) in deferred_claimed_ids:
            continue
        print(
            f"[CaseFollowup] 检查 {task['account_id']} / {task['marketplace']} / "
            f"{task['brand_name']} / Case {task['case_id']}"
        )
        result = process_claimed_followup(settings, task, update_records=update_records)
        results.append(result)
        print(
            f"[CaseFollowup] 结果 Case {task['case_id']}: "
            f"{result.get('result')} ({result.get('decision_reason', '')})"
        )
        technical_error_code = str(result.get("technical_error_code") or "")
        if not (
            config.get("connection_circuit_breaker", True)
            and technical_error_code in CONNECTION_CIRCUIT_ERROR_CODES
        ):
            continue

        next_run = str(result.get("rescheduled_at") or "")
        if not next_run:
            retry_hours = max(0.1, float(config.get("error_retry_interval_hours") or 1.0))
            next_run = _db_datetime(datetime.now() + timedelta(hours=retry_hours))
        circuit_reason = f"connection_circuit_open: {technical_error_code}"
        deferred_count = 0
        for sibling in claimed[index + 1:]:
            if sibling["account_id"] != task["account_id"]:
                continue
            reschedule_case_followup(
                db_path,
                int(sibling["id"]),
                next_run,
                "error",
                str(sibling.get("case_status") or ""),
                circuit_reason,
                sibling.get("evidence_path"),
                error=circuit_reason,
            )
            deferred_claimed_ids.add(int(sibling["id"]))
            deferred_count += 1

        due_at = _db_datetime(datetime.now())
        deferred_count += defer_due_case_followups_for_account(
            db_path,
            str(task["account_id"]),
            next_run,
            due_at,
            circuit_reason,
            followup_ids=followup_ids,
            exclude_ids=[int(item["id"]) for item in claimed],
        )
        result["connection_circuit_deferred_count"] = deferred_count
        if deferred_count:
            print(
                f"[CaseFollowup] profile 连接熔断已开启，"
                f"同账号 {deferred_count} 个任务延后到 {next_run}"
            )
    return results


def seconds_until_next_followup(
    settings: dict[str, Any],
    followup_ids: list[int] | None = None,
) -> float | None:
    due = get_next_case_followup_due(_db_path(settings), followup_ids=followup_ids)
    if not due:
        return None
    return max(0.0, (_parse_local_datetime(due) - datetime.now()).total_seconds())


def _pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def launch_case_followup_worker(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = settings or load_yaml(str(PROJECT_ROOT / "config" / "settings.yaml"))
    config = get_case_followup_config(settings)
    if not config.get("enabled") or not config.get("auto_start_worker"):
        return {"started": False, "reason": "disabled"}
    if get_next_case_followup_due(_db_path(settings)) is None:
        return {"started": False, "reason": "no_pending_followups"}

    state_dir = PROJECT_ROOT / "runtime" / "state"
    logs_dir = PROJECT_ROOT / "runtime" / "logs"
    state_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    meta_path = state_dir / "case_followup_worker.json"
    if meta_path.exists():
        try:
            existing = json.loads(meta_path.read_text(encoding="utf-8"))
            if _pid_running(int(existing.get("pid") or 0)):
                return {"started": False, "reason": "already_running", **existing}
        except Exception:
            pass

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stdout_path = logs_dir / f"case_followup_worker_{stamp}.out.log"
    stderr_path = logs_dir / f"case_followup_worker_{stamp}.err.log"
    cmd = [sys.executable, str(PROJECT_ROOT / "scripts" / "run_case_followups.py"), "--watch"]
    creationflags = 0
    popen_kwargs: dict[str, Any] = {"cwd": str(PROJECT_ROOT), "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        creationflags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
        popen_kwargs["creationflags"] = creationflags
    else:
        popen_kwargs["start_new_session"] = True

    with stdout_path.open("a", encoding="utf-8") as stdout_file, stderr_path.open("a", encoding="utf-8") as stderr_file:
        proc = subprocess.Popen(cmd, stdout=stdout_file, stderr=stderr_file, **popen_kwargs)

    meta = {
        "pid": proc.pid,
        "started_at": datetime.now().isoformat(),
        "stdout_log": str(stdout_path),
        "stderr_log": str(stderr_path),
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"started": True, **meta}
