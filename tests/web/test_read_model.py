"""Read model priority tests.

Active authentication/submission checkpoint > Case latest reply > Dashboard
same-day check > explicit Case ID + submission > local batch_state.
"""

import json
from datetime import datetime

from src.auth_recovery import create_auth_block, ensure_submission_checkpoint
from src.db import (
    enqueue_case_followup,
    finish_case_followup,
    insert_submission,
    insert_verification,
)
from src.web.services.read_model import list_applications, resolve_brand_status

ACCOUNT = "us_store_999"
SITE = "US"
BRAND = "TESTBRAND"
NOW = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
TODAY = datetime.now().strftime("%Y-%m-%d")


def _write_batch_state(data_root):
    data_root.mkdir(parents=True, exist_ok=True)
    (data_root / "batch_test_state.json").write_text(json.dumps({
        "created_at": NOW,
        "batches": [{
            "batch_no": 1,
            "items": [{
                "account_id": ACCOUNT,
                "brand_name": BRAND,
                "site": SITE,
                "status": "completed",
                "result": {"status": "success", "case_id": "999000111"},
            }],
        }],
    }), encoding="utf-8")


def test_batch_state_is_lowest_priority(web_settings):
    _write_batch_state(web_settings.data_root)
    result = resolve_brand_status(
        str(web_settings.db_path), ACCOUNT, SITE, BRAND, data_root=web_settings.data_root
    )
    assert result["source"] == "batch_state"
    assert result["status"] == "submitted"


def test_submission_case_id_beats_batch_state(web_settings):
    _write_batch_state(web_settings.data_root)
    insert_submission(str(web_settings.db_path), ACCOUNT, SITE, BRAND,
                      "success", case_id="12345678901", submitted_at=NOW)
    result = resolve_brand_status(
        str(web_settings.db_path), ACCOUNT, SITE, BRAND, data_root=web_settings.data_root
    )
    assert result["source"] == "submission_case_id"
    assert result["status"] == "under_review"


def test_dashboard_today_beats_submission(web_settings):
    _write_batch_state(web_settings.data_root)
    insert_submission(str(web_settings.db_path), ACCOUNT, SITE, BRAND,
                      "success", case_id="12345678901", submitted_at=NOW)
    insert_verification(str(web_settings.db_path), ACCOUNT, SITE, BRAND,
                        method="dashboard", result="pass", verified_at=NOW)
    result = resolve_brand_status(
        str(web_settings.db_path), ACCOUNT, SITE, BRAND, data_root=web_settings.data_root
    )
    assert result["source"] == "dashboard_today"
    assert result["status"] == "approved"


def test_case_reply_beats_everything(web_settings):
    _write_batch_state(web_settings.data_root)
    insert_submission(str(web_settings.db_path), ACCOUNT, SITE, BRAND,
                      "success", case_id="12345678901", submitted_at=NOW)
    insert_verification(str(web_settings.db_path), ACCOUNT, SITE, BRAND,
                        method="dashboard", result="pass", verified_at=NOW)
    followup_id, created = enqueue_case_followup(
        str(web_settings.db_path), ACCOUNT, SITE, BRAND, "12345678901",
        submitted_at=NOW, scheduled_at=NOW,
    )
    assert created
    finish_case_followup(
        str(web_settings.db_path), followup_id, "completed", "declined",
        "Denied", "declined by Amazon", None,
    )
    result = resolve_brand_status(
        str(web_settings.db_path), ACCOUNT, SITE, BRAND, data_root=web_settings.data_root
    )
    assert result["source"] == "case_reply"
    assert result["status"] == "declined"


def test_case_followup_technical_error_is_not_exposed_as_amazon_reply(web_settings):
    followup_id, created = enqueue_case_followup(
        str(web_settings.db_path), ACCOUNT, SITE, BRAND, "12345678903",
        submitted_at=NOW, scheduled_at=NOW,
    )
    assert created
    finish_case_followup(
        str(web_settings.db_path), followup_id, "retry", "error",
        None, "adspower_profile_unavailable (AdsPowerAPIError)", None,
        error="adspower_profile_unavailable",
    )

    result = resolve_brand_status(
        str(web_settings.db_path), ACCOUNT, SITE, BRAND, data_root=web_settings.data_root
    )

    assert result["status"] == "error"
    assert result["source"] == "case_followup_error"
    assert result["detail"] == "adspower_profile_unavailable (AdsPowerAPIError)"


def test_reconciled_checkpoint_does_not_reuse_an_older_case_reply(web_settings):
    old_case = "12345678901"
    new_case = "12345678902"
    followup_id, created = enqueue_case_followup(
        str(web_settings.db_path), ACCOUNT, SITE, BRAND, old_case,
        submitted_at=NOW, scheduled_at=NOW,
    )
    assert created
    finish_case_followup(
        str(web_settings.db_path), followup_id, "completed", "declined",
        "Denied", "older case declined", None,
    )
    checkpoint = ensure_submission_checkpoint(
        str(web_settings.db_path),
        owner_type="automation_job",
        owner_id="fixture-reconciled-job",
        account_id=ACCOUNT,
        marketplace=SITE,
        brand_name=BRAND,
    )
    from src.db import get_conn

    conn = get_conn(str(web_settings.db_path))
    conn.execute(
        """UPDATE submission_checkpoints
           SET status='completed', phase='dashboard_approved', case_id=?
           WHERE id=?""",
        (new_case, checkpoint["id"]),
    )
    conn.commit()
    conn.close()
    insert_verification(
        str(web_settings.db_path), ACCOUNT, SITE, BRAND,
        method="dashboard", result="pass", verified_at=NOW,
    )

    result = resolve_brand_status(
        str(web_settings.db_path), ACCOUNT, SITE, BRAND, data_root=web_settings.data_root
    )
    assert result["source"] == "dashboard_today"
    assert result["status"] == "approved"


def test_application_rows_keep_outcomes_scoped_to_their_case_ids(web_settings):
    old_case = "22345678901"
    new_case = "22345678902"
    insert_submission(
        str(web_settings.db_path), ACCOUNT, SITE, BRAND,
        "declined", case_id=old_case, submitted_at=NOW,
    )
    insert_submission(
        str(web_settings.db_path), ACCOUNT, SITE, BRAND,
        "approved", case_id=new_case, submitted_at=NOW,
    )
    followup_id, created = enqueue_case_followup(
        str(web_settings.db_path), ACCOUNT, SITE, BRAND, old_case,
        submitted_at=NOW, scheduled_at=NOW,
    )
    assert created
    finish_case_followup(
        str(web_settings.db_path), followup_id, "completed", "declined",
        "Denied", "older case declined", None,
    )
    checkpoint = ensure_submission_checkpoint(
        str(web_settings.db_path),
        owner_type="automation_job",
        owner_id="fixture-row-scoping-job",
        account_id=ACCOUNT,
        marketplace=SITE,
        brand_name=BRAND,
    )
    from src.db import get_conn

    conn = get_conn(str(web_settings.db_path))
    conn.execute(
        """UPDATE submission_checkpoints
           SET status='completed', phase='dashboard_approved', case_id=?
           WHERE id=?""",
        (new_case, checkpoint["id"]),
    )
    conn.commit()
    conn.close()
    insert_verification(
        str(web_settings.db_path), ACCOUNT, SITE, BRAND,
        method="dashboard", result="pass", verified_at=NOW,
    )

    rows = list_applications(
        str(web_settings.db_path),
        data_root=web_settings.data_root,
        account_id=ACCOUNT,
        marketplace=SITE,
        brand_name=BRAND,
    )
    by_case = {row["case_id"]: row for row in rows}
    assert by_case[new_case]["authoritative"]["status"] == "approved"
    assert by_case[old_case]["authoritative"]["status"] == "declined"
    assert by_case[old_case]["authoritative"]["source"] == "case_reply"
    assert by_case[new_case]["automation"] is not None
    assert by_case[old_case]["automation"] is None


def test_unknown_when_no_data(web_settings):
    result = resolve_brand_status(
        str(web_settings.db_path), ACCOUNT, SITE, "NO_SUCH_BRAND",
        data_root=web_settings.data_root,
    )
    assert result["source"] == "none"
    assert result["status"] == "unknown"


def test_active_auth_checkpoint_is_visible_and_survives_without_submission(viewer_client, web_settings):
    checkpoint = ensure_submission_checkpoint(
        str(web_settings.db_path),
        owner_type="direct_run",
        owner_id="fixture-auth-read-model",
        account_id=ACCOUNT,
        marketplace=SITE,
        brand_name="AUTH-BRAND",
    )
    create_auth_block(
        web_settings,
        account_id=ACCOUNT,
        marketplace=SITE,
        brand_name="AUTH-BRAND",
        block_type="login_required",
        phase="before_submit",
        source_type="direct_run",
        source_id="fixture-auth-read-model",
        checkpoint_id=checkpoint["id"],
    )

    response = viewer_client.get("/api/applications")
    assert response.status_code == 200
    item = next(row for row in response.json()["applications"] if row["brand_name"] == "AUTH-BRAND")
    assert item["authoritative"]["status"] == "waiting_login"
    assert item["authoritative"]["source"] == "automation_checkpoint"
    assert item["automation"]["submit_fenced"] is False
    assert "继续当前站点" in item["automation"]["next_action"]
