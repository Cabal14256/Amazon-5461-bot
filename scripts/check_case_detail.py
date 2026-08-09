#!/usr/bin/env python3
"""Run one generic, no-application-submit Amazon 5461 Case inspection."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.case_followup import check_case_detail, record_case_outcome  # noqa: E402
from src.config_loader import load_yaml  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect one Seller Central 5461 Case detail")
    parser.add_argument("--account", required=True)
    parser.add_argument("--site", required=True)
    parser.add_argument("--brand", required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--register", action="store_true", help="Write a terminal result to Excel/SQLite")
    args = parser.parse_args(argv)

    settings = load_yaml(str(PROJECT_ROOT / "config" / "settings.yaml"))
    result = check_case_detail(args.account, args.site, args.brand, args.case_id, settings=settings)
    if args.register and result.get("result") in {
        "approved", "false_approved", "declined", "action_required", "answered_unknown"
    }:
        record_case_outcome(
            settings,
            {
                "account_id": args.account,
                "marketplace": args.site.upper(),
                "brand_name": args.brand,
                "case_id": str(args.case_id),
            },
            result,
        )

    print(f"Case ID: {args.case_id}")
    print(f"Case status: {result.get('case_status') or 'unknown'}")
    print(f"Result: {result.get('result')}")
    print(f"Reason: {result.get('decision_reason')}")
    approval_verification = result.get("approval_verification") or {}
    if approval_verification:
        print(f"Manage Brand: {(approval_verification.get('manage_brand') or {}).get('result')}")
        print(f"Add Product: {(approval_verification.get('add_product') or {}).get('result')}")
    print(f"Evidence: {result.get('evidence_dir')}")
    if result.get("result") == "error":
        return 1
    if result.get("result") == "blocked":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
