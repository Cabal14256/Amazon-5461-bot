"""Historical evidence backfill selection and privacy-safe reporting."""

from __future__ import annotations

import json
import sys

from scripts import backfill_incident_bundles as backfill
from src.db import get_incident, init_db, record_incident
from src.web.config import WebSettings


def test_scan_includes_nested_reapplication_batch_states(tmp_path):
    top = tmp_path / "data" / "batch_fixture.json"
    nested = tmp_path / "runtime" / "state" / "reapplications" / "campaign_1" / "batch.json"
    top.parent.mkdir(parents=True)
    nested.parent.mkdir(parents=True)
    top.write_text("{}", encoding="utf-8")
    nested.write_text("{}", encoding="utf-8")

    scanned = {path.resolve() for path in backfill._scan_files(tmp_path)}

    assert scanned == {top.resolve(), nested.resolve()}


def test_targeted_triaged_incident_is_reevaluated_without_identity_output(
    tmp_path, monkeypatch, capsys
):
    db_path = tmp_path / "runtime" / "state" / "ledger.db"
    evidence_root = tmp_path / "runtime" / "evidence"
    init_db(str(db_path))
    incident, _ = record_incident(
        str(db_path), signature="triaged-incomplete", scope_type="site",
        flow_type="5461", account_id="private-account", marketplace="US",
        brand_name="PRIVATE-BRAND", classification="selector_missing",
    )
    bundle = evidence_root / "incidents" / str(incident["id"])
    bundle.mkdir(parents=True)
    (bundle / "manifest.json").write_text(json.dumps({"incident_id": incident["id"]}))
    conn = backfill.get_conn(str(db_path))
    conn.execute(
        """UPDATE repair_incidents
           SET status='triaged', evidence_status='incomplete',
               missing_evidence_json='[]', evidence_bundle_path=? WHERE id=?""",
        (str(bundle), int(incident["id"])),
    )
    conn.commit()
    conn.close()
    settings = WebSettings(db_path=db_path, evidence_root=evidence_root, repo_root=tmp_path)
    monkeypatch.setattr(backfill, "load_settings", lambda: settings)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "backfill_incident_bundles.py",
            "--write",
            "--incident-id",
            str(incident["id"]),
            "--root",
            str(tmp_path),
        ],
    )

    assert backfill.main() == 0

    output = capsys.readouterr().out
    assert "re-evaluated 1 existing bundle" in output
    assert "private-account" not in output
    assert "PRIVATE-BRAND" not in output
    refreshed = get_incident(str(db_path), int(incident["id"]))
    assert refreshed["status"] == "triaged"
    assert refreshed["missing_evidence"] == [
        "page_summary",
        "selectors_and_probes",
        "dom_shadow_contract",
        "previous_success",
    ]
