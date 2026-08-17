"""Strict incident evidence gate and exact previous-success matching."""

from __future__ import annotations

import json

from src.db import (
    get_incident,
    init_db,
    record_incident,
    set_incident_evidence_bundle,
)
from src.incidents.evidence_bundle import assess_evidence_bundle, build_evidence_bundle
from src.incidents.success_contracts import load_previous_success, register_known_good_contract
from tests.evidence_fixtures import previous_success


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
        run_context={
            "account_id": "us_store_999",
            "brand_name": "TESTBRAND",
            "marketplace": "US",
            "flow_type": "5461",
        },
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
        run_context={
            "account_id": "us_store_999",
            "brand_name": "TESTBRAND",
            "marketplace": "US",
            "flow_type": "5461",
        },
        selectors={"declared_candidates": ["kat-button"], "probes": {"count": 1}},
        dom_contract={
            "schema_version": 2,
            "page_family": "product_identity",
            "nodes": [{"tag": "kat-button", "attrs": {"id": "TESTBRAND-us_store_999"}}],
        },
        previous_success=previous_success(),
    )
    assert assess_evidence_bundle(complete) == {"status": "ready", "missing": []}
    stored = json.loads((complete / "dom-contract.json").read_text(encoding="utf-8"))
    serialized = json.dumps(stored)
    assert "TESTBRAND" not in serialized
    assert "us_store_999" not in serialized


def test_bundle_v2_rejects_legacy_tampering_and_wrong_site(tmp_path):
    kwargs = {
        "page_evidence": {"visible_text": "Apply to sell"},
        "run_context": {"marketplace": "US", "flow_type": "5461"},
        "selectors": {"declared_candidates": ["kat-button"], "probes": {"count": 1}},
        "dom_contract": {
            "schema_version": 2,
            "page_family": "product_identity",
            "nodes": [{"tag": "kat-button"}],
        },
    }
    wrong_site = build_evidence_bundle(
        3, tmp_path, previous_success=previous_success("MX"), **kwargs
    )
    assert assess_evidence_bundle(wrong_site)["missing"] == ["previous_success"]

    complete = build_evidence_bundle(
        4, tmp_path, previous_success=previous_success("US"), **kwargs
    )
    dom_path = complete / "dom-contract.json"
    dom_path.write_text('{"schema_version": 2, "nodes": []}', encoding="utf-8")
    assert assess_evidence_bundle(complete)["missing"] == ["dom_shadow_contract"]

    legacy = build_evidence_bundle(
        5, tmp_path, previous_success=previous_success("US"), **kwargs
    )
    manifest_path = legacy / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("schema_version")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert assess_evidence_bundle(legacy)["missing"] == [
        "page_summary",
        "selectors_and_probes",
        "dom_shadow_contract",
        "previous_success",
    ]


def test_rebuild_removes_stale_optional_contracts(tmp_path):
    complete = build_evidence_bundle(
        6,
        tmp_path,
        page_evidence={"visible_text": "Apply"},
        run_context={"marketplace": "US", "flow_type": "5461"},
        selectors={"declared_candidates": ["kat-button"], "probes": {"count": 1}},
        dom_contract={"schema_version": 2, "nodes": [{"tag": "button"}]},
        previous_success=previous_success(),
    )
    assert (complete / "dom-contract.json").exists()
    assert (complete / "previous-success.json").exists()
    rebuilt = build_evidence_bundle(6, tmp_path, page_evidence={"visible_text": "Apply"})
    assert not (rebuilt / "dom-contract.json").exists()
    assert not (rebuilt / "previous-success.json").exists()


def test_new_success_contract_reopens_only_exact_waiting_incident(tmp_path):
    db_path = tmp_path / "state" / "ledger.db"
    evidence_root = tmp_path / "evidence"
    init_db(str(db_path))
    incident, _ = record_incident(
        str(db_path), signature="waiting-contract", scope_type="site", flow_type="5461",
        marketplace="US", classification="selector_missing",
    )
    bundle = build_evidence_bundle(
        int(incident["id"]),
        evidence_root,
        page_evidence={"visible_text": "Apply"},
        run_context={"marketplace": "US", "flow_type": "5461"},
        selectors={"declared_candidates": ["kat-button"], "probes": {"count": 1}},
        dom_contract={
            "schema_version": 2,
            "page_family": "product_identity",
            "nodes": [{"tag": "kat-button"}],
        },
    )
    gate = assess_evidence_bundle(bundle)
    set_incident_evidence_bundle(
        str(db_path), int(incident["id"]), str(bundle),
        evidence_status=gate["status"], missing_evidence=gate["missing"],
    )
    assert get_incident(str(db_path), int(incident["id"]))["status"] == "waiting_evidence"

    register_known_good_contract(
        str(db_path), evidence_root, flow_type="5461", marketplace="US",
        page_family="product_identity", evidence_node="product_identity",
        source_status="dry_run", contract=_contract(),
    )
    refreshed = get_incident(str(db_path), int(incident["id"]))
    assert refreshed["status"] == "open"
    assert refreshed["evidence_status"] == "ready"
    assert refreshed["missing_evidence"] == []
