"""Bounded, value-free DOM and open Shadow DOM contracts for repair evidence."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from src.incidents.contracts import DOM_CONTRACT_VERSION

_COLLECTOR_SCRIPT = Path(__file__).with_suffix(".js").read_text(encoding="utf-8")


def capture_dom_shadow_contract(page, *, max_nodes: int = 300, max_depth: int = 10) -> dict:
    """Capture structural controls without input values, storage or page HTML."""
    captured_at = datetime.now().isoformat(timespec="seconds")
    try:
        payload = page.evaluate(
            _COLLECTOR_SCRIPT,
            {"maxNodes": int(max_nodes), "maxDepth": int(max_depth)},
        )
        if not isinstance(payload, dict):
            raise ValueError("dom_contract_not_object")
        payload["captured_at"] = captured_at
        return payload
    except Exception as exc:  # evidence capture must never break the business flow
        return {
            "schema_version": DOM_CONTRACT_VERSION,
            "captured_at": captured_at,
            "page_family": "unknown",
            "nodes": [],
            "truncated": False,
            "capture_errors": [f"{type(exc).__name__}: {exc}"[:240]],
        }
