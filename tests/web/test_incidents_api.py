"""Stage-5 /api/incidents endpoints."""

import json
import sqlite3

from src.db import get_conn, record_incident, set_incident_evidence_bundle
from src.incidents import build_evidence_bundle
from tests.web.conftest import TEST_PASSWORD, create_user, login


def _seed(db_path, **overrides):
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
    incident, _ = record_incident(str(db_path), **kwargs)
    return incident


def _operator_client(client, web_settings):
    create_user(web_settings, "operator1", TEST_PASSWORD, "operator")
    login(client, "operator1", TEST_PASSWORD)
    return client


def _audit_rows(db_path, action="incident_close"):
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT action, target_id, result FROM web_audit_events WHERE action=? ORDER BY id",
        (action,),
    ).fetchall()
    conn.close()
    return rows


def test_list_incidents_requires_auth(client):
    assert client.get("/api/incidents").status_code == 401


def test_list_incidents_shape_and_filters(viewer_client, web_settings):
    _seed(web_settings.db_path)
    _seed(web_settings.db_path, signature="ffffeeee11112222", classification="captcha",
          confidence=0.10, account_id="uk_store_998", marketplace="UK")

    response = viewer_client.get("/api/incidents")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert len(payload["incidents"]) == 2
    incident = payload["incidents"][0]
    assert set(incident) == {
        "id", "signature", "scope_type", "flow_type", "account_id", "marketplace",
        "brand_name", "detector_type", "classification", "confidence", "status",
        "occurrence_count", "first_seen_at", "last_seen_at", "evidence_bundle_path",
        "evidence_status", "missing_evidence", "evidence_checked_at",
        "resolution_note", "codex_thread_id",
    }
    assert len(incident["signature"]) == 16

    response = viewer_client.get("/api/incidents", params={"classification": "captcha"})
    payload = response.json()
    assert payload["total"] == 1
    assert payload["incidents"][0]["classification"] == "captcha"

    # Comma-separated multi-value (repair-center filter semantics: any of these).
    response = viewer_client.get(
        "/api/incidents", params={"classification": "captcha,selector_missing"}
    )
    assert response.json()["total"] == 2
    response = viewer_client.get("/api/incidents", params={"classification": "captcha, two_fa"})
    assert response.json()["total"] == 1

    response = viewer_client.get("/api/incidents", params={"account_id": "uk_store_998"})
    assert response.json()["total"] == 1

    response = viewer_client.get("/api/incidents", params={"status": "open", "limit": 1, "offset": 1})
    payload = response.json()
    assert payload["total"] == 2 and len(payload["incidents"]) == 1


def test_get_incident_detail_with_bundle(viewer_client, web_settings):
    incident = _seed(web_settings.db_path)
    bundle = build_evidence_bundle(
        incident["id"],
        web_settings.evidence_root,
        page_evidence={"visible_text": "form ready"},
        run_context={"account_id": "us_store_999", "detector_type": "batch"},
    )
    set_incident_evidence_bundle(str(web_settings.db_path), incident["id"], str(bundle))

    response = viewer_client.get(f"/api/incidents/{incident['id']}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["incident"]["id"] == incident["id"]
    names = {entry["name"] for entry in payload["bundle_files"]}
    assert "manifest.json" in names
    assert "visible-text.redacted.txt" in names
    for entry in payload["bundle_files"]:
        assert set(entry) == {"name", "path", "size", "kind"}
        assert entry["size"] > 0
        assert entry["kind"] in {"text", "image", "binary"}


def test_get_incident_detail_unknown_404(viewer_client):
    response = viewer_client.get("/api/incidents/424242")
    assert response.status_code == 404
    assert response.json()["detail"] == "unknown_incident"


def test_bundle_path_outside_allowlist_yields_empty_files(viewer_client, web_settings, tmp_path):
    incident = _seed(web_settings.db_path)
    outside = tmp_path / "outside-allowlist"
    outside.mkdir()
    (outside / "secret.txt").write_text("must never be listed", encoding="utf-8")
    set_incident_evidence_bundle(str(web_settings.db_path), incident["id"], str(outside))

    response = viewer_client.get(f"/api/incidents/{incident['id']}")
    assert response.status_code == 200
    assert response.json()["bundle_files"] == []


def test_close_requires_operator(viewer_client, web_settings):
    incident = _seed(web_settings.db_path)
    response = viewer_client.post(f"/api/incidents/{incident['id']}/close", json={"note": "x"})
    assert response.status_code == 403


def test_close_happy_path_and_conflict(client, web_settings):
    incident = _seed(web_settings.db_path)
    operator = _operator_client(client, web_settings)

    response = operator.post(
        f"/api/incidents/{incident['id']}/close", json={"note": "handled manually"}
    )
    assert response.status_code == 200
    closed = response.json()["incident"]
    assert closed["status"] == "closed_human"
    assert closed["resolution_note"] == "handled manually"

    again = operator.post(f"/api/incidents/{incident['id']}/close", json={"note": "again"})
    assert again.status_code == 409
    assert again.json()["detail"] == "incident_already_closed"

    missing = operator.post("/api/incidents/424242/close", json={"note": "ghost"})
    assert missing.status_code == 404
    assert missing.json()["detail"] == "unknown_incident"

    results = [row[2] for row in _audit_rows(web_settings.db_path)]
    assert "ok" in results
    assert "incident_already_closed" in results


def test_overview_incidents_open(viewer_client, web_settings):
    response = viewer_client.get("/api/overview")
    assert response.status_code == 200
    assert response.json()["incidents_open"] == 0

    incident = _seed(web_settings.db_path)
    assert viewer_client.get("/api/overview").json()["incidents_open"] == 1

    conn = get_conn(str(web_settings.db_path))
    conn.execute("UPDATE repair_incidents SET status='closed_human' WHERE id=?", (incident["id"],))
    conn.commit()
    conn.close()
    assert viewer_client.get("/api/overview").json()["incidents_open"] == 0


def test_incident_out_matches_contract(viewer_client, web_settings):
    incident = _seed(web_settings.db_path)
    response = viewer_client.get(f"/api/incidents/{incident['id']}")
    assert response.status_code == 200
    data = response.json()["incident"]
    assert data["signature"] == "abc123def4567890"
    assert data["scope_type"] == "account"
    assert data["flow_type"] == "5461"
    assert data["detector_type"] == "batch"
    assert data["classification"] == "selector_missing"
    assert data["status"] == "open"
    assert data["occurrence_count"] == 1
    assert isinstance(data["confidence"], (int, float))
    # JSON round-trip sanity of the full payload.
    json.dumps(data)
