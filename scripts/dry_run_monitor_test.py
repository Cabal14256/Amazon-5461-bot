#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
旁路监控模块的纯 Mock 测试 -- 完全不依赖浏览器。

测试范围:
1. MonitorSession start/stop 接口
2. get_errors() 正确返回 4xx/5xx 事件
3. get_rate_limit_events() 正确过滤 429
4. save() 写入 events.jsonl / network.jsonl
5. MonitorMerger.get_errors_summary() 正确解析已有的 network.jsonl
6. run_full_5461_batch.py --help 包含 --enable-monitor 参数
"""

import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)

import json
import tempfile
import shutil
import subprocess
from pathlib import Path
from dataclasses import dataclass, asdict

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.capture.monitor_merger import MonitorMerger


# ---------------------------------------------------------------------------
# Mock 对象
# ---------------------------------------------------------------------------

class MockPage:
    """模拟 Playwright Page，支持 evaluate 和 screenshot。"""

    def __init__(self):
        self._eval_results = {}
        self._screenshot_count = 0

    def evaluate(self, script: str):
        # 返回固定值，模拟监控脚本注入成功
        return "monitor_started"

    def screenshot(self, path: str, full_page: bool = True):
        self._screenshot_count += 1
        # 创建空文件模拟截图
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"\x89PNG\r\n\x1a\n")

    def on(self, event: str, handler):
        pass


class MockPageMonitor:
    """Mock PageMonitor，用于测试 MonitorSession 不依赖真实浏览器。"""

    def __init__(self, page, redact_fn=None):
        self.page = page
        self.redact = redact_fn
        self.events = []
        self._started = False

    def start(self):
        self._started = True
        return "monitor_started"

    def stop(self):
        self._started = False

    def drain_events(self):
        # 模拟产生一些事件
        from src.capture.monitor import DOMEvent
        import time
        self.events.append(DOMEvent(
            timestamp=time.time(),
            type="mutation",
            detail={"addedNodes": 1, "targetTag": "DIV"},
        ))
        self.events.append(DOMEvent(
            timestamp=time.time(),
            type="console",
            detail={"level": "error", "message": "Test console error"},
        ))
        return self.events[-2:]

    def take_snapshot(self):
        return {"url": "https://example.com", "title": "Test"}


class MockNetworkCapture:
    """Mock NetworkCapture，用于测试。"""

    def __init__(self, page, redact_fn=None):
        self.page = page
        self.redact = redact_fn
        self.events = []
        self._started = False

    def start(self):
        self._started = True

    def stop(self):
        self._started = False

    def clear(self):
        self.events.clear()

    def get_events(self):
        return self.events.copy()

    def get_error_events(self):
        return [e for e in self.events if e.type == "requestfailed" or (e.status and e.status >= 400)]

    def has_status_code(self, code: int) -> bool:
        return any(e.status == code for e in self.events if e.type == "response")

    def save(self, out_path: Path):
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            for event in self.events:
                f.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------------------

def test_monitor_session_start_stop():
    """测试 1: MonitorSession start/stop 接口"""
    print("[INFO] Test 1: MonitorSession start/stop ...")

    from src.capture.monitor_session import MonitorSession

    page = MockPage()
    tmpdir = tempfile.mkdtemp()
    try:
        session = MonitorSession(
            page=page,
            out_dir=tmpdir,
            enable_snapshots=False,
            enable_screenshots=False,
            enable_dom_monitor=False,   # 禁用，避免后台线程干扰
            enable_network=False,
        )

        # 使用 monkeypatch 替换真实的子监控器为 Mock
        session._page_monitor = MockPageMonitor(page)
        session._network_capture = MockNetworkCapture(page)

        ok = session.start()
        assert ok is True, "start() should return True"
        assert session._started is True, "session should be started"

        summary = session.stop()
        assert session._started is False, "session should be stopped"
        assert "events_count" in summary, "summary should have events_count"
        assert "requests_count" in summary, "summary should have requests_count"

        print("[OK] Test 1 passed")
        return True
    except Exception as e:
        print(f"[NG] Test 1 failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_get_errors():
    """测试 2: get_errors() 正确返回 4xx/5xx 事件"""
    print("[INFO] Test 2: get_errors() ...")

    from src.capture.monitor_session import MonitorSession
    from src.capture.network_capture import NetworkEvent

    page = MockPage()
    tmpdir = tempfile.mkdtemp()
    try:
        session = MonitorSession(
            page=page,
            out_dir=tmpdir,
            enable_snapshots=False,
            enable_screenshots=False,
            enable_dom_monitor=False,   # 禁用，避免后台线程干扰
            enable_network=False,
        )

        net = MockNetworkCapture(page)
        import time
        net.events.append(NetworkEvent(
            timestamp=time.time(), type="response", method="GET",
            url="https://example.com/api", status=404,
            content_type="application/json", resource_type="xhr",
        ))
        net.events.append(NetworkEvent(
            timestamp=time.time(), type="response", method="POST",
            url="https://example.com/api2", status=500,
            content_type="text/html", resource_type="document",
        ))
        net.events.append(NetworkEvent(
            timestamp=time.time(), type="request", method="GET",
            url="https://example.com/ok", status=None,
            resource_type="xhr",
        ))
        net.events.append(NetworkEvent(
            timestamp=time.time(), type="requestfailed", method="GET",
            url="https://example.com/fail", error_text="net::ERR_FAILED",
            resource_type="xhr",
        ))

        session._network_capture = net
        session._page_monitor = MockPageMonitor(page)
        session.start()
        session.stop()

        errors = session.get_errors()
        assert len(errors) == 3, f"Expected 3 errors, got {len(errors)}"
        statuses = [e.get("status") for e in errors if "status" in e]
        assert 404 in statuses, "Expected 404 in errors"
        assert 500 in statuses, "Expected 500 in errors"

        print("[OK] Test 2 passed")
        return True
    except Exception as e:
        print(f"[NG] Test 2 failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_get_rate_limit_events():
    """测试 3: get_rate_limit_events() 正确过滤 429"""
    print("[INFO] Test 3: get_rate_limit_events() ...")

    from src.capture.monitor_session import MonitorSession
    from src.capture.network_capture import NetworkEvent

    page = MockPage()
    tmpdir = tempfile.mkdtemp()
    try:
        session = MonitorSession(
            page=page,
            out_dir=tmpdir,
            enable_snapshots=False,
            enable_screenshots=False,
            enable_dom_monitor=False,   # 禁用，避免后台线程干扰
            enable_network=False,
        )

        net = MockNetworkCapture(page)
        import time
        net.events.append(NetworkEvent(
            timestamp=time.time(), type="response", method="GET",
            url="https://example.com/api", status=429,
            content_type="application/json", resource_type="xhr",
        ))
        net.events.append(NetworkEvent(
            timestamp=time.time(), type="response", method="GET",
            url="https://example.com/api2", status=200,
            content_type="application/json", resource_type="xhr",
        ))
        net.events.append(NetworkEvent(
            timestamp=time.time(), type="response", method="POST",
            url="https://example.com/api3", status=429,
            content_type="text/html", resource_type="document",
        ))

        session._network_capture = net
        session._page_monitor = MockPageMonitor(page)
        session.start()
        session.stop()

        rate_limits = session.get_rate_limit_events()
        assert len(rate_limits) == 2, f"Expected 2 rate limit events, got {len(rate_limits)}"

        print("[OK] Test 3 passed")
        return True
    except Exception as e:
        print(f"[NG] Test 3 failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_save_outputs():
    """测试 4: save() 写入 events.jsonl / network.jsonl"""
    print("[INFO] Test 4: save() outputs ...")

    from src.capture.monitor_session import MonitorSession
    from src.capture.network_capture import NetworkEvent
    from src.capture.monitor import DOMEvent

    page = MockPage()
    tmpdir = tempfile.mkdtemp()
    try:
        session = MonitorSession(
            page=page,
            out_dir=tmpdir,
            enable_snapshots=False,
            enable_screenshots=False,
        )

        import time
        pm = MockPageMonitor(page)
        pm.events = [
            DOMEvent(timestamp=time.time(), type="mutation", detail={"addedNodes": 1}),
            DOMEvent(timestamp=time.time(), type="console", detail={"level": "error", "message": "err1"}),
        ]

        net = MockNetworkCapture(page)
        net.events = [
            NetworkEvent(timestamp=time.time(), type="request", method="GET", url="https://a.com"),
            NetworkEvent(timestamp=time.time(), type="response", method="GET", url="https://a.com", status=200),
        ]

        session._page_monitor = pm
        session._network_capture = net
        session.save()

        events_file = Path(tmpdir) / "events.jsonl"
        network_file = Path(tmpdir) / "network.jsonl"
        console_file = Path(tmpdir) / "console.jsonl"

        assert events_file.exists(), "events.jsonl should exist"
        assert network_file.exists(), "network.jsonl should exist"
        assert console_file.exists(), "console.jsonl should exist"

        with open(events_file, "r", encoding="utf-8") as f:
            events_lines = [l for l in f if l.strip()]
        assert len(events_lines) == 2, f"Expected 2 events, got {len(events_lines)}"

        with open(network_file, "r", encoding="utf-8") as f:
            network_lines = [l for l in f if l.strip()]
        assert len(network_lines) == 2, f"Expected 2 network events, got {len(network_lines)}"

        print("[OK] Test 4 passed")
        return True
    except Exception as e:
        print(f"[NG] Test 4 failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_monitor_merger_errors_summary():
    """测试 5: MonitorMerger.get_errors_summary() 正确解析已有的 network.jsonl"""
    print("[INFO] Test 5: MonitorMerger.get_errors_summary() ...")

    tmpdir = tempfile.mkdtemp()
    try:
        monitor_dir = Path(tmpdir) / "monitor"
        monitor_dir.mkdir(parents=True, exist_ok=True)

        import time
        network_events = [
            {"timestamp": time.time(), "type": "request", "method": "GET", "url": "https://a.com", "resource_type": "xhr"},
            {"timestamp": time.time(), "type": "response", "method": "GET", "url": "https://a.com", "status": 200, "resource_type": "xhr"},
            {"timestamp": time.time(), "type": "response", "method": "POST", "url": "https://b.com", "status": 429, "resource_type": "xhr"},
            {"timestamp": time.time(), "type": "response", "method": "GET", "url": "https://c.com", "status": 403, "resource_type": "document"},
            {"timestamp": time.time(), "type": "requestfailed", "method": "GET", "url": "https://d.com", "error_text": "net::ERR_FAILED", "resource_type": "xhr"},
            {"timestamp": time.time(), "type": "response", "method": "GET", "url": "https://e.com", "status": 500, "resource_type": "xhr"},
        ]

        with open(monitor_dir / "network.jsonl", "w", encoding="utf-8") as f:
            for ev in network_events:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")

        merger = MonitorMerger()
        summary = merger.get_errors_summary(str(monitor_dir))

        assert summary["total_requests"] == 5, f"Expected 5 total requests, got {summary['total_requests']}"
        assert summary["rate_limits_429"] == 1, f"Expected 1 rate limit, got {summary['rate_limits_429']}"
        assert summary["errors_4xx"] == 2, f"Expected 2 4xx errors (429+403), got {summary['errors_4xx']}"
        assert summary["errors_5xx"] == 1, f"Expected 1 5xx error, got {summary['errors_5xx']}"
        assert summary["failed_requests"] == 1, f"Expected 1 failed request, got {summary['failed_requests']}"
        assert len(summary["error_details"]) == 4, f"Expected 4 error details, got {len(summary['error_details'])}"

        print("[OK] Test 5 passed")
        return True
    except Exception as e:
        print(f"[NG] Test 5 failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_argparse_enable_monitor():
    """测试 6: run_full_5461_batch.py --help 包含 --enable-monitor 参数"""
    print("[INFO] Test 6: argparse --enable-monitor ...")

    script_path = project_root / "scripts" / "run_full_5461_batch.py"
    try:
        result = subprocess.run(
            [sys.executable, str(script_path), "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        help_text = result.stdout + result.stderr
        assert "--enable-monitor" in help_text, f"--enable-monitor not found in help text:\n{help_text}"
        print("[OK] Test 6 passed")
        return True
    except Exception as e:
        print(f"[NG] Test 6 failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_for_brand_factory():
    """测试 7: MonitorSession.for_brand 工厂方法生成正确路径"""
    print("[INFO] Test 7: MonitorSession.for_brand factory ...")

    from src.capture.monitor_session import MonitorSession

    page = MockPage()
    try:
        session = MonitorSession.for_brand(
            page=page,
            account_id="us_store_123",
            brand_name="TESTBRAND",
            evidence_root="test_evidence",
        )
        expected_suffix = "us_store_123/TESTBRAND/monitor"
        assert session.out_dir.as_posix().endswith(expected_suffix), \
            f"Expected path ending with {expected_suffix}, got {session.out_dir}"
        print("[OK] Test 7 passed")
        return True
    except Exception as e:
        print(f"[NG] Test 7 failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_monitor_merger_diagnose():
    """测试 8: MonitorMerger.diagnose() 自动诊断"""
    print("[INFO] Test 8: MonitorMerger.diagnose() ...")

    tmpdir = tempfile.mkdtemp()
    try:
        monitor_dir = Path(tmpdir) / "monitor"
        monitor_dir.mkdir(parents=True, exist_ok=True)

        import time
        network_events = [
            {"timestamp": time.time(), "type": "response", "method": "GET", "url": "https://a.com", "status": 429},
            {"timestamp": time.time(), "type": "response", "method": "GET", "url": "https://a.com?error=410001", "status": 400},
            {"timestamp": time.time(), "type": "request", "method": "GET", "url": "https://sellercentral.amazon.com/signin"},
        ]
        with open(monitor_dir / "network.jsonl", "w", encoding="utf-8") as f:
            for ev in network_events:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")

        merger = MonitorMerger()
        diagnosis = merger.diagnose(str(monitor_dir))

        assert diagnosis["has_429"] is True, "Expected has_429=True"
        assert diagnosis["has_410001"] is True, "Expected has_410001=True"
        assert diagnosis["has_login_redirect"] is True, "Expected has_login_redirect=True"
        assert len(diagnosis["rate_limit_events"]) == 1, "Expected 1 rate limit event"
        assert "429" in diagnosis["recommendation"], "Expected recommendation to mention 429"

        print("[OK] Test 8 passed")
        return True
    except Exception as e:
        print(f"[NG] Test 8 failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_drain_and_save():
    """测试 9: drain_and_save() 中途保存不停止监控"""
    print("[INFO] Test 9: drain_and_save() ...")

    from src.capture.monitor_session import MonitorSession

    page = MockPage()
    tmpdir = tempfile.mkdtemp()
    try:
        session = MonitorSession(
            page=page,
            out_dir=tmpdir,
            enable_snapshots=False,
            enable_screenshots=False,
            enable_dom_monitor=False,   # 禁用，避免后台线程干扰
            enable_network=False,
        )
        session._page_monitor = MockPageMonitor(page)
        session._network_capture = MockNetworkCapture(page)

        session.start()
        assert session._started is True

        session.drain_and_save()
        assert session._started is True, "drain_and_save should not stop monitoring"

        # 验证文件已写入
        events_file = Path(tmpdir) / "events.jsonl"
        assert events_file.exists(), "events.jsonl should exist after drain_and_save"

        session.stop()
        print("[OK] Test 9 passed")
        return True
    except Exception as e:
        print(f"[NG] Test 9 failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def main():
    print("=" * 70)
    print("MonitorSession / MonitorMerger Dry-Run Tests")
    print("=" * 70)

    tests = [
        test_monitor_session_start_stop,
        test_get_errors,
        test_get_rate_limit_events,
        test_save_outputs,
        test_monitor_merger_errors_summary,
        test_argparse_enable_monitor,
        test_for_brand_factory,
        test_monitor_merger_diagnose,
        test_drain_and_save,
    ]

    passed = 0
    failed = 0
    for test in tests:
        if test():
            passed += 1
        else:
            failed += 1
        print()

    print("=" * 70)
    print(f"Results: {passed} passed, {failed} failed, {len(tests)} total")
    print("=" * 70)

    if failed > 0:
        sys.exit(1)
    else:
        print("[OK] All tests passed!")
        sys.exit(0)


if __name__ == "__main__":
    main()
