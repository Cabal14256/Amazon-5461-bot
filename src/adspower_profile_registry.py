#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AdsPower profile discovery and dry-run account sync helpers.

This module intentionally does not create or update AdsPower profiles. It can
query existing profiles, match them to account ids, and compute safe proposed
updates for accounts.json.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml

from src.adspower_backend import create_adspower_client
from src.config_loader import resolve_accounts_path

try:
    from src.email_resolver import extract_email_from_profile
except Exception:  # pragma: no cover - fallback for isolated utility use
    def extract_email_from_profile(profile: dict[str, Any]) -> str:
        text = " ".join(str(profile.get(k) or "") for k in ["email", "username", "login", "name", "profile_name", "remark", "note"])
        m = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", text, re.I)
        return m.group(0) if m else ""

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class ProfileMatch:
    profile: dict[str, Any]
    confidence: int
    matched_by: list[str] = field(default_factory=list)

    def public_profile(self) -> dict[str, Any]:
        p = self.profile
        return {
            "profile_id": p.get("profile_id") or p.get("user_id") or p.get("id"),
            "profile_no": p.get("profile_no") or p.get("serial_number"),
            "name": p.get("name") or p.get("profile_name"),
            "remark": p.get("remark") or p.get("note"),
            "group_id": p.get("group_id"),
            "has_email": bool(p.get("email") or extract_email_from_profile(p)),
            "has_username": bool(p.get("username") or p.get("login")),
            "confidence": self.confidence,
            "matched_by": self.matched_by,
        }


def normalize_account_id(raw: str) -> str:
    raw = str(raw).strip()
    if raw.startswith("us_store_"):
        return raw
    m = re.search(r"(\d{3,})", raw)
    if not m:
        raise ValueError(f"无法从账号中提取数字: {raw}")
    return f"us_store_{m.group(1)}"


def account_num(account_id: str) -> str:
    m = re.search(r"(\d{3,})", str(account_id))
    if not m:
        raise ValueError(f"无法从账号中提取数字: {account_id}")
    return m.group(1)


def load_settings(settings_path: Path | None = None, backend_override: str | None = None) -> dict[str, Any]:
    path = settings_path or ROOT / "config" / "settings.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    data.setdefault("adspower", {})
    if backend_override:
        data["adspower"]["backend"] = backend_override
    return data


def load_accounts(accounts_path: Path | None = None) -> tuple[Path, dict[str, Any]]:
    path = accounts_path or resolve_accounts_path(ROOT / "config" / "accounts.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("accounts", [])
    return path, data


def public_account(acc: dict[str, Any]) -> dict[str, Any]:
    return {
        "account_id": acc.get("account_id"),
        "adspower_profile_id": acc.get("adspower_profile_id"),
        "adspower_name": acc.get("adspower_name"),
        "adspower_remark": acc.get("adspower_remark"),
        "marketplace": acc.get("marketplace"),
        "status": acc.get("status"),
        "has_email": bool(acc.get("email")),
        "has_username": bool(acc.get("username")),
    }


def query_profiles(settings: dict[str, Any], search_value: str | None = None, page_size: int = 200, max_pages: int = 5) -> list[dict[str, Any]]:
    client = create_adspower_client(settings)
    profiles: list[dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        res = client.query_profiles(search_value=search_value, page=page, page_size=page_size)
        batch = list(res.get("profiles") or [])
        profiles.extend(batch)
        total = int(res.get("total") or len(profiles))
        if not batch or len(profiles) >= total or len(batch) < page_size:
            break
    return profiles


def _profile_text(profile: dict[str, Any]) -> str:
    fields = [
        profile.get("profile_id"),
        profile.get("user_id"),
        profile.get("id"),
        profile.get("profile_no"),
        profile.get("serial_number"),
        profile.get("name"),
        profile.get("profile_name"),
        profile.get("remark"),
        profile.get("note"),
        profile.get("username"),
        profile.get("email"),
        profile.get("login"),
    ]
    return " ".join(str(x or "") for x in fields)


def _profile_id(profile: dict[str, Any]) -> str:
    return str(profile.get("profile_id") or profile.get("user_id") or profile.get("id") or "")


def _profile_name(profile: dict[str, Any]) -> str:
    return str(profile.get("name") or profile.get("profile_name") or "")


def match_profile(account_id: str, profiles: Iterable[dict[str, Any]], existing_profile_id: str | None = None) -> ProfileMatch | None:
    num = account_num(account_id)
    candidates: list[ProfileMatch] = []
    for p in profiles:
        score = 0
        reasons: list[str] = []
        pid = _profile_id(p)
        name = _profile_name(p)
        serial = str(p.get("profile_no") or p.get("serial_number") or "")
        remark = str(p.get("remark") or p.get("note") or "")
        username = str(p.get("username") or p.get("email") or p.get("login") or "")
        text = _profile_text(p)

        if existing_profile_id and pid and pid == str(existing_profile_id):
            score += 100
            reasons.append("profile_id_existing")
        if serial == num:
            score += 90
            reasons.append("profile_no_exact")
        if name == num:
            score += 85
            reasons.append("name_exact")
        if re.search(rf"(^|\D){re.escape(num)}(\D|$)", name):
            score += 60
            reasons.append("name_contains_account_num")
        if re.search(rf"(^|\D){re.escape(num)}(\D|$)", remark):
            score += 45
            reasons.append("remark_contains_account_num")
        if re.search(rf"(^|\D){re.escape(num)}(\D|$)", username):
            score += 35
            reasons.append("username_or_email_contains_account_num")
        if re.search(rf"(^|\D){re.escape(num)}(EU|UK|BE|US|MX)?(\D|$)", text, re.I):
            score += 15
            reasons.append("profile_text_contains_account_num")

        if score:
            candidates.append(ProfileMatch(profile=p, confidence=min(score, 100), matched_by=reasons))

    if not candidates:
        return None
    candidates.sort(key=lambda c: c.confidence, reverse=True)
    return candidates[0]


def proposed_account_updates(acc: dict[str, Any], match: ProfileMatch | None, overwrite: bool = False) -> dict[str, Any]:
    if not match:
        return {}
    p = match.profile
    profile_id = _profile_id(p)
    name = _profile_name(p)
    remark = str(p.get("remark") or p.get("note") or "")
    username = str(p.get("username") or p.get("login") or "")
    email = str(p.get("email") or "") or extract_email_from_profile(p)

    proposals: dict[str, Any] = {}

    def maybe(field: str, value: Any) -> None:
        if value is None or value == "":
            return
        if overwrite or not acc.get(field):
            if acc.get(field) != value:
                proposals[field] = value

    maybe("adspower_profile_id", profile_id)
    maybe("adspower_name", name)
    maybe("adspower_remark", remark)
    maybe("username", username or email)
    maybe("email", email or username)
    if profile_id and acc.get("status") == "pending_setup":
        proposals["status"] = "active"
    return proposals


def discover_account(account_id: str, settings: dict[str, Any], accounts_payload: dict[str, Any], page_size: int = 200, max_pages: int = 5, overwrite: bool = False) -> dict[str, Any]:
    account_id = normalize_account_id(account_id)
    accounts = accounts_payload.get("accounts", []) or []
    acc = next((a for a in accounts if a.get("account_id") == account_id), None)
    num = account_num(account_id)
    profiles = query_profiles(settings, search_value=num, page_size=page_size, max_pages=max_pages)
    if not profiles:
        profiles = query_profiles(settings, search_value=None, page_size=page_size, max_pages=max_pages)
    match = match_profile(account_id, profiles, existing_profile_id=(acc or {}).get("adspower_profile_id"))
    proposals = proposed_account_updates(acc or {}, match, overwrite=overwrite) if acc else {}
    return {
        "account_id": account_id,
        "account_exists": bool(acc),
        "account": public_account(acc) if acc else None,
        "profiles_scanned": len(profiles),
        "matched_profile": match.public_profile() if match else None,
        "proposed_updates": proposals,
        "action": "dry_run_update" if proposals else "no_change" if acc else "account_missing",
    }


def audit_accounts(account_ids: list[str], settings: dict[str, Any], accounts_payload: dict[str, Any], page_size: int = 200, max_pages: int = 5, overwrite: bool = False) -> list[dict[str, Any]]:
    return [discover_account(a, settings, accounts_payload, page_size=page_size, max_pages=max_pages, overwrite=overwrite) for a in account_ids]
