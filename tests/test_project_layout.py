from inspect import signature
from pathlib import Path

from src.capture.monitor_session import MonitorSession
from src.executor.state_loop import StateLoopExecutor
from src.knowledge.evidence_loader import EvidenceLoader


def test_evidence_defaults_use_runtime_partition():
    assert signature(MonitorSession.for_brand).parameters["evidence_root"].default == "runtime/evidence"
    assert signature(StateLoopExecutor).parameters["evidence_root"].default == "runtime/evidence"
    assert EvidenceLoader().evidence_root == Path("runtime/evidence")
