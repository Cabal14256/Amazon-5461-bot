"""Stage-5 repair_incidents DB helpers."""

import pytest

from src.db import (
    INCIDENT_REPAIR_CLASSES,
    INCIDENT_TERMINAL_STATUSES,
    close_incident,
    count_open_incidents,
    get_incident,
    init_db,
    list_incidents,
    record_incident,
)


@pytest.fixture()
def db_path(tmp_path):
    path = tmp_path / "ledger.db"
    init_db(str(path))
    return str(path)


def _record(db_path, **overrides):
    kwargs = {
        "signature": "abc123def4567890",
        "scope_type": "account",
        "flow_type": "5461",
        "account_id": "us_store_999",
        "marketplace": "US",
        "brand_name": "TESTBRAND",
        "detector_type": "batch",
        "classification": "selector_missing",
        "confidence": 0.50,
    }
    kwargs.update(overrides)
    return record_incident(db_path, **kwargs)


def test_constants():
    assert INCIDENT_TERMINAL_STATUSES == {"closed_human", "closed_duplicate", "released", "rejected"}
    assert INCIDENT_REPAIR_CLASSES == {
        "selector_missing", "state_unknown", "dom_contract_changed",
        "navigation_changed", "semantic_control_missing", "flow_loop_exhausted",
        "result_contract_changed",
    }


def test_record_creates_incident(db_path):
    incident, created = _record(db_path)
    assert created is True
    assert incident["status"] == "open"
    assert incident["occurrence_count"] == 1
    assert incident["confidence"] == pytest.approx(0.50)
    assert incident["first_seen_at"] and incident["last_seen_at"]
    assert incident["signature"] == "abc123def4567890"


def test_same_signature_aggregates(db_path):
    first, created1 = _record(db_path)
    second, created2 = _record(db_path)
    assert created1 is True and created2 is False
    assert second["id"] == first["id"]
    assert second["occurrence_count"] == 2


def test_terminal_incident_does_not_aggregate(db_path):
    first, _ = _record(db_path)
    close_incident(db_path, first["id"], "handled by human")
    second, created = _record(db_path)
    assert created is True
    assert second["id"] != first["id"]


def test_repair_class_confidence_bump_and_cap(db_path):
    _record(db_path)
    expected = [0.60, 0.70, 0.80, 0.85, 0.85]
    for want in expected:
        incident, created = _record(db_path)
        assert created is False
        assert incident["confidence"] == pytest.approx(want)


def test_human_class_confidence_not_bumped(db_path):
    _record(db_path, classification="captcha", confidence=0.10)
    incident, created = _record(db_path, classification="captcha", confidence=0.10)
    assert created is False
    assert incident["confidence"] == pytest.approx(0.10)
    assert incident["occurrence_count"] == 2


def test_evidence_bundle_path_overwritten_when_non_empty(db_path):
    _record(db_path, evidence_bundle_path="/tmp/bundle-1")
    incident, _ = _record(db_path, evidence_bundle_path="/tmp/bundle-2")
    assert incident["evidence_bundle_path"] == "/tmp/bundle-2"
    # Empty path keeps the previous one.
    incident, _ = _record(db_path, evidence_bundle_path="")
    assert incident["evidence_bundle_path"] == "/tmp/bundle-2"


def test_cross_scope_confidence_bump(db_path):
    _record(db_path, account_id="us_store_999")
    other, _ = _record(db_path, account_id="uk_store_998", marketplace="UK")
    # Same signature under a different account/marketplace: +0.15.
    assert other["confidence"] == pytest.approx(0.65)


def test_cross_scope_bump_capped(db_path):
    _record(db_path, account_id="us_store_999")
    other, _ = _record(db_path, account_id="uk_store_998", marketplace="UK", confidence=0.80)
    assert other["confidence"] == pytest.approx(0.85)


def test_close_incident_idempotent(db_path):
    incident, _ = _record(db_path)
    closed = close_incident(db_path, incident["id"], "fixed manually")
    assert closed is not None
    assert closed["status"] == "closed_human"
    assert closed["resolution_note"] == "fixed manually"
    # Second close is a no-op.
    assert close_incident(db_path, incident["id"], "again") is None
    # Unknown id.
    assert close_incident(db_path, 99999, "nope") is None


def test_list_incidents_filters_and_pagination(db_path):
    _record(db_path, classification="captcha", confidence=0.10, account_id="us_store_999")
    _record(db_path, classification="selector_missing", account_id="uk_store_998",
            marketplace="UK", signature="bbbbccccdddd0000")
    third, _ = _record(db_path, classification="rate_limit", confidence=0.10,
                       account_id="uk_store_998", marketplace="UK", signature="eeeeffff00001111")
    close_incident(db_path, third["id"], "cooldown")

    rows, total = list_incidents(db_path)
    assert total == 3 and len(rows) == 3

    rows, total = list_incidents(db_path, status="open")
    assert total == 2 and all(r["status"] == "open" for r in rows)

    rows, total = list_incidents(db_path, status="closed_human")
    assert total == 1 and rows[0]["classification"] == "rate_limit"

    rows, total = list_incidents(db_path, classification="captcha")
    assert total == 1

    rows, total = list_incidents(db_path, account_id="uk_store_998")
    assert total == 2

    rows, total = list_incidents(db_path, marketplace="UK")
    assert total == 2

    page1, total = list_incidents(db_path, limit=2, offset=0)
    page2, _ = list_incidents(db_path, limit=2, offset=2)
    assert total == 3 and len(page1) == 2 and len(page2) == 1
    assert {r["id"] for r in page1}.isdisjoint({r["id"] for r in page2})


def test_count_open_incidents(db_path):
    assert count_open_incidents(db_path) == 0
    first, _ = _record(db_path)
    _record(db_path, signature="ffffeeee11112222", classification="captcha", confidence=0.10)
    assert count_open_incidents(db_path) == 2
    close_incident(db_path, first["id"], "done")
    assert count_open_incidents(db_path) == 1


def test_get_incident(db_path):
    incident, _ = _record(db_path)
    fetched = get_incident(db_path, incident["id"])
    assert fetched is not None and fetched["id"] == incident["id"]
    assert get_incident(db_path, 99999) is None
