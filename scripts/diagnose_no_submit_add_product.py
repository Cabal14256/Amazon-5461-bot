#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-submit Add Product safety diagnostic.

Purpose:
- validate recent form-filler changes without clicking Continue or submitting
- verify account email resolution/backfill path
- verify new UI item type / Product ID exemption idempotence

This script intentionally does NOT click Continue / Next / Apply to sell / Submit.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from playwright.sync_api import sync_playwright

from auto_add_account_data import ensure_account_exists, sync_brand_pack_for_account, normalize_account_id, normalize_site
from scripts._flow_cli_common import load_runtime
from src.email_resolver import resolve_account_email, update_account_email
from src.form_filler import KatalFormFiller
from src.marketplace_switcher import switch_marketplace


JS_STATE = r"""() => {
    function findExternalProductIdRow() {
        const byTestId = document.querySelector('[data-testid="ResponsiveNoOpDecorator-externally_assigned_product_identifier"]')
                      || document.querySelector('[data-testid="NoOpDecorator-externally_assigned_product_identifier"]');
        if (byTestId) return byTestId;
        const named = document.querySelector('[name^="externally_assigned_product_identifier"], [id^="externally_assigned_product_identifier"], kat-input[name*="externally_assigned_product_identifier"], kat-dropdown[name*="externally_assigned_product_identifier"]');
        if (named) {
            let node = named;
            for (let depth = 0; depth < 8 && node; depth++, node = node.parentElement) {
                const text = (node.textContent || '').toLowerCase();
                const hasToggleText = text.includes('does not have a product id');
                const hasExternalField = !!node.querySelector('[name^="externally_assigned_product_identifier"], [id^="externally_assigned_product_identifier"], kat-input[name*="externally_assigned_product_identifier"], kat-dropdown[name*="externally_assigned_product_identifier"]');
                if (hasExternalField && (hasToggleText || node.querySelector('kat-toggle, kat-checkbox, input[type="checkbox"], [role="switch"], [role="checkbox"]'))) return node;
            }
        }
        const candidates = Array.from(document.querySelectorAll('kat-checkbox, kat-toggle, label, span, div, p'))
            .filter(el => ((el.textContent || el.getAttribute('label') || el.getAttribute('aria-label') || '').toLowerCase()).includes('does not have a product id'));
        for (const el of candidates) {
            let node = el;
            for (let depth = 0; depth < 8 && node; depth++, node = node.parentElement) {
                if (node.querySelector && node.querySelector('[name^="externally_assigned_product_identifier"], [id^="externally_assigned_product_identifier"], kat-input[name*="externally_assigned_product_identifier"], kat-dropdown[name*="externally_assigned_product_identifier"]')) return node;
                if (node.querySelector && node.querySelector('kat-toggle, kat-checkbox, input[type="checkbox"], [role="switch"], [role="checkbox"]') && (node.textContent || '').toLowerCase().includes('external product id')) return node;
            }
        }
        return null;
    }
    function buttonState() {
        const selectors = [
            'kat-button[data-testid="continue-button"]',
            'kat-button#form-submit-button',
            'kat-button#next-button',
            'kat-button[data-testid="next-button"]',
            'button#next-button'
        ];
        let b = null;
        for (const sel of selectors) {
            b = document.querySelector(sel);
            if (b) break;
        }
        if (!b) {
            b = Array.from(document.querySelectorAll('kat-button,button')).find(x => /^(continue|next|continue to description)$/i.test((x.textContent || x.getAttribute('label') || '').trim()));
        }
        if (!b) return null;
        let innerDisabled = null;
        if (b.shadowRoot) {
            const inner = b.shadowRoot.querySelector('button');
            if (inner) innerDisabled = !!inner.disabled || inner.hasAttribute('disabled');
        }
        return {
            id: b.id || null,
            testid: b.getAttribute('data-testid'),
            text: (b.textContent || b.getAttribute('label') || '').trim(),
            disabledAttr: b.getAttribute('disabled'),
            disabledProp: !!b.disabled,
            ariaDisabled: b.getAttribute('aria-disabled'),
            innerDisabled,
            hidden: b.offsetParent === null
        };
    }
    function pidState() {
        const row = findExternalProductIdRow();
        if (!row) return {found:false, reason:'row_not_found'};
        const t = row.querySelector('kat-toggle, kat-checkbox, input[type="checkbox"], [role="switch"], [role="checkbox"]');
        if (!t) return {found:false, reason:'toggle_not_found', rowText:(row.textContent||'').slice(0,500)};
        let inner = null;
        if (t.shadowRoot) inner = t.shadowRoot.querySelector('input[type="checkbox"], [role="switch"], [role="checkbox"]');
        return {
            found: true,
            tag: t.tagName,
            outerChecked: !!t.checked || t.hasAttribute('checked'),
            outerAria: t.getAttribute('aria-checked') || t.getAttribute('aria-pressed'),
            innerChecked: inner && 'checked' in inner ? !!inner.checked : null,
            innerAria: inner ? (inner.getAttribute('aria-checked') || inner.getAttribute('aria-pressed')) : null,
            rowText: (row.textContent || '').replace(/\s+/g,' ').trim().slice(0,500)
        };
    }
    const row = findExternalProductIdRow();
    const errors = Array.from(document.querySelectorAll('[class*="error"], [aria-invalid="true"], [state="error"], kat-alert'))
        .map(e => ({
            tag: e.tagName,
            id: e.id || null,
            name: e.getAttribute('name'),
            state: e.getAttribute('state'),
            aria: e.getAttribute('aria-invalid'),
            text: (e.textContent || '').replace(/\s+/g,' ').trim().slice(0,300),
            inExternalProductIdRow: !!(row && (e === row || row.contains(e)))
        }))
        .filter(e => e.text || e.state || e.aria)
        .slice(0,100);
    const buttons = Array.from(document.querySelectorAll('kat-button,button'))
        .map(b => ({
            id: b.id || null,
            testid: b.getAttribute('data-testid'),
            text: (b.textContent || b.getAttribute('label') || '').trim(),
            disabledAttr: b.getAttribute('disabled'),
            disabledProp: !!b.disabled,
            ariaDisabled: b.getAttribute('aria-disabled'),
            hidden: b.offsetParent === null
        }))
        .slice(0,100);
    const itemType = (() => {
        const url = new URL(location.href);
        const params = (url.search + '&' + url.hash).toLowerCase();
        const dd = document.querySelector('kat-dropdown#item_type_keyword-0-value, kat-dropdown[name="item_type_keyword-0-value"], kat-dropdown[id*="item_type_keyword"], select[name*="itemTypeKeyword" i]');
        return {
            urlOk: params.includes('itemtype=cell-phone-screen-protectors') || params.includes('cell-phone-screen-protectors'),
            dropdownFound: !!dd,
            dropdownValue: dd ? (dd.value || dd.getAttribute('value') || '') : '',
            bodyMentions: (document.body.innerText || '').toLowerCase().includes('screen protector')
        };
    })();
    return {
        url: location.href,
        title: document.title,
        pid: pidState(),
        actionButton: buttonState(),
        itemType,
        errorCount: errors.length,
        externalProductIdErrorCount: errors.filter(e => e.inExternalProductIdRow).length,
        errors,
        buttons,
        bodyTextHead: (document.body.innerText || '').slice(0,2500)
    };
}"""


def extract_item_name(statement_text: str, brand: str) -> str:
    import re
    m = re.search(r"Item name[：:]\s*(.+)", statement_text or "")
    if m:
        return m.group(1).strip()[:180]
    return f"{brand} Screen Protector"


def main() -> int:
    parser = argparse.ArgumentParser(description="No-submit Add Product safety diagnostic")
    parser.add_argument("--account", required=True, help="account id, e.g. us_store_591 or 591")
    parser.add_argument("--brand", required=True, help="brand name, e.g. JZG")
    parser.add_argument("--site", default="US", help="site code, e.g. US/UK/BE")
    parser.add_argument("--keep-open", action="store_true", help="keep diagnostic tab open")
    args = parser.parse_args()

    account_id = normalize_account_id(args.account)
    brand_name = args.brand
    site = normalize_site(args.site) or "US"

    print(f"[diag] account={account_id} brand={brand_name} site={site}")
    account_meta = ensure_account_exists(account_id, auto_create=True, site=site)
    sync_result = sync_brand_pack_for_account(brand_name, account_id, site=site)
    print(f"[diag] account created={account_meta.get('created')} refreshed={account_meta.get('refreshed')} matched_profile={account_meta.get('matched_profile')}")
    print(f"[diag] synced sku={sync_result.get('sku')}")

    settings, acc, manifest, marketplace, mkt_cfg, cdp_url = load_runtime(account_id, brand_name)
    account_email = resolve_account_email(acc)
    if account_email and update_account_email(account_id, account_email, ROOT / "config" / "accounts.json"):
        print(f"[diag] email backfilled={account_email}")

    site_cfg = (acc.get("marketplace_configs") or {}).get(site, {})
    entry_url = site_cfg.get("entry_url") or acc.get("entry_url")
    item_type_hint = site_cfg.get("item_type_keyword") or acc.get("item_type_keyword", "cell-phone-screen-protectors")
    statement_file = (manifest.get("5461", {}).get("statement_files", {}) or {}).get(site) or (manifest.get("5461", {}).get("statement_files", {}) or {}).get(marketplace)
    statement_text = ""
    if statement_file:
        p = ROOT / "brand_packs" / brand_name / statement_file
        if p.exists():
            statement_text = p.read_text(encoding="utf-8", errors="ignore")
    item_name = extract_item_name(statement_text, brand_name)

    out = ROOT / "runtime" / "evidence" / datetime.now().strftime("%Y-%m-%d") / account_id / brand_name / "no_submit_safety"
    out.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()
        page.set_default_timeout(30000)
        logs: list[str] = []
        page.on("console", lambda msg: logs.append(f"[{msg.type}] {msg.text}"))

        switch_marketplace(page, site)

        try:
            page.goto(entry_url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print(f"[diag] goto warning: {e}")
        time.sleep(8)

        filler = KatalFormFiller(page)
        fill_result = filler.fill_product_identity_form(brand_name, item_name=item_name, item_type_hint=item_type_hint)
        time.sleep(1.5)
        state_after_fill = page.evaluate(JS_STATE)
        helper_state_after_fill = filler._get_new_ui_no_product_id_state()

        # Repeat only idempotent safety helpers. No Continue click.
        second_pid_result = filler._set_new_ui_no_product_id(True)
        ensure_result = filler.ensure_add_product_ready_for_continue(filler.detect_add_product_ui(), item_type_hint=item_type_hint)
        time.sleep(1.5)
        state_after_ensure = page.evaluate(JS_STATE)
        helper_state_after_ensure = filler._get_new_ui_no_product_id_state()

        result = {
            "account_id": account_id,
            "brand_name": brand_name,
            "site": site,
            "entry_url": entry_url,
            "item_type_hint": item_type_hint,
            "item_name": item_name,
            "resolved_email": account_email,
            "sync_result": sync_result,
            "fill_result": fill_result,
            "state_after_fill": state_after_fill,
            "helper_state_after_fill": helper_state_after_fill,
            "second_pid_result": second_pid_result,
            "ensure_result": ensure_result,
            "state_after_ensure": state_after_ensure,
            "helper_state_after_ensure": helper_state_after_ensure,
            "checks": {
                "item_type_ok": bool(fill_result.get("item_type_keyword")) or bool(state_after_ensure.get("itemType", {}).get("urlOk")),
                "no_product_id_on": bool(state_after_ensure.get("pid", {}).get("innerChecked") or state_after_ensure.get("pid", {}).get("outerAria") == "true" or state_after_ensure.get("pid", {}).get("outerChecked")),
                "external_pid_errors": state_after_ensure.get("externalProductIdErrorCount"),
                "action_button_disabled": (state_after_ensure.get("actionButton") or {}).get("disabledAttr") or (state_after_ensure.get("actionButton") or {}).get("innerDisabled"),
                "email_resolved": bool(account_email),
                "no_submit_clicked": True,
            },
        }

        (out / "no_submit_safety.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        (out / "console_logs.txt").write_text("\n".join(logs[-500:]), encoding="utf-8")
        try:
            page.screenshot(path=str(out / "no_submit_safety.png"), full_page=False, timeout=15000)
        except Exception as e:
            print(f"[diag] screenshot failed: {e}")

        print(json.dumps({"out": str(out), **result["checks"]}, ensure_ascii=False, indent=2))

        if not args.keep_open:
            try:
                page.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
