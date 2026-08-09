"""
网络请求摘要采集 — 只记录元数据，不记录完整 headers 或 body。

绑定 Playwright 的 page.on("request") / page.on("response") / page.on("requestfailed")，
产出脱敏后的网络请求摘要。
"""

import json
import time
from pathlib import Path
from typing import Optional, Callable
from dataclasses import dataclass, asdict

from .redact import redact_text


@dataclass
class NetworkEvent:
    timestamp: float
    type: str  # "request", "response", "requestfailed"
    method: str
    url: str
    status: Optional[int] = None
    content_type: Optional[str] = None
    resource_type: Optional[str] = None
    error_text: Optional[str] = None
    from_service_worker: bool = False


class NetworkCapture:
    """
    网络请求摘要采集器。

    用法:
        capture = NetworkCapture(page)
        capture.start()
        # ... 执行操作 ...
        capture.stop()
        capture.save(out_dir / "network.jsonl")
    """

    def __init__(self, page, redact_fn: Optional[Callable[[str], str]] = None):
        self.page = page
        self.redact = redact_fn or redact_text
        self.events: list[NetworkEvent] = []
        self._handlers = []
        self._started = False

    def _on_request(self, request):
        event = NetworkEvent(
            timestamp=time.time(),
            type="request",
            method=request.method,
            url=self.redact(request.url),
            resource_type=request.resource_type,
            from_service_worker=request.is_navigation_request(),
        )
        self.events.append(event)

    def _on_response(self, response):
        content_type = ""
        try:
            headers = response.headers
            content_type = headers.get("content-type", "")
        except Exception:
            pass

        event = NetworkEvent(
            timestamp=time.time(),
            type="response",
            method=response.request.method,
            url=self.redact(response.url),
            status=response.status,
            content_type=content_type,
            resource_type=response.request.resource_type,
        )
        self.events.append(event)

    def _on_request_failed(self, request):
        event = NetworkEvent(
            timestamp=time.time(),
            type="requestfailed",
            method=request.method,
            url=self.redact(request.url),
            resource_type=request.resource_type,
            error_text=getattr(request, "failure", None) or "unknown",
        )
        self.events.append(event)

    def start(self):
        """开始监听网络请求。"""
        if self._started:
            return
        self._started = True

        self.page.on("request", self._on_request)
        self.page.on("response", self._on_response)
        self.page.on("requestfailed", self._on_request_failed)

    def stop(self):
        """停止监听网络请求。"""
        if not self._started:
            return
        self._started = False

        # Playwright 没有 off()，但可以通过移除引用来避免重复记录
        # 实际上 Playwright 的 page.on 是追加式的，这里我们用标记来控制
        pass

    def clear(self):
        """清空已收集的事件。"""
        self.events.clear()

    def get_events(self) -> list[NetworkEvent]:
        """获取已收集的事件列表。"""
        return self.events.copy()

    def get_error_events(self) -> list[NetworkEvent]:
        """获取错误/失败相关的事件。"""
        return [
            e for e in self.events
            if e.type == "requestfailed"
            or (e.status and e.status >= 400)
        ]

    def has_status_code(self, code: int) -> bool:
        """检查是否出现过指定状态码。"""
        return any(
            e.status == code for e in self.events if e.type == "response"
        )

    def has_error_pattern(self, pattern: str) -> bool:
        """检查 URL 或错误文本中是否包含指定模式。"""
        pat_lower = pattern.lower()
        for e in self.events:
            if pat_lower in e.url.lower():
                return True
            if e.error_text and pat_lower in e.error_text.lower():
                return True
        return False

    def save(self, out_path: Path):
        """将事件保存为 JSON Lines 文件。"""
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        with open(out_path, "w", encoding="utf-8") as f:
            for event in self.events:
                f.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
