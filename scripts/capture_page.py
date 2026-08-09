"""
单次页面采集 CLI — 用于人工操作时旁路记录

用法:
    python scripts/capture_page.py --cdp http://127.0.0.1:9222 --out evidence/manual --name "uk-old-ui-check"
    python scripts/capture_page.py --cdp http://127.0.0.1:9222 --out evidence/manual --name "connect-brand-popup" --url https://sellercentral.amazon.com/...

需要 Playwright 已安装: pip install playwright
"""

import argparse
import time
import sys
from pathlib import Path
from datetime import datetime

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.capture import PageCapture, default_redact


def main():
    parser = argparse.ArgumentParser(description="单次页面证据采集")
    parser.add_argument("--cdp", required=True, help="CDP 地址，如 http://127.0.0.1:9222")
    parser.add_argument("--out", default="evidence/manual", help="输出目录")
    parser.add_argument("--name", required=True, help="证据包名称")
    parser.add_argument("--url", help="如果指定，先导航到该 URL 再采集")
    parser.add_argument("--wait", type=int, default=3, help="页面加载后等待秒数")
    parser.add_argument("--full-page", action="store_true", default=True, help="全页截图")
    parser.add_argument("--no-screenshot", action="store_true", help="不截图，仅采集结构化数据")

    args = parser.parse_args()

    out_dir = Path(args.out) / datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[Capture] 连接到 CDP: {args.cdp}")
    print(f"[Capture] 输出目录: {out_dir}")

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        # 连接到已有浏览器
        browser = p.chromium.connect_over_cdp(args.cdp)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.pages[0] if context.pages else context.new_page()

        if args.url:
            print(f"[Capture] 导航到: {args.url}")
            page.goto(args.url, wait_until="networkidle")

        print(f"[Capture] 等待 {args.wait}s...")
        time.sleep(args.wait)

        # 采集
        capture = PageCapture(page, redact_fn=default_redact)
        evidence = capture.capture(args.name, out_dir)

        print(f"[Capture] 完成!")
        print(f"[Capture] URL: {evidence.url}")
        print(f"[Capture] Title: {evidence.title}")
        print(f"[Capture] Body Length: {evidence.body_length}")
        print(f"[Capture] Detected States: {evidence.detected_states or 'none'}")
        print(f"[Capture] Alerts: {len(evidence.alerts)}")
        print(f"[Capture] Interactives: {len(evidence.interactives)}")
        print(f"[Capture] Screenshot: {evidence.screenshot_path or 'none'}")
        print(f"[Capture] 文件位置: {out_dir}")

        browser.close()


if __name__ == "__main__":
    main()
