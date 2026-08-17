"""Strict incident evidence gate and exact previous-success matching."""

from __future__ import annotations

import json

from src.db import init_db
from src.incidents.evidence_bundle import assess_evidence_bundle, build_evidence_bundle
from src.incidents.success_contracts import load_previous_success, register_known_good_contract


def _contract():
    return {
        "schema_version": 1,
        "page_family": "product_identity",
        "nodes": [{"tag": "kat-button", "attrs": {"data_testid": "apply-to-sell"}}],
    }


def test_success_contract_matches_exact_site_and_page_stage(tmp_path):
    db_path = tmp_path / "state" / "ledger.db"
    evidence_root = tmp_path / "evidence"
    init_db(str(db_path))
    registered = register_known_good_contract(
        str(db_path), evidence_root, flow_type="5461", marketplace="US",
        page_family="product_identity", evidence_node="product_identity",
        source_status="dry_run", contract=_contract(),
        run_context={"account_id": "us_store_999", "brand_name": "TESTBRAND"},
    )
    assert registered is not None
    matched = load_previous_success(
        str(db_path), flow_type="5461", marketplace="US",
        page_family="product_identity", evidence_node="product_identity",
    )
    assert matched and matched["contract_hash"] == registered["contract_hash"]
    assert load_previous_success(
        str(db_path), flow_type="5461", marketplace="MX",
        page_family="product_identity", evidence_node="product_identity",
    ) is None
    assert load_previous_success(
        str(db_path), flow_type="5461", marketplace="US",
        page_family="description", evidence_node="description",
    ) is None


def test_evidence_gate_requires_all_four_contracts_and_redacts_dom(tmp_path):
    incomplete = build_evidence_bundle(
        1, tmp_path, page_evidence={"visible_text": "Apply to sell"},
    )
    assert assess_evidence_bundle(incomplete)["missing"] == [
        "selectors_and_probes", "dom_shadow_contract", "previous_success",
    ]

    complete = build_evidence_bundle(
        2,
        tmp_path,
        page_evidence={"visible_text": "Apply to sell"},
        run_context={"account_id": "us_store_999", "brand_name": "TESTBRAND"},
        selectors={"declared_candidates": ["kat-button"], "probes": {"count": 1}},
        dom_contract={
            "schema_version": 1,
            "nodes": [{"tag": "kat-button", "attrs": {"id": "TESTBRAND-us_store_999"}}],
        },
        previous_success={"contract_hash": "a" * 64, "contract": _contract()},
    )
    assert assess_evidence_bundle(complete) == {"status": "ready", "missing": []}
    stored = json.loads((complete / "dom-contract.json").read_text(encoding="utf-8"))
    serialized = json.dumps(stored)
    assert "TESTBRAND" not in serialized
    assert "us_store_999" not in serialized
