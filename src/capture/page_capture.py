"""
页面快照采集器 — 核心证据采集模块

产出结构化证据包：
- screenshot.png — 全页截图
- dom_snapshot.json — 结构化 DOM 摘要
- page_text.txt — 脱敏后纯文本
- summary.json — 元数据 + 检测到的状态信号
"""

import json
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable, List

from .redact import redact_text, redact_dict


@dataclass
class PageEvidence:
    """单次页面采集的结构化证据。"""
    url: str
    title: str
    body_text: str              # 脱敏后纯文本（截断）
    body_length: int            # 原始 body 长度
    headings: List[dict]        # h1/h2/h3 摘要
    alerts: List[dict]          # 错误/警告提示
    interactives: List[dict]    # 可交互元素（含 selector hints）
    detected_states: List[str] = field(default_factory=list)  # 检测到的可能状态
    screenshot_path: Optional[str] = None
    timestamp: str = ""
    meta: dict = field(default_factory=dict)


class PageCapture:
    """
    页面快照采集器。

    用法:
        capture = PageCapture(page)
        evidence = await capture.capture("step_01", out_dir)
        # evidence 包含结构化数据，同时文件已写入 out_dir
    """

    # 注入页面的 DOM 提取脚本
    _EXTRACT_SCRIPT = """
    (() => {
        // 提取标题
        const headings = Array.from(document.querySelectorAll('h1, h2, h3'))
            .map(h => ({
                tag: h.tagName,
                text: h.textContent.trim().slice(0, 300),
                id: h.id || '',
                class: h.className || '',
            }));

        // 提取 alert / error / warning 元素
        const alertSelectors = [
            '[role="alert"]',
            '.a-alert-error',
            '.a-alert-warning',
            '.a-alert-success',
            '[data-testid*="error"]',
            '[data-cy*="error"]',
            '[data-testid*="alert"]',
            '[class*="error"]',
            '[class*="alert"]',
            '.a-color-error',
            '.a-color-state',
        ];
        const alerts = [];
        for (const sel of alertSelectors) {
            try {
                const els = document.querySelectorAll(sel);
                for (const el of els) {
                    const text = el.textContent.trim();
                    if (text.length > 0 && text.length < 500) {
                        alerts.push({
                            tag: el.tagName,
                            text: text.slice(0, 300),
                            class: el.className || '',
                            testid: el.getAttribute('data-testid') || '',
                            cy: el.getAttribute('data-cy') || '',
                        });
                    }
                }
            } catch (e) {}
        }
        // 去重
        const alertTexts = new Set();
        const uniqueAlerts = [];
        for (const a of alerts) {
            if (!alertTexts.has(a.text)) {
                alertTexts.add(a.text);
                uniqueAlerts.push(a);
            }
        }

        // 提取可交互元素（含 selector hints）
        const interactiveSelectors = [
            'button', 'a', 'input', 'select', 'textarea',
            '[role="button"]', '[role="link"]',
            'kat-button', 'kat-input', 'kat-checkbox',
            'kat-radiobutton', 'kat-dropdown', 'kat-textarea',
        ];
        const interactives = [];
        for (const sel of interactiveSelectors) {
            try {
                const els = document.querySelectorAll(sel);
                for (const el of els) {
                    const text = el.textContent.trim();
                    interactives.push({
                        tag: el.tagName.toLowerCase(),
                        text: text.slice(0, 150),
                        id: el.id || '',
                        class: el.className || '',
                        name: el.getAttribute('name') || '',
                        type: el.getAttribute('type') || '',
                        disabled: el.disabled || el.getAttribute('disabled') === '' || false,
                        ariaLabel: el.getAttribute('aria-label') || '',
                        ariaPressed: el.getAttribute('aria-pressed') || '',
                        dataCy: el.getAttribute('data-cy') || '',
                        dataTestid: el.getAttribute('data-testid') || '',
                        href: el.getAttribute('href') || '',
                        placeholder: el.getAttribute('placeholder') || '',
                        value: (el.value || '').toString().slice(0, 100),
                    });
                }
            } catch (e) {}
        }

        // 提取 body 可见文本（排除 script/style/noscript，避免 JS 代码污染）
        function getVisibleText(el) {
            if (!el) return '';
            const skip = new Set(['SCRIPT','STYLE','NOSCRIPT','TEMPLATE']);
            let text = '';
            for (const child of el.childNodes) {
                if (child.nodeType === 3) {
                    text += child.textContent;
                } else if (child.nodeType === 1 && !skip.has(child.tagName)) {
                    text += getVisibleText(child);
                }
            }
            return text;
        }
        const bodyText = getVisibleText(document.body).replace(/\s+/g, ' ').trim();

        return {
            url: location.href,
            title: document.title,
            bodyText: bodyText,
            bodyLength: bodyText.length,
            headings: headings.slice(0, 30),
            alerts: uniqueAlerts.slice(0, 20),
            interactives: interactives.slice(0, 100),
        };
    })();
    """

    # 状态检测关键词映射
    _STATE_SIGNALS = {
        "add_product_start": ["add a product", "i'm adding a product not sold", "product type"],
        "5461_triggered": ["5461", "not approved to create asins", "brand authorization required", "request approval", "apply to sell"],
        "connect_brand": ["clarify which brand", "connect this brand", "brex-widget", "multiple brands found"],
        "declined_case_shown": ["declined", "rejected", "denied", "case id"],
        "login_expired": ["sign in", "signin", "login", "password"],
        "error_410001": ["410001"],
        "error_429": ["429", "too many requests", "rate limit"],
        "already_approved": ["description", "product description"],
        "blank_page": [],  # 通过 bodyLength 判断
    }

    def __init__(self, page, redact_fn: Optional[Callable[[str], str]] = None):
        self.page = page
        self.redact = redact_fn or redact_text

    def capture(self, name: str, out_dir: Path) -> PageEvidence:
        """
        采集一次完整证据包，写入磁盘，返回结构化数据。
        【同步版本】兼容 sync Playwright API。

        Args:
            name: 证据包名称，如 "01_add_product_start"
            out_dir: 输出目录

        Returns:
            PageEvidence 对象
        """
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        # 1. 截图
        screenshot_path = out_dir / f"{name}_screenshot.png"
        try:
            self.page.screenshot(path=str(screenshot_path), full_page=True)
        except Exception as e:
            screenshot_path = None
            print(f"[PageCapture] 截图失败: {e}")

        # 2. 提取结构化 DOM 数据
        raw_data = self.page.evaluate(self._EXTRACT_SCRIPT)

        # 3. 脱敏
        body_text = self.redact(raw_data.get("bodyText", ""))

        # 4. 检测可能的状态
        detected_states = self._detect_states(raw_data)

        # 5. 构建证据对象
        timestamp = datetime.now().isoformat()
        evidence = PageEvidence(
            url=raw_data.get("url", ""),
            title=raw_data.get("title", ""),
            body_text=body_text[:8000],  # 截断，避免过大
            body_length=raw_data.get("bodyLength", 0),
            headings=raw_data.get("headings", []),
            alerts=raw_data.get("alerts", []),
            interactives=raw_data.get("interactives", []),
            detected_states=detected_states,
            screenshot_path=str(screenshot_path) if screenshot_path else None,
            timestamp=timestamp,
            meta={
                "capture_name": name,
                "capture_version": "1.0",
            },
        )

        # 6. 写入文件
        self._write_evidence(evidence, raw_data, out_dir, name)

        return evidence

    def _write_evidence(self, evidence: PageEvidence, raw_data: dict, out_dir: Path, name: str):
        """将证据写入磁盘。【同步版本】"""
        # DOM 快照（完整结构化数据，已脱敏）
        dom_snapshot = redact_dict(raw_data)
        dom_path = out_dir / f"{name}_dom_snapshot.json"
        with open(dom_path, "w", encoding="utf-8") as f:
            json.dump(dom_snapshot, f, ensure_ascii=False, indent=2)

        # 纯文本（方便 AI 直接读取）
        text_path = out_dir / f"{name}_page_text.txt"
        with open(text_path, "w", encoding="utf-8") as f:
            f.write(f"URL: {evidence.url}\n")
            f.write(f"Title: {evidence.title}\n")
            f.write(f"Timestamp: {evidence.timestamp}\n")
            f.write(f"Body Length: {evidence.body_length}\n")
            f.write(f"Detected States: {', '.join(evidence.detected_states) or 'none'}\n")
            f.write(f"Alerts Count: {len(evidence.alerts)}\n")
            f.write(f"Interactives Count: {len(evidence.interactives)}\n")
            f.write("\n" + "=" * 60 + "\n")
            f.write("HEADINGS:\n")
            for h in evidence.headings:
                f.write(f"  [{h.get('tag')}] {h.get('text', '')}\n")
            f.write("\n" + "=" * 60 + "\n")
            f.write("ALERTS:\n")
            for a in evidence.alerts:
                f.write(f"  [{a.get('tag')}] {a.get('text', '')}\n")
            f.write("\n" + "=" * 60 + "\n")
            f.write("INTERACTIVE ELEMENTS (first 30):\n")
            for el in evidence.interactives[:30]:
                hint = f"#{el.get('id')}" if el.get('id') else f".{el.get('class', '').split()[0]}" if el.get('class') else el.get('tag', '')
                f.write(f"  [{el.get('tag')}] {hint} | {el.get('text', '')[:50]} | disabled={el.get('disabled')}\n")
            f.write("\n" + "=" * 60 + "\n")
            f.write("BODY TEXT (first 3000 chars):\n")
            f.write(evidence.body_text[:3000])

        # 摘要 JSON
        summary_path = out_dir / f"{name}_summary.json"
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(asdict(evidence), f, ensure_ascii=False, indent=2)

    def _detect_states(self, raw_data: dict) -> List[str]:
        """基于页面内容检测可能的状态。"""
        states = []
        body_text = raw_data.get("bodyText", "").lower()
        body_length = raw_data.get("bodyLength", 0)
        url = raw_data.get("url", "").lower()
        title = raw_data.get("title", "").lower()
        alerts_text = " ".join(a.get("text", "").lower() for a in raw_data.get("alerts", []))

        combined = f"{body_text} {url} {title} {alerts_text}"

        for state_name, signals in self._STATE_SIGNALS.items():
            if state_name == "blank_page":
                if body_length < 2000:
                    states.append(state_name)
                continue

            for signal in signals:
                if signal in combined:
                    states.append(state_name)
                    break

        return states

    def quick_capture(self, name: str, out_dir: Path) -> dict:
        """
        快速采集（仅结构化数据，不截图），用于高频轮询场景。
        【同步版本】兼容 sync Playwright API。
        """
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        raw_data = self.page.evaluate(self._EXTRACT_SCRIPT)
        raw_data["timestamp"] = datetime.now().isoformat()
        raw_data["detected_states"] = self._detect_states(raw_data)

        # 脱敏后保存
        safe_data = redact_dict(raw_data)
        path = out_dir / f"{name}_quick.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(safe_data, f, ensure_ascii=False, indent=2)

        return safe_data
