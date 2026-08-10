from datetime import datetime, timedelta

from src import case_followup
from src.case_followup import case_detail_url, classify_case_reply, extract_case_detail
from src.db import (
    claim_due_case_followups,
    enqueue_case_followup,
    finish_case_followup,
    init_db,
    list_case_followups,
)
from src.marketplace_switcher import MarketplaceSwitcher


def _message(sender: str, body: str) -> dict:
    return {"sender": sender, "body": body, "raw_text": body, "timestamp_text": ""}


def test_case_detail_url_uses_existing_marketplace_domain_mapping():
    assert case_detail_url("BE", "19999999999") == (
        "https://sellercentral.amazon.co.uk/cu/case-dashboard/view-case/"
        "?ie=UTF8&caseID=19999999999"
    )
    assert "sellercentral.amazon.com/" in case_detail_url("MX", "12345678901")


def test_classifies_confirmed_decline_reply():
    result = classify_case_reply(
        "Answered",
        [
            _message(
                "Amazon",
                'During our review, there is no verifiable information that "ExampleBrand" offers '
                "SCREEN_PROTECTOR. We had to decline your application and associated GTIN exemption request.",
            ),
            _message("You", "Error 5461 application submitted for ExampleBrand."),
        ],
    )
    assert result["result"] == "declined"
    assert result["is_success"] is False
    assert result["has_amazon_reply"] is True


def test_classifies_spanish_explicit_decline_reply():
    result = classify_case_reply(
        "Answered",
        [
            _message(
                "Amazon",
                "En vista de lo anterior, hemos tenido que rechazar tu solicitud "
                "y la solicitud de exención de GTIN asociada.",
            )
        ],
    )
    assert result["result"] == "declined"
    assert result["is_success"] is False


def test_answered_status_alone_is_not_approved():
    result = classify_case_reply("Answered", [_message("You", "Application submitted")])
    assert result["result"] == "pending"
    assert result["is_success"] is None


def test_classifies_explicit_approval_and_action_required():
    approved = classify_case_reply(
        "Answered",
        [_message("Amazon", "Your application has been approved. You may now create ASINs for this brand.")],
    )
    assert approved["result"] == "approved"
    assert approved["is_success"] is True

    action = classify_case_reply(
        "Answered",
        [_message("Amazon", "Please reply to this case and provide additional documents for review.")],
    )
    assert action["result"] == "action_required"
    assert action["is_success"] is None


def test_classifies_accepted_application_reply_as_approved():
    result = classify_case_reply(
        "Answered",
        [
            _message(
                "Amazon",
                "We have completed our review and accepted your application. "
                'You can now create new ASINs for "ExampleBrand", "SCREEN_PROTECTOR".',
            )
        ],
    )
    assert result["result"] == "approved"


def test_classifies_german_accepted_application_reply_as_approved():
    result = classify_case_reply(
        "Answered",
        [
            _message(
                "Amazon",
                "Wir freuen uns, Ihnen mitteilen zu können, dass wir unsere Prüfung "
                "abgeschlossen und Ihren Antrag akzeptiert haben. Sie können jetzt neue "
                "ASINs für HOMEMO, SCREEN_PROTECTOR erstellen.",
            )
        ],
    )

    assert result["result"] == "approved"
    assert result["is_success"] is True
    assert result["is_success"] is True


def test_classifies_brand_and_gtin_decline_reply():
    result = classify_case_reply(
        "Answered",
        [
            _message(
                "Amazon",
                "In view of the aforementioned, we had to decline your brand "
                "and associated GTIN exemption request.",
            )
        ],
    )
    assert result["result"] == "declined"
    assert result["is_success"] is False


def test_classifies_french_explicit_rejection_reply():
    result = classify_case_reply(
        "Answered",
        [
            _message(
                "Amazon",
                "Au vu de ce qui précède, nous avons dû rejeter votre demande.",
            )
        ],
    )
    assert result["result"] == "declined"
    assert result["is_success"] is False


def test_classifies_german_had_to_decline_reply():
    result = classify_case_reply(
        "Answered",
        [
            _message(
                "Amazon",
                "Aufgrund des oben genannten Vorstehenden mussten wir Ihren Antrag ablehnen.",
            )
        ],
    )
    assert result["result"] == "declined"
    assert result["is_success"] is False


class _FakePage:
    def evaluate(self, _script, expected_case_id):
        return {
            "url": f"https://example.invalid/?caseID={expected_case_id}",
            "title": "Case",
            "body_text": f"Case Summary\nID:\n{expected_case_id}\nStatus:\nAnswered",
            "expected_case_visible": True,
            "messages": [],
        }


def test_extract_case_detail_keeps_answered_as_transport_status():
    detail = extract_case_detail(_FakePage(), "19999999999")
    assert detail["case_id"] == "19999999999"
    assert detail["case_status"] == "Answered"
    assert "body_text" not in detail


def test_case_approval_is_replaced_by_effective_verification(monkeypatch, tmp_path):
    class FakeBody:
        def inner_text(self, timeout=None):
            return "Seller Central Case detail"

    class FakePage:
        def __init__(self):
            self.url = "about:blank"

        def set_default_timeout(self, _timeout):
            pass

        def goto(self, url, **_kwargs):
            self.url = url

        def wait_for_load_state(self, *_args, **_kwargs):
            pass

        def locator(self, _selector):
            return FakeBody()

        def close(self):
            pass

    class FakeManager:
        def __init__(self, **_kwargs):
            self.page = FakePage()

        def connect_by_account(self, _account_id):
            return self.page, {
                "marketplace": "BE",
                "marketplace_configs": {"BE": {"entry_url": "https://example.invalid/add"}},
            }

        def new_tab(self):
            return self.page

        def close(self):
            pass

    monkeypatch.setattr(case_followup, "BrowserManager", FakeManager)
    monkeypatch.setattr(case_followup, "switch_marketplace", lambda *_args, **_kwargs: (True, "BE"))
    monkeypatch.setattr(case_followup, "_save_page_evidence", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        case_followup,
        "extract_case_detail",
        lambda *_args, **_kwargs: {
            "expected_case_visible": True,
            "url": "https://example.invalid/case",
            "case_status": "Answered",
            "messages": [
                _message("Amazon", "Your application has been approved. You may now create ASINs.")
            ],
        },
    )
    monkeypatch.setattr(
        case_followup,
        "verify_approved_case",
        lambda *_args, **_kwargs: {
            "result": "false_approved",
            "is_success": False,
            "decision_reason": "Manage Brand found; Add Product restricted",
            "manage_brand": {"result": "found"},
            "add_product": {"result": "fail"},
            "evidence_files": [],
        },
    )

    result = case_followup.check_case_detail(
        "us_store_000",
        "BE",
        "ExampleBrand",
        "19999999995",
        settings={
            "paths": {"evidence_root": str(tmp_path / "evidence")},
            "browser": {"action_timeout_ms": 100, "page_timeout_ms": 100},
        },
    )

    assert result["case_reply_result"] == "approved"
    assert result["result"] == "false_approved"
    assert result["is_success"] is False
    assert result["approval_verification"]["add_product"]["result"] == "fail"


def test_followup_queue_is_idempotent_and_claims_only_due_rows(tmp_path):
    db_path = str(tmp_path / "ledger.db")
    init_db(db_path)
    now = datetime.now()
    past = (now - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
    future = (now + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    submitted = now.strftime("%Y-%m-%d %H:%M:%S")

    first_id, created = enqueue_case_followup(
        db_path, "us_store_000", "BE", "ExampleBrand", "19999999999", submitted, past
    )
    duplicate_id, duplicate_created = enqueue_case_followup(
        db_path, "us_store_000", "BE", "ExampleBrand", "19999999999", submitted, future
    )
    enqueue_case_followup(
        db_path, "us_store_000", "BE", "OtherBrand", "19999999998", submitted, future
    )

    assert duplicate_id == first_id
    assert created is True
    assert duplicate_created is False
    claimed = claim_due_case_followups(db_path, limit=10)
    assert [row["case_id"] for row in claimed] == ["19999999999"]
    assert claimed[0]["attempt_count"] == 1

    finish_case_followup(
        db_path,
        first_id,
        "completed",
        "declined",
        "Answered",
        "explicit decline",
        "runtime/evidence/example",
    )
    rows = list_case_followups(db_path)
    assert rows[0]["status"] == "completed"
    assert rows[0]["final_result"] == "declined"


def test_targeted_claim_does_not_take_another_due_followup(tmp_path):
    db_path = str(tmp_path / "ledger.db")
    init_db(db_path)
    due = (datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
    first_id, _ = enqueue_case_followup(
        db_path, "us_store_667", "SE", "WILLONE", "19999999993", due, due
    )
    target_id, _ = enqueue_case_followup(
        db_path, "us_store_671", "BE", "HOMEMO", "19999999992", due, due
    )

    claimed = claim_due_case_followups(db_path, limit=5, followup_ids=[target_id])

    assert [row["id"] for row in claimed] == [target_id]
    untouched = next(row for row in list_case_followups(db_path) if row["id"] == first_id)
    assert untouched["status"] == "pending"


def test_connection_circuit_defers_other_due_tasks_for_same_account(monkeypatch, tmp_path):
    db_path = str(tmp_path / "ledger.db")
    init_db(db_path)
    due = (datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
    first_id, _ = enqueue_case_followup(
        db_path, "us_store_671", "BE", "HOMEMO", "19999999991", due, due
    )
    second_id, _ = enqueue_case_followup(
        db_path, "us_store_671", "BE", "JZG", "19999999990", due, due
    )
    third_id, _ = enqueue_case_followup(
        db_path, "us_store_667", "BE", "WILLONE", "19999999989", due, due
    )
    monkeypatch.setattr(
        case_followup,
        "check_case_detail",
        lambda *_args, **_kwargs: {
            "result": "error",
            "case_status": "",
            "decision_reason": "cdp_connect_failed (TimeoutError)",
            "technical_error_code": "cdp_connect_failed",
            "evidence_dir": "",
        },
    )
    settings = {
        "paths": {"db_path": db_path},
        "case_followup": {
            "error_retry_interval_hours": 1,
            "max_attempts": 3,
            "connection_circuit_breaker": True,
        },
    }

    results = case_followup.process_due_case_followups(
        settings=settings,
        limit=1,
        update_records=False,
        followup_ids=[first_id, second_id],
    )
    rows = {row["id"]: row for row in list_case_followups(db_path)}

    assert results[0]["connection_circuit_deferred_count"] == 1
    assert rows[first_id]["status"] == "retry"
    assert rows[second_id]["status"] == "retry"
    assert rows[second_id]["attempt_count"] == 0
    assert rows[third_id]["status"] == "pending"
    assert rows[second_id]["error"] == "connection_circuit_open: cdp_connect_failed"


def test_pending_result_is_rescheduled_without_registration(monkeypatch, tmp_path):
    db_path = str(tmp_path / "ledger.db")
    init_db(db_path)
    timestamp = (datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
    followup_id, _ = enqueue_case_followup(
        db_path, "us_store_000", "BE", "ExampleBrand", "19999999999", timestamp, timestamp
    )
    task = claim_due_case_followups(db_path, limit=1)[0]
    settings = {
        "paths": {"db_path": db_path},
        "case_followup": {
            "retry_interval_hours": 6,
            "error_retry_interval_hours": 1,
            "max_attempts": 3,
            "registration_path": str(tmp_path / "registry.xlsx"),
        },
    }

    monkeypatch.setattr(
        case_followup,
        "check_case_detail",
        lambda *args, **kwargs: {
            "result": "pending",
            "case_status": "Open",
            "decision_reason": "no Amazon reply",
            "evidence_dir": "runtime/evidence/example",
        },
    )
    result = case_followup.process_claimed_followup(settings, task, update_records=False)
    assert result["result"] == "pending"
    row = next(item for item in list_case_followups(db_path) if item["id"] == followup_id)
    assert row["status"] == "retry"
    assert row["attempt_count"] == 1
    assert row["scheduled_at"] > datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def test_false_approved_is_terminal_and_registered(monkeypatch, tmp_path):
    db_path = str(tmp_path / "ledger.db")
    init_db(db_path)
    timestamp = (datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
    followup_id, _ = enqueue_case_followup(
        db_path, "us_store_000", "BE", "ExampleBrand", "19999999996", timestamp, timestamp
    )
    task = claim_due_case_followups(db_path, limit=1)[0]
    settings = {
        "paths": {"db_path": db_path},
        "case_followup": {
            "retry_interval_hours": 6,
            "error_retry_interval_hours": 1,
            "max_attempts": 3,
            "registration_path": str(tmp_path / "registry.xlsx"),
        },
    }
    recorded = []
    monkeypatch.setattr(
        case_followup,
        "check_case_detail",
        lambda *args, **kwargs: {
            "result": "false_approved",
            "case_status": "Answered",
            "decision_reason": "Case approved but Add Product remained restricted",
            "evidence_dir": "runtime/evidence/example",
        },
    )
    monkeypatch.setattr(
        case_followup,
        "record_case_outcome",
        lambda _settings, _task, result: recorded.append(result["result"]),
    )

    result = case_followup.process_claimed_followup(settings, task, update_records=True)

    assert result["result"] == "false_approved"
    assert recorded == ["false_approved"]
    row = next(item for item in list_case_followups(db_path) if item["id"] == followup_id)
    assert row["status"] == "completed"
    assert row["final_result"] == "false_approved"


def test_schedule_persists_unique_feishu_record_binding(monkeypatch, tmp_path):
    from src import feishu_bitable

    db_path = str(tmp_path / "ledger.db")
    settings = {
        "paths": {"db_path": db_path},
        "case_followup": {
            "delay_hours": 24,
            "registration_path": str(tmp_path / "registry.xlsx"),
        },
        "feishu_bitable": {"enabled": True, "bind_on_schedule": True},
    }
    ensured = []
    monkeypatch.setattr(
        feishu_bitable,
        "ensure_submission_records",
        lambda *_args, **kwargs: ensured.append(kwargs)
        or {
            "status": "dry_run",
            "detail": {"status": "would_create"},
            "shared": {"status": "existing"},
        },
    )
    monkeypatch.setattr(
        feishu_bitable,
        "bind_case_to_record",
        lambda *_args, **_kwargs: {
            "status": "bound",
            "reason": "unique existing Feishu record matched",
            "record_id": "rec_test",
            "country_option": "比利时1",
            "candidate_count": 1,
        },
    )
    monkeypatch.setattr(
        feishu_bitable,
        "update_bound_progress",
        lambda *_args, **_kwargs: {"status": "dry_run", "value": "申请中"},
    )

    scheduled = case_followup.schedule_case_followup(
        settings,
        "eu_store_000",
        "BE",
        "ExampleBrand",
        "19999999997",
        sku="SKU-1",
        feishu_country_option="比利时1",
        submission_title="ExampleBrand Screen Protector",
        submission_content="Brand：ExampleBrand\nSKU：SKU-1",
        uk_sku="SKU-UK",
        uk_title="ExampleBrand Screen Protector UK",
        uk_content="Brand：ExampleBrand\nSKU：SKU-UK",
    )

    row = next(item for item in list_case_followups(db_path) if item["id"] == scheduled["id"])
    assert row["feishu_binding_status"] == "bound"
    assert row["feishu_record_id"] == "rec_test"
    assert row["feishu_country_option"] == "比利时1"
    assert scheduled["feishu_progress_update"]["value"] == "申请中"
    assert scheduled["feishu_record_ensure"]["detail"]["status"] == "would_create"
    assert ensured[0]["title"] == "ExampleBrand Screen Protector"
    assert ensured[0]["uk_sku"] == "SKU-UK"


def test_marketplace_switch_evidence_passes_timeout_to_screenshot(tmp_path):
    class FakePage:
        def __init__(self):
            self.kwargs = None

        def screenshot(self, **kwargs):
            self.kwargs = kwargs

    page = FakePage()
    MarketplaceSwitcher(page, evidence_dir=str(tmp_path))._take_screenshot("switch")
    assert page.kwargs["path"] == str(tmp_path / "switch.png")
    assert page.kwargs["timeout"] == 10000
    assert page.kwargs["full_page"] is True
