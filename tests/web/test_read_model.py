"""Read model priority tests.

Case latest reply > Dashboard same-day check > explicit Case ID + submission
> local batch_state.
"""

import json
from datetime import datetime

from src.db import (
    enqueue_case_followup,
    finish_case_followup,
    insert_submission,
    insert_verification,
)
from src.web.services.read_model import resolve_brand_status

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


def test_unknown_when_no_data(web_settings):
    result = resolve_brand_status(
        str(web_settings.db_path), ACCOUNT, SITE, "NO_SUCH_BRAND",
        data_root=web_settings.data_root,
    )
    assert result["source"] == "none"
    assert result["status"] == "unknown"
