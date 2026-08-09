#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Discover/audit AdsPower profiles and compute dry-run account sync updates.

No external mutations are performed: this script does not create/update AdsPower
profiles and does not write accounts.json. It is intended as stage-3 discovery,
audit, and dry-run sync tooling.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.adspower_profile_registry import (  # noqa: E402
    audit_accounts,
    discover_account,
    load_accounts,
    load_settings,
    normalize_account_id,
)


def print_result_human(report: dict[str, Any]) -> None:
    print("AdsPower Profile Sync (dry-run only)")
    print(f"Project: {ROOT}")
    print(f"Accounts: {report['accounts_path']}")
    print(f"Backend: {report['backend']}")
    print(f"Mode: {report['mode']}")
    if report.get("error"):
        print(f"ERROR: {report['error']}")
        return
    for item in report.get("results", []):
        print(f"\nAccount: {item['account_id']}")
        print(f"  Account exists: {item['account_exists']}")
        if item.get("account"):
            print(f"  Config: {item['account']}")
        print(f"  Profiles scanned: {item['profiles_scanned']}")
        print(f"  Matched profile: {item['matched_profile']}")
        print(f"  Proposed updates: {item['proposed_updates']}")
        print(f"  Action: {item['action']}")
    summary = report.get("summary", {})
    print(f"\nSummary: {summary}")
    if summary.get("proposed_update_accounts"):
        print("\nNote: this was a dry-run. No files or AdsPower profiles were changed.")


def build_targets(args: argparse.Namespace, accounts_payload: dict[str, Any]) -> list[str]:
    if args.audit_all:
        return [a.get("account_id") for a in accounts_payload.get("accounts", []) if a.get("account_id")]
    targets = []
    if args.discover:
        targets.append(args.discover)
    if args.sync:
        targets.append(args.sync)
    targets.extend(args.accounts or [])
    # Keep order, de-duplicate normalized IDs.
    seen = set()
    normalized = []
    for t in targets:
        aid = normalize_account_id(t)
        if aid not in seen:
            seen.add(aid)
            normalized.append(aid)
    return normalized


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "accounts_checked": len(results),
        "matched_accounts": sum(1 for r in results if r.get("matched_profile")),
        "proposed_update_accounts": sum(1 for r in results if r.get("proposed_updates")),
        "missing_accounts": sum(1 for r in results if not r.get("account_exists")),
        "actions": {k: sum(1 for r in results if r.get("action") == k) for k in sorted({r.get("action") for r in results})},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="AdsPower profile discovery/audit/dry-run sync; no writes")
    parser.add_argument("accounts", nargs="*", help="account ids/numbers, e.g. us_store_596 or 596")
    parser.add_argument("--discover", help="discover one account/profile match")
    parser.add_argument("--sync", help="compute dry-run account config updates for one account")
    parser.add_argument("--audit-all", action="store_true", help="audit all accounts in accounts.json")
    parser.add_argument("--backend", choices=["api", "cli"], help="override adspower.backend from settings.yaml")
    parser.add_argument("--settings", type=Path, help="settings.yaml path")
    parser.add_argument("--accounts-path", type=Path, help="accounts.json path; defaults to active resolver")
    parser.add_argument("--page-size", type=int, default=200, help="AdsPower profile page size, 1-200")
    parser.add_argument("--max-pages", type=int, default=5, help="max profile pages to scan")
    parser.add_argument("--overwrite", action="store_true", help="include updates that would overwrite non-empty config fields in dry-run output")
    parser.add_argument("--json", action="store_true", help="print JSON only")
    args = parser.parse_args()

    if args.page_size < 1 or args.page_size > 200:
        parser.error("--page-size must be between 1 and 200")
    if not (args.audit_all or args.discover or args.sync or args.accounts):
        parser.error("provide --discover, --sync, --audit-all, or account ids")

    accounts_path, accounts_payload = load_accounts(args.accounts_path)
    settings = load_settings(args.settings, backend_override=args.backend)
    backend = (settings.get("adspower") or {}).get("backend", "api")
    targets = build_targets(args, accounts_payload)
    mode = "audit_all" if args.audit_all else "sync_dry_run" if args.sync else "discover"

    report: dict[str, Any] = {
        "script": "scripts/adspower_profile_sync.py",
        "mode": mode,
        "backend": backend,
        "accounts_path": str(accounts_path),
        "dry_run": True,
        "results": [],
        "summary": {},
    }

    try:
        if args.audit_all:
            results = audit_accounts(targets, settings, accounts_payload, page_size=args.page_size, max_pages=args.max_pages, overwrite=args.overwrite)
        else:
            results = [
                discover_account(t, settings, accounts_payload, page_size=args.page_size, max_pages=args.max_pages, overwrite=args.overwrite)
                for t in targets
            ]
        report["results"] = results
        report["summary"] = summarize(results)
    except Exception as exc:
        report["error"] = str(exc)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print_result_human(report)
        return 1

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_result_human(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
