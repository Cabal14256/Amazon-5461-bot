"""
Executor module -- State loop execution engine for Amazon 5461 automation.

Provides:
- ActionRunner: atomic page actions with selector fallback chains
- LegacyBridge: unified call() interface over existing flow_submit_5461 functions
- StateLoopExecutor: core state-machine-driven execution loop
"""

from .action_runner import ActionRunner
from .legacy_bridge import LegacyBridge
from .state_loop import StateLoopExecutor

__all__ = [
    "ActionRunner",
    "LegacyBridge",
    "StateLoopExecutor",
]
