"""Stage-5 persisted anomaly detection: repair incident queue.

Deterministic, model-free classification of automation failures into a
persistent incident queue (SQLite ``repair_incidents``) with deduplicating
signatures, confidence scoring, and redacted evidence bundles.
"""

from src.incidents.detector import (
    REPAIR_CLASSES,
    classify_failure,
    has_actionable_rate_limit,
    initial_confidence,
    is_telemetry_network_event,
    should_record,
)
from src.incidents.evidence_bundle import assess_evidence_bundle, build_evidence_bundle
from src.incidents.signature import compute_signature

__all__ = [
    "REPAIR_CLASSES",
    "build_evidence_bundle",
    "assess_evidence_bundle",
    "classify_failure",
    "compute_signature",
    "has_actionable_rate_limit",
    "initial_confidence",
    "is_telemetry_network_event",
    "should_record",
]
