"""
实时 DOM 监听 — MutationObserver 注入 + 事件收集

用于检测：按钮突然出现、错误提示弹出、表单加载完成、页面跳转等。
"""

import json
import time
from pathlib import Path
from typing import Optional, Callable, List
from dataclasses import dataclass, asdict


@dataclass
class DOMEvent:
    timestamp: float
    type: str  # "mutation", "console", "navigation", "snapshot"
    detail: dict


class PageMonitor:
    """
    页面实时监控器。

    用法:
        monitor = PageMonitor(page)
        monitor.start()
        # ... 执行操作 ...
        events = monitor.drain_events()
        monitor.stop()
        monitor.save(out_dir)
    """

    # 注入页面的 MutationObserver 脚本
    _MONITOR_SCRIPT = """
    (() => {
        if (window.__sellerMonitor) return;

        window.__sellerMonitorEvents = [];
        window.__sellerMonitor = new MutationObserver((mutations) => {
            for (const m of mutations) {
                const record = {
                    type: 'mutation',
                    timestamp: Date.now(),
                    mutationType: m.type,
                    targetTag: m.target?.tagName || '',
                    targetId: m.target?.id || '',
                    targetClass: m.target?.className || '',
                    addedNodes: m.addedNodes.length,
                    removedNodes: m.removedNodes.length,
                    attributeName: m.attributeName || '',
                    oldValue: (m.oldValue || '').slice(0, 200),
                };
                window.__sellerMonitorEvents.push(record);
                // 限制队列长度，防止内存泄漏
                if (window.__sellerMonitorEvents.length > 5000) {
                    window.__sellerMonitorEvents = window.__sellerMonitorEvents.slice(-3000);
                }
            }
        });

        window.__sellerMonitor.observe(document.body || document.documentElement, {
            childList: true,
            subtree: true,
            attributes: true,
            attributeOldValue: true,
            characterData: true,
            characterDataOldValue: true,
        });

        // 监听 console 错误
        window.__sellerConsoleErrors = [];
        const origError = console.error;
        console.error = function(...args) {
            window.__sellerConsoleErrors.push({
                timestamp: Date.now(),
                type: 'console',
                level: 'error',
                message: args.map(a => String(a)).join(' ').slice(0, 500),
            });
            if (window.__sellerConsoleErrors.length > 1000) {
                window.__sellerConsoleErrors = window.__sellerConsoleErrors.slice(-500);
            }
            return origError.apply(this, args);
        };

        return 'monitor_started';
    })();
    """

    # 提取事件脚本
    _DRAIN_SCRIPT = """
    (() => {
        const events = window.__sellerMonitorEvents || [];
        const consoleErrors = window.__sellerConsoleErrors || [];
        window.__sellerMonitorEvents = [];
        window.__sellerConsoleErrors = [];
        return {
            mutations: events,
            consoleErrors: consoleErrors,
        };
    })();
    """

    # 提取页面快照脚本
    _SNAPSHOT_SCRIPT = """
    (() => {
        const headings = Array.from(document.querySelectorAll('h1, h2, h3'))
            .map(h => ({
                tag: h.tagName,
                text: h.textContent.trim().slice(0, 200),
                id: h.id || '',
                class: h.className || '',
            }));

        const alerts = Array.from(document.querySelectorAll(
            '[role="alert"], .a-alert-error, .a-alert-warning, .a-alert-success, ' +
            '[data-testid*="error"], [data-cy*="error"], [class*="error"], [class*="alert"]'
        )).map(el => ({
            tag: el.tagName,
            text: el.textContent.trim().slice(0, 300),
            class: el.className || '',
            testid: el.getAttribute('data-testid') || '',
        }));

        const interactives = Array.from(document.querySelectorAll(
            'button, a, input, select, textarea, [role="button"], kat-button, kat-input, kat-checkbox, kat-radiobutton'
        )).map(el => ({
            tag: el.tagName,
            text: el.textContent.trim().slice(0, 100),
            id: el.id || '',
            class: el.className || '',
            name: el.getAttribute('name') || '',
            type: el.getAttribute('type') || '',
            disabled: el.disabled || false,
            ariaLabel: el.getAttribute('aria-label') || '',
            dataCy: el.getAttribute('data-cy') || '',
            dataTestid: el.getAttribute('data-testid') || '',
            href: el.getAttribute('href') || '',
        }));

        return {
            url: location.href,
            title: document.title,
            bodyText: document.body?.textContent?.trim().slice(0, 5000) || '',
            bodyLength: document.body?.textContent?.length || 0,
            headings: headings.slice(0, 20),
            alerts: alerts.slice(0, 10),
            interactives: interactives.slice(0, 50),
        };
    })();
    """

    def __init__(self, page, redact_fn: Optional[Callable[[str], str]] = None):
        self.page = page
        self.redact = redact_fn
        self.events: List[DOMEvent] = []
        self._started = False
        self._last_drain = 0

    def start(self):
        """注入 MutationObserver 并开始监听。【同步版本】"""
        if self._started:
            return
        result = self.page.evaluate(self._MONITOR_SCRIPT)
        self._started = True
        return result

    def stop(self):
        """停止监听（注意：MutationObserver 无法真正移除，只是停止 drain）。"""
        self._started = False

    def drain_events(self) -> List[DOMEvent]:
        """从页面提取累积的事件。【同步版本】"""
        if not self._started:
            return []

        data = self.page.evaluate(self._DRAIN_SCRIPT)
        now = time.time()

        new_events = []
        for m in data.get("mutations", []):
            new_events.append(DOMEvent(
                timestamp=now,
                type="mutation",
                detail=m,
            ))
        for c in data.get("consoleErrors", []):
            new_events.append(DOMEvent(
                timestamp=now,
                type="console",
                detail=c,
            ))

        self.events.extend(new_events)
        self._last_drain = now
        return new_events

    def take_snapshot(self) -> dict:
        """采集当前页面结构化快照。【同步版本】"""
        snapshot = self.page.evaluate(self._SNAPSHOT_SCRIPT)
        if self.redact:
            snapshot["bodyText"] = self.redact(snapshot.get("bodyText", ""))
        return snapshot

    def has_significant_changes(self, min_added: int = 1) -> bool:
        """检查是否有显著的 DOM 变化（如新增节点）。"""
        for e in self.events:
            if e.type == "mutation":
                detail = e.detail
                if detail.get("addedNodes", 0) >= min_added:
                    return True
        return False

    def has_error_in_console(self) -> bool:
        """检查是否有 console.error 输出。"""
        return any(e.type == "console" and e.detail.get("level") == "error" for e in self.events)

    def get_console_errors(self) -> List[DOMEvent]:
        """获取所有 console error 事件。"""
        return [
            e for e in self.events
            if e.type == "console" and e.detail.get("level") == "error"
        ]

    def get_recent_mutations(self, seconds: float = 5.0) -> List[DOMEvent]:
        """获取最近 N 秒内的 mutation 事件。"""
        cutoff = time.time() - seconds
        return [e for e in self.events if e.type == "mutation" and e.timestamp >= cutoff]

    def save(self, out_dir: Path):
        """保存所有事件到 JSON Lines 文件。"""
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        # 保存事件
        events_path = out_dir / "events.jsonl"
        with open(events_path, "w", encoding="utf-8") as f:
            for event in self.events:
                f.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")

        # 保存 console 错误摘要
        errors = self.get_console_errors()
        if errors:
            console_path = out_dir / "console_errors.json"
            with open(console_path, "w", encoding="utf-8") as f:
                json.dump([asdict(e) for e in errors], f, ensure_ascii=False, indent=2)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
