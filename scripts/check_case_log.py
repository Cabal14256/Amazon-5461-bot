#!/usr/bin/env python3
"""
check_case_log.py - 自动打开 Seller Central Cases 页面，检查指定品牌的 Case ID

用法:
    python scripts/check_case_log.py <account_id> <brand_name> [--site SITE]

示例:
    python scripts/check_case_log.py us_store_580 HOMEMO --site BE
    python scripts/check_case_log.py us_store_573 WILLONE --site UK

功能:
    1. 连接 AdsPower 浏览器
    2. 切换到指定站点 marketplace
    3. 打开 Case Dashboard（/hz/myqdashboard/ref=xx_myqd_favb_xx）
    4. 搜索最近与品牌相关的 Case
    5. 提取 Case ID
    6. 自动更新 Excel 登记表和 SQLite ledger
"""
import sys
import re
import json
import time
import argparse
import openpyxl
from pathlib import Path
from datetime import datetime
from typing import Optional

# Fix Windows GBK encoding
if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from browser_manager import BrowserManager
from marketplace_switcher import switch_marketplace

PROJECT_ROOT = Path(__file__).parent.parent
_private_path = PROJECT_ROOT / "runtime" / "private" / "accounts.json"
_config_path  = PROJECT_ROOT / "config" / "accounts.json"
CONFIG_PATH = _private_path if _private_path.exists() else _config_path
EXCEL_PATH = PROJECT_ROOT / "data" / "5461申请登记表.xlsx"

# Case Dashboard URLs per domain
# 5461/品牌申请的 Case 需要走 Seller Central MyQ Dashboard。
# 旧的 /cu/case-dashboard 会进错客服 case 页面，经常搜不到 5461 application case。
CASE_DASHBOARD_URLS = {
    "amazon.co.uk":  "https://sellercentral.amazon.co.uk/hz/myqdashboard/ref=xx_myqd_favb_xx",
    "amazon.com":    "https://sellercentral.amazon.com/hz/myqdashboard/ref=xx_myqd_favb_xx",
    "amazon.de":     "https://sellercentral.amazon.de/hz/myqdashboard/ref=xx_myqd_favb_xx",
    "amazon.fr":     "https://sellercentral.amazon.fr/hz/myqdashboard/ref=xx_myqd_favb_xx",
    "amazon.es":     "https://sellercentral.amazon.es/hz/myqdashboard/ref=xx_myqd_favb_xx",
    "amazon.it":     "https://sellercentral.amazon.it/hz/myqdashboard/ref=xx_myqd_favb_xx",
    "amazon.com.mx": "https://sellercentral.amazon.com/hz/myqdashboard/ref=xx_myqd_favb_xx",
    "amazon.nl":     "https://sellercentral.amazon.nl/hz/myqdashboard/ref=xx_myqd_favb_xx",
    "amazon.se":     "https://sellercentral.amazon.se/hz/myqdashboard/ref=xx_myqd_favb_xx",
    "amazon.pl":     "https://sellercentral.amazon.pl/hz/myqdashboard/ref=xx_myqd_favb_xx",
}


def load_account(account_id: str) -> dict:
    with open(CONFIG_PATH, encoding='utf-8') as f:
        data = json.load(f)
    accounts = data.get('accounts', data)
    for a in accounts:
        if a.get('account_id') == account_id:
            return a
    raise ValueError(f"Account not found: {account_id}")


def get_marketplace_config(account: dict, site: str) -> dict:
    """Get marketplace config for given site."""
    configs = account.get('marketplace_configs', {})
    if site in configs:
        return configs[site]
    # Fallback to account-level
    return {
        'marketplace': account.get('marketplace', site),
        'domain': account.get('domain', 'amazon.co.uk'),
        'mons_sel_mkid': account.get('mons_sel_mkid', ''),
    }


def extract_case_ids_from_page(page, brand_name: str) -> list:
    """
    从 Case Dashboard 页面提取与品牌相关的 Case ID。
    返回 [(case_id, subject, date), ...] 列表。
    """
    results = []

    # 等待页面加载
    try:
        page.wait_for_load_state("networkidle", timeout=3000)
    except Exception:
        pass

    # 方法1: 从页面文字中匹配 Case ID（11位数字）+ 品牌名附近
    page_text = page.evaluate("() => document.body.innerText")

    # 先找所有 Case ID 候选（10-12位数字）
    all_case_ids = re.findall(r'\b(\d{10,12})\b', page_text)

    # 找品牌名附近的行
    lines = page_text.split('\n')
    brand_lower = brand_name.lower()

    brand_lines_idx = [i for i, l in enumerate(lines) if brand_lower in l.lower()]
    for idx in brand_lines_idx:
        # 搜索附近 ±5 行内的 Case ID
        window = lines[max(0, idx-5):idx+6]
        window_text = '\n'.join(window)
        ids_in_window = re.findall(r'\b(\d{10,12})\b', window_text)
        for cid in ids_in_window:
            subject_line = lines[idx].strip()
            results.append((cid, subject_line, ""))

    # 方法2: 尝试从 DOM 结构提取（Case Dashboard 行）
    try:
        rows = page.evaluate("""() => {
            const results = [];
            // Try common Case Dashboard row selectors
            const selectors = [
                'tr[data-case-id]',
                '[data-testid="case-row"]',
                '.case-list-item',
                'kat-table-row',
            ];
            for (const sel of selectors) {
                const els = document.querySelectorAll(sel);
                if (els.length > 0) {
                    els.forEach(el => {
                        const text = el.innerText || el.textContent || '';
                        const idMatch = text.match(/\\b(\\d{10,12})\\b/);
                        if (idMatch) {
                            results.push({case_id: idMatch[1], text: text.trim().substring(0, 200)});
                        }
                    });
                    break;
                }
            }
            // Fallback: scan all elements with long numeric IDs
            if (results.length === 0) {
                const allText = document.body.innerText;
                const matches = [...allText.matchAll(/\\b(\\d{10,12})\\b/g)];
                matches.forEach(m => results.push({case_id: m[1], text: ''}));
            }
            return results;
        }""")

        for row in rows:
            cid = row.get('case_id', '')
            text = row.get('text', '')
            if cid and brand_lower in text.lower():
                results.append((cid, text[:100], ""))

    except Exception as e:
        print(f"[DOM提取] 异常: {e}")

    # 去重
    seen = set()
    unique = []
    for item in results:
        if item[0] not in seen:
            seen.add(item[0])
            unique.append(item)

    return unique


def update_excel(excel_path: Path, account_id: str, brand_name: str,
                 site: str, case_id: str):
    """更新 Excel 登记表中对应行的 Case ID。"""
    wb = openpyxl.load_workbook(excel_path)
    ws = wb['5461申请记录']

    updated = False
    for row in ws.iter_rows(min_row=2):
        r_account = str(row[3].value or '')
        r_site    = str(row[2].value or '')
        r_brand   = str(row[4].value or '')
        r_case_id = row[6].value

        if (account_id in r_account and
                r_site.upper() == site.upper() and
                r_brand.upper() == brand_name.upper() and
                not r_case_id):
            row[6].value = case_id
            row[7].value = '申请中'
            row[8].value = f'check_case_log 补录 {datetime.now().strftime("%H:%M")}'
            updated = True
            print(f"[Excel] 已更新行 {row[0].row}: Case ID = {case_id}")
            break  # 只更新最近一条空 Case ID 的记录

    if not updated:
        print(f"[Excel] 未找到需要更新的行（账号={account_id}, 站点={site}, 品牌={brand_name}）")

    wb.save(excel_path)
    return updated


def update_sqlite(account_id: str, brand_name: str, site: str, case_id: str):
    """更新 SQLite submissions 表。"""
    import sqlite3
    db_path = PROJECT_ROOT / "data" / "ledger.db"
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    # 先查是否有该记录
    cur.execute(
        "SELECT id FROM submissions WHERE account_id=? AND brand_name=? AND marketplace=? AND (case_id IS NULL OR case_id='')",
        (account_id, brand_name, site)
    )
    row = cur.fetchone()
    if row:
        cur.execute(
            "UPDATE submissions SET case_id=?, submit_result='success', note='check_case_log 补录' WHERE id=?",
            (case_id, row[0])
        )
        print(f"[DB] 已更新 submissions id={row[0]}: Case ID = {case_id}")
    else:
        cur.execute(
            "INSERT INTO submissions (account_id, marketplace, brand_name, submitted_at, case_id, submit_result, note) VALUES (?,?,?,?,?,?,?)",
            (account_id, site, brand_name, datetime.now().isoformat(), case_id, 'success', 'check_case_log 补录')
        )
        print(f"[DB] 已新增 submissions 记录: Case ID = {case_id}")
    conn.commit()
    conn.close()


def check_case_log(account_id: str, brand_name: str, site: str = "BE") -> Optional[str]:
    print(f"{'='*60}")
    print(f"Case Log 检查: {account_id} / {brand_name} / {site}")
    print(f"{'='*60}")

    account = load_account(account_id)
    mk_config = get_marketplace_config(account, site)
    domain = mk_config.get('domain', 'amazon.co.uk')
    profile_id = account.get('adspower_profile_id', '')

    if not profile_id:
        print(f"[错误] 账号 {account_id} 没有 adspower_profile_id")
        return None

    print(f"账号: {account_id}")
    print(f"AdsPower profile: {profile_id}")
    print(f"域名: {domain}")
    print(f"站点: {site}")

    # Case Dashboard URL
    case_url = CASE_DASHBOARD_URLS.get(domain, f"https://sellercentral.{domain}/hz/myqdashboard/ref=xx_myqd_favb_xx")
    print(f"Case Dashboard: {case_url}")

    bm = BrowserManager()
    try:
        # 连接 AdsPower 浏览器
        print(f"\n[浏览器] 连接 AdsPower: {profile_id}")
        bm.connect(profile_id)
        page = bm.page

        # 切换到目标 marketplace
        print(f"[市场切换] 切换到 {site}...")
        try:
            switch_marketplace(page, site)
            print(f"[市场切换] ✅ 成功切换到 {site}")
        except Exception as e:
            print(f"[市场切换] 警告: {e}，继续尝试...")

        # 导航到 Case Dashboard
        print(f"[导航] 打开 Case Dashboard...")
        page.goto(case_url, wait_until='domcontentloaded', timeout=30000)
        # 等待页面内容加载（Case Dashboard 用 iframe / lazy load）
        try:
            page.wait_for_load_state("networkidle", timeout=6000)
        except Exception:
            pass

        # 截图留证（超时不阻断）
        evidence_dir = PROJECT_ROOT / "evidence" / datetime.now().strftime('%Y-%m-%d') / account_id / brand_name / "case_log"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime('%H%M%S')
        try:
            # 尝试滚动到主内容区域
            page.evaluate("window.scrollTo(0, 300)")
            page.screenshot(path=str(evidence_dir / f"case_dashboard_{ts}.png"), timeout=15000)
            print(f"[截图] 已保存: {evidence_dir}/case_dashboard_{ts}.png")
        except Exception as se:
            print(f"[截图] 超时跳过: {se}")
            try:
                page.screenshot(path=str(evidence_dir / f"case_dashboard_{ts}.png"),
                                full_page=False, clip={"x":0,"y":0,"width":1280,"height":900}, timeout=10000)
                print(f"[截图] 可见区域已保存")
            except Exception:
                pass

        # 提取 Case ID
        print(f"[提取] 搜索与 {brand_name} 相关的 Case...")
        found = extract_case_ids_from_page(page, brand_name)

        # 不再硬跳 /abis/approval/search：该 URL 不是此流程正确的 Selling
        # Applications/Draft 复核入口，可能停留在旧 Add Product 页面造成误判。
        # Draft 续提应使用 Dashboard/当前页面实际发现的 Go to application 链接。

        if not found:
            print(f"[结果] ❌ 未找到与 {brand_name} 相关的 Case ID")
            print(f"[提示] 请手动查看截图: {evidence_dir}/case_dashboard_{ts}.png")
            return None

        print(f"[结果] 找到 {len(found)} 个候选 Case:")
        for cid, subject, date in found:
            print(f"  Case ID: {cid}  |  {subject[:80]}")

        # 取第一个（最新的）
        best_case_id = found[0][0]
        print(f"\n[选定] Case ID: {best_case_id}")

        # 更新 Excel 和 DB
        update_excel(EXCEL_PATH, account_id, brand_name, site, best_case_id)
        update_sqlite(account_id, brand_name, site, best_case_id)

        print(f"\n{'='*60}")
        print(f"✅ Case ID 已补录: {best_case_id}")
        print(f"{'='*60}")
        return best_case_id

    except Exception as e:
        print(f"[错误] 检查 Case Log 时异常: {e}")
        import traceback
        traceback.print_exc()
        return None

    finally:
        try:
            bm.close()
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser(description='检查 Amazon Seller Central Case Log，提取并记录 Case ID')
    parser.add_argument('account_id', help='账号 ID (如 us_store_580)')
    parser.add_argument('brand_name', help='品牌名 (如 HOMEMO)')
    parser.add_argument('--site', default='BE', help='站点 (默认 BE)')
    args = parser.parse_args()

    result = check_case_log(args.account_id, args.brand_name, args.site)
    if result:
        print(f"\n结果: Case ID = {result}")
        sys.exit(0)
    else:
        print(f"\n结果: 未找到 Case ID，请手动检查")
        sys.exit(1)


if __name__ == '__main__':
    main()
