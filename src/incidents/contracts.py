"""Dependency-free constants and hashes for incident evidence contracts."""

from __future__ import annotations

import hashlib
import json

EVIDENCE_MANIFEST_VERSION = 2
DOM_CONTRACT_VERSION = 2
REQUIRED_EVIDENCE = (
    "page_summary",
    "selectors_and_probes",
    "dom_shadow_contract",
    "previous_success",
)
SUCCESS_CONTRACT_STATUSES = frozenset({"success", "under_review", "dry_run"})


def canonical_json_hash(payload) -> str:
    """Stable SHA-256 for already-sanitized JSON-compatible evidence."""
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
