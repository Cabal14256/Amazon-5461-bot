#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AdsPower CLI healthcheck for Amazon 5461 automation.

This diagnostic script integrates the `adspower-browser` CLI skill without changing
any 5461 production submission flow. It only reads local account config and queries
AdsPower Local API through the CLI.

Examples:
    python scripts/adspower_cli_healthcheck.py
    python scripts/adspower_cli_healthcheck.py 596
    python scripts/adspower_cli_healthcheck.py --all-accounts
    python scripts/adspower_cli_healthcheck.py --search 596
    python scripts/adspower_cli_healthcheck.py --json
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config_loader import resolve_accounts_path  # noqa: E402

DEFAULT_CLI = "npx adspower-browser"


class AdsCliResult(dict):
    """Dictionary result for a CLI call."""


def _safe_text(value: str | None, max_len: int = 600) -> str:
    if not value:
        return ""
    text = value.strip()
    # Avoid dumping accidental secrets if CLI ever echoes a bearer/API key.
    text = re.sub(r"(?i)(api[-_ ]?key|authorization|bearer)(['\"=: ]+)[^\s,'\"]+", r"\1\2***", text)
    return text[:max_len]


def _split_command(command: str) -> list[str]:
    # Windows-safe enough for the intended simple default and explicit command override.
    # Users can pass --cli "C:\\path\\adspower-browser.cmd" if they need no shell splitting.
    import shlex

    parts = shlex.split(command, posix=False)
    if parts:
        resolved = shutil.which(parts[0])
        if resolved:
            parts[0] = resolved
    return parts


def run_ads_cli(cli: str, args: list[str], timeout: int = 30) -> AdsCliResult:
    cmd = _split_command(cli) + args
    try:
        proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        return AdsCliResult(ok=False, command=" ".join(cmd), returncode=None, stdout="", stderr=str(exc), json=None)
    except subprocess.TimeoutExpired as exc:
        return AdsCliResult(
            ok=False,
            command=" ".join(cmd),
            returncode=None,
            stdout=_safe_text(exc.stdout if isinstance(exc.stdout, str) else None),
            stderr=f"timeout after {timeout}s",
            json=None,
        )

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    parsed = parse_json_from_output(stdout)
    return AdsCliResult(
        ok=proc.returncode == 0,
        command=" ".join(cmd),
        returncode=proc.returncode,
        stdout=_safe_text(stdout),
        stderr=_safe_text(stderr),
        json=parsed,
    )


def parse_json_from_output(text: str) -> Any | None:
    """Best-effort parse for CLI output that may include log lines before JSON."""
    if not text:
        return None
    stripped = text.strip()
    for candidate in (stripped,):
        try:
            return json.loads(candidate)
        except Exception:
            pass
    # Try locating first object/array and parsing suffixes.
    starts = [idx for idx in (stripped.find("{"), stripped.find("[")) if idx >= 0]
    for start in sorted(starts):
        try:
            return json.loads(stripped[start:])
        except Exception:
            continue
    return None


def status(ok: bool, warn: bool = False) -> str:
    if ok:
        return "OK"
    return "WARN" if warn else "FAIL"


def load_accounts() -> tuple[Path, list[dict[str, Any]]]:
    accounts_path = resolve_accounts_path(ROOT / "config" / "accounts.json")
    with accounts_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return accounts_path, list(data.get("accounts", []))


def normalize_account_id(raw: str) -> str:
    raw = str(raw).strip()
    if re.fullmatch(r"\d+", raw):
        return f"us_store_{raw}"
    return raw


def public_account(acc: dict[str, Any]) -> dict[str, Any]:
    """Return non-sensitive account fields for reporting."""
    return {
        "account_id": acc.get("account_id"),
        "adspower_profile_id": acc.get("adspower_profile_id"),
        "adspower_name": acc.get("adspower_name"),
        "adspower_remark": acc.get("adspower_remark"),
        "marketplace": acc.get("marketplace"),
        "has_email": bool(acc.get("email")),
        "has_username": bool(acc.get("username")),
    }


def extract_profiles(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    for key in ("list", "profiles", "items"):
        value = data.get(key) if isinstance(data, dict) else None
        if isinstance(value, list):
            return [p for p in value if isinstance(p, dict)]
    return []


def find_profile_summary(profiles: list[dict[str, Any]], profile_id: str | None) -> dict[str, Any] | None:
    if not profile_id:
        return None
    for p in profiles:
        ids = {
            str(p.get("profile_id") or ""),
            str(p.get("user_id") or ""),
            str(p.get("id") or ""),
        }
        if str(profile_id) in ids:
            return {
                "profile_id": p.get("profile_id") or p.get("user_id") or p.get("id"),
                "profile_no": p.get("profile_no") or p.get("serial_number"),
                "name": p.get("name") or p.get("profile_name"),
                "remark": p.get("remark") or p.get("note"),
                "group_id": p.get("group_id"),
            }
    return None


def add_check(report: dict[str, Any], name: str, ok: bool, details: Any = None, warn: bool = False) -> None:
    s = status(ok, warn=warn)
    report["checks"][name] = {"status": s, "details": details}
    report["summary"][s.lower()] += 1


def build_healthcheck(args: argparse.Namespace) -> dict[str, Any]:
    accounts_path, accounts = load_accounts()
    target_ids = [normalize_account_id(a) for a in args.accounts]
    if args.all_accounts:
        target_ids = [str(a.get("account_id")) for a in accounts if a.get("account_id")]

    by_id = {a.get("account_id"): a for a in accounts}
    selected_accounts = [by_id.get(t) for t in target_ids] if target_ids else []

    report: dict[str, Any] = {
        "script": "scripts/adspower_cli_healthcheck.py",
        "accounts_path": str(accounts_path),
        "cli": args.cli,
        "checks": {},
        "accounts": [],
        "search": None,
        "summary": {"ok": 0, "warn": 0, "fail": 0},
    }

    # `npx` is acceptable if installed; the package itself may be fetched/cached by npm.
    cli_prog = _split_command(args.cli)[0] if args.cli else ""
    cli_available = bool(shutil.which(cli_prog)) if cli_prog != "npx" else bool(shutil.which("npx"))
    add_check(report, "cli_command_available", cli_available, cli_prog)

    version = run_ads_cli(args.cli, ["--version"], timeout=args.timeout)
    add_check(report, "cli_version", version["ok"], {"stdout": version["stdout"], "stderr": version["stderr"]})

    status_result = run_ads_cli(args.cli, ["check-status"], timeout=args.timeout)
    runtime_ok = status_result["ok"] and "not running" not in (status_result["stdout"] + status_result["stderr"]).lower()
    add_check(
        report,
        "adspower_runtime",
        runtime_ok,
        {"stdout": status_result["stdout"], "stderr": status_result["stderr"], "returncode": status_result["returncode"]},
        warn=True,
    )

    profiles: list[dict[str, Any]] = []
    profile_list_result: AdsCliResult | None = None
    if runtime_ok or args.force_list:
        params: dict[str, Any] = {"limit": args.limit, "page": 1}
        if args.search:
            params["name"] = args.search
        profile_list_result = run_ads_cli(args.cli, ["get-browser-list", json.dumps(params, ensure_ascii=False)], timeout=args.timeout)
        profiles = extract_profiles(profile_list_result.get("json"))
        add_check(
            report,
            "browser_list_query",
            profile_list_result["ok"],
            {"profiles_found": len(profiles), "stdout": profile_list_result["stdout"], "stderr": profile_list_result["stderr"]},
            warn=True,
        )
    else:
        add_check(report, "browser_list_query", False, "skipped because AdsPower runtime is not running", warn=True)

    if args.search:
        report["search"] = {"query": args.search, "profiles_found": len(profiles), "profiles": [find_profile_summary([p], p.get("profile_id") or p.get("user_id") or p.get("id")) or p for p in profiles[: args.show_profiles]]}

    for requested, acc in zip(target_ids, selected_accounts):
        if not acc:
            item = {"account_id": requested, "status": "FAIL", "checks": {"account_in_config": {"status": "FAIL", "details": "not found"}}}
            report["accounts"].append(item)
            report["summary"]["fail"] += 1
            continue

        profile_id = acc.get("adspower_profile_id")
        item = {"account": public_account(acc), "checks": {}, "summary": {"ok": 0, "warn": 0, "fail": 0}}

        def account_check(name: str, ok: bool, details: Any = None, warn: bool = False) -> None:
            s = status(ok, warn=warn)
            item["checks"][name] = {"status": s, "details": details}
            item["summary"][s.lower()] += 1

        account_check("account_in_config", True, public_account(acc))
        account_check("adspower_profile_id_present", bool(profile_id), profile_id)

        profile_summary = find_profile_summary(profiles, profile_id) if profiles else None
        if profiles:
            account_check("profile_in_ads_browser_list", bool(profile_summary), profile_summary, warn=True)
        else:
            account_check("profile_in_ads_browser_list", False, "not checked; no browser list available", warn=True)

        if profile_id and runtime_ok and args.active:
            active_result = run_ads_cli(args.cli, ["get-browser-active", str(profile_id)], timeout=args.timeout)
            active_text = (active_result["stdout"] + active_result["stderr"]).lower()
            active_ok = active_result["ok"] and not any(token in active_text for token in ["not running", "error", "fail"])
            account_check(
                "profile_active_query",
                active_ok,
                {"stdout": active_result["stdout"], "stderr": active_result["stderr"], "returncode": active_result["returncode"]},
                warn=True,
            )

        report["accounts"].append(item)
        for k, v in item["summary"].items():
            report["summary"][k] += v

    return report


def print_human(report: dict[str, Any]) -> None:
    print("AdsPower CLI Healthcheck")
    print(f"Project: {ROOT}")
    print(f"Accounts: {report['accounts_path']}")
    print(f"CLI: {report['cli']}")
    print("")
    for name, item in report["checks"].items():
        print(f"[{item['status']}] {name}: {item['details']}")
    if report.get("search"):
        search = report["search"]
        print(f"\nSearch '{search['query']}': {search['profiles_found']} profile(s) shown up to limit")
        for p in search.get("profiles", []):
            print(f"  - {p}")
    for item in report.get("accounts", []):
        acc = item.get("account") or {"account_id": item.get("account_id")}
        print(f"\nAccount: {acc.get('account_id')}")
        for name, check in item.get("checks", {}).items():
            print(f"  [{check['status']}] {name}: {check['details']}")
        if item.get("summary"):
            print(f"  Summary: {item['summary']}")
    print(f"\nSummary: {report['summary']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="AdsPower CLI healthcheck; does not launch or submit Amazon flows")
    parser.add_argument("accounts", nargs="*", help="account ids/numbers, e.g. us_store_596 or 596")
    parser.add_argument("--all-accounts", action="store_true", help="check all accounts in accounts.json")
    parser.add_argument("--active", action="store_true", help="query get-browser-active for selected account profiles")
    parser.add_argument("--search", help="search profile list by AdsPower profile name keyword")
    parser.add_argument("--force-list", action="store_true", help="try get-browser-list even if check-status says runtime is not running")
    parser.add_argument("--limit", type=int, default=200, help="profile list page size, 1-200")
    parser.add_argument("--show-profiles", type=int, default=20, help="max profiles to show for --search")
    parser.add_argument("--cli", default=DEFAULT_CLI, help="AdsPower CLI command, default: npx adspower-browser")
    parser.add_argument("--timeout", type=int, default=30, help="per CLI call timeout seconds")
    parser.add_argument("--json", action="store_true", help="print JSON only")
    args = parser.parse_args()

    if args.limit < 1 or args.limit > 200:
        parser.error("--limit must be between 1 and 200")

    report = build_healthcheck(args)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_human(report)

    return 1 if report["summary"]["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
