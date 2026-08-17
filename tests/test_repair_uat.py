"""Formal repair UAT CLI gates without invoking a real Codex process."""

from __future__ import annotations

import argparse
import sqlite3

from cli.amazon5461 import cmd_repair_uat
from src.codex_client.availability import CodexAvailability
from src.db import get_incident, init_db, record_incident, set_incident_evidence_bundle
from src.incidents import assess_evidence_bundle, build_evidence_bundle
from src.web.config import WebSettings


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
            "dom_contract": {"schema_version": 1, "nodes": [{"tag": "kat-button"}]},
            "previous_success": {"contract_hash": "a" * 64, "contract": {"nodes": [{"tag": "kat-button"}]}},
        }
    bundle = build_evidence_bundle(
        int(incident["id"]), evidence, page_evidence={"visible_text": "Apply"}, **kwargs,
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
