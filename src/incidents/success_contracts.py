"""De-identified known-good DOM contracts used by the incident evidence gate."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from src.db import find_success_contract, register_success_contract
from src.incidents.evidence_bundle import sanitize_payload

SUCCESS_STATUSES = {"success", "under_review", "dry_run"}


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
    encoded = json.dumps(sanitized, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
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
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return register_success_contract(
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
    return {
        "schema_version": int(row.get("schema_version") or 1),
        "flow_type": str(row.get("flow_type") or ""),
        "marketplace": str(row.get("marketplace") or ""),
        "page_family": str(row.get("page_family") or ""),
        "evidence_node": str(row.get("evidence_node") or ""),
        "source_status": str(row.get("source_status") or ""),
        "contract_hash": str(row.get("contract_hash") or ""),
        "contract": contract,
    }
