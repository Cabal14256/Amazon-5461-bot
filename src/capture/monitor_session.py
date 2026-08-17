"""
旁路监控会话 -- 整合 PageMonitor + NetworkCapture + 周期性快照/截图。

设计原则:
- start() / stop() 接口，可在任何 with 语句或 try/finally 中使用
- 轻量级，不阻塞主流程
- 失败静默（采集失败不影响主流程）
- 自动脱敏（复用 redact.py）
"""

import json
import time
import threading
from pathlib import Path
from datetime import datetime
from typing import Optional, List

from .monitor import PageMonitor
from .network_capture import NetworkCapture
from .page_capture import PageCapture
from .redact import redact_text


class MonitorSession:
    """
    旁路监控会话 -- 整合 PageMonitor + NetworkCapture + 周期性快照/截图。
    """

    def __init__(
        self,
        page,
        out_dir: str,
        snapshot_interval_sec: int = 30,
        screenshot_interval_sec: int = 60,
        enable_snapshots: bool = True,
        enable_screenshots: bool = False,
        enable_network: bool = True,
        enable_dom_monitor: bool = True,
    ):
        self.page = page
        self.out_dir = Path(out_dir)
        self.snapshot_interval_sec = snapshot_interval_sec
        self.screenshot_interval_sec = screenshot_interval_sec
        self.enable_snapshots = enable_snapshots
        self.enable_screenshots = enable_screenshots
        self.enable_network = enable_network
        self.enable_dom_monitor = enable_dom_monitor

        self._started = False
        self._start_time: Optional[float] = None
        self._stop_time: Optional[float] = None
        self._timer_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # 子监控器
        self._page_monitor: Optional[PageMonitor] = None
        self._network_capture: Optional[NetworkCapture] = None
        self._page_capture: Optional[PageCapture] = None

        # 统计
        self._snapshot_count = 0
        self._screenshot_count = 0
        self._console_events: list = []

    def start(self) -> bool:
        """
        初始化所有监控子系统，启动周期性采集线程。
        返回 True/False 表示是否成功启动。
        """
        if self._started:
            return True

        try:
            self.out_dir.mkdir(parents=True, exist_ok=True)

            if self.enable_dom_monitor and self._page_monitor is None:
                self._page_monitor = PageMonitor(self.page, redact_fn=redact_text)
                self._page_monitor.start()

            if self.enable_network and self._network_capture is None:
                self._network_capture = NetworkCapture(self.page, redact_fn=redact_text)
                self._network_capture.start()

            if (self.enable_snapshots or self.enable_screenshots) and self._page_capture is None:
                self._page_capture = PageCapture(self.page, redact_fn=redact_text)

            self._started = True
            self._start_time = time.time()

            # 注意：不启动后台线程——Playwright sync API 不支持跨 greenlet 调用
            # 周期性采集通过主流程调用 drain_and_save() 实现

            return True
        except Exception as e:
            print(f"[WARN] MonitorSession start failed: {e}")
            return False

    def stop(self) -> dict:
        """
        停止所有监控，保存数据到磁盘，返回摘要 dict。
        """
        if not self._started:
            return self.get_summary()

        self._started = False
        self._stop_time = time.time()

        # 最后 drain 一次 DOM 事件
        if self._page_monitor:
            try:
                self._page_monitor.drain_events()
                self._page_monitor.stop()
            except Exception as e:
                print(f"[WARN] PageMonitor stop failed: {e}")

        # 停止网络监听
        if self._network_capture:
            try:
                self._network_capture.stop()
            except Exception as e:
                print(f"[WARN] NetworkCapture stop failed: {e}")

        # 保存到磁盘
        try:
            self.save()
        except Exception as e:
            print(f"[WARN] MonitorSession save failed: {e}")

        return self.get_summary()

    def drain_and_save(self):
        """
        中途保存当前收集到的数据（不停止监控）。
        用于长时间运行时的增量保存。
        """
        if not self._started:
            return

        # drain DOM 事件
        if self._page_monitor:
            try:
                self._page_monitor.drain_events()
            except Exception as e:
                print(f"[WARN] drain_events failed: {e}")

        # 保存当前数据
        try:
            self.save()
        except Exception as e:
            print(f"[WARN] drain_and_save failed: {e}")

    def save(self, out_dir: Path = None):
        """
        写入 events.jsonl, network.jsonl, console.jsonl。
        如有快照写入 snapshots/，如有截图写入 screenshots/。
        """
        out_dir = Path(out_dir) if out_dir else self.out_dir
        out_dir.mkdir(parents=True, exist_ok=True)

        # 保存 DOM 事件
        if self._page_monitor and self._page_monitor.events:
            events_path = out_dir / "events.jsonl"
            with open(events_path, "w", encoding="utf-8") as f:
                for event in self._page_monitor.events:
                    f.write(json.dumps({
                        "timestamp": event.timestamp,
                        "type": event.type,
                        "detail": event.detail,
                    }, ensure_ascii=False) + "\n")

        # 保存网络事件
        if self._network_capture and self._network_capture.events:
            network_path = out_dir / "network.jsonl"
            with open(network_path, "w", encoding="utf-8") as f:
                for event in self._network_capture.events:
                    f.write(json.dumps({
                        "timestamp": event.timestamp,
                        "type": event.type,
                        "method": event.method,
                        "url": event.url,
                        "status": event.status,
                        "content_type": event.content_type,
                        "resource_type": event.resource_type,
                        "error_text": event.error_text,
                        "from_service_worker": event.from_service_worker,
                    }, ensure_ascii=False) + "\n")

        # 保存 console 错误
        console_errors = self._get_all_console_errors()
        if console_errors:
            console_path = out_dir / "console.jsonl"
            with open(console_path, "w", encoding="utf-8") as f:
                for err in console_errors:
                    f.write(json.dumps(err, ensure_ascii=False) + "\n")

    def get_summary(self) -> dict:
        """
        返回监控摘要：事件数、429/410001 出现次数、console 错误数等。
        """
        events_count = len(self._page_monitor.events) if self._page_monitor else 0
        requests_count = len(self._network_capture.events) if self._network_capture else 0
        console_errors_count = len(self._get_all_console_errors())

        rate_limit_count = 0
        error_410001_count = 0
        if self._network_capture:
            for e in self._network_capture.events:
                if e.status == 429:
                    rate_limit_count += 1
                if e.url and "410001" in e.url:
                    error_410001_count += 1

        duration_sec = 0.0
        if self._start_time:
            end = self._stop_time or time.time()
            duration_sec = round(end - self._start_time, 2)

        return {
            "events_count": events_count,
            "requests_count": requests_count,
            "console_errors_count": console_errors_count,
            "snapshots_count": self._snapshot_count,
            "screenshots_count": self._screenshot_count,
            "rate_limit_429_count": rate_limit_count,
            "error_410001_count": error_410001_count,
            "duration_sec": duration_sec,
            "out_dir": str(self.out_dir),
        }

    def get_errors(self) -> list:
        """
        返回网络错误（4xx/5xx + requestfailed）列表，用于快速诊断。
        """
        if not self._network_capture:
            return []

        errors = []
        for e in self._network_capture.events:
            if e.type == "requestfailed":
                errors.append({
                    "timestamp": e.timestamp,
                    "type": e.type,
                    "method": e.method,
                    "url": e.url,
                    "error_text": e.error_text,
                })
            elif e.status and e.status >= 400:
                errors.append({
                    "timestamp": e.timestamp,
                    "type": e.type,
                    "method": e.method,
                    "url": e.url,
                    "status": e.status,
                })
        return errors

    def get_rate_limit_events(self) -> list:
        """
        返回 429 事件列表。
        """
        if not self._network_capture:
            return []

        events = []
        for e in self._network_capture.events:
            if e.status == 429:
                events.append({
                    "timestamp": e.timestamp,
                    "type": e.type,
                    "method": e.method,
                    "url": e.url,
                    "status": e.status,
                })
        return events

    def _periodic_loop(self):
        """
        后台线程：周期性执行快照和截图。
        """
        last_snapshot = 0.0
        last_screenshot = 0.0

        while not self._stop_event.is_set():
            now = time.time()

            # DOM 事件定期 drain
            if self._page_monitor and self._started:
                try:
                    self._page_monitor.drain_events()
                except Exception as e:
                    print(f"[WARN] periodic drain_events failed: {e}")

            # 周期性快照
            if self.enable_snapshots and self._page_capture and (now - last_snapshot >= self.snapshot_interval_sec):
                try:
                    self._do_snapshot()
                    last_snapshot = now
                except Exception as e:
                    print(f"[WARN] periodic snapshot failed: {e}")

            # 周期性截图
            if self.enable_screenshots and self._page_capture and (now - last_screenshot >= self.screenshot_interval_sec):
                try:
                    self._do_screenshot()
                    last_screenshot = now
                except Exception as e:
                    print(f"[WARN] periodic screenshot failed: {e}")

            # 每秒检查一次
            self._stop_event.wait(timeout=1.0)

    def _do_snapshot(self):
        """执行一次 DOM 快照并保存到 snapshots/ 目录。"""
        if not self._page_capture:
            return

        snapshots_dir = self.out_dir / "snapshots"
        snapshots_dir.mkdir(parents=True, exist_ok=True)

        name = f"snapshot_{self._snapshot_count:04d}_{int(time.time())}"
        self._page_capture.quick_capture(name, snapshots_dir)
        self._snapshot_count += 1

    def _do_screenshot(self):
        """执行一次截图并保存到 screenshots/ 目录。"""
        if not self._page_capture or not self.page:
            return

        screenshots_dir = self.out_dir / "screenshots"
        screenshots_dir.mkdir(parents=True, exist_ok=True)

        path = screenshots_dir / f"screenshot_{self._screenshot_count:04d}_{int(time.time())}.png"
        try:
            self.page.screenshot(path=str(path), full_page=True)
            self._screenshot_count += 1
        except Exception as e:
            print(f"[WARN] screenshot failed: {e}")

    def _get_all_console_errors(self) -> list:
        """获取所有 console 错误事件（来自 PageMonitor）。"""
        errors = []
        if self._page_monitor:
            for e in self._page_monitor.events:
                if e.type == "console" and e.detail.get("level") == "error":
                    errors.append({
                        "timestamp": e.timestamp,
                        "type": e.type,
                        "level": e.detail.get("level"),
                        "message": e.detail.get("message", ""),
                    })
        return errors

    @classmethod
    def for_brand(
        cls,
        page,
        account_id: str,
        brand_name: str,
        evidence_root: str = "runtime/evidence",
        **kwargs
    ) -> "MonitorSession":
        """
        工厂方法：自动生成 out_dir = runtime/evidence/{date}/{account}/{brand}/monitor/
        """
        today = datetime.now().strftime("%Y-%m-%d")
        out_dir = Path(evidence_root) / today / account_id / brand_name / "monitor"
        return cls(page=page, out_dir=str(out_dir), **kwargs)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
