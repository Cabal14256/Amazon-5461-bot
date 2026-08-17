"""De-identified known-good DOM contracts used by the incident evidence gate."""

from __future__ import annotations

import json
import re
from pathlib import Path

from src.db import (
    find_success_contract,
    list_waiting_incidents_for_success_contract,
    register_success_contract,
    set_incident_evidence_bundle,
)
from src.incidents.contracts import SUCCESS_CONTRACT_STATUSES, canonical_json_hash
from src.incidents.evidence_bundle import (
    assess_evidence_bundle,
    read_evidence_dimensions,
    rebuild_bundle_with_previous_success,
    sanitize_payload,
)
from src.state_files import atomic_write_json

SUCCESS_STATUSES = SUCCESS_CONTRACT_STATUSES


def infer_page_family(page_evidence: dict | None, dom_contract: dict | None) -> str:
    contract_family = str((dom_contract or {}).get("page_family") or "").strip().lower()
    if contract_family and contract_family != "unknown":
        return contract_family
    state = (page_evidence or {}).get("recognized_state") or {}
    page_type = str(state.get("page_type") or "").strip().lower()
    if page_type:
        return page_type
    return "unknown"


def infer_evidence_node(page_family: str, page_evidence: dict | None = None) -> str:
    explicit = str((page_evidence or {}).get("evidence_node") or "").strip().lower()
    return explicit or str(page_family or "unknown").strip().lower()


def _safe_segment(value: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", str(value).lower()).strip("-") or "unknown"


def register_known_good_contract(
    db_path: str,
    evidence_root: Path,
    *,
    flow_type: str,
    marketplace: str,
    page_family: str,
    evidence_node: str,
    source_status: str,
    contract: dict,
    run_context: dict | None = None,
) -> dict | None:
    if str(source_status) not in SUCCESS_STATUSES or not isinstance(contract, dict):
        return None
    if not contract.get("nodes") or str(page_family) == "unknown":
        return None
    sanitized = sanitize_payload(contract, run_context=run_context)
    digest = canonical_json_hash(sanitized)
    fixture_root = Path(evidence_root) / "success-contracts"
    fixture_root.mkdir(parents=True, exist_ok=True)
    filename = "-".join(
        (
            _safe_segment(flow_type),
            _safe_segment(marketplace),
            _safe_segment(page_family),
            _safe_segment(evidence_node),
            digest[:16],
        )
    ) + ".json"
    path = fixture_root / filename
    payload = {
        "schema_version": int(sanitized.get("schema_version") or 1),
        "flow_type": str(flow_type),
        "marketplace": str(marketplace).upper(),
        "page_family": str(page_family),
        "evidence_node": str(evidence_node),
        "source_status": str(source_status),
        "contract_hash": digest,
        "contract": sanitized,
    }
    atomic_write_json(path, payload)
    registered = register_success_contract(
        db_path,
        flow_type=flow_type,
        marketplace=marketplace,
        page_family=page_family,
        evidence_node=evidence_node,
        source_status=source_status,
        schema_version=int(payload["schema_version"]),
        contract_hash=digest,
        contract_path=str(path),
    )
    _reevaluate_waiting_incidents(
        db_path,
        flow_type=flow_type,
        marketplace=marketplace,
        page_family=page_family,
        evidence_node=evidence_node,
    )
    return registered


def load_previous_success(
    db_path: str,
    *,
    flow_type: str,
    marketplace: str,
    page_family: str,
    evidence_node: str,
) -> dict | None:
    row = find_success_contract(
        db_path,
        flow_type=flow_type,
        marketplace=marketplace,
        page_family=page_family,
        evidence_node=evidence_node,
    )
    if row is None:
        return None
    try:
        payload = json.loads(Path(str(row["contract_path"])).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    contract = payload.get("contract") if isinstance(payload, dict) else None
    if not isinstance(contract, dict) or not contract:
        return None
    loaded = {
        "schema_version": int(row.get("schema_version") or 1),
        "flow_type": str(row.get("flow_type") or ""),
        "marketplace": str(row.get("marketplace") or ""),
        "page_family": str(row.get("page_family") or ""),
        "evidence_node": str(row.get("evidence_node") or ""),
        "source_status": str(row.get("source_status") or ""),
        "contract_hash": str(row.get("contract_hash") or ""),
        "contract": contract,
    }
    if canonical_json_hash(contract) != loaded["contract_hash"]:
        return None
    for key in ("flow_type", "marketplace", "page_family", "evidence_node", "source_status"):
        payload_value = str(payload.get(key) or "")
        row_value = str(loaded.get(key) or "")
        if key == "marketplace":
            payload_value, row_value = payload_value.upper(), row_value.upper()
        if payload_value != row_value:
            return None
    if loaded["source_status"] not in SUCCESS_STATUSES:
        return None
    if str(payload.get("contract_hash") or "") != loaded["contract_hash"]:
        return None
    return loaded


def _reevaluate_waiting_incidents(
    db_path: str,
    *,
    flow_type: str,
    marketplace: str,
    page_family: str,
    evidence_node: str,
) -> None:
    """Best-effort exact-match unlock after a genuine success fixture arrives."""
    previous_success = load_previous_success(
        db_path,
        flow_type=flow_type,
        marketplace=marketplace,
        page_family=page_family,
        evidence_node=evidence_node,
    )
    if previous_success is None:
        return
    expected = {
        "flow_type": str(flow_type).lower(),
        "marketplace": str(marketplace).upper(),
        "page_family": str(page_family).lower(),
        "evidence_node": str(evidence_node).lower(),
    }
    for incident in list_waiting_incidents_for_success_contract(
        db_path,
        flow_type=flow_type,
        marketplace=marketplace,
    ):
        bundle_path = Path(str(incident.get("evidence_bundle_path") or ""))
        dimensions = read_evidence_dimensions(bundle_path)
        if not dimensions:
            continue
        actual = {
            "flow_type": str(dimensions.get("flow_type") or "").lower(),
            "marketplace": str(dimensions.get("marketplace") or "").upper(),
            "page_family": str(dimensions.get("page_family") or "").lower(),
            "evidence_node": str(dimensions.get("evidence_node") or "").lower(),
        }
        if actual != expected:
            continue
        try:
            rebuilt = rebuild_bundle_with_previous_success(bundle_path, previous_success)
            gate = assess_evidence_bundle(rebuilt)
            set_incident_evidence_bundle(
                db_path,
                int(incident["id"]),
                str(rebuilt),
                evidence_status=gate["status"],
                missing_evidence=gate["missing"],
            )
        except (OSError, ValueError, KeyError, TypeError):
            # A success capture is side-channel evidence and must never break
            # the underlying batch. The incident remains fail-closed.
            continue
