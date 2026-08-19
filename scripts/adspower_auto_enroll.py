#!/usr/bin/env python3
"""自动登记 AdsPower 新环境到 runtime/private/accounts.json（只登记账号，不生成品牌文案）。

逻辑：
1. 通过 AdsPower 本地 API 扫描全部环境；
2. 从环境 名称/备注 中提取账号编号（3 位以上数字）；
3. 与 accounts.json 对比（按 account_id 与已绑定 profile_id 双重判重），找出未登记环境，
   以及可唯一补齐 profile 的 pending_setup 账号；
4. --apply 时登记新账号或补齐已有 pending_setup 账号：
   默认 marketplace=US，marketplace_configs 自动包含全部站点（US/MX/UK/BE/NL/SE/DE/FR/ES/IT，
   即默认覆盖 US + EU），status 按 profile 匹配结果置为 active/pending_setup。

默认 dry-run，只输出报告不写文件；--apply 才会修改 accounts.json（写入前先备份）。
拿不准的一律跳过并列入报告：无编号、多个编号、同一编号对应多个未绑定环境。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import auto_add_account_data as aad  # noqa: E402

NUM_RE = re.compile(r"(\d{3,})")


def profile_id_of(profile: dict[str, Any]) -> str:
    return str(profile.get("user_id") or profile.get("profile_id") or profile.get("id") or "").strip()


def profile_label(profile: dict[str, Any]) -> str:
    """非敏感标识：名称 + 备注，用于报告。"""
    name = str(profile.get("name") or profile.get("profile_name") or "").strip()
    remark = str(profile.get("remark") or profile.get("note") or "").strip()
    return " | ".join(part for part in [name, remark] if part) or "(unnamed)"


def extract_numbers(profile: dict[str, Any]) -> list[str]:
    """从名称/备注中提取账号编号候选；不扫 username/email，避免邮箱里的无关数字误匹配。"""
    text = " ".join(
        str(profile.get(k) or "") for k in ("name", "profile_name", "remark", "note")
    )
    return sorted(set(NUM_RE.findall(text)))


def discover_new_accounts(profiles: list[dict[str, Any]]) -> dict[str, Any]:
    """Classify profiles, including safe refreshes for existing pending accounts."""
    payload = aad.load_accounts_payload()
    accounts = payload.get("accounts", [])
    known_accounts = {
        str(a.get("account_id") or ""): a
        for a in accounts
        if str(a.get("account_id") or "")
    }
    known_ids = set(known_accounts)
    bound_pids = {str(a.get("adspower_profile_id") or "") for a in accounts} - {""}

    by_num: dict[str, list[dict[str, Any]]] = {}
    refresh_by_num: dict[str, list[dict[str, Any]]] = {}
    exists: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    no_number: list[str] = []

    for profile in profiles:
        pid = profile_id_of(profile)
        label = profile_label(profile)
        if pid and pid in bound_pids:
            exists.append({"profile": label, "reason": "profile_id 已绑定"})
            continue
        nums = extract_numbers(profile)
        if not nums:
            no_number.append(label)
            continue
        if len(nums) > 1:
            by_num.setdefault("__multi__", []).append(profile)
            continue
        num = nums[0]
        account_id = aad.normalize_account_id(num)
        if account_id in known_ids:
            account = known_accounts[account_id]
            if str(account.get("adspower_profile_id") or "").strip():
                exists.append({"profile": label, "account_id": account_id, "reason": "account_id 已存在"})
            elif not pid:
                ambiguous.append({"account_num": num, "reason": "匹配环境缺少 profile_id"})
            else:
                refresh_by_num.setdefault(num, []).append(profile)
            continue
        by_num.setdefault(num, []).append(profile)

    new_accounts: list[dict[str, Any]] = []
    for num, group in sorted(by_num.items()):
        if num == "__multi__":
            for p in group:
                ambiguous.append({"profile": profile_label(p), "reason": f"含多个编号: {extract_numbers(p)}"})
            continue
        if len(group) > 1:
            ambiguous.append({
                "account_num": num,
                "reason": f"同一编号对应 {len(group)} 个未绑定环境",
                "profiles": [profile_label(p) for p in group],
            })
            continue
        new_accounts.append({
            "account_num": num,
            "account_id": aad.normalize_account_id(num),
            "profile": profile_label(group[0]),
            "profile_id": profile_id_of(group[0]),
        })

    refresh_accounts: list[dict[str, Any]] = []
    for num, group in sorted(refresh_by_num.items()):
        if len(group) > 1:
            ambiguous.append({
                "account_num": num,
                "reason": f"已有未绑定账号对应 {len(group)} 个环境",
                "profiles": [profile_label(p) for p in group],
            })
            continue
        refresh_accounts.append({
            "account_num": num,
            "account_id": aad.normalize_account_id(num),
            "profile": profile_label(group[0]),
            "profile_id": profile_id_of(group[0]),
        })

    return {
        "profiles_scanned": len(profiles),
        "accounts_registered": len(accounts),
        "new": new_accounts,
        "refresh": refresh_accounts,
        "exists": exists,
        "ambiguous": ambiguous,
        "no_number": no_number,
    }


def backup_accounts() -> Path | None:
    path = aad.ACCOUNTS_JSON_PATH
    if not path.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.stem}.bak-{stamp}{path.suffix}")
    shutil.copy2(path, backup)
    return backup


def main() -> int:
    parser = argparse.ArgumentParser(description="自动登记 AdsPower 新环境到 accounts.json（默认 dry-run）")
    parser.add_argument("--apply", action="store_true", help="实际写入 accounts.json（默认只输出报告）")
    parser.add_argument("--json", action="store_true", help="只输出 JSON 报告")
    args = parser.parse_args()

    profiles = aad._query_adspower_profiles_direct(None)
    if not profiles:
        report = {"error": "AdsPower API 无返回（AdsPower 未启动或 API 配置不可用）"}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1

    report = discover_new_accounts(profiles)

    if args.apply and (report["new"] or report["refresh"]):
        backup = backup_accounts()
        report["backup"] = str(backup) if backup else None
        enrolled, refreshed, failed = [], [], []
        for item in report["new"]:
            try:
                meta = aad.ensure_account_exists(item["account_num"], auto_create=True, site=None)
                acc = meta["account"]
                enrolled.append({
                    "account_id": acc.get("account_id"),
                    "marketplace": acc.get("marketplace"),
                    "status": acc.get("status"),
                    "matched_profile": meta.get("matched_profile"),
                })
            except Exception as exc:  # 单个失败不阻断其余登记
                failed.append({"account_id": item["account_id"], "error": str(exc)})
        profiles_by_id = {profile_id_of(profile): profile for profile in profiles}
        for item in report["refresh"]:
            try:
                meta = aad.refresh_existing_account_from_profile(
                    item["account_id"], profiles_by_id[item["profile_id"]]
                )
                acc = meta["account"]
                refreshed.append({
                    "account_id": acc.get("account_id"),
                    "marketplace": acc.get("marketplace"),
                    "status": acc.get("status"),
                })
            except Exception as exc:
                failed.append({"account_id": item["account_id"], "error": str(exc)})
        report["enrolled"] = enrolled
        report["refreshed"] = refreshed
        report["failed"] = failed
    elif args.apply:
        report["enrolled"] = []
        report["refreshed"] = []
        report["failed"] = []

    report["mode"] = "apply" if args.apply else "dry-run"

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"AdsPower 自动登记（{report['mode']}）")
        print(f"  扫描环境: {report['profiles_scanned']}  已登记账号: {report['accounts_registered']}")
        print(f"  新登记候选: {len(report['new'])}")
        for item in report["new"]:
            print(f"    + {item['account_id']}  <- {item['profile']}")
        for item in report.get("enrolled", []):
            print(f"    ✓ {item['account_id']}  marketplace={item['marketplace']} status={item['status']}"
                  f"{' (matched AdsPower profile)' if item['matched_profile'] else ''}")
        for item in report.get("refreshed", []):
            print(f"    ✓ 已补齐 {item['account_id']}  marketplace={item['marketplace']} status={item['status']}")
        for item in report.get("failed", []):
            print(f"    ✗ {item['account_id']}  登记失败: {item['error']}")
        if report["ambiguous"]:
            print(f"  需人工确认（未写入）: {len(report['ambiguous'])}")
            for item in report["ambiguous"]:
                print(f"    ? {item.get('profile') or item.get('account_num')}: {item['reason']}")
        if report["no_number"]:
            print(f"  无编号跳过: {len(report['no_number'])}（环境名称/备注中未找到账号编号）")
        if not args.apply and report["new"]:
            print("\n这是 dry-run，未修改任何文件。确认无误后加 --apply 执行登记。")
        elif args.apply and report.get("backup"):
            print(f"\naccounts.json 已更新，备份: {report['backup']}")

    return 0 if not report.get("failed") else 2


if __name__ == "__main__":
    sys.exit(main())
