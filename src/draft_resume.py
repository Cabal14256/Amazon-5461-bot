"""Draft /sq/approvalrequest resume helpers for Amazon 5461 automation.

This module productizes the useful parts of historical _submit_*_draft.py scripts:
- open reEvaluateApplicationEndpoint / /sq/approvalrequest draft URL
- fill SQ approval fields
- upload documents
- optionally submit and wait for Case ID

It is conservative by default: callers must pass submit=True to click Submit.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

from playwright.sync_api import Page

from .email_resolver import clean_email, get_autofill_email_from_page
from .flow_submit_5461 import fill_katal_input, click_katal_button, extract_case_id


SQ_FIELD_SELECTORS = {
    "product_id": [
        'kat-input[data-cy="level-0:question-0:text"]',
        'kat-input[name="level-0:question-0:text"]',
    ],
    "product_title": [
        'kat-input[data-cy="level-0:question-1:text"]',
        'kat-input[name="level-0:question-1:text"]',
    ],
    "manufacturer": [
        'kat-input[data-cy="level-0:question-2:text"]',
        'kat-input[name="level-0:question-2:text"]',
    ],
    "product_description": [
        'kat-input[data-cy="level-0:question-3:text"]',
        'kat-input[name="level-0:question-3:text"]',
        'kat-textarea[data-cy="level-0:question-3:text"]',
        'kat-textarea[name="level-0:question-3:text"]',
    ],
    "email": [
        'kat-input[data-cy="contact_info_email_input"]',
        'kat-input#contact_info_email_input',
        'kat-input[name="email"]',
    ],
}

SUBMIT_SELECTORS = [
    'kat-button#submit_button',
    'kat-button[data-cy="submit_button"]',
    '#submit_button',
    '[data-cy="submit_button"]',
]


def build_re_evaluate_url(base_domain: str, application_id: str) -> str:
    domain = (base_domain or "sellercentral.amazon.com").replace("https://", "").rstrip("/")
    return f"https://{domain}/hz/reEvaluateApplicationEndpoint?applicationId={application_id}&redirectionEnabled=true"


def _first_present_selector(page: Page, selectors: list[str]) -> str:
    for sel in selectors:
        try:
            if page.locator(sel).count() > 0:
                return sel
        except Exception:
            continue
    return selectors[0]


def _selector_has_value(page: Page, selectors: list[str]) -> bool:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if not locator.count():
                continue
            value = locator.evaluate(
                """el => {
                    const inner = el.shadowRoot && el.shadowRoot.querySelector('input,textarea');
                    return String((inner && inner.value) || el.value || el.getAttribute('value') || '').trim();
                }"""
            )
            if value:
                return True
        except Exception:
            continue
    return False


def is_sq_approval_request(page: Page) -> bool:
    try:
        url = page.url.lower()
        if "/sq/approvalrequest" in url or "reevaluateapplicationendpoint" in url:
            return True
        return bool(page.evaluate("""() => !!document.querySelector(
            'kat-input[data-cy="level-0:question-1:text"], kat-input[name="level-0:question-1:text"], kat-input[data-cy="level-0:question-2:text"]'
        )"""))
    except Exception:
        return False


def open_draft_application(page: Page, draft_url: str, timeout_ms: int = 60000) -> Dict[str, Any]:
    result: Dict[str, Any] = {"ok": False, "url": draft_url, "final_url": "", "error": ""}
    try:
        try:
            page.goto(draft_url, wait_until="commit", timeout=timeout_ms)
            page.wait_for_load_state("domcontentloaded", timeout=min(timeout_ms, 30000))
        except Exception as e:
            result["error"] = f"goto_warning: {e}"
            # Seller Central may abort during redirects; salvage if SQ form is available.
        time.sleep(6)
        result["final_url"] = page.url
        result["ok"] = is_sq_approval_request(page)
        if not result["ok"] and not result["error"]:
            result["error"] = "sq_approval_form_not_detected"
        return result
    except Exception as e:
        result["error"] = str(e)
        try:
            result["final_url"] = page.url
        except Exception:
            pass
        return result


def fill_sq_approval_form(
    page: Page,
    brand_name: str,
    statement_text: str,
    account_email: str = "",
    product_title: str = "",
    product_id: str = "",
) -> Dict[str, Any]:
    """Fill /sq/approvalrequest fields. Product ID can remain blank for GTIN exemption."""
    result: Dict[str, Any] = {"ok": False, "steps": [], "fields": {}, "errors": []}
    if not product_title:
        m = re.search(r"Item name[：:]\s*(.+)", statement_text or "")
        product_title = m.group(1).strip() if m else f"{brand_name} Screen Protector"

    values = {
        "product_id": product_id,
        "product_title": product_title,
        "manufacturer": brand_name,
        "product_description": (statement_text or "")[:900],
        "email": clean_email(account_email) or get_autofill_email_from_page(page, wait_sec=1.0),
    }

    for key, value in values.items():
        if key == "product_id" and not value:
            result["fields"][key] = {"skipped": True, "reason": "gtin_exemption_blank"}
            continue
        if key == "email" and not value:
            result["fields"][key] = {"skipped": True, "reason": "no_email_available"}
            continue
        selector = _first_present_selector(page, SQ_FIELD_SELECTORS[key])
        ok = fill_katal_input(page, selector, value)
        result["fields"][key] = {"selector": selector, "ok": ok, "preview": str(value)[:80]}
        if ok:
            result["steps"].append(key)
        else:
            result["errors"].append(f"fill_failed:{key}:{selector}")
        time.sleep(0.5)

    for key in ["product_title", "manufacturer", "product_description", "email"]:
        if result["fields"].get(key, {}).get("skipped"):
            continue
        verified = _selector_has_value(page, SQ_FIELD_SELECTORS[key])
        result["fields"].setdefault(key, {})["verified"] = verified
        if verified:
            result["fields"][key]["ok"] = True

    # Trigger validation without forcing submit yet.
    try:
        page.evaluate("""() => {
            document.querySelectorAll('kat-input,kat-textarea,input,textarea').forEach(el => {
                const root = el.shadowRoot || el;
                const inner = root.querySelector && root.querySelector('input,textarea');
                for (const target of [inner, el].filter(Boolean)) {
                    try { target.dispatchEvent(new Event('input', {bubbles:true})); } catch(e) {}
                    try { target.dispatchEvent(new Event('change', {bubbles:true})); } catch(e) {}
                    try { target.dispatchEvent(new Event('blur', {bubbles:true})); } catch(e) {}
                }
            });
        }""")
    except Exception:
        pass

    required_ok = all(result["fields"].get(k, {}).get("ok") for k in ["product_title", "manufacturer", "product_description"])
    # EU/BE can omit email field; US should normally have it but don't block form fill status here.
    result["ok"] = required_ok
    return result


def upload_sq_documents(page: Page, upload_files: list[str]) -> Dict[str, Any]:
    result: Dict[str, Any] = {"ok": False, "count": 0, "missing": [], "error": ""}
    files = [str(Path(f)) for f in upload_files or []]
    result["missing"] = [f for f in files if not Path(f).exists()]
    if result["missing"]:
        result["error"] = "missing_files"
        return result
    if not files:
        result["error"] = "no_files"
        return result
    try:
        file_input = page.locator('input[type="file"]').first
        file_input.set_input_files(files, timeout=30000)
        deadline = time.time() + 45
        while time.time() < deadline:
            time.sleep(3)
            body = page.locator("body").inner_text(timeout=10000) or ""
            body_lower = body.lower()
            if "document upload failed" in body_lower or "upload failed" in body_lower:
                result["error"] = "document_upload_failed"
                return result
            try:
                selected = file_input.evaluate(
                    "el => el.files ? el.files.length : 0"
                )
            except Exception:
                selected = 0
            # Amazon clears the native input after ingesting the files and then
            # renders one removable file card per uploaded filename.  Either
            # state is valid evidence that all requested files were accepted.
            uploaded_cards_visible = all(
                Path(file_name).name.casefold() in body.casefold()
                for file_name in files
            )
            if selected >= len(files) or uploaded_cards_visible:
                # Give the server-side upload UI time to surface validation errors.
                time.sleep(6)
                body = (page.locator("body").inner_text(timeout=10000) or "").lower()
                if "document upload failed" in body or "upload failed" in body:
                    result["error"] = "document_upload_failed"
                    return result
                result["ok"] = True
                result["count"] = len(files)
                return result
        result["error"] = "document_upload_confirmation_timeout"
        return result
    except Exception as e:
        result["error"] = str(e)
        return result


def acknowledge_sq_document_requirements(page: Page) -> Dict[str, Any]:
    """Check visible document attestations through normal Playwright clicks."""
    result: Dict[str, Any] = {"ok": False, "checked": 0, "total": 0, "error": ""}
    try:
        native = page.locator('input[type="checkbox"]:visible')
        for i in range(native.count()):
            box = native.nth(i)
            if not box.is_enabled():
                continue
            result["total"] += 1
            if not box.is_checked():
                box.check(timeout=10000)
            if box.is_checked():
                result["checked"] += 1

        katal = page.locator("kat-checkbox:visible")
        for i in range(katal.count()):
            box = katal.nth(i)
            if not box.is_enabled():
                continue
            result["total"] += 1
            checked = box.evaluate(
                "el => !!(el.checked || el.getAttribute('checked') !== null || el.getAttribute('aria-checked') === 'true')"
            )
            if not checked:
                box.click(timeout=10000)
            checked = box.evaluate(
                "el => !!(el.checked || el.getAttribute('checked') !== null || el.getAttribute('aria-checked') === 'true')"
            )
            if checked:
                result["checked"] += 1

        result["ok"] = result["total"] > 0 and result["checked"] == result["total"]
        if not result["ok"]:
            result["error"] = "not_all_document_requirements_checked"
        return result
    except Exception as e:
        result["error"] = str(e)
        return result


def submit_sq_draft_and_wait_case(page: Page, wait_sec: int = 90) -> Dict[str, Any]:
    """Click SQ submit and wait for a non-declined Case ID."""
    result: Dict[str, Any] = {"clicked": False, "case_id": None, "status": "unknown", "error": ""}
    clicked = False
    deadline = time.time() + 30
    for sel in SUBMIT_SELECTORS:
        try:
            button = page.locator(sel).first
            if button.count() > 0:
                while time.time() < deadline and not button.is_enabled():
                    time.sleep(2)
                if not button.is_enabled():
                    continue
                clicked = click_katal_button(page, sel)
                if clicked:
                    break
        except Exception:
            continue
    result["clicked"] = clicked
    if not clicked:
        result["status"] = "submit_button_not_enabled"
        return result

    deadline = time.time() + wait_sec
    while time.time() < deadline:
        time.sleep(4)
        cid = extract_case_id(page)
        if cid:
            result["case_id"] = cid
            result["status"] = "success"
            return result
        try:
            text = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
            if "draft" in text.lower() and "submit" in text.lower():
                result["status"] = "still_draft"
            elif "error" in text.lower() or "410001" in text or "429" in text:
                result["status"] = "error_or_rate_limited"
        except Exception:
            pass
    if not result.get("case_id"):
        result["status"] = result.get("status") if result.get("status") != "unknown" else "no_case_id_timeout"
    return result


def resume_draft_application(
    page: Page,
    draft_url: str,
    brand_name: str,
    statement_text: str,
    upload_files: list[str],
    account_email: str = "",
    product_title: str = "",
    product_id: str = "",
    submit: bool = False,
    evidence_dir: str | Path | None = None,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "status": "unknown",
        "draft_url": draft_url,
        "case_id": None,
        "open": {},
        "fill": {},
        "upload": {},
        "acknowledge": {},
        "submit": {},
        "evidence_files": [],
    }
    out = Path(evidence_dir) if evidence_dir else None
    if out:
        out.mkdir(parents=True, exist_ok=True)

    opened = open_draft_application(page, draft_url)
    result["open"] = opened
    if out:
        try:
            p = out / "draft_opened.png"
            page.screenshot(path=str(p), full_page=False, timeout=12000)
            result["evidence_files"].append(str(p))
        except Exception:
            pass
    if not opened.get("ok"):
        result["status"] = "open_failed"
        return result

    fill = fill_sq_approval_form(page, brand_name, statement_text, account_email, product_title, product_id)
    result["fill"] = fill
    if out:
        try:
            p = out / "draft_filled.png"
            page.screenshot(path=str(p), full_page=False, timeout=12000)
            result["evidence_files"].append(str(p))
        except Exception:
            pass
    if not fill.get("ok"):
        result["status"] = "fill_failed"
        return result

    upload = upload_sq_documents(page, upload_files)
    result["upload"] = upload
    if out:
        try:
            p = out / "draft_uploaded.png"
            page.screenshot(path=str(p), full_page=False, timeout=12000)
            result["evidence_files"].append(str(p))
        except Exception:
            pass
    if not upload.get("ok"):
        result["status"] = "upload_failed"
        return result

    acknowledge = acknowledge_sq_document_requirements(page)
    result["acknowledge"] = acknowledge
    if not acknowledge.get("ok"):
        result["status"] = "acknowledge_failed"
        return result

    if not submit:
        result["status"] = "ready_to_submit"
        return result

    submit_result = submit_sq_draft_and_wait_case(page)
    result["submit"] = submit_result
    result["case_id"] = submit_result.get("case_id")
    result["status"] = submit_result.get("status", "unknown")
    if out:
        try:
            p = out / "draft_after_submit.png"
            page.screenshot(path=str(p), full_page=False, timeout=12000)
            result["evidence_files"].append(str(p))
        except Exception:
            pass
    return result
