#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量 5461 工作流脚本
支持多账户、多品牌批量执行，并在运行前自动：
- 补 accounts.json 中缺失账号
- 为当前账号同步品牌文案 / SKU
- 支持 --brands all

用法:
    python scripts/run_full_5461_batch.py --accounts us_store_530,us_store_529 --brands OUNNE,WILLONE
    python scripts/run_full_5461_batch.py --accounts us_store_543 --brands all
    python scripts/run_full_5461_batch.py --accounts 550 --brands mocodi,HOMEMO --dry-run
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)

import argparse
import json
import random
import re
import time
from pathlib import Path
from datetime import datetime

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from scripts._flow_cli_common import load_runtime
from src.adspower_backend import create_adspower_client


def _cdp_connect(playwright_instance, cdp_url: str, timeout: int = 30):
    """connect_over_cdp — direct call (sync_api must run on same thread as playwright start)."""
    return playwright_instance.chromium.connect_over_cdp(cdp_url, timeout=max(1, int(timeout)) * 1000)
from src.flow_submit_5461 import submit_5461_from_add_product
from src.email_resolver import resolve_account_email, update_account_email
from src.marketplace_switcher import MARKETPLACE_CONFIG, MarketplaceRegion
from auto_add_account_data import ensure_account_exists, normalize_account_id, normalize_site, normalize_account_num, sync_brand_pack_for_account

PICTURE_ROOT = project_root / "picture"


def sellercentral_home_url(site: str | None) -> str:
    """Return the Seller Central home URL for a marketplace region.

    After a batch finishes, leave the AdsPower profile on a neutral home page
    rather than an application/form page.  Per operating convention, NA/US-style
    marketplaces use sellercentral.amazon.com and EU marketplaces use
    sellercentral.amazon.co.uk.
    """
    site_norm = normalize_site(site)
    info = MARKETPLACE_CONFIG.get(site_norm or "")
    if info and info.region == MarketplaceRegion.EU:
        return "https://sellercentral.amazon.co.uk/home"
    return "https://sellercentral.amazon.com/home"


def group_items_all_completed(batch: dict, group_key: tuple[str, str | None] | None) -> bool:
    if not group_key:
        return False
    account_id, site = group_key
    group_items = [
        item for item in batch.get("items", [])
        if item.get("account_id") == account_id and item.get("site") == site
    ]
    return bool(group_items) and all(item.get("status") in ("completed", "skipped") for item in group_items)


def navigate_group_home(context, anchor_page, group_key: tuple[str, str | None] | None, batch: dict) -> None:
    """Navigate the retained group page to the appropriate Seller Central home.

    Only runs when all items in the account/site group completed.  If any brand
    is unresolved and a page was intentionally preserved, do not disturb it.
    """
    if context is None or not group_key or not group_items_all_completed(batch, group_key):
        return

    account_id, site = group_key
    home_url = sellercentral_home_url(site)
    page = None
    try:
        if anchor_page and not anchor_page.is_closed():
            page = anchor_page
    except Exception:
        page = None
    if page is None:
        try:
            page = context.pages[0] if context.pages else context.new_page()
        except Exception as exc:
            print(f"[会话] {account_id}/{site or '默认'} 完成后主页跳转失败: {exc}")
            return

    try:
        page.goto(home_url, wait_until="commit", timeout=30000)
        try:
            page.wait_for_load_state("domcontentloaded", timeout=30000)
        except Exception:
            pass
        print(f"[会话] {account_id}/{site or '默认'} 全部完成，已跳转主页: {home_url}")
    except Exception as exc:
        print(f"[会话] {account_id}/{site or '默认'} 完成后主页跳转失败: {exc}")


def load_config(config_path: str) -> dict:
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_batch_state(state_path: str, state: dict):
    with open(state_path, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def load_batch_state(state_path: str) -> dict:
    if not Path(state_path).exists():
        return None
    with open(state_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def expand_brands(brands: list[str]) -> list[str]:
    normalized = [b.strip() for b in brands if b and b.strip()]
    if len(normalized) == 1 and normalized[0].lower() == "all":
        return sorted([p.name for p in PICTURE_ROOT.iterdir() if p.is_dir()])
    return normalized


def normalize_accounts(accounts: list[str]) -> list[str]:
    return [normalize_account_id(a.strip()) for a in accounts if a and a.strip()]


def resolve_statement_text(project_root: Path, brand_name: str, account_id: str, site: str | None, marketplace: str, manifest: dict, sync_result: dict) -> tuple[str, str]:
    """Resolve 5461 statement text with site/account-specific files first.

    This intentionally prefers files produced by sync_brand_pack_for_account()
    for the requested site (for example MX account docs) before trusting the
    manifest.  Some older manifests mapped MX to *_us.txt, which leaked English
    titles into Mexico submissions.
    """
    account_num = normalize_account_num(account_id)
    candidates: list[str] = []

    site_norm = normalize_site(site)
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


def extract_statement_payload(statement_text: str) -> dict[str, str]:
    """Extract the exact Bitable title/SKU while preserving the submitted body."""

    text = str(statement_text or "").strip()
    title_match = re.search(r"Item name[：:]\s*([^\r\n]+)", text, re.IGNORECASE)
    sku_match = re.search(r"SKU[：:]\s*([^\r\n]+)", text, re.IGNORECASE)
    return {
        "title": title_match.group(1).strip() if title_match else "",
        "sku": sku_match.group(1).strip() if sku_match else "",
        "content": text,
    }


def resolve_account_specific_uk_payload(
    project_root: Path,
    brand_name: str,
    account_id: str,
    manifest: dict,
    sync_result: dict,
) -> dict[str, str]:
    """Return UK material only when an account-specific statement is available."""

    text, source = resolve_statement_text(
        project_root,
        brand_name,
        account_id,
        "UK",
        "UK",
        manifest,
        sync_result,
    )
    account_num = normalize_account_num(account_id)
    expected_suffix = f".account_{account_num}.txt"
    if not source or not Path(source).name.endswith(expected_suffix):
        return {"title": "", "sku": "", "content": ""}
    payload = extract_statement_payload(text)
    expected_prefix = f"{account_num}-UK-{brand_name}-".casefold()
    if not payload["sku"].casefold().startswith(expected_prefix):
        return {"title": "", "sku": "", "content": ""}
    return payload


def create_batch_state(
    accounts: list,
    brands: list,
    config: dict,
    site: str | None = None,
    feishu_country_option: str | None = None,
) -> dict:
    items = []
    for account in accounts:
        for brand in brands:
            items.append({
                "account_id": account,
                "brand_name": brand,
                "site": normalize_site(site),
                "feishu_country_option": str(feishu_country_option or "").strip(),
                "status": "pending",
                "result": None,
                "started_at": None,
                "completed_at": None,
                "error": None,
                "retry_count": 0
            })

    batch_size = config.get("batch_size", 1)
    batches = []
    for i in range(0, len(items), batch_size):
        batches.append({
            "batch_no": i // batch_size + 1,
            "items": items[i:i + batch_size],
            "status": "pending",
            "started_at": None,
            "completed_at": None
        })

    return {
        "created_at": datetime.now().isoformat(),
        "config": config,
        "batches": batches,
        "summary": {
            "total": len(items),
            "completed": 0,
            "failed": 0,
            "pending": len(items)
        }
    }


def schedule_followup_for_result(item: dict, result: dict, config: dict, dry_run: bool = False) -> dict | None:
    """Queue a delayed Case check without changing the submission result."""
    if dry_run:
        return None
    followup_config = dict(config.get("case_followup") or {})
    if not followup_config.get("enabled", True):
        return None

    dashboard = result.get("dashboard_check") if isinstance(result.get("dashboard_check"), dict) else {}
    case_id = result.get("case_id") or dashboard.get("case_id")
    status = str(result.get("status") or result.get("submit_result") or "").lower()
    dashboard_status = str(dashboard.get("status") or "").lower()
    eligible = bool(
        case_id
        and (
            status in {"success", "under_review", "approved"}
            or dashboard_status in {"under_review", "approved"}
        )
    )
    if not eligible:
        return None

    from src.case_followup import schedule_case_followup
    from src.config_loader import load_yaml

    settings = load_yaml(str(project_root / "config" / "settings.yaml"))
    settings["case_followup"] = followup_config
    return schedule_case_followup(
        settings=settings,
        account_id=item["account_id"],
        site=normalize_site(item.get("site")) or str(result.get("actual_marketplace") or ""),
        brand_name=item["brand_name"],
        case_id=str(case_id),
        sku=str(result.get("synced_sku") or ""),
        submitted_at=item.get("completed_at") or datetime.now().isoformat(),
        delay_hours=float(followup_config.get("delay_hours", 24.0)),
        feishu_country_option=str(
            result.get("feishu_country_option") or item.get("feishu_country_option") or ""
        ),
        submission_title=str(result.get("feishu_title") or ""),
        submission_content=str(result.get("feishu_content") or ""),
        uk_sku=str(result.get("feishu_uk_sku") or ""),
        uk_title=str(result.get("feishu_uk_title") or ""),
        uk_content=str(result.get("feishu_uk_content") or ""),
    )


def _is_state_loop_worthy(error_text: str) -> bool:
    """
    判断 old flow 失败是否适合升级到 StateLoopExecutor 重试。

    适合升级的错误：未知页面状态、超时未格基于 410001/429/服务器错误。
    不适合升级的错误：410001、429、login_expired、提交后未获取 Case ID。
    """
    if not error_text:
        return False
    error_lower = error_text.lower()

    # 不升级的错误类型（这些类型用旧流程重试就行）
    no_escalate = [
        "410001", "429", "too many requests", "rate limit",
        "login", "signin", "login_expired",
        "server_error", "an error occurred",
        "提交后未检测到成功",  # Case ID 超时，可能已提交
    ]
    for kw in no_escalate:
        if kw in error_lower:
            return False

    # 适合升级的错误类型（未知页面/未知状态）
    escalate = [
        "unknown", "unexpected", "stuck", "no action",
        "max_steps", "page_type", "not found",
        "未找到", "超时", "元素未出现",
    ]
    for kw in escalate:
        if kw in error_lower:
            return True

    return False


def run_single_item(item: dict, config: dict, dry_run: bool = False, page=None, skip_market_switch: bool = False, enable_monitor: bool = False, skip_add_product_goto: bool = False) -> dict:
    account_id = item["account_id"]
    brand_name = item["brand_name"]
    site = item.get("site")

    print(f"\n{'='*70}")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 开始: {account_id} / {brand_name}")
    print(f"{'='*70}")

    # -- 旁路监控启动 --
    monitor = None
    if enable_monitor and page is not None:
        try:
            from src.capture.monitor_session import MonitorSession
            monitor = MonitorSession.for_brand(page, account_id, brand_name)
            ok = monitor.start()
            if ok:
                print(f"[MONITOR] 旁路监控已启动: {monitor.out_dir}")
            else:
                print(f"[WARN] 旁路监控启动失败，继续执行主流程")
                monitor = None
        except Exception as e:
            print(f"[WARN] 旁路监控初始化失败: {e}")
            monitor = None

    try:
        account_meta = ensure_account_exists(account_id, auto_create=True, site=site)
        sync_result = sync_brand_pack_for_account(brand_name, account_id, site=site)
        print(f"[SYNC] 账号: {account_id} | 品牌: {brand_name} | SKU: {sync_result['sku']}")

        if dry_run:
            print("[DRY RUN] 模拟执行")
            time.sleep(0.2)
            result = {
                "status": "dry_run",
                "note": f"模拟执行成功；已同步 SKU {sync_result['sku']}",
                "account_created": account_meta["created"],
                "matched_profile": account_meta["matched_profile"],
            }
            if monitor:
                summary = monitor.stop()
                result["monitor_summary"] = summary
                result["network_errors"] = monitor.get_errors()
                print(f"[MONITOR] 监控摘要: events={summary['events_count']}, requests={summary['requests_count']}, 429={summary['rate_limit_429_count']}")
            return result

        acc = account_meta["account"]
        account_email = resolve_account_email(acc)
        if account_email and update_account_email(account_id, account_email, project_root / "config" / "accounts.json"):
            acc["email"] = account_email
            if not acc.get("username"):
                acc["username"] = account_email
            print(f"[EMAIL] 已从账号/AdsPower信息解析并回填邮箱: {account_email}")
        if not acc.get("adspower_profile_id"):
            raise RuntimeError(f"账号 {account_id} 已自动补入 accounts.json，但缺少 adspower_profile_id，无法实际启动工作流")

        settings, acc, manifest, marketplace, mkt_cfg, cdp_url = load_runtime(account_id, brand_name)
        
        # 使用用户指定的站点覆盖默认市场
        effective_marketplace = normalize_site(site) or marketplace
        
        # 检查账号是否有指定站点的配置
        mp_configs = acc.get("marketplace_configs", {})
        if site and site not in mp_configs:
            raise RuntimeError(
                f"账号 {account_id} 缺少 {site} 站点配置，"
                f"请在 accounts.json 的 marketplace_configs 中配置 {site} 的 entry_url 和 item_type_keyword"
            )
        
        # 如果账号有站点特定配置，直接使用；否则回退到域名替换逻辑
        if effective_marketplace in mp_configs:
            site_cfg = mp_configs[effective_marketplace]
            add_product_url = site_cfg.get("entry_url", acc.get("entry_url", ""))
            item_type_keyword = site_cfg.get("item_type_keyword", acc.get("item_type_keyword", "cell-phone-screen-protectors"))
            print(f"[OVERRIDE] 使用 marketplace_configs.{effective_marketplace} 的站点配置")
        else:
            add_product_url = acc.get("entry_url", "")
            item_type_keyword = acc.get("item_type_keyword", "cell-phone-screen-protectors")
        
        if effective_marketplace != marketplace:
            print(f"[OVERRIDE] 使用指定站点: {effective_marketplace} (覆盖账号默认的 {marketplace})")
            marketplace = effective_marketplace
            # 回退：如果 marketplace_configs 中没有，做域名替换
            if effective_marketplace not in mp_configs:
                current_domain = None
                if "sellercentral.amazon.com/" in add_product_url and "sellercentral.amazon.com.mx/" not in add_product_url:
                    current_domain = "sellercentral.amazon.com"
                elif "sellercentral.amazon.co.uk/" in add_product_url:
                    current_domain = "sellercentral.amazon.co.uk"
                elif "sellercentral.amazon.ca/" in add_product_url:
                    current_domain = "sellercentral.amazon.ca"
                
                if effective_marketplace in ("UK", "BE", "NL", "SE", "DE", "FR", "ES", "IT", "PL"):
                    target_domain = "sellercentral.amazon.co.uk"
                elif effective_marketplace == "CA":
                    target_domain = "sellercentral.amazon.ca"
                elif effective_marketplace == "MX":
                    target_domain = "sellercentral.amazon.com"
                else:
                    target_domain = "sellercentral.amazon.com"
                
                if current_domain and current_domain != target_domain:
                    add_product_url = add_product_url.replace(current_domain, target_domain)
                    print(f"[OVERRIDE] URL 域名已切换: {current_domain} → {target_domain}")
                    import re
                    add_product_url = re.sub(r'[&?]recommendedBrowseNodeId=[^&]+', '', add_product_url)
                    add_product_url = re.sub(r'[&?]displayPath=[^&]+', '', add_product_url)
                    add_product_url = re.sub(r'[&?]mons_sel_mkid=[^&#]+', '', add_product_url)
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
        submission_payload = extract_statement_payload(statement_text)
        if (normalize_site(site) or marketplace).upper() == "UK":
            uk_payload = dict(submission_payload)
        else:
            uk_payload = resolve_account_specific_uk_payload(
                project_root,
                brand_name,
                account_id,
                manifest,
                sync_result,
            )

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
            print(f"请求站点: {normalize_site(site)}")
        print(f"声明文本: {len(statement_text)} 字符")
        if statement_source:
            print(f"声明文件: {statement_source}")
        print(f"上传文件: {len(upload_file_paths)} 个")

        # 默认自动提交，无需人工确认（可通过 --confirm 启用人工确认）
        require_confirm = config.get("require_human_confirm", False)
        if getattr(run_single_item, '_yes_mode', False):
            require_confirm = False
        
        # Verify page is still alive before passing to submit function
        # Use page.url (property access) instead of page.evaluate() to avoid
        # "Sync API inside asyncio loop" errors when PageMonitor runs an event loop.
        if page is not None:
            try:
                _ = page.url  # lightweight liveness check; raises if page/context is closed
            except Exception as exc:
                # Browser ownership belongs to run_batch(). Starting a nested
                # Playwright runtime here is unsafe; let the batch boundary
                # reconnect cleanly on the next attempt.
                raise RuntimeError("传入的 page 已失效；请由批处理器重新建立浏览器连接") from exc

        # --use-state-loop switch ------------------------------------------------
        use_state_loop = config.get("use_state_loop", False)

        if use_state_loop and not dry_run:
            print("[INFO] using StateLoopExecutor (use_state_loop=True)")
            from src.executor import StateLoopExecutor

            brand_data = {
                "account_id": account_id,
                "brand_name": brand_name,
                "entry_url": add_product_url,
                "marketplace": marketplace,
                "statement_text": statement_text,
                "upload_files": upload_file_paths,
                "email": account_email or acc.get("email", ""),
                "brand_keywords": None,
                "item_type_keyword": item_type_keyword,
                "item_name": "",
            }
            # Try to load brand keywords from manifest
            manifest_path = project_root / "brand_packs" / brand_name / "manifest.json"
            if manifest_path.exists():
                try:
                    import json as _json
                    mf = _json.loads(manifest_path.read_text(encoding="utf-8"))
                    brand_data["brand_keywords"] = mf.get("brand_selection_keywords", None)
                except Exception:
                    pass

            executor = StateLoopExecutor(
                page=page,
                brand_data=brand_data,
                evidence_root=settings["paths"]["evidence_root"],
            )
            loop_result = executor.run()

            # Map loop result to batch-compatible shape
            status_map = {
                "success": "success",
                "failed": "failed",
                "stopped": "failed",
            }
            result = {
                "status": status_map.get(loop_result.get("result", "failed"), "failed"),
                "note": loop_result.get("error", ""),
                "case_id": loop_result.get("case_id"),
                "steps": loop_result.get("steps", []),
                "account_created": account_meta["created"],
                "matched_profile": account_meta["matched_profile"],
                "synced_sku": sync_result["sku"],
                "feishu_title": submission_payload["title"],
                "feishu_content": submission_payload["content"],
                "feishu_uk_sku": uk_payload["sku"],
                "feishu_uk_title": uk_payload["title"],
                "feishu_uk_content": uk_payload["content"],
            }
            if monitor:
                summary = monitor.stop()
                result["monitor_summary"] = summary
                result["network_errors"] = monitor.get_errors()
                print(f"[MONITOR] 监控摘要: events={summary['events_count']}, requests={summary['requests_count']}, 429={summary['rate_limit_429_count']}")
            return result
        else:
            result = submit_5461_from_add_product(
                cdp_url=cdp_url,
                account_id=account_id,
                marketplace=marketplace,
                brand_name=brand_name,
                add_product_url=add_product_url,
                statement_text=statement_text,
                upload_files=upload_file_paths,
                evidence_root=settings["paths"]["evidence_root"],
                require_human_confirm=require_confirm,
                item_type_keyword=item_type_keyword,
                account_email=account_email or acc.get("email", ""),
                keep_browser_open=True,
                page=page,
                skip_market_switch=skip_market_switch,
                skip_add_product_goto=skip_add_product_goto,
            )

            result = {
                "status": result.get("submit_result", "unknown"),
                "note": result.get("note", ""),
                "case_id": result.get("case_id"),
                "dashboard_check": result.get("dashboard_check"),
                "evidence_files": result.get("evidence_files", []),
                "steps": result.get("steps", []),
                "account_created": account_meta["created"],
                "matched_profile": account_meta["matched_profile"],
                "synced_sku": sync_result["sku"],
                "feishu_title": submission_payload["title"],
                "feishu_content": submission_payload["content"],
                "feishu_uk_sku": uk_payload["sku"],
                "feishu_uk_title": uk_payload["title"],
                "feishu_uk_content": uk_payload["content"],
                "console_logs": result.get("console_logs", ""),  # 429检测需要
                "marketplace_switched": bool(result.get("marketplace_switched")),
                "actual_marketplace": result.get("actual_marketplace"),
                "page_ready_for_reuse": result.get("page_ready_for_reuse"),
                "page_reuse_reason": result.get("page_reuse_reason"),
            }
            if monitor:
                summary = monitor.stop()
                result["monitor_summary"] = summary
                result["network_errors"] = monitor.get_errors()
                print(f"[MONITOR] 监控摘要: events={summary['events_count']}, requests={summary['requests_count']}, 429={summary['rate_limit_429_count']}")

            try:
                from src.failure_classifier import classify_failure
                result["health_classification"] = classify_failure(result, result.get("network_errors", []))
            except Exception:
                pass

            # —— 自动判断是否升级到 StateLoopExecutor ————————————————————
            # 条件：老流程失败 + 错误类型属于“未知页面状态”，且未手动开启 state loop
            _auto_state_loop = config.get("auto_state_loop_fallback", False)
            # StateLoopExecutor is still production-experimental. Keep fallback opt-in only.
            # Enable explicitly with config.auto_state_loop_fallback=true or --use-state-loop.
            if (_auto_state_loop
                    and not use_state_loop
                    and not dry_run
                    and result["status"] not in ("success", "already_approved", "under_review")
                    and _is_state_loop_worthy(result.get("note", "") + result.get("error", ""))):
                print(f"[INFO] old flow 失败原因: {result.get('note','')}")
                print("[INFO] 自动升级到 StateLoopExecutor 重试...")
                from src.executor import StateLoopExecutor
                brand_data_fb = {
                    "account_id": account_id,
                    "brand_name": brand_name,
                    "entry_url": add_product_url,
                    "marketplace": marketplace,
                    "statement_text": statement_text,
                    "upload_files": upload_file_paths,
                    "email": acc.get("email", ""),
                    "brand_keywords": None,
                    "item_type_keyword": item_type_keyword,
                    "item_name": "",
                }
                try:
                    mf_path = project_root / "brand_packs" / brand_name / "manifest.json"
                    if mf_path.exists():
                        import json as _json2
                        mf2 = _json2.loads(mf_path.read_text(encoding="utf-8"))
                        brand_data_fb["brand_keywords"] = mf2.get("brand_selection_keywords")
                except Exception:
                    pass
                executor_fb = StateLoopExecutor(
                    page=page,
                    brand_data=brand_data_fb,
                    evidence_root=settings["paths"]["evidence_root"],
                )
                loop_result = executor_fb.run()
                if loop_result.get("result") == "success":
                    result["status"] = "success"
                    result["case_id"] = loop_result.get("case_id")
                    result["note"] = "[auto-state-loop fallback] " + result.get("note", "")
                    print(f"[INFO] StateLoopExecutor 备用成功! Case ID: {result.get('case_id')}")
                else:
                    result["state_loop_note"] = loop_result.get("error", "")
                    print(f"[WARN] StateLoopExecutor 备用也失败: {result['state_loop_note']}")

            return result

    except Exception as e:
        print(f"[错误] {e}")
        import traceback
        traceback.print_exc()
        result = {
            "status": "error",
            "error": str(e)
        }
        if monitor:
            try:
                summary = monitor.stop()
                result["monitor_summary"] = summary
                result["network_errors"] = monitor.get_errors()
            except Exception:
                pass
        return result


def run_batch(state: dict, batch_no: int, dry_run: bool = False, enable_monitor: bool = False) -> dict:
    batches = state["batches"]

    if batch_no < 1 or batch_no > len(batches):
        print(f"[错误] 无效的批次号: {batch_no}")
        return state

    batch = batches[batch_no - 1]
    config = state["config"]

    print(f"\n{'#'*70}")
    print(f"执行批次 {batch_no} / {len(batches)}")
    print(f"项目数: {len(batch['items'])}")
    print(f"{'#'*70}")

    batch["status"] = "running"
    batch["started_at"] = datetime.now().isoformat()

    # 按 (account_id, site) 分组排序，同组品牌复用浏览器会话
    items = [i for i in batch["items"] if i["status"] not in ("completed", "skipped")]
    items.sort(key=lambda x: (x["account_id"], x.get("site") or ""))

    from playwright.sync_api import sync_playwright
    from scripts._flow_cli_common import load_runtime

    current_group = None
    p = None
    browser = None
    context = None  # 保存 context，每个品牌从中创建独立页面
    anchor_page = None  # 同组保留页面；组内全部完成后跳转到 Seller Central 主页
    group_market_switched = False  # 同组内第一个品牌切换市场后，后续品牌可跳过
    reuse_page = None  # 上一个品牌成功后留下的可复用页面

    for item in items:
        group_key = (item["account_id"], item.get("site"))

        # 新分组：重新连接 AdsPower 浏览器，获取 context
        if group_key != current_group:
            reuse_page = None  # 跨分组不复用页面
            if browser:
                navigate_group_home(context, anchor_page, current_group, batch)
            if p:
                try:
                    p.stop()
                except Exception:
                    pass

            account_id, site = group_key
            print(f"\n{'='*70}")
            print(f"[会话] 新分组: {account_id} / 站点 {site or '默认'} — 创建浏览器连接")
            print(f"{'='*70}")

            # 获取 AdsPower CDP URL（用该组第一个品牌）
            print(f"[DBG] calling load_runtime for {account_id} / {item['brand_name']}", flush=True)
            settings, acc, manifest, marketplace, mkt_cfg, cdp_url = load_runtime(account_id, item["brand_name"])
            print(f"[DBG] load_runtime done, cdp_endpoint_ready={bool(cdp_url)}", flush=True)

            import asyncio as _asyncio
            try:
                _loop = _asyncio.get_event_loop()
                print(f"[DBG] asyncio loop before start: running={_loop.is_running()} closed={_loop.is_closed()}", flush=True)
            except Exception as _le:
                print(f"[DBG] asyncio loop check: {_le}", flush=True)
            print("[DBG] calling sync_playwright().start()", flush=True)
            p = sync_playwright().start()
            connect_timeout_sec = max(
                1,
                int((settings.get("browser", {}) or {}).get("cdp_connect_timeout_ms") or 30_000) // 1000,
            )
            restart_attempts = max(
                0,
                int((settings.get("browser", {}) or {}).get("cdp_restart_attempts", 1)),
            )
            print("[DBG] playwright started, connecting to sanitized CDP endpoint", flush=True)
            for connect_attempt in range(restart_attempts + 1):
                try:
                    browser = _cdp_connect(p, cdp_url, timeout=connect_timeout_sec)
                    break
                except Exception as connect_exc:
                    try:
                        p.stop()
                    except Exception:
                        pass
                    if connect_attempt >= restart_attempts:
                        raise RuntimeError(
                            f"cdp_connect_failed_after_recovery ({type(connect_exc).__name__})"
                        ) from connect_exc
                    print(
                        f"[会话] CDP 连接失败，自动重启 profile "
                        f"({connect_attempt + 1}/{restart_attempts})",
                        flush=True,
                    )
                    adsp = create_adspower_client(settings)
                    if hasattr(adsp, "restart_profile"):
                        restarted = adsp.restart_profile(
                            profile_id=acc["adspower_profile_id"],
                            health_timeout_sec=float(
                                (settings.get("browser", {}) or {}).get("cdp_health_timeout_sec") or 5.0
                            ),
                            ready_timeout_sec=float(
                                (settings.get("browser", {}) or {}).get("cdp_ready_timeout_sec") or 30.0
                            ),
                            restart_wait_sec=float(
                                (settings.get("browser", {}) or {}).get("profile_restart_wait_sec") or 5.0
                            ),
                        )
                    else:
                        adsp.stop_profile(profile_id=acc["adspower_profile_id"])
                        time.sleep(
                            float((settings.get("browser", {}) or {}).get("profile_restart_wait_sec") or 5.0)
                        )
                        restarted = adsp.start_profile(
                            profile_id=acc["adspower_profile_id"],
                            launch_args=["--remote-allow-origins=*"],
                        )
                    cdp_url = str(restarted.get("ws_endpoint") or "")
                    if not cdp_url:
                        raise RuntimeError(
                            "adspower_profile_restart_returned_no_cdp_endpoint"
                        ) from connect_exc
                    p = sync_playwright().start()
            context = browser.contexts[0] if browser.contexts else browser.new_context()

            # 保留一个 anchor 页面确保 context 存活。
            # 先新建 anchor，再关闭其他旧标签页（避免先关导致 context 销毁）。
            try:
                anchor_page = context.new_page()
            except Exception:
                existing_pages = context.pages
                anchor_page = existing_pages[-1] if existing_pages else context.new_page()
            # 导航 anchor 到 about:blank 清除残留状态
            try:
                anchor_page.goto("about:blank", wait_until="commit", timeout=5000)
            except Exception:
                pass
            anchor_page.set_default_timeout(30000)
            # 关闭所有旧标签页（非 anchor，非 AdsPower 起始页），清理上次执行残留
            for old_p in list(context.pages):
                if old_p != anchor_page and "start.adspower.net" not in (old_p.url or ""):
                    try:
                        old_p.close()
                    except Exception:
                        pass
            print(f"[会话] anchor 页面就绪（context 内共 {len(context.pages)} 个标签页）")

            current_group = group_key
            group_market_switched = False  # 新分组，重置市场切换标志

        # ── 页面选择：优先复用上一品牌留下的页面，否则新建 ───────────────
        # 复用条件：上一品牌成功 + page_ready_for_reuse=True + 同组同市场
        # 复用时跳过 add_product_url 的 goto（面板已关，页面停在 Product Identity）
        skip_switch = group_market_switched  # 同组内非首个品牌跳过市场切换
        skip_goto = False
        if reuse_page is not None:
            try:
                _ = reuse_page.url  # 检测页面是否仍然存活
                fresh_page = reuse_page
                skip_goto = True
                print(f"[页面] {item['brand_name']} — 复用上一品牌标签页（跳过 goto）")
            except Exception:
                reuse_page = None
                fresh_page = None

        if not skip_goto or fresh_page is None:
            # 新建独立页面（隔离品牌间状态：弹窗残留、勾选框、错误页面等）
            # 注意：不在此处绑 dialog handler，由 flow_submit_5461.py 独占处理，
            #       避免双重 handler 导致 "Cannot dismiss dialog which is already handled!"
            try:
                fresh_page = context.new_page()
            except Exception as page_err:
                print(f"[错误] 无法为品牌 {item['brand_name']} 创建新页面: {page_err}")
                item["status"] = "failed"
                item["error"] = f"new_page failed: {page_err}"
                item["started_at"] = datetime.now().isoformat()
                item["completed_at"] = datetime.now().isoformat()
                state["summary"]["failed"] += 1
                state["summary"]["pending"] -= 1
                reuse_page = None
                continue
            skip_goto = False
            print(f"[页面] {item['brand_name']} — 新建独立标签页（context 内共 {len(context.pages)} 个）{'  [跳过市场切换]' if skip_switch else '  [将切换市场]'}")

        fresh_page.set_default_timeout(30000)

        item["status"] = "running"
        item["started_at"] = datetime.now().isoformat()
        result = None
        try:
            result = run_single_item(item, config, dry_run, page=fresh_page, skip_market_switch=skip_switch, enable_monitor=enable_monitor, skip_add_product_goto=skip_goto)
        except Exception as run_err:
            print(f"[错误] run_single_item 异常: {run_err}")
            import traceback
            traceback.print_exc()
            result = {"status": "error", "error": str(run_err)}
        finally:
            # 只有确认成功（明确 Case ID / under_review）或 dry-run 才关闭页面。
            # draft / unknown / submit 后仍未完成时必须保留页面；否则会中断还在转圈的提交请求，导致没有 Case ID。
            close_page = False
            if result:
                dashboard = result.get("dashboard_check") if isinstance(result.get("dashboard_check"), dict) else {}
                case_id = result.get("case_id") or dashboard.get("case_id")
                status = str(result.get("status") or result.get("submit_result") or "").lower()
                dash_status = str(dashboard.get("status") or "").lower()
                close_page = bool(
                    dry_run
                    or (
                        case_id
                        and (status in ("success", "under_review", "approved") or dash_status in ("under_review", "approved"))
                    )
                )
            # 成功时判断是否可复用（flow 已关面板并返回 page_ready_for_reuse）
            page_reusable = bool(result and result.get("page_ready_for_reuse"))
            if close_page and page_reusable:
                # 页面已由 flow 准备好复用，batch 侧不关页面，传给下一品牌
                reuse_page = fresh_page
                print(f"[页面] {item['brand_name']} 页面保留供下一品牌复用")
            elif close_page:
                reuse_page = None
                try:
                    fresh_page.close()
                    print(f"[页面] {item['brand_name']} 页面已关闭（已确认成功/Case ID）")
                except Exception:
                    pass
            else:
                print(f"[页面] {item['brand_name']} 页面保留（未确认 Case ID/提交完成）")

        if result is None:
            result = {"status": "error", "error": "run_single_item 未返回结果"}

        item["result"] = result
        item["completed_at"] = datetime.now().isoformat()

        try:
            followup = schedule_followup_for_result(item, result, config, dry_run=dry_run)
            if followup:
                result["case_followup"] = followup
                created_text = "已创建" if followup.get("created") else "已存在"
                print(
                    f"[CaseFollowup] {created_text}: Case {followup['case_id']}，"
                    f"计划检查时间 {followup['scheduled_at']}"
                )
        except Exception as followup_error:
            # Case follow-up is downstream bookkeeping. Never downgrade a
            # successfully submitted application because scheduling failed.
            result["case_followup_error"] = str(followup_error)
            print(f"[CaseFollowup] 排队失败，提交结果保持不变: {followup_error}")
        finally:
            # These values are needed only by the immediate Feishu ensure call.
            # Avoid retaining a second copy of the statement in batch_state.json.
            for private_key in (
                "feishu_title",
                "feishu_content",
                "feishu_uk_sku",
                "feishu_uk_title",
                "feishu_uk_content",
            ):
                result.pop(private_key, None)

        if result.get("marketplace_switched") and (not result.get("actual_marketplace") or result.get("actual_marketplace") == item.get("site") or result.get("actual_marketplace") == normalize_site(item.get("site"))):
            group_market_switched = True
            print(f"[会话] 已确认本组市场 {group_key} 切换完成；后续品牌跳过市场切换")

        if result["status"] in ("success", "dry_run", "under_review"):
            item["status"] = "completed"
            state["summary"]["completed"] += 1
            state["summary"]["pending"] -= 1
            # 结果成功也保持市场切换标志；dry-run 不一定有真实切换，但不会影响真实执行。
            group_market_switched = True
        else:
            item["status"] = "failed"
            item["error"] = result.get("error") or result.get("note", "")
            state["summary"]["failed"] += 1
            state["summary"]["pending"] -= 1

            unresolved_status = str(result.get("status") or result.get("submit_result") or "").lower()
            unresolved_dashboard = result.get("dashboard_check") if isinstance(result.get("dashboard_check"), dict) else {}
            unresolved_dash_status = str(unresolved_dashboard.get("status") or "").lower()
            if (not dry_run) and (unresolved_status in ("draft", "partial", "submitted_no_case_id_pending_dashboard", "unknown") or unresolved_dash_status == "draft"):
                print()
                print("=" * 70)
                print(f"[!] 品牌 {item['brand_name']} 未确认提交完成: status={unresolved_status or 'unknown'}, dashboard={unresolved_dash_status or 'unknown'}")
                print("    页面已保留。为避免中断仍在转圈的提交请求或继续触发限流，批次暂停。")
                print("    请先在 AdsPower 中人工确认/提交/等待 Case ID，再决定是否继续后续品牌。")
                print("=" * 70)
                break

            # --- 429/410001 暂停机制 ---
            # 检测 console_logs 是否含 429，若是则暂停等人决定
            is_429 = False
            console_log_path = result.get("console_logs")
            if console_log_path:
                try:
                    log_text = Path(console_log_path).read_text(encoding="utf-8", errors="ignore")
                    if "status of 429" in log_text or "Too Many Requests" in log_text:
                        is_429 = True
                except Exception:
                    pass
            if not is_429:
                # 也检查 error 字段
                err_str = str(result.get("error", "") or result.get("note", ""))
                if "429" in err_str or "410001" in err_str:
                    is_429 = True

            if is_429 and not dry_run:
                print()
                print("=" * 70)
                print(f"[!] 品牌 {item['brand_name']} 触发了 429/410001 限流")
                print("    页面保持打开，可在 AdsPower 中手动查看当前状态")
                print("    选择下一步操作：")
                print("      Enter  — 跳过此品牌，继续下一个")
                print("      r      — 重试此品牌（立即）")
                print("      q      — 终止整个批次")
                print("=" * 70)
                import sys as _sys
                choice = ""
                if _sys.stdin.isatty():
                    try:
                        choice = input(">> ").strip().lower()
                    except EOFError:
                        # stdin closed (background process / piped input) — skip, do not terminate
                        print("[非交互模式] stdin EOF，自动跳过，继续下一个品牌")
                        choice = ""
                    except KeyboardInterrupt:
                        choice = "q"
                else:
                    print("[非交互模式] 自动跳过，继续下一个品牌")
                    choice = ""

                if choice == "q":
                    print("[!] 用户选择终止批次")
                    break
                elif choice == "r":
                    # 重试：把当前品牌状态重置，重新插入当前位置后面
                    print(f"[!] 重试 {item['brand_name']}...")
                    item["status"] = "pending"
                    item["result"] = None
                    item["error"] = ""
                    state["summary"]["failed"] -= 1
                    state["summary"]["pending"] += 1
                    current_idx = items.index(item)
                    items.insert(current_idx + 1, item)
                    continue
                else:
                    print(f"[!] 跳过 {item['brand_name']}，继续下一个品牌")
                    continue
            # --- 暂停机制结束 ---

        if not dry_run:
            delay_min = config.get("delay_between_items_min", 15)
            delay_max = config.get("delay_between_items_max", 30)
            # 兼容旧的 delay_between_items 固定值写法
            if "delay_between_items" in config and "delay_between_items_min" not in config:
                delay_min = config["delay_between_items"]
                delay_max = config["delay_between_items"]
            delay = random.randint(delay_min, delay_max)
            if delay > 0:
                print(f"等待 {delay} 秒（品牌间随机冷却 {delay_min}-{delay_max}s，避免429限流）...")
                time.sleep(delay)

    # 全部处理结束：先把保留页跳到对应主页，再仅断开 Playwright。
    # AdsPower profile 保持运行，避免 browser.close() 造成 profile 生命周期混乱。
    if browser:
        navigate_group_home(context, anchor_page, current_group, batch)
    if p:
        try:
            p.stop()
        except Exception:
            pass

    batch["status"] = "completed"
    batch["completed_at"] = datetime.now().isoformat()
    return state


def print_summary(state: dict):
    print(f"\n{'='*70}")
    print("执行摘要")
    print(f"{'='*70}")

    summary = state["summary"]
    print(f"总计: {summary['total']}")
    print(f"  完成: {summary['completed']}")
    print(f"  失败: {summary['failed']}")
    print(f"  待执行: {summary['pending']}")

    print(f"\n批次详情:")
    for batch in state["batches"]:
        status_icon = "✓" if batch["status"] == "completed" else "○" if batch["status"] == "pending" else "..."
        print(f"  批次 {batch['batch_no']}: {status_icon}")
        for item in batch["items"]:
            icon = "✓" if item["status"] == "completed" else "✗" if item["status"] == "failed" else "○"
            print(f"    {icon} {item['account_id']} / {item['brand_name']}: {item['status']}")

    failed_items = []
    for batch in state["batches"]:
        for item in batch["items"]:
            if item["status"] == "failed":
                failed_items.append(item)

    if failed_items:
        print(f"\n失败项目:")
        for item in failed_items:
            print(f"  - {item['account_id']} / {item['brand_name']}")
            print(f"    错误: {item.get('error', 'Unknown')}")

    print(f"{'='*70}")


def main():
    parser = argparse.ArgumentParser(description='批量运行 5461 工作流')
    parser.add_argument('--config', help='配置文件路径')
    parser.add_argument('--accounts', help='账户列表（逗号分隔，如 us_store_530,us_store_529 或 530,529）')
    parser.add_argument('--brands', help='品牌列表（逗号分隔，如 OUNNE,WILLONE；或 all）')
    parser.add_argument('--batch-size', type=int, default=100, help='每批执行数量')
    parser.add_argument('--continue-from', type=int, default=1, help='从指定批次继续')
    parser.add_argument('--dry-run', action='store_true', help='模拟执行模式')
    parser.add_argument('--site', help='指定站点/国家，如 US / UK / DE / MX / BE / NL / SE / FR / ES / IT')
    parser.add_argument('--state-file', default='data/batch_state.json', help='状态文件路径')
    parser.add_argument('--yes', '-y', action='store_true', help='自动确认，无需人工干预')
    parser.add_argument('--no-confirm', action='store_true', help='跳过最终提交前的人工确认')
    parser.add_argument('--enable-monitor', action='store_true', help='兼容参数：旁路监控现在真实执行默认开启')
    parser.add_argument('--disable-monitor', action='store_true', help='关闭旁路监控（默认真实执行开启，dry-run 默认关闭）')
    parser.add_argument('--use-state-loop', action='store_true', help='使用 StateLoopExecutor 状态循环执行器（替代 _run_core，默认关闭）')
    parser.add_argument('--case-followup-delay-hours', type=float, help='取得 Case ID 后延迟多少小时检查详情')
    parser.add_argument('--disable-case-followup', action='store_true', help='本批次不创建 Case 延迟复核任务')
    parser.add_argument(
        '--feishu-country-option',
        help='飞书国家EU原始选项，例如 比利时1；用于区分再次或多次申请',
    )

    args = parser.parse_args()

    from src.case_followup import get_case_followup_config
    from src.config_loader import load_yaml

    runtime_settings = load_yaml(str(project_root / "config" / "settings.yaml"))
    default_followup_config = get_case_followup_config(runtime_settings)

    print("="*70)
    print("批量 5461 工作流")
    print("="*70)

    try:
        state_path = project_root / args.state_file

        if state_path.exists() and not args.config and not args.accounts:
            print(f"\n加载现有状态: {state_path}")
            state = load_batch_state(str(state_path))
        else:
            print(f"\n创建新批次...")

            if args.config:
                config = load_config(args.config)
                # accounts.json 中 accounts 是 dict 列表，需要提取 account_id
                raw_accounts = config.get("accounts", [])
                if raw_accounts and isinstance(raw_accounts[0], dict):
                    accounts = [a["account_id"] for a in raw_accounts if a.get("account_id")]
                else:
                    accounts = normalize_accounts(raw_accounts)
                brands = expand_brands(config.get("brands", []))
                if not args.site and config.get("site"):
                    args.site = config.get("site")
            elif args.accounts and args.brands:
                accounts = normalize_accounts(args.accounts.split(","))
                brands = expand_brands(args.brands.split(","))
                config = {
                    "batch_size": args.batch_size,
                    "delay_between_items_min": 15,  # 品牌间随机冷却下限（秒）
                    "delay_between_items_max": 30,  # 品牌间随机冷却上限（秒）
                    "require_human_confirm": False  # 默认自动提交
                }
            else:
                print("[错误] 请提供 --config 或 --accounts 和 --brands")
                sys.exit(1)

            if not accounts or not brands:
                print("[错误] 账户列表和品牌列表不能为空")
                sys.exit(1)

            for account_id in accounts:
                meta = ensure_account_exists(account_id, auto_create=True)
                if meta["created"]:
                    print(f"[AUTO] 已补账号配置: {account_id} | matched_profile={meta['matched_profile']}")
                elif meta.get("refreshed"):
                    print(f"[AUTO] 已刷新账号资料: {account_id} | matched_profile={meta['matched_profile']}")

            if args.batch_size:
                config["batch_size"] = args.batch_size

            state = create_batch_state(
                accounts,
                brands,
                config,
                site=args.site,
                feishu_country_option=args.feishu_country_option or config.get("feishu_country_option"),
            )
            state_path.parent.mkdir(parents=True, exist_ok=True)
            save_batch_state(str(state_path), state)
            print(f"状态已保存: {state_path}")

        if args.feishu_country_option:
            for batch in state.get("batches", []):
                for item in batch.get("items", []):
                    if item.get("status") not in {"completed", "skipped"}:
                        item["feishu_country_option"] = args.feishu_country_option.strip()

        followup_config = dict(default_followup_config)
        followup_config.update(state.get("config", {}).get("case_followup") or {})
        if args.case_followup_delay_hours is not None:
            followup_config["delay_hours"] = max(0.0, float(args.case_followup_delay_hours))
        if args.disable_case_followup:
            followup_config["enabled"] = False
        state.setdefault("config", {})["case_followup"] = followup_config
        runtime_settings["case_followup"] = followup_config
        print(
            f"[CaseFollowup] enabled={followup_config.get('enabled')} "
            f"delay_hours={followup_config.get('delay_hours')}"
        )

        print(f"\n总批次数: {len(state['batches'])}")
        print(f"总项目数: {state['summary']['total']}")

        # Pass --yes flag down to run_single_item
        run_single_item._yes_mode = args.yes

        # Monitor policy: real runs default ON for diagnosability; dry-run default OFF.
        # --disable-monitor is the opt-out; --enable-monitor remains accepted for compatibility.
        if args.disable_monitor:
            enable_monitor = False
        elif args.dry_run:
            enable_monitor = bool(getattr(args, 'enable_monitor', False) or state.get("config", {}).get("enable_monitor", False))
        else:
            enable_monitor = True
        if enable_monitor:
            state.setdefault("config", {})["enable_monitor"] = True
            print("[INFO] 旁路监控已启用（真实执行默认开启，可用 --disable-monitor 关闭）")

        use_state_loop = getattr(args, 'use_state_loop', False)
        if not use_state_loop and state.get("config"):
            use_state_loop = state["config"].get("use_state_loop", False)
        if use_state_loop:
            state.setdefault("config", {})["use_state_loop"] = True
            print("[INFO] --use-state-loop: StateLoopExecutor 已启用")

        for batch_no in range(args.continue_from, len(state['batches']) + 1):
            state = run_batch(state, batch_no, dry_run=args.dry_run, enable_monitor=enable_monitor)
            save_batch_state(str(state_path), state)

            if batch_no < len(state['batches']) and not args.dry_run:
                # 默认自动继续，无需确认
                print(f"\n批次 {batch_no} 完成。自动继续下一批...")
                cont = 'y'

            if batch_no < len(state['batches']) and not args.dry_run:
                # 增加批次间延迟，确保 AdsPower 浏览器完全重启
                delay = state["config"].get("delay_between_batches", 10)
                if delay > 0:
                    print(f"等待 {delay} 秒后继续（确保浏览器重启）...")
                    time.sleep(delay)

        print_summary(state)
        print(f"\n状态文件: {state_path}")
    finally:
        if not args.dry_run:
            try:
                from src.case_followup import launch_case_followup_worker

                worker = launch_case_followup_worker(runtime_settings)
                if worker.get("started"):
                    print(f"[CaseFollowup] 后台 worker 已启动，PID={worker.get('pid')}")
                elif worker.get("reason") == "already_running":
                    print(f"[CaseFollowup] 后台 worker 已在运行，PID={worker.get('pid')}")
            except Exception as worker_error:
                print(f"[CaseFollowup] 后台 worker 启动失败: {worker_error}")
        print("执行完成")


if __name__ == "__main__":
    main()
