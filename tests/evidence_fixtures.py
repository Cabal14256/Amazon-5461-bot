"""Shared exact-match evidence fixtures for repair tests."""

from __future__ import annotations

from src.incidents.contracts import canonical_json_hash


def dom_contract(page_family: str = "product_identity") -> dict:
    return {
        "schema_version": 2,
        "page_family": page_family,
        "nodes": [{"tag": "kat-button", "attrs": {"data_testid": "apply-to-sell"}}],
    }


def previous_success(
    marketplace: str = "US",
    page_family: str = "product_identity",
    evidence_node: str | None = None,
) -> dict:
    contract = dom_contract(page_family)
    return {
        "schema_version": 2,
        "flow_type": "5461",
        "marketplace": marketplace.upper(),
        "page_family": page_family,
        "evidence_node": evidence_node or page_family,
        "source_status": "dry_run",
        "contract_hash": canonical_json_hash(contract),
        "contract": contract,
    }
