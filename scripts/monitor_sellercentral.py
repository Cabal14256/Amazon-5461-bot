"""
实时监控工具 — 旁路监控 Seller Central 页面

用法:
    python scripts/monitor_sellercentral.py \
        --cdp http://127.0.0.1:9222 \
        --out evidence/live-monitor \
        --interval 2 \
        --screenshot-interval 30 \
        --duration 300

产出:
    evidence/live-monitor/20260518-143022/
    ├── events.jsonl          # DOM 变化 + Console 错误
    ├── network.jsonl         # 请求/响应/失败
    ├── snapshots/            # 每次页面变化时的 dom_snapshot.json
    └── screenshots/          # 每 30s 全页截图
"""

import argparse
import sys
import time
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from playwright.sync_api import sync_playwright
from src.capture import PageMonitor, NetworkCapture, PageCapture, default_redact


def main():
    parser = argparse.ArgumentParser(description="Seller Central 实时监控")
    parser.add_argument("--cdp", required=True, help="CDP 地址")
    parser.add_argument("--out", default="evidence/live-monitor", help="输出根目录")
    parser.add_argument("--interval", type=float, default=2.0, help="DOM 事件 drain 间隔（秒）")
    parser.add_argument("--screenshot-interval", type=int, default=30, help="截图间隔（秒）")
    parser.add_argument("--duration", type=int, default=0, help="运行时长（秒），0=无限")
    parser.add_argument("--url", help="如果指定，先导航到该 URL")
    parser.add_argument("--no-network", action="store_true", help="不采集网络请求")

    args = parser.parse_args()

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out) / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    snapshots_dir = out_dir / "snapshots"
    screenshots_dir = out_dir / "screenshots"
    snapshots_dir.mkdir(exist_ok=True)
    screenshots_dir.mkdir(exist_ok=True)

    print(f"[Monitor] 启动监控 | Run ID: {run_id}")
    print(f"[Monitor] 输出目录: {out_dir}")
    print(f"[Monitor] DOM drain 间隔: {args.interval}s")
    print(f"[Monitor] 截图间隔: {args.screenshot_interval}s")
    if args.duration > 0:
        print(f"[Monitor] 运行时长: {args.duration}s")
    print(f"[Monitor] 按 Ctrl+C 停止")

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(args.cdp)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.pages[0] if context.pages else context.new_page()

        if args.url:
            page.goto(args.url, wait_until="networkidle")

        # 初始化监控器
        monitor = PageMonitor(page, redact_fn=default_redact)
        monitor.start()

        # 初始化网络采集
        net_capture = None
        if not args.no_network:
            net_capture = NetworkCapture(page, redact_fn=default_redact)
            net_capture.start()

        # 初始化页面采集（用于截图）
        page_capture = PageCapture(page, redact_fn=default_redact)

        last_screenshot = 0
        snapshot_counter = 0
        start_time = time.time()

        try:
            while True:
                # 检查运行时长
                if args.duration > 0 and (time.time() - start_time) >= args.duration:
                    print("[Monitor] 达到设定时长，停止监控")
                    break

                # Drain DOM 事件
                monitor.drain_events()

                # 检查是否有显著变化，有则保存快照
                if monitor.has_significant_changes(min_added=1):
                    snapshot_counter += 1
                    snapshot = monitor.take_snapshot()
                    snapshot_path = snapshots_dir / f"snapshot_{snapshot_counter:04d}_{int(time.time())}.json"
                    import json
                    with open(snapshot_path, "w", encoding="utf-8") as f:
                        json.dump(snapshot, f, ensure_ascii=False, indent=2)
                    print(f"[Monitor] 检测到 DOM 变化，保存快照 #{snapshot_counter}")

                # 定期截图
                now = time.time()
                if now - last_screenshot >= args.screenshot_interval:
                    screenshot_name = f"screenshot_{int(now)}"
                    screenshot_path = screenshots_dir / f"{screenshot_name}.png"
                    try:
                        page.screenshot(path=str(screenshot_path), full_page=True)
                        print(f"[Monitor] 截图保存: {screenshot_path.name}")
                    except Exception as e:
                        print(f"[Monitor] 截图失败: {e}")
                    last_screenshot = now

                # 检查 console 错误
                errors = monitor.get_console_errors()
                if errors:
                    print(f"[Monitor] Console 错误: {len(errors)} 条")

                time.sleep(args.interval)

        except KeyboardInterrupt:
            print("\n[Monitor] 收到中断信号，停止监控")

        finally:
            # 保存所有事件
            print("[Monitor] 保存监控数据...")
            monitor.stop()
            monitor.save(out_dir)

            if net_capture:
                net_capture.stop()
                net_capture.save(out_dir / "network.jsonl")

            # 保存最终摘要
            summary = {
                "run_id": run_id,
                "start_time": datetime.fromtimestamp(start_time).isoformat(),
                "end_time": datetime.now().isoformat(),
                "duration_seconds": time.time() - start_time,
                "total_events": len(monitor.events),
                "total_mutations": len([e for e in monitor.events if e.type == "mutation"]),
                "total_console_errors": len(monitor.get_console_errors()),
                "snapshot_count": snapshot_counter,
                "final_url": page.url,
            }

            if net_capture:
                summary["total_network_events"] = len(net_capture.events)
                summary["network_errors"] = len(net_capture.get_error_events())

            import json
            with open(out_dir / "summary.json", "w", encoding="utf-8") as f:
                json.dump(summary, f, ensure_ascii=False, indent=2)

            print(f"[Monitor] 监控结束 | 总事件: {summary['total_events']} | 目录: {out_dir}")
            browser.close()


if __name__ == "__main__":
    main()
