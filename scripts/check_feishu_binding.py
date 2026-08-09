#!/usr/bin/env python3
"""Read-only check for binding one 5461 task to an existing Feishu row."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.config_loader import load_yaml  # noqa: E402
from src.feishu_bitable import bind_case_to_record  # noqa: E402


def _masked_record_id(value: str) -> str:
    value = str(value or "")
    if not value:
        return ""
    return "***" + value[-6:]


def main() -> int:
    parser = argparse.ArgumentParser(description="只读检查飞书现有记录绑定")
    parser.add_argument("--account", required=True)
    parser.add_argument("--site", required=True)
    parser.add_argument("--brand", required=True)
    parser.add_argument("--sku", required=True)
    parser.add_argument("--country-option", default="", help="国家EU原始选项，例如 比利时1")
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env", override=False)
    settings = load_yaml(str(PROJECT_ROOT / "config" / "settings.yaml"))
    result = bind_case_to_record(
        settings,
        account_id=args.account,
        site=args.site,
        brand_name=args.brand,
        sku=args.sku,
        country_option=args.country_option,
    )
    print(f"Binding status: {result.get('status')}")
    print(f"Reason: {result.get('reason')}")
    print(f"Candidate count: {result.get('candidate_count', 0)}")
    if result.get("country_option"):
        print(f"Country option: {result['country_option']}")
    if result.get("record_id"):
        print(f"Record ID: {_masked_record_id(result['record_id'])}")
    print("No Feishu record was changed.")
    return 0 if result.get("status") == "bound" else 1


if __name__ == "__main__":
    raise SystemExit(main())

