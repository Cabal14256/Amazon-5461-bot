"""
Capture module — 页面证据采集层

提供页面快照、实时 DOM 监听、网络请求摘要、敏感信息脱敏等能力。
旁路采集，不阻塞主流程。
"""

from .dom_contract import capture_dom_shadow_contract
from .monitor import PageMonitor
from .network_capture import NetworkCapture
from .page_capture import PageCapture, PageEvidence
from .redact import redact_text, default_redact

__all__ = [
    "PageCapture",
    "PageEvidence",
    "PageMonitor",
    "NetworkCapture",
    "redact_text",
    "default_redact",
    "capture_dom_shadow_contract",
]
