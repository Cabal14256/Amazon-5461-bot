"""Evidence tests: allowlist enforcement and text redaction."""

import os

import pytest

from src.db import get_conn


def _seed_evidence(settings):
    day_dir = settings.evidence_root / "2026-08-10" / "us_store_999"
    day_dir.mkdir(parents=True)
    log = day_dir / "run.log"
    log.write_text(
        "submit ok\npassword: fixture-evidence-secret\nemail fixture-secret-user@example.com\n",
        encoding="utf-8",
    )
    image = day_dir / "shot.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    return log, image


def test_evidence_list(viewer_client, web_settings):
    _seed_evidence(web_settings)
    response = viewer_client.get("/api/evidence/list", params={"date": "2026-08-10"})
    assert response.status_code == 200
    paths = [item["path"] for item in response.json()["evidence"]]
    assert "2026-08-10/us_store_999/run.log" in paths
    assert "2026-08-10/us_store_999/shot.png" in paths
    filtered = viewer_client.get("/api/evidence/list",
                                 params={"date": "2026-08-10", "account_id": "us_store_999"})
    assert filtered.json()["total"] == 2


def test_evidence_text_redacted_and_audited(viewer_client, web_settings):
    _seed_evidence(web_settings)
    response = viewer_client.get("/api/evidence/file",
                                 params={"path": "2026-08-10/us_store_999/run.log"})
    assert response.status_code == 200
    assert "fixture-evidence-secret" not in response.text
    assert "fixture-secret-user@example.com" not in response.text
    assert "[REDACTED" in response.text

    conn = get_conn(str(web_settings.db_path))
    rows = conn.execute(
        "SELECT action, target_id FROM web_audit_events WHERE action='evidence_download'"
    ).fetchall()
    conn.close()
    assert any(r["target_id"] == "2026-08-10/us_store_999/run.log" for r in rows)


def test_evidence_image_served_raw(viewer_client, web_settings):
    _, image = _seed_evidence(web_settings)
    response = viewer_client.get("/api/evidence/file",
                                 params={"path": "2026-08-10/us_store_999/shot.png"})
    assert response.status_code == 200
    assert response.content == image.read_bytes()


@pytest.mark.parametrize("bad_path", [
    "../private/accounts.json",
    "../../config/settings.yaml",
    "2026-08-10/../../runtime/private/accounts.json",
    "C:/Windows/win.ini",
    "/etc/passwd",
])
def test_path_traversal_rejected(viewer_client, web_settings, bad_path):
    _seed_evidence(web_settings)
    response = viewer_client.get("/api/evidence/file", params={"path": bad_path})
    assert response.status_code in (403, 404), bad_path


def test_symlink_escape_rejected(viewer_client, web_settings):
    _seed_evidence(web_settings)
    outside = web_settings.evidence_root.parent / "outside-secret.txt"
    outside.write_text("top secret outside", encoding="utf-8")
    link = web_settings.evidence_root / "2026-08-10" / "escape.txt"
    try:
        os.symlink(str(outside), str(link))
    except OSError:
        pytest.skip("symlink creation not permitted on this host")
    response = viewer_client.get("/api/evidence/file",
                                 params={"path": "2026-08-10/escape.txt"})
    assert response.status_code in (403, 404)


def test_missing_file_is_404(viewer_client, web_settings):
    _seed_evidence(web_settings)
    response = viewer_client.get("/api/evidence/file",
                                 params={"path": "2026-08-10/us_store_999/nope.log"})
    assert response.status_code == 404
