#!/usr/bin/env python3
"""Stable command wrapper for Codex and human operators.

This wrapper intentionally exposes a small, predictable command surface instead
of asking agents to choose from many historical scripts.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _python() -> str:
    return sys.executable or "python"


def _ensure_private_accounts_hint() -> None:
    accounts_path = os.getenv("AMAZON5461_ACCOUNTS_PATH") or "runtime/private/accounts.json"
    resolved = (PROJECT_ROOT / accounts_path).resolve() if not Path(accounts_path).is_absolute() else Path(accounts_path)
    if not resolved.exists():
        print(
            "[preflight] 未找到真实账号配置。请创建 runtime/private/accounts.json "
            "或设置 AMAZON5461_ACCOUNTS_PATH。当前路径: " + str(resolved),
            file=sys.stderr,
        )


def _run(cmd: list[str]) -> int:
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    return subprocess.call(cmd, cwd=str(PROJECT_ROOT), env=env)


def cmd_diagnose(args: argparse.Namespace) -> int:
    _ensure_private_accounts_hint()
    cmd = [
        _python(),
        "scripts/diagnose_no_submit_add_product.py",
        "--account",
        args.account,
        "--brand",
        args.brand,
    ]
    if args.site:
        cmd.extend(["--site", args.site])
    return _run(cmd)


def cmd_dry_run(args: argparse.Namespace) -> int:
    _ensure_private_accounts_hint()
    cmd = [
        _python(),
        "scripts/run_full_5461_batch.py",
        "--accounts",
        args.account,
        "--brands",
        args.brand,
        "--dry-run",
        "--state-file",
        args.state_file,
    ]
    if args.site:
        cmd.extend(["--site", args.site])
    return _run(cmd)


def cmd_run(args: argparse.Namespace) -> int:
    if not args.submit:
        print("[safety] Real run requires --submit. Use 'dry-run' for non-submit checks.", file=sys.stderr)
        return 2
    _ensure_private_accounts_hint()
    cmd = [
        _python(),
        "scripts/run_full_5461_batch.py",
        "--accounts",
        args.account,
        "--brands",
        args.brand,
        "--state-file",
        args.state_file,
    ]
    if args.site:
        cmd.extend(["--site", args.site])
    if args.case_followup_delay_hours is not None:
        cmd.extend(["--case-followup-delay-hours", str(args.case_followup_delay_hours)])
    if args.disable_case_followup:
        cmd.append("--disable-case-followup")
    if args.feishu_country_option:
        cmd.extend(["--feishu-country-option", args.feishu_country_option])
    return _run(cmd)


def cmd_case_check(args: argparse.Namespace) -> int:
    _ensure_private_accounts_hint()
    cmd = [
        _python(),
        "scripts/check_case_detail.py",
        "--account",
        args.account,
        "--site",
        args.site,
        "--brand",
        args.brand,
        "--case-id",
        args.case_id,
    ]
    if args.register:
        cmd.append("--register")
    return _run(cmd)


def cmd_followups(args: argparse.Namespace) -> int:
    cmd = [_python(), "scripts/run_case_followups.py"]
    cmd.append("--watch" if args.watch else "--once")
    if args.no_register:
        cmd.append("--no-register")
    return _run(cmd)


def cmd_feishu_bind_check(args: argparse.Namespace) -> int:
    cmd = [
        _python(),
        "scripts/check_feishu_binding.py",
        "--account",
        args.account,
        "--site",
        args.site,
        "--brand",
        args.brand,
        "--sku",
        args.sku,
    ]
    if args.country_option:
        cmd.extend(["--country-option", args.country_option])
    return _run(cmd)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m cli.amazon5461")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--account", required=True, help="Account ID, e.g. us_store_600 or 600")
        p.add_argument("--brand", required=True, help="Brand name, e.g. OUNNE")
        p.add_argument("--site", default=None, help="Marketplace site, e.g. US, UK, MX, DE")

    p = sub.add_parser("diagnose", help="Read-only / no-submit diagnosis")
    add_common(p)
    p.set_defaults(func=cmd_diagnose)

    p = sub.add_parser("dry-run", help="Run the workflow without submitting")
    add_common(p)
    p.add_argument("--state-file", default="runtime/state/batch_state.json")
    p.set_defaults(func=cmd_dry_run)

    p = sub.add_parser("run", help="Real run; requires --submit")
    add_common(p)
    p.add_argument("--submit", action="store_true", help="Required for real submit mode")
    p.add_argument("--state-file", default="runtime/state/batch_state.json")
    p.add_argument("--case-followup-delay-hours", type=float, default=None)
    p.add_argument("--disable-case-followup", action="store_true")
    p.add_argument(
        "--feishu-country-option",
        default=None,
        help="Exact 国家EU option, e.g. 比利时1, used to distinguish repeat applications",
    )
    p.set_defaults(func=cmd_run)

    p = sub.add_parser(
        "case-check",
        help="Inspect a Case and verify an approval; never submits an application",
    )
    p.add_argument("--account", required=True)
    p.add_argument("--brand", required=True)
    p.add_argument("--site", required=True)
    p.add_argument("--case-id", required=True)
    p.add_argument("--register", action="store_true", help="Write a terminal Case result to Excel/SQLite")
    p.set_defaults(func=cmd_case_check)

    p = sub.add_parser("followups", help="Process delayed Case follow-up tasks")
    p.add_argument("--watch", action="store_true", help="Wait for future scheduled tasks")
    p.add_argument("--no-register", action="store_true")
    p.set_defaults(func=cmd_followups)

    p = sub.add_parser("feishu-bind-check", help="Read-only match of one task to an existing Feishu row")
    p.add_argument("--account", required=True)
    p.add_argument("--brand", required=True)
    p.add_argument("--site", required=True)
    p.add_argument("--sku", required=True)
    p.add_argument("--country-option", default="")
    p.set_defaults(func=cmd_feishu_bind_check)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
