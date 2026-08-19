"""Sanitized settings API; all writes stay inside tmp_path fixtures."""

from __future__ import annotations

import json

import yaml
from fastapi.testclient import TestClient

from src.db import get_conn

from .conftest import TEST_PASSWORD, create_user, login


def _write_settings(web_settings, tmp_path) -> None:
    path = tmp_path / "config" / "settings.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(
        yaml.safe_dump(
            {
                "adspower": {
                    "api_key": "fixture-never-return-this",
                    "start_profile_timeout_sec": 60,
                },
                "browser": {"action_timeout_ms": 20_000},
                "case_followup": {
                    "enabled": True,
                    "delay_hours": 2.0,
                    "claim_group_limit": 20,
                    "max_parallel_profiles": 3,
                },
                "web": {
                    "submit_enabled": True,
                    "submit_max_brands": 7,
                    "session_ttl_hours": 12.0,
                },
                "custom_unexposed": {"preserve": "yes"},
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    web_settings.settings_path = path
    web_settings.settings_backup_root = tmp_path / "runtime" / "private" / "settings-backups"


def test_effective_settings_are_sanitized(viewer_client, web_settings, tmp_path):
    _write_settings(web_settings, tmp_path)

    response = viewer_client.get("/api/settings/effective")
    assert response.status_code == 200, response.text
    payload = response.json()
    fields = {field["key"]: field for field in payload["fields"]}

    assert payload["limits"] == {"diagnose": 20, "dry_run": 20, "submit": 7}
    assert payload["job_defaults"] == {
        "case_followup_delay_hours": 2.0,
        "case_followup_enabled": True,
    }
    assert fields["browser.action_timeout_ms"]["value"] == 20_000
    assert fields["web.submit_enabled"]["editable"] is False
    serialized = response.text
    assert "api_key" not in serialized
    assert "fixture-never-return-this" not in serialized
    assert "custom_unexposed" not in serialized


def test_admin_patch_is_atomic_backed_up_and_audited(admin_client, web_settings, tmp_path):
    _write_settings(web_settings, tmp_path)
    initial = admin_client.get("/api/settings/effective").json()

    response = admin_client.patch(
        "/api/settings",
        json={
            "revision": initial["revision"],
            "changes": {
                "browser.action_timeout_ms": 25_000,
                "web.submit_max_brands": 9,
            },
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload["changed"]) == {
        "browser.action_timeout_ms",
        "web.submit_max_brands",
    }
    assert payload["restart_required"] == []
    assert payload["snapshot"]["limits"]["submit"] == 9
    assert web_settings.submit_max_brands == 9

    persisted = yaml.safe_load(web_settings.settings_path.read_text(encoding="utf-8"))
    assert persisted["browser"]["action_timeout_ms"] == 25_000
    assert persisted["adspower"]["api_key"] == "fixture-never-return-this"
    assert persisted["custom_unexposed"] == {"preserve": "yes"}
    assert len(list(web_settings.settings_backup_root.glob("settings-*.yaml"))) == 1

    conn = get_conn(str(web_settings.db_path))
    row = conn.execute(
        "SELECT result, detail FROM web_audit_events WHERE action='settings_update'"
    ).fetchone()
    conn.close()
    assert row["result"] == "ok"
    detail = json.loads(row["detail"])
    assert set(detail["changes"]) == {
        "browser.action_timeout_ms",
        "web.submit_max_brands",
    }
    assert "fixture-never-return-this" not in row["detail"]


def test_settings_patch_requires_admin(app, web_settings, tmp_path):
    _write_settings(web_settings, tmp_path)
    create_user(web_settings, "operator-settings", TEST_PASSWORD, "operator")
    client = TestClient(app)
    login(client, "operator-settings", TEST_PASSWORD)
    revision = client.get("/api/settings/effective").json()["revision"]

    response = client.patch(
        "/api/settings",
        json={"revision": revision, "changes": {"web.submit_max_brands": 8}},
    )
    assert response.status_code == 403
    assert yaml.safe_load(web_settings.settings_path.read_text(encoding="utf-8"))["web"]["submit_max_brands"] == 7


def test_settings_patch_rejects_unsafe_invalid_and_stale_changes(admin_client, web_settings, tmp_path):
    _write_settings(web_settings, tmp_path)
    revision = admin_client.get("/api/settings/effective").json()["revision"]

    cases = [
        ({"adspower.api_key": "nope"}, 422, "unknown_setting:adspower.api_key"),
        ({"web.submit_enabled": False}, 422, "setting_not_editable:web.submit_enabled"),
        ({"web.submit_max_brands": 21}, 422, "value_too_large:web.submit_max_brands"),
        (
            {"case_followup.max_parallel_profiles": 4, "case_followup.claim_group_limit": 3},
            422,
            "parallel_profiles_exceed_claim_group",
        ),
    ]
    for changes, status, detail in cases:
        response = admin_client.patch(
            "/api/settings", json={"revision": revision, "changes": changes}
        )
        assert response.status_code == status
        assert response.json()["detail"] == detail

    response = admin_client.patch(
        "/api/settings",
        json={"revision": "stale-revision", "changes": {"web.submit_max_brands": 8}},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "settings_revision_conflict"
