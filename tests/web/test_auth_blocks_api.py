from __future__ import annotations

from src.auth_recovery import create_auth_block, ensure_submission_checkpoint
from src.db import create_automation_job, get_conn
from src.web.api import auth_blocks as api
from tests.web.conftest import TEST_PASSWORD, create_user, login


def _block(settings):
    db_path = str(settings.db_path)
    create_automation_job(
        db_path,
        job_id="job-web-auth",
        job_type="submit",
        created_by="fixture",
        account_id="us_store_999",
        marketplace="US",
        brands=["TESTBRAND"],
    )
    checkpoint = ensure_submission_checkpoint(
        db_path,
        owner_type="automation_job",
        owner_id="job-web-auth",
        account_id="us_store_999",
        marketplace="US",
        brand_name="TESTBRAND",
    )
    return create_auth_block(
        settings,
        account_id="us_store_999",
        marketplace="US",
        brand_name="TESTBRAND",
        block_type="login_required",
        phase="before_submit",
        source_type="automation_job",
        source_id="job-web-auth",
        evidence_path=str(settings.evidence_root / "auth.png"),
        checkpoint_id=checkpoint["id"],
    )


def test_viewer_sees_redacted_persistent_block_but_cannot_act(viewer_client, web_settings):
    _block(web_settings)
    response = viewer_client.get("/api/auth-blocks")
    assert response.status_code == 200
    item = response.json()["auth_blocks"][0]
    assert item["account_id"] == "us_store_999"
    assert item["profile_hint"] != "fixture-profile-id-xyz"
    assert "profile_key" not in item
    assert item["evidence_path"] == "auth.png"
    assert item["can_open_profile"] is False
    assert viewer_client.post(f"/api/auth-blocks/{item['id']}/open-profile").status_code == 403
    assert viewer_client.post(f"/api/auth-blocks/{item['id']}/verify-and-resume").status_code == 403


def test_reviewer_actions_are_idempotent_and_audited(client, web_settings, monkeypatch):
    block = _block(web_settings)
    create_user(web_settings, "reviewer-auth", TEST_PASSWORD, "reviewer")
    login(client, "reviewer-auth", TEST_PASSWORD)
    monkeypatch.setattr(api, "open_auth_profile", lambda _settings, block_id: {"status": "opened", "block_id": block_id})
    monkeypatch.setattr(
        api,
        "verify_auth_block",
        lambda _settings, block_id, passive=False: {
            "status": "resolved",
            "block_id": block_id,
            "actions": ["submission_resume"],
        },
    )

    opened = client.post(f"/api/auth-blocks/{block['id']}/open-profile")
    resumed = client.post(f"/api/auth-blocks/{block['id']}/verify-and-resume")
    assert opened.status_code == 200
    assert resumed.status_code == 200
    assert resumed.json()["actions"] == ["submission_resume"]

    conn = get_conn(str(web_settings.db_path))
    actions = [
        row["action"]
        for row in conn.execute(
            "SELECT action FROM web_audit_events WHERE target_type='auth_block' ORDER BY id"
        ).fetchall()
    ]
    conn.close()
    assert actions == ["auth_block_open_profile", "auth_block_verify_resume"]
