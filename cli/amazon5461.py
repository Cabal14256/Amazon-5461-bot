#!/usr/bin/env python3
"""Stable command wrapper for Codex and human operators.

This wrapper intentionally exposes a small, predictable command surface instead
of asking agents to choose from many historical scripts.
"""

from __future__ import annotations

import argparse
import json
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


def cmd_case_id_recoveries(args: argparse.Namespace) -> int:
    cmd = [_python(), "scripts/run_case_id_recoveries.py"]
    cmd.append("--watch" if args.watch else "--once")
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


def _normalized_account_id(value: str) -> str:
    from auto_add_account_data import normalize_account_id

    return normalize_account_id(str(value).strip())


def cmd_reapply_start(args: argparse.Namespace) -> int:
    from src.config_loader import load_account, load_brand_manifest, load_yaml
    from src.reapplication import create_campaign, launch_reapplication_worker, resolve_route

    settings = load_yaml(str(PROJECT_ROOT / "config" / "settings.yaml"))
    account_id = _normalized_account_id(args.account)
    route = resolve_route(settings, args.region)
    start_site = str(args.start_site or route[0]).upper()
    if start_site not in route:
        print(f"[preflight] {start_site} 不在 {args.region.upper()} 路线中", file=sys.stderr)
        return 2
    try:
        account = load_account("config/accounts.json", account_id)
        load_brand_manifest("brand_packs", args.brand)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(f"[preflight] 账号或品牌包未就绪: {exc}", file=sys.stderr)
        return 2
    if not str(account.get("adspower_profile_id") or "").strip():
        print("[preflight] 账号缺少 AdsPower profile ID", file=sys.stderr)
        return 2

    route_text = " → ".join(route[route.index(start_site) :])
    if not args.submit:
        print(
            f"[dry-run] 将创建重申请活动: {account_id} / {args.brand} / "
            f"{args.region.upper()} / {route_text}"
        )
        print("[dry-run] 未提供 --submit，未写数据库、未启动浏览器、未提交申请")
        return 0
    if not args.yes:
        print("[safety] 创建真实重申请活动还需要 --yes", file=sys.stderr)
        return 2

    campaign = create_campaign(
        settings,
        account_id,
        args.brand,
        args.region,
        submit_authorized=True,
        start_site=start_site,
    )
    print(
        f"[Reapplication] campaign={campaign['id']} status={campaign['status']} "
        f"route={route_text}"
    )
    if not args.no_worker:
        worker = launch_reapplication_worker(settings)
        if worker.get("started"):
            print(f"[Reapplication] 后台 worker 已启动，PID={worker.get('pid')}")
        else:
            print(f"[Reapplication] worker 未新启: {worker.get('reason')}")
    return 0


def cmd_reapply_worker(args: argparse.Namespace) -> int:
    command = [_python(), "scripts/run_reapplications.py"]
    command.append("--watch" if args.watch else "--once")
    return _run(command)


def cmd_reapply_status(_args: argparse.Namespace) -> int:
    from src.config_loader import load_yaml
    from src.reapplication import list_campaigns

    settings = load_yaml(str(PROJECT_ROOT / "config" / "settings.yaml"))
    campaigns = list_campaigns(settings)
    if not campaigns:
        print("[Reapplication] 暂无活动")
        return 0
    for campaign in campaigns:
        route = " → ".join(campaign["route"])
        current = campaign["route"][int(campaign["current_route_index"])]
        print(
            f"campaign={campaign['id']} account={campaign['account_id']} "
            f"brand={campaign['brand_name']} region={campaign['region']} "
            f"status={campaign['status']} current={current} route={route}"
        )
    return 0


def cmd_repair_uat(args: argparse.Namespace) -> int:
    """Run the migration/evidence/switch audit before one read-only triage."""
    from src.codex_client.availability import check_availability
    from src.codex_client.triage import run_triage
    from src.db import (
        INCIDENT_REPAIR_CLASSES,
        count_triage_jobs_today,
        get_incident,
        init_db,
        record_web_audit,
        recover_stale_triage_jobs,
    )
    from src.web.config import load_settings

    settings = load_settings()
    incident_id = int(args.incident_id)
    checks: list[dict] = []
    outcome = "internal_error"
    exit_code = 1
    triage_outcome = None
    try:
        init_db(str(settings.db_path))
        recover_stale_triage_jobs(str(settings.db_path))
        try:
            from src.repair.release_recovery import reconcile_git_operations

            reconcile_git_operations(settings)
        except ImportError:
            pass

        incident = get_incident(str(settings.db_path), incident_id)
        checks.append({"name": "incident_exists", "ok": incident is not None})
        if incident is None:
            outcome, exit_code = "unknown_incident", 2
        else:
            required_checks = (
                ("incidents_enabled", bool(settings.incidents_enabled)),
                ("codex_enabled", bool(settings.codex_enabled)),
                ("release_disabled", not bool(settings.codex_release_enabled)),
                ("repair_class", incident["classification"] in INCIDENT_REPAIR_CLASSES),
                ("incident_open", incident["status"] == "open"),
                ("evidence_ready", incident.get("evidence_status") == "ready"),
                (
                    "daily_quota",
                    count_triage_jobs_today(str(settings.db_path))
                    < int(settings.codex_daily_call_limit),
                ),
            )
            checks.extend({"name": name, "ok": ok} for name, ok in required_checks)
            availability = check_availability(settings.codex_command)
            checks.append({"name": "codex_available", "ok": bool(availability.available)})
            failed = [check["name"] for check in checks if not check["ok"]]
            if failed:
                outcome, exit_code = "gate_failed", 2
            elif args.check_only:
                outcome, exit_code = "check_ok", 0
            else:
                triage_outcome = run_triage(
                    settings,
                    incident_id,
                    trigger="uat_cli",
                    availability=availability,
                )
                outcome = str(triage_outcome.get("outcome") or "error")
                exit_code = 0 if outcome == "ok" else 2 if outcome in {
                    "evidence_incomplete", "disabled", "quota_exceeded", "unavailable"
                } else 1
    except Exception as exc:  # never leave UAT without a terminal audit
        outcome, exit_code = "internal_error", 1
        checks.append({"name": "internal_error", "ok": False, "error": type(exc).__name__})

    detail = {
        "check_only": bool(args.check_only),
        "outcome": outcome,
        "checks": checks,
        "triage_job_id": (
            (triage_outcome or {}).get("job", {}).get("id")
            if isinstance((triage_outcome or {}).get("job"), dict)
            else None
        ),
    }
    try:
        record_web_audit(
            str(settings.db_path),
            action="repair_uat",
            target_type="repair_incident",
            target_id=str(incident_id),
            result=outcome,
            detail=json.dumps(detail, ensure_ascii=False),
        )
    except Exception:
        exit_code = 1
        outcome = "audit_failed"
        detail["outcome"] = outcome
    if args.json:
        print(json.dumps(detail, ensure_ascii=False))
    else:
        print(f"[Repair UAT] incident={incident_id} outcome={outcome}")
        for check in checks:
            print(f"  {'PASS' if check['ok'] else 'FAIL'} {check['name']}")
    return exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m cli.amazon5461")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--account", required=True, help="Account ID, e.g. us_store_600 or 600")
        p.add_argument("--brand", required=True, help="Brand name, e.g. DEMO_ORBIT")
        p.add_argument("--site", default=None, help="Marketplace site, e.g. US, UK, MX, DE")

    p = sub.add_parser("diagnose", help="Read-only / no-submit diagnosis")
    add_common(p)
    p.set_defaults(func=cmd_diagnose)

    p = sub.add_parser(
        "dry-run",
        help="Open a real browser and walk the full form up to (not including) the submit click; screenshots saved, takes minutes",
    )
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

    p = sub.add_parser(
        "case-id-recoveries",
        help="Recover missing Case IDs from View Selling Applications",
    )
    p.add_argument("--watch", action="store_true", help="Wait for future recovery tasks")
    p.set_defaults(func=cmd_case_id_recoveries)

    p = sub.add_parser("feishu-bind-check", help="Read-only match of one task to an existing Feishu row")
    p.add_argument("--account", required=True)
    p.add_argument("--brand", required=True)
    p.add_argument("--site", required=True)
    p.add_argument("--sku", required=True)
    p.add_argument("--country-option", default="")
    p.set_defaults(func=cmd_feishu_bind_check)

    p = sub.add_parser("reapply-start", help="Create a finite cross-market reapplication campaign")
    p.add_argument("--account", required=True)
    p.add_argument("--brand", required=True)
    p.add_argument("--region", required=True, choices=["NA", "EU", "na", "eu"])
    p.add_argument("--start-site", default=None, help="Optional route starting site")
    p.add_argument("--submit", action="store_true", help="Authorize real Seller Central submissions")
    p.add_argument("--yes", action="store_true", help="Required confirmation for a real campaign")
    p.add_argument("--no-worker", action="store_true", help="Create the campaign without starting its worker")
    p.set_defaults(func=cmd_reapply_start)

    p = sub.add_parser("reapply-worker", help="Process authorized reapplication attempts")
    p.add_argument("--watch", action="store_true")
    p.set_defaults(func=cmd_reapply_worker)

    p = sub.add_parser("reapply-status", help="List local reapplication campaign states")
    p.set_defaults(func=cmd_reapply_status)

    p = sub.add_parser("repair-uat", help="Migrate, gate-check and optionally run one read-only Codex triage")
    p.add_argument("--incident-id", type=int, required=True)
    p.add_argument("--check-only", action="store_true", help="Run gates and audit without creating a repair job")
    p.add_argument("--json", action="store_true", help="Emit one machine-readable JSON result")
    p.set_defaults(func=cmd_repair_uat)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
