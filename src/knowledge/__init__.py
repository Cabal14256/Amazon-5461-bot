"""
Knowledge module -- 页面知识库解析层

提供状态机、选择器注册表、证据加载器三个核心组件。
"""

from .state_machine import StateMachine
from .selector_registry import SelectorRegistry
from .evidence_loader import EvidenceLoader

__all__ = [
    "StateMachine",
    "SelectorRegistry",
    "EvidenceLoader",
]
