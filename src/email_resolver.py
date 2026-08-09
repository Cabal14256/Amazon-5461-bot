"""Email resolution helpers for Amazon 5461 automation.

Conservative order:
1. explicit account config fields
2. AdsPower profile fields/name/remark text
3. current Seller Central / 5461 page autofill evidence

This module never fetches external services by itself; callers pass in account/profile/page.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable, Optional

try:
    from src.config_loader import resolve_accounts_path
except Exception:
    try:
        from config_loader import resolve_accounts_path
    except Exception:
        def resolve_accounts_path(default_path):
            return Path(default_path)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
INVALID_EMAIL_NEEDLES = (
    "example",
    "test@example",
    "user@example",
    "noreply@",
    "no-reply@",
    "donotreply@",
    "do-not-reply@",
)


def clean_email(value: Any) -> str:
    text = str(value or "").strip().strip("'\"<>[](){}，,;；")
    if not text or "@" not in text:
        return ""
    match = EMAIL_RE.search(text)
    if not match:
        return ""
    email = match.group(0).strip().lower()
    if any(bad in email for bad in INVALID_EMAIL_NEEDLES):
        return ""
    return email


def extract_email_from_texts(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        if isinstance(value, (list, tuple, set)):
            email = extract_email_from_texts(*value)
            if email:
                return email
            continue
        if isinstance(value, dict):
            # Prefer likely account fields first, then scan all values.
            preferred = [
                value.get("email"), value.get("username"), value.get("login"),
                value.get("name"), value.get("profile_name"), value.get("remark"), value.get("note"),
            ]
            email = extract_email_from_texts(*preferred)
            if email:
                return email
            email = extract_email_from_texts(*value.values())
            if email:
                return email
            continue
        email = clean_email(value)
        if email:
            return email
    return ""


def extract_email_from_profile(profile: dict[str, Any] | None) -> str:
    if not profile:
        return ""
    return extract_email_from_texts(
        profile.get("email"),
        profile.get("username"),
        profile.get("login"),
        profile.get("name"),
        profile.get("profile_name"),
        profile.get("remark"),
        profile.get("note"),
        profile,
    )


def resolve_account_email(account: dict[str, Any] | None, profile: dict[str, Any] | None = None) -> str:
    account = account or {}
    return extract_email_from_texts(
        account.get("email"),
        account.get("username"),
        account.get("login"),
        account.get("note"),
        account.get("adspower_name"),
        account.get("adspower_remark"),
        profile,
    )


def update_account_email(account_id: str, email: str, accounts_json_path: str | Path) -> bool:
    """Backfill config/accounts.json email/username if currently empty."""
    email = clean_email(email)
    if not account_id or not email:
        return False
    path = resolve_accounts_path(accounts_json_path)
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        accounts = payload.get("accounts", []) if isinstance(payload, dict) else []
        changed = False
        for acc in accounts:
            if acc.get("account_id") != account_id:
                continue
            if not clean_email(acc.get("email")):
                acc["email"] = email
                changed = True
            if not clean_email(acc.get("username")):
                acc["username"] = email
                changed = True
            break
        if changed:
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return changed
    except Exception:
        return False


def get_autofill_email_from_page(page, wait_sec: float = 1.5) -> str:
    """Best-effort email discovery from the currently loaded Seller Central page."""
    try:
        import time
        if wait_sec:
            time.sleep(wait_sec)
        email = page.evaluate(r"""() => {
            function validEmail(text) {
                if (!text || typeof text !== 'string') return null;
                const m = text.match(/[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}/);
                if (!m) return null;
                const e = m[0].toLowerCase();
                if (/(example|test@|user@|noreply@|no-reply@|donotreply@|do-not-reply@)/i.test(e)) return null;
                return e;
            }

            // 1) 5461 contact email field or its shadow input/autofill value.
            const emailField = document.querySelector('kat-input#contact_info_email_input, kat-input[name="email"], input[type="email"]');
            if (emailField) {
                const direct = validEmail(emailField.value || emailField.getAttribute('value') || '');
                if (direct) return { email: direct, source: 'form_field' };
                const inner = emailField.shadowRoot && emailField.shadowRoot.querySelector('input');
                const innerEmail = inner && validEmail(inner.value || inner.getAttribute('value') || '');
                if (innerEmail) return { email: innerEmail, source: 'form_shadow_field' };
            }

            // 2) Current visible text, including expanded account menus if already present.
            const bodyEmail = validEmail(document.body && document.body.innerText || '');
            if (bodyEmail) return { email: bodyEmail, source: 'body_text' };

            // 3) Storage values.
            for (const store of [window.localStorage, window.sessionStorage]) {
                try {
                    for (let i = 0; i < store.length; i++) {
                        const v = store.getItem(store.key(i));
                        const e = validEmail(v || '');
                        if (e) return { email: e, source: 'browser_storage' };
                    }
                } catch (e) {}
            }

            // 4) Known/simple globals.
            const paths = [
                ['sellerCentral', 'user', 'email'],
                ['amzn', 'user', 'email'],
                ['UserContext', 'email'],
            ];
            for (const path of paths) {
                try {
                    let cur = window;
                    for (const key of path) cur = cur && cur[key];
                    const e = validEmail(cur || '');
                    if (e) return { email: e, source: 'js_global:' + path.join('.') };
                } catch (e) {}
            }
            return { email: '', source: '' };
        }""")
        return clean_email((email or {}).get("email"))
    except Exception:
        return ""
