"""Stage-6 /api/incidents/{id}/triage + repair-jobs endpoints.

A mock Codex CLI (a small Python fixture script injected through the
``codex.command`` setting, never PATH) stands in for the real executable:
it answers ``--version`` and ``exec`` with a fixed JSONL stream plus an
``-o`` result file.  The real ``codex`` binary is never invoked.
"""

import json
import sqlite3
import sys

import pytest

from src.codex_client.auto_triage import run_auto_triage_scan
from src.db import get_incident, record_incident, set_incident_evidence_bundle
from src.incidents import build_evidence_bundle
from tests.web.conftest import TEST_PASSWORD, create_user, login

MOCK_CODEX = r'''
import json
import sys


def main():
    argv = sys.argv[1:]
    if "--version" in argv:
        print("codex-cli 0.147.0-mock")
        return 0
    sys.stdin.read()  # prompt on stdin
    out_path = argv[argv.index("-o") + 1]
    payload = json.dumps({
        "classification": "selector_change",
        "confidence": 0.77,
        "reason": "mock triage outcome",
        "affected_components": ["config/selectors/us.yaml"],
        "recommended_scope": "selector update",
        "safe_to_generate_patch": True,
        "requires_human_review": False,
        "missing_evidence": [],
    })
    print(json.dumps({"type": "thread.started", "thread_id": "thr_mock"}))
    print(json.dumps({
        "type": "item.completed",
        "item": {"id": "item_0", "type": "agent_message", "text": payload},
    }))
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(payload)
    return 0


sys.exit(main())
'''


@pytest.fixture()
def mock_codex(web_settings, tmp_path):
    """Point codex.command at the mock CLI fixture script."""
    script = tmp_path / "mock_codex.py"
    script.write_text(MOCK_CODEX, encoding="utf-8")
    web_settings.codex_enabled = True
    web_settings.codex_command = f'"{sys.executable}" "{script.as_posix()}"'
    web_settings.codex_logs_root = web_settings.logs_root / "repair"
    web_settings.codex_state_root = web_settings.state_root / "repair"
    web_settings.codex_timeout_sec = 30.0
    return web_settings


def _seed(db_path, evidence_root=None, **overrides):
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
    if evidence_root is not None:
        bundle = build_evidence_bundle(
            incident["id"],
            evidence_root,
            page_evidence={"visible_text": "Apply to sell"},
            run_context={"account_id": "us_store_999"},
        )
        set_incident_evidence_bundle(str(db_path), incident["id"], str(bundle))
    return get_incident(str(db_path), incident["id"])


def _operator_client(client, web_settings):
    create_user(web_settings, "operator1", TEST_PASSWORD, "operator")
    login(client, "operator1", TEST_PASSWORD)
    return client


def _triage_audits(db_path):
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT result, detail FROM web_audit_events WHERE action='incident_triage' ORDER BY id"
    ).fetchall()
    conn.close()
    return rows


def test_triage_requires_operator(viewer_client, mock_codex):
    incident = _seed(mock_codex.db_path)
    response = viewer_client.post(f"/api/incidents/{incident['id']}/triage")
    assert response.status_code == 403


def test_triage_happy_path_end_to_end(client, mock_codex):
    incident = _seed(mock_codex.db_path, evidence_root=mock_codex.evidence_root)
    operator = _operator_client(client, mock_codex)

    response = operator.post(f"/api/incidents/{incident['id']}/triage")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["outcome"] == "ok"
    assert payload["job"]["status"] == "succeeded"
    assert payload["job"]["codex_session_id"] == "thr_mock"
    assert payload["triage"]["classification"] == "selector_change"
    assert payload["incident"]["status"] == "triaged"
    # The detector classification is preserved alongside the triage result.
    assert payload["incident"]["classification"] == "selector_missing"

    audits = _triage_audits(mock_codex.db_path)
    assert [row[0] for row in audits] == ["ok"]
    detail = json.loads(audits[0][1])
    assert detail["trigger"] == "manual"
    assert detail["job_id"] == payload["job"]["id"]

    # GET detail now carries latest_triage with the result.json content.
    detail_response = operator.get(f"/api/incidents/{incident['id']}")
    assert detail_response.status_code == 200
    latest = detail_response.json()["latest_triage"]
    assert latest["job"]["status"] == "succeeded"
    assert latest["result"]["confidence"] == pytest.approx(0.77)

    # repair-jobs listing (viewer+).
    jobs_response = operator.get(f"/api/incidents/{incident['id']}/repair-jobs")
    assert jobs_response.status_code == 200
    jobs = jobs_response.json()["jobs"]
    assert len(jobs) == 1
    job = jobs_response.json()["jobs"][0]
    assert job["stage"] == "triage"
    # JSONL events persisted verbatim.
    with open(job["jsonl_log_path"], encoding="utf-8") as fh:
        lines = [json.loads(line) for line in fh if line.strip()]
    assert lines[0]["type"] == "thread.started"
    # result.json persisted under the configured state root.
    with open(job["result_json_path"], encoding="utf-8") as fh:
        assert json.load(fh)["classification"] == "selector_change"


def test_triage_retry_creates_second_job(client, mock_codex):
    incident = _seed(mock_codex.db_path)
    operator = _operator_client(client, mock_codex)
    first = operator.post(f"/api/incidents/{incident['id']}/triage")
    second = operator.post(f"/api/incidents/{incident['id']}/triage")
    assert first.status_code == 200 and second.status_code == 200
    jobs = operator.get(f"/api/incidents/{incident['id']}/repair-jobs").json()["jobs"]
    assert len(jobs) == 2
    assert {job["status"] for job in jobs} == {"succeeded"}


def test_triage_unknown_incident_404_and_audited(client, mock_codex):
    operator = _operator_client(client, mock_codex)
    response = operator.post("/api/incidents/424242/triage")
    assert response.status_code == 404
    assert response.json()["detail"] == "unknown_incident"
    assert _triage_audits(mock_codex.db_path)[0][0] == "unknown_incident"


def test_triage_disabled_returns_503(client, web_settings):
    incident = _seed(web_settings.db_path)
    operator = _operator_client(client, web_settings)
    response = operator.post(f"/api/incidents/{incident['id']}/triage")
    assert response.status_code == 503
    assert response.json()["detail"] == "codex_disabled"
    assert _triage_audits(web_settings.db_path)[0][0] == "unavailable"


def test_repair_jobs_unknown_incident_404(viewer_client, web_settings):
    assert viewer_client.get("/api/incidents/424242/repair-jobs").status_code == 404


def test_auto_triage_scan_via_detector_entry(mock_codex):
    """Auto trigger: confidence >= threshold + fix-candidate class, direct call."""
    eligible = _seed(mock_codex.db_path, mock_codex.evidence_root,
                     signature="aaaa1111bbbb2222", confidence=0.70)
    _seed(mock_codex.db_path, signature="cccc3333dddd4444", confidence=0.40)
    _seed(mock_codex.db_path, signature="eeee5555ffff6666",
          classification="captcha", confidence=0.95)

    outcomes = run_auto_triage_scan(mock_codex)
    assert [o["outcome"] for o in outcomes] == ["ok"]
    assert get_incident(str(mock_codex.db_path), eligible["id"])["status"] == "triaged"

    audits = _triage_audits(mock_codex.db_path)
    assert len(audits) == 1
    detail = json.loads(audits[0][1])
    assert detail["trigger"] == "auto"

    # Idempotent: a second scan does not re-triage a triaged incident.
    assert run_auto_triage_scan(mock_codex) == []
