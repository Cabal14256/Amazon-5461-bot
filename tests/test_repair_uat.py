"""Formal repair UAT CLI gates without invoking a real Codex process."""

from __future__ import annotations

import argparse
import sqlite3

from cli.amazon5461 import cmd_repair_uat
from src.codex_client.availability import CodexAvailability
from src.db import get_incident, init_db, record_incident, set_incident_evidence_bundle
from src.incidents import assess_evidence_bundle, build_evidence_bundle
from src.web.config import WebSettings
from tests.evidence_fixtures import dom_contract, previous_success


def _env(tmp_path, *, complete: bool):
    db_path = tmp_path / "runtime" / "state" / "ledger.db"
    evidence = tmp_path / "runtime" / "evidence"
    init_db(str(db_path))
    incident, _ = record_incident(
        str(db_path), signature="uat-incident", scope_type="site", flow_type="5461",
        marketplace="US", classification="selector_missing", confidence=0.8,
    )
    kwargs = {}
    if complete:
        kwargs = {
            "selectors": {"declared_candidates": ["kat-button"], "probes": {"count": 1}},
            "dom_contract": dom_contract(),
            "previous_success": previous_success(),
        }
    bundle = build_evidence_bundle(
        int(incident["id"]), evidence, page_evidence={"visible_text": "Apply"},
        run_context={"marketplace": "US", "flow_type": "5461"}, **kwargs,
    )
    gate = assess_evidence_bundle(bundle)
    set_incident_evidence_bundle(
        str(db_path), int(incident["id"]), str(bundle),
        evidence_status=gate["status"], missing_evidence=gate["missing"],
    )
    settings = WebSettings(
        db_path=db_path, evidence_root=evidence, repo_root=tmp_path,
        codex_enabled=True, incidents_enabled=True, codex_release_enabled=False,
        codex_command="fixture-codex", session_secret="0" * 64,
    )
    return settings, get_incident(str(db_path), int(incident["id"]))


def test_repair_uat_check_only_migrates_audits_and_creates_no_job(tmp_path, monkeypatch):
    settings, incident = _env(tmp_path, complete=True)
    monkeypatch.setattr("src.web.config.load_settings", lambda: settings)
    monkeypatch.setattr(
        "src.codex_client.availability.check_availability",
        lambda _command: CodexAvailability(True, version="fixture"),
    )
    code = cmd_repair_uat(argparse.Namespace(
        incident_id=incident["id"], check_only=True, json=True,
    ))
    conn = sqlite3.connect(settings.db_path)
    jobs = conn.execute("SELECT COUNT(*) FROM codex_repair_jobs").fetchone()[0]
    audits = conn.execute("SELECT result FROM web_audit_events WHERE action='repair_uat'").fetchall()
    conn.close()
    assert code == 0
    assert jobs == 0
    assert audits == [("check_ok",)]


def test_repair_uat_incomplete_evidence_refuses_before_job(tmp_path, monkeypatch):
    settings, incident = _env(tmp_path, complete=False)
    monkeypatch.setattr("src.web.config.load_settings", lambda: settings)
    monkeypatch.setattr(
        "src.codex_client.availability.check_availability",
        lambda _command: CodexAvailability(True, version="fixture"),
    )
    code = cmd_repair_uat(argparse.Namespace(
        incident_id=incident["id"], check_only=False, json=True,
    ))
    conn = sqlite3.connect(settings.db_path)
    jobs = conn.execute("SELECT COUNT(*) FROM codex_repair_jobs").fetchone()[0]
    conn.close()
    assert code == 2
    assert jobs == 0


def test_repair_uat_revalidates_files_instead_of_trusting_ready_db(tmp_path, monkeypatch):
    settings, incident = _env(tmp_path, complete=True)
    bundle = settings.evidence_root / "incidents" / str(incident["id"])
    (bundle / "dom-contract.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr("src.web.config.load_settings", lambda: settings)
    monkeypatch.setattr(
        "src.codex_client.availability.check_availability",
        lambda _command: CodexAvailability(True, version="fixture"),
    )

    code = cmd_repair_uat(
        argparse.Namespace(incident_id=incident["id"], check_only=False, json=True)
    )

    refreshed = get_incident(str(settings.db_path), int(incident["id"]))
    assert code == 2
    assert refreshed["evidence_status"] == "incomplete"
    assert "dom_shadow_contract" in refreshed["missing_evidence"]


def test_repair_uat_refuses_unresolved_git_reconciliation(tmp_path, monkeypatch):
    settings, incident = _env(tmp_path, complete=True)
    monkeypatch.setattr("src.web.config.load_settings", lambda: settings)
    monkeypatch.setattr(
        "src.codex_client.availability.check_availability",
        lambda _command: CodexAvailability(True, version="fixture"),
    )
    monkeypatch.setattr(
        "src.db.count_unresolved_git_reconciliation", lambda _db_path: 1
    )

    code = cmd_repair_uat(
        argparse.Namespace(incident_id=incident["id"], check_only=True, json=True)
    )

    assert code == 2


def test_repair_uat_reports_reconciliation_audit_failure_as_internal(tmp_path, monkeypatch):
    settings, incident = _env(tmp_path, complete=True)
    monkeypatch.setattr("src.web.config.load_settings", lambda: settings)
    monkeypatch.setattr(
        "src.repair.release_recovery.reconcile_git_operations",
        lambda _settings: [
            {"operation_id": 1, "outcome": "adopted_release", "audit_error": True}
        ],
    )
    monkeypatch.setattr(
        "src.codex_client.availability.check_availability",
        lambda _command: CodexAvailability(True, version="fixture"),
    )

    code = cmd_repair_uat(
        argparse.Namespace(incident_id=incident["id"], check_only=True, json=True)
    )

    assert code == 1
