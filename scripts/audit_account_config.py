#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audit account config before Amazon 5461 runs.

No browser launch by default. This checks local config, AdsPower profile metadata,
marketplace config, brand pack text/images, and email resolvability.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from auto_add_account_data import ensure_account_exists, normalize_account_id, normalize_site, try_match_adspower_profile, sync_brand_pack_for_account
from src.email_resolver import resolve_account_email, extract_email_from_profile
from scripts._flow_cli_common import load_runtime


def status(ok: bool, warn: bool = False) -> str:
    if ok:
        return "OK"
    return "WARN" if warn else "FAIL"


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit account config for 5461 automation")
    parser.add_argument("account", help="account id, e.g. us_store_591 or 591")
    parser.add_argument("--brand", default="JZG", help="brand to audit brand_pack assets")
    parser.add_argument("--site", default="US", help="site code")
    parser.add_argument("--json", action="store_true", help="print JSON only")
    args = parser.parse_args()

    account_id = normalize_account_id(args.account)
    site = normalize_site(args.site) or "US"
    brand = args.brand

    report: dict[str, Any] = {
        "account_id": account_id,
        "site": site,
        "brand": brand,
        "checks": {},
        "summary": {"ok": 0, "warn": 0, "fail": 0},
    }

    def add(name: str, ok: bool, details: Any = None, warn: bool = False):
        s = status(ok, warn=warn)
        report["checks"][name] = {"status": s, "details": details}
        report["summary"][s.lower()] += 1

    meta = ensure_account_exists(account_id, auto_create=True, site=site)
    acc = meta.get("account") or {}
    profile = try_match_adspower_profile(account_id) or {}
    profile_id = acc.get("adspower_profile_id") or profile.get("profile_id") or profile.get("user_id")

    add("account_exists", bool(acc), {"created": meta.get("created"), "refreshed": meta.get("refreshed")})
    add("adspower_profile_id", bool(profile_id), profile_id)
    add("adspower_profile_metadata", bool(profile), {
        "profile_id": profile.get("profile_id") or profile.get("user_id") or profile.get("id"),
        "name": profile.get("name") or profile.get("profile_name"),
        "remark": profile.get("remark") or profile.get("note"),
        "email": extract_email_from_profile(profile),
    }, warn=True)

    email = resolve_account_email(acc, profile)
    add("email_resolvable", bool(email), {"email": email, "account_email": acc.get("email"), "username": acc.get("username")}, warn=True)

    mp_configs = acc.get("marketplace_configs") or {}
    site_cfg = mp_configs.get(site) or {}
    add("marketplace_config_for_site", bool(site_cfg), site_cfg)
    add("entry_url", bool(site_cfg.get("entry_url") or acc.get("entry_url")), site_cfg.get("entry_url") or acc.get("entry_url"))
    add("item_type_keyword", bool(site_cfg.get("item_type_keyword") or acc.get("item_type_keyword")), site_cfg.get("item_type_keyword") or acc.get("item_type_keyword"))

    try:
        sync_result = sync_brand_pack_for_account(brand, account_id, site=site)
        add("brand_pack_sync", True, sync_result)
    except Exception as e:
        sync_result = {}
        add("brand_pack_sync", False, str(e))

    try:
        settings, acc2, manifest, marketplace, mkt_cfg, cdp_url = load_runtime(account_id, brand)
        statement_file = (manifest.get("5461", {}).get("statement_files", {}) or {}).get(site) or (manifest.get("5461", {}).get("statement_files", {}) or {}).get(marketplace)
        statement_path = ROOT / "brand_packs" / brand / statement_file if statement_file else None
        upload_files = manifest.get("5461", {}).get("upload_files", [])
        missing_uploads = [f for f in upload_files if not (ROOT / "brand_packs" / brand / f).exists()]
        add("statement_file", bool(statement_path and statement_path.exists()), str(statement_path) if statement_path else None)
        add("upload_files", bool(upload_files) and not missing_uploads, {"count": len(upload_files), "missing": missing_uploads})
        add("cdp_url_available", bool(cdp_url), "present" if cdp_url else "missing", warn=True)
    except Exception as e:
        add("runtime_load", False, str(e))

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"账号体检: {account_id} / {site} / {brand}")
        for name, item in report["checks"].items():
            print(f"[{item['status']}] {name}: {item['details']}")
        print("Summary:", report["summary"])

    return 1 if report["summary"]["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
