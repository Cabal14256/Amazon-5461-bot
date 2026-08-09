#!/usr/bin/env python3
"""
run_sequential_be.py - 逐个提交，品牌间随机等待，避免 429/410001

用法:
    python scripts/run_sequential_be.py <account_id> <brand1,brand2,...> [--site SITE] [--min-wait N] [--max-wait N]
"""
import sys, time, random, subprocess, json, argparse
from pathlib import Path
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

PROJECT_ROOT = Path(__file__).parent.parent


def run_brand(account_id: str, brand: str, site: str) -> dict:
    cmd = [
        sys.executable,
        str(PROJECT_ROOT / 'submit_5461.py'),
        account_id, brand,
        '--site', site
    ]
    print(f'\n{"="*60}')
    print(f'[{datetime.now().strftime("%H:%M:%S")}] 开始: {account_id} / {brand} / {site}')
    print(f'{"="*60}')

    result = subprocess.run(
        cmd,
        capture_output=False,
        text=True,
        cwd=str(PROJECT_ROOT)
    )

    # Read the latest result JSON
    results_dir = PROJECT_ROOT / 'artifacts' / 'results'
    json_files = sorted(results_dir.glob(f'submit_5461_{brand}_*.json'))
    if json_files:
        with open(json_files[-1], encoding='utf-8') as f:
            return json.load(f)
    return {'brand': brand, 'success': result.returncode == 0, 'case_id': None}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('account_id')
    parser.add_argument('brands', help='逗号分隔品牌列表')
    parser.add_argument('--site', default='BE')
    parser.add_argument('--min-wait', type=int, default=25, help='最短等待秒数（默认25）')
    parser.add_argument('--max-wait', type=int, default=55, help='最长等待秒数（默认55）')
    args = parser.parse_args()

    brands = [b.strip() for b in args.brands.split(',') if b.strip()]
    summary = []

    print(f'账号: {args.account_id}  站点: {args.site}')
    print(f'品牌列表: {brands}')
    print(f'随机间隔: {args.min_wait}~{args.max_wait} 秒')
    print()

    for i, brand in enumerate(brands):
        result = run_brand(args.account_id, brand, args.site)
        summary.append(result)

        status = '✅' if result.get('success') else '❌'
        case_id = result.get('case_id') or '未找到'
        print(f'\n[结果] {status} {brand}: Case ID = {case_id}')

        # 品牌间随机等待（最后一个不等）
        if i < len(brands) - 1:
            wait = random.randint(args.min_wait, args.max_wait)
            print(f'\n[冷却] 等待 {wait} 秒后继续下一品牌...')
            for remaining in range(wait, 0, -5):
                print(f'  {remaining}s...')
                time.sleep(min(5, remaining))

    print(f'\n{"="*60}')
    print('执行摘要')
    print(f'{"="*60}')
    for r in summary:
        status = '✅' if r.get('success') else '❌'
        case_id = r.get('case_id') or '未找到'
        print(f'  {status} {r.get("brand","?"):20s}  Case ID: {case_id}')
    print(f'{"="*60}')


if __name__ == '__main__':
    main()
