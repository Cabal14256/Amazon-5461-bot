#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
完整的 5461 工作流脚本
从 Add Product 自动流转到 5461 提交
支持计划模式分批执行

用法:
    python scripts/run_full_5461_workflow.py <account_id> <brand_name> [--batch-size N] [--dry-run]
    
示例:
    python scripts/run_full_5461_workflow.py us_store_530 OUNNE
    python scripts/run_full_5461_workflow.py us_store_530 OUNNE --batch-size 5
    python scripts/run_full_5461_workflow.py us_store_530 OUNNE --dry-run
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)

import argparse
import json
import time
from pathlib import Path
from datetime import datetime

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from scripts._flow_cli_common import load_runtime
from src.flow_submit_5461 import submit_5461_from_add_product
from src.email_resolver import resolve_account_email, update_account_email
from auto_add_account_data import ensure_account_exists, normalize_site, sync_brand_pack_for_account, normalize_account_num


def resolve_statement_text(project_root: Path, brand_name: str, account_id: str, site: str | None, marketplace: str, manifest: dict, sync_result: dict) -> tuple[str, str]:
    account_num = normalize_account_num(account_id)
    site_norm = normalize_site(site)
    candidates: list[str] = []

    if site_norm:
        suffix = site_norm.lower()
        candidates.extend([
            f"brand_packs/{brand_name}/docs/5461_statement_{suffix}.account_{account_num}.txt",
            f"brand_packs/{brand_name}/docs/5461_statement_{suffix}.txt",
        ])

    for doc_path in sync_result.get("docs_written", []):
        if "5461_statement" in doc_path:
            if site_norm and f"5461_statement_{site_norm.lower()}" not in doc_path:
                continue
            candidates.append(doc_path)

    statement_files = manifest.get("5461", {}).get("statement_files", {}) or {}
    for key in (site_norm, marketplace):
        if key and statement_files.get(key):
            candidates.append(f"brand_packs/{brand_name}/{statement_files[key]}")

    for rel in candidates:
        path = project_root / rel
        if path.exists():
            return path.read_text(encoding="utf-8"), rel
    return "", ""


def load_batch_plan(plan_path: str) -> list:
    """加载分批执行计划"""
    if not Path(plan_path).exists():
        return []
    
    with open(plan_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_batch_plan(plan_path: str, plan: list):
    """保存分批执行计划"""
    with open(plan_path, 'w', encoding='utf-8') as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)


def create_batch_plan(accounts: list, brands: list, batch_size: int = 1, site: str | None = None) -> list:
    """创建分批执行计划"""
    plan = []
    for account in accounts:
        for brand in brands:
            plan.append({
                "account_id": account,
                "brand_name": brand,
                "site": normalize_site(site),
                "status": "pending",  # pending, running, completed, failed
                "result": None,
                "scheduled_at": None,
                "completed_at": None,
                "error": None
            })
    
    # 按 batch_size 分组
    batches = []
    for i in range(0, len(plan), batch_size):
        batch = plan[i:i + batch_size]
        batches.append({
            "batch_no": i // batch_size + 1,
            "items": batch,
            "status": "pending"
        })
    
    return batches


def run_single_workflow(account_id: str, brand_name: str, dry_run: bool = False, site: str | None = None) -> dict:
    """运行单个工作流"""
    print(f"\n{'='*70}")
    print(f"运行工作流: {account_id} / {brand_name}")
    print(f"{'='*70}")
    
    try:
        # 先确保账号存在；不存在时自动补 accounts.json
        account_meta = ensure_account_exists(account_id, auto_create=True, site=site)
        if account_meta["created"]:
            print(f"[AUTO] 已补账号配置: {account_id} | matched_profile={account_meta['matched_profile']}")
        elif account_meta.get("refreshed"):
            print(f"[AUTO] 已刷新账号资料: {account_id} | matched_profile={account_meta['matched_profile']}")

        # 再按当前账号同步 brand pack 文案，确保工作流读取到正确 SKU/账号号段
        sync_result = sync_brand_pack_for_account(brand_name, account_id, site=site)
        print(f"[SYNC] 已同步文案: {sync_result['brand']} / 账号 {sync_result['target_account']} / SKU {sync_result['sku']}")

        if dry_run:
            print("[DRY RUN] 模拟执行模式")
            return {
                "status": "dry_run",
                "account_id": account_id,
                "brand_name": brand_name,
                "note": f"模拟执行成功；已同步 SKU {sync_result['sku']}"
            }

        if not account_meta["account"].get("adspower_profile_id"):
            raise RuntimeError(f"账号 {account_id} 已自动补入 accounts.json，但缺少 adspower_profile_id，无法实际启动工作流")

        # 加载运行时配置
        settings, acc, manifest, marketplace, mkt_cfg, cdp_url = load_runtime(account_id, brand_name)
        
        # 如果指定了 site 参数，覆盖 marketplace
        site_normalized = normalize_site(site)
        original_marketplace = marketplace
        if site_normalized:
            marketplace = site_normalized
            print(f"[OVERRIDE] 使用指定站点: {marketplace} (覆盖账号配置的 {acc.get('marketplace', 'N/A')})")
        
        # 获取配置
        add_product_url = acc.get("entry_url", "")
        
        # 如果站点切换了，更新 URL 域名
        if site_normalized and site_normalized != original_marketplace:
            # 先提取当前域名
            current_domain = None
            if "sellercentral.amazon.com/" in add_product_url and "sellercentral.amazon.com.mx/" not in add_product_url:
                current_domain = "sellercentral.amazon.com"
            elif "sellercentral.amazon.co.uk/" in add_product_url:
                current_domain = "sellercentral.amazon.co.uk"
            elif "sellercentral.amazon.ca/" in add_product_url:
                current_domain = "sellercentral.amazon.ca"

            
            # 根据目标市场确定目标域名
            if marketplace in ("UK", "BE", "NL", "SE", "DE", "FR", "ES", "IT", "PL"):
                target_domain = "sellercentral.amazon.co.uk"
            elif marketplace == "CA":
                target_domain = "sellercentral.amazon.ca"
            elif marketplace == "MX":
                target_domain = "sellercentral.amazon.com"
            else:
                target_domain = "sellercentral.amazon.com"
            
            # 替换域名
            if current_domain and current_domain != target_domain:
                add_product_url = add_product_url.replace(current_domain, target_domain)
                print(f"[OVERRIDE] URL 域名已切换: {current_domain} → {target_domain}")
                # 域名切换时才清理站点特定参数
                import re
                add_product_url = re.sub(r'[&?]recommendedBrowseNodeId=[^&]+', '', add_product_url)
                add_product_url = re.sub(r'[&?]displayPath=[^&]+', '', add_product_url)
                # 确保 URL 格式正确（没有多余的 & 或 ?）
                add_product_url = add_product_url.replace('?&', '?').replace('&&', '&')
                if add_product_url.endswith('&') or add_product_url.endswith('?'):
                    add_product_url = add_product_url[:-1]
                print(f"[OVERRIDE] 已清理站点特定参数，使用通用 URL")
            elif not current_domain:
                add_product_url = f"https://{target_domain}/abis/listing/create/product_identity?productType=SCREEN_PROTECTOR#product_identity"
                print(f"[OVERRIDE] URL 已重建为: {target_domain}")
            else:
                print(f"[OVERRIDE] URL 域名相同，保留所有参数")
        
        upload_files = manifest.get("5461", {}).get("upload_files", [])
        statement_text, statement_source = resolve_statement_text(project_root, brand_name, account_id, site, marketplace, manifest, sync_result)
        
        # 转换上传文件路径
        upload_file_paths = []
        for f in upload_files:
            fp = project_root / "brand_packs" / brand_name / f
            if fp.exists():
                upload_file_paths.append(str(fp))
            else:
                print(f"[警告] 文件不存在: {fp}")
        
        print(f"账户: {account_id}")
        print(f"品牌: {brand_name}")
        print(f"市场: {marketplace}")
        if site:
            print(f"请求站点: {site_normalized}")
        print(f"声明文本长度: {len(statement_text)} 字符")
        if statement_source:
            print(f"声明文件: {statement_source}")
        print(f"上传文件: {upload_file_paths}")
        
        # 获取 item_type_keyword（从账号配置，默认 screen-protectors）
        item_type_keyword = acc.get("item_type_keyword", "cell-phone-screen-protectors")
        
        # 获取账号邮箱（用于 5461 表单）
        account_email = resolve_account_email(acc)
        if account_email and update_account_email(account_id, account_email, project_root / "config" / "accounts.json"):
            acc["email"] = account_email
            if not acc.get("username"):
                acc["username"] = account_email
            print(f"[EMAIL] 已从账号/AdsPower信息解析并回填邮箱: {account_email}")
        
        # 运行完整工作流
        result = submit_5461_from_add_product(
            cdp_url=cdp_url,
            account_id=account_id,
            marketplace=marketplace,
            brand_name=brand_name,
            add_product_url=add_product_url,
            statement_text=statement_text,
            upload_files=upload_file_paths,
            evidence_root=settings["paths"]["evidence_root"],
            require_human_confirm=True,  # 始终要求人工确认提交
            item_type_keyword=item_type_keyword,
            account_email=account_email
        )
        
        return {
            "status": result.get("submit_result", "unknown"),
            "account_id": account_id,
            "brand_name": brand_name,
            "note": result.get("note", ""),
            "case_id": result.get("case_id", ""),
            "evidence_files": result.get("evidence_files", []),
            "steps": result.get("steps", [])
        }
        
    except Exception as e:
        print(f"[错误] {e}")
        return {
            "status": "error",
            "account_id": account_id,
            "brand_name": brand_name,
            "error": str(e)
        }


def run_batch(batches: list, current_batch_no: int = 1, dry_run: bool = False) -> list:
    """运行指定批次"""
    if current_batch_no < 1 or current_batch_no > len(batches):
        print(f"[错误] 无效的批次号: {current_batch_no}")
        return batches
    
    batch = batches[current_batch_no - 1]
    print(f"\n{'#'*70}")
    print(f"执行批次 {current_batch_no} / {len(batches)}")
    print(f"项目数: {len(batch['items'])}")
    print(f"{'#'*70}")
    
    batch["status"] = "running"
    
    for item in batch["items"]:
        if item["status"] == "completed":
            print(f"\n[跳过] {item['account_id']} / {item['brand_name']} 已完成")
            continue
        
        item["status"] = "running"
        item["scheduled_at"] = datetime.now().isoformat()
        
        # 运行工作流
        result = run_single_workflow(
            account_id=item["account_id"],
            brand_name=item["brand_name"],
            dry_run=dry_run,
            site=item.get("site")
        )
        
        # 更新状态
        item["result"] = result
        item["completed_at"] = datetime.now().isoformat()
        
        if result["status"] in ("success", "partial", "dry_run"):
            item["status"] = "completed"
        else:
            item["status"] = "failed"
            item["error"] = result.get("error") or result.get("note", "")
        
        # 批次间延迟
        time.sleep(2)
    
    batch["status"] = "completed"
    return batches


def print_summary(batches: list):
    """打印执行摘要"""
    print(f"\n{'='*70}")
    print("执行摘要")
    print(f"{'='*70}")
    
    total = 0
    completed = 0
    failed = 0
    pending = 0
    
    for batch in batches:
        for item in batch["items"]:
            total += 1
            if item["status"] == "completed":
                completed += 1
            elif item["status"] == "failed":
                failed += 1
            else:
                pending += 1
    
    print(f"总计: {total}")
    print(f"  完成: {completed}")
    print(f"  失败: {failed}")
    print(f"  待执行: {pending}")
    
    if failed > 0:
        print(f"\n失败项目:")
        for batch in batches:
            for item in batch["items"]:
                if item["status"] == "failed":
                    print(f"  - {item['account_id']} / {item['brand_name']}: {item.get('error', '')}")
    
    print(f"{'='*70}")


def main():
    parser = argparse.ArgumentParser(description='运行完整的 5461 工作流')
    parser.add_argument('account_id', help='账户 ID（如 us_store_530）')
    parser.add_argument('brand_name', help='品牌名称（如 OUNNE）')
    parser.add_argument('--batch-size', type=int, default=1, help='每批执行数量（默认 1）')
    parser.add_argument('--dry-run', action='store_true', help='模拟执行模式')
    parser.add_argument('--site', help='指定站点/国家，如 US / UK / DE / MX / BE / NL / SE / FR / ES / IT')
    parser.add_argument('--plan-file', default='data/batch_plan.json', help='计划文件路径')
    parser.add_argument('--continue-batch', type=int, default=1, help='从指定批次继续')
    
    args = parser.parse_args()
    
    print("="*70)
    print("完整的 5461 工作流")
    print("流程: Add Product -> 点击 Apply to sell -> 填写 5461 表单 -> 提交")
    print("="*70)
    
    plan_path = project_root / args.plan_file
    
    # 加载或创建执行计划
    if plan_path.exists():
        print(f"\n加载现有计划: {plan_path}")
        batches = load_batch_plan(str(plan_path))
    else:
        print(f"\n创建新计划...")
        batches = create_batch_plan(
            accounts=[args.account_id],
            brands=[args.brand_name],
            batch_size=args.batch_size,
            site=args.site
        )
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        save_batch_plan(str(plan_path), batches)
        print(f"计划已保存: {plan_path}")
    
    print(f"\n总批次数: {len(batches)}")
    
    # 执行批次
    for batch_no in range(args.continue_batch, len(batches) + 1):
        batches = run_batch(batches, batch_no, dry_run=args.dry_run)
        save_batch_plan(str(plan_path), batches)
        
        # 询问是否继续下一批
        if batch_no < len(batches) and not args.dry_run:
            cont = input(f"\n批次 {batch_no} 完成。是否继续下一批？(y/n): ").strip().lower()
            if cont != 'y':
                print("暂停执行")
                break
    
    # 打印摘要
    print_summary(batches)
    
    print(f"\n计划文件: {plan_path}")
    print("执行完成")


if __name__ == "__main__":
    main()
