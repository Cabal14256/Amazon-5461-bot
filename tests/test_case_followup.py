from datetime import datetime, timedelta

from src import case_followup
from src.case_followup import case_detail_url, classify_case_reply, extract_case_detail
from src.db import (
    claim_due_case_followups,
    enqueue_case_followup,
    finish_case_followup,
    init_db,
    list_case_followups,
    reopen_case_followup_for_retry,
    requeue_stale_case_followups,
    upsert_case_outcome_status,
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


def test_windows_followup_worker_does_not_inherit_iana_timezone():
    env = case_followup.build_worker_subprocess_env(
        {"TZ": "Asia/Taipei", "KEEP_ME": "yes"},
        platform_name="nt",
    )

    assert "TZ" not in env
    assert env["KEEP_ME"] == "yes"
    assert env["PYTHONUTF8"] == "1"


def test_posix_followup_worker_preserves_timezone_override():
    env = case_followup.build_worker_subprocess_env(
        {"TZ": "Asia/Taipei"},
        platform_name="posix",
    )

    assert env["TZ"] == "Asia/Taipei"


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


def test_classifies_not_approved_and_applications_closed_as_declined():
    result = classify_case_reply(
        "Answered",
        [
            _message(
                "Amazon",
                "You are not approved to create new ASINs for this brand, and "
                "we are not accepting applications for approval.",
            )
        ],
    )

    assert result["result"] == "declined"
    assert result["is_success"] is False


def test_classifies_spanish_accepted_review_as_approved():
    result = classify_case_reply(
        "Answered",
        [
            _message(
                "Amazon",
                "Nos complace informarte que finalizamos la revisión y "
                "aceptamos tu solicitud. Ahora puedes crear nuevos ASINs.",
            )
        ],
    )

    assert result["result"] == "approved"
    assert result["is_success"] is True


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
                "ASINs für DEMO_HOME, SCREEN_PROTECTOR erstellen.",
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

        def close_tabs_except_hosts(self, _allowed_hosts, kept_pages=None):
            return {"closed": 0, "kept": 0, "failed": 0}

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
        db_path, "us_store_007", "SE", "DEMO_WILL", "19999999993", due, due
    )
    target_id, _ = enqueue_case_followup(
        db_path, "us_store_002", "BE", "DEMO_HOME", "19999999992", due, due
    )

    claimed = claim_due_case_followups(db_path, limit=5, followup_ids=[target_id])

    assert [row["id"] for row in claimed] == [target_id]
    untouched = next(row for row in list_case_followups(db_path) if row["id"] == first_id)
    assert untouched["status"] == "pending"


def test_oldest_group_claim_leaves_later_groups_unclaimed(tmp_path):
    db_path = str(tmp_path / "ledger.db")
    init_db(db_path)
    due = (datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
    first_id, _ = enqueue_case_followup(
        db_path, "us_store_004", "BE", "DEMO_JADE", "19999999981", due, due
    )
    second_id, _ = enqueue_case_followup(
        db_path, "us_store_004", "BE", "DEMO_VISTA", "19999999982", due, due
    )
    later_id, _ = enqueue_case_followup(
        db_path, "us_store_005", "BE", "DEMO_SHIELD", "19999999983", due, due
    )

    claimed = claim_due_case_followups(
        db_path, limit=20, oldest_group_only=True
    )

    assert [row["id"] for row in claimed] == [first_id, second_id]
    rows = {row["id"]: row for row in list_case_followups(db_path)}
    assert rows[later_id]["status"] == "pending"
    assert rows[later_id]["attempt_count"] == 0


def test_account_scoped_group_claim_does_not_touch_older_account(tmp_path):
    db_path = str(tmp_path / "ledger.db")
    init_db(db_path)
    due = (datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
    old_id, _ = enqueue_case_followup(
        db_path, "us_store_007", "SE", "DEMO_WILL", "19999999971", due, due
    )
    target_id, _ = enqueue_case_followup(
        db_path, "us_store_004", "BE", "DEMO_JADE", "19999999972", due, due
    )

    claimed = claim_due_case_followups(
        db_path,
        limit=20,
        oldest_group_only=True,
        account_id="us_store_004",
        marketplace="BE",
    )

    assert [row["id"] for row in claimed] == [target_id]
    rows = {row["id"]: row for row in list_case_followups(db_path)}
    assert rows[old_id]["status"] == "pending"
    assert rows[old_id]["attempt_count"] == 0


def test_claim_records_worker_pid_and_dead_worker_requeued_immediately(tmp_path):
    db_path = str(tmp_path / "ledger.db")
    init_db(db_path)
    due = (datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
    followup_id, _ = enqueue_case_followup(
        db_path, "us_store_004", "BE", "DEMO_JADE", "19999999961", due, due
    )

    claimed = claim_due_case_followups(db_path, limit=1, claimed_pid=424242)
    assert claimed[0]["claimed_pid"] == 424242

    # last_checked_at is fresh (far from the 1h time threshold), but the
    # claiming worker is verifiably dead, so recovery must not wait.
    stale_before = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    requeued = requeue_stale_case_followups(db_path, stale_before, pid_alive=lambda pid: False)

    assert requeued == 1
    row = next(r for r in list_case_followups(db_path) if r["id"] == followup_id)
    assert row["status"] == "retry"
    assert row["claimed_pid"] is None


def test_stale_requeue_keeps_rows_of_live_worker(tmp_path):
    db_path = str(tmp_path / "ledger.db")
    init_db(db_path)
    due = (datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
    followup_id, _ = enqueue_case_followup(
        db_path, "us_store_004", "BE", "DEMO_JADE", "19999999962", due, due
    )
    claim_due_case_followups(db_path, limit=1, claimed_pid=424243)

    stale_before = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    requeued = requeue_stale_case_followups(db_path, stale_before, pid_alive=lambda pid: True)

    assert requeued == 0
    row = next(r for r in list_case_followups(db_path) if r["id"] == followup_id)
    assert row["status"] == "running"


def test_stale_requeue_time_fallback_for_rows_without_pid(tmp_path):
    db_path = str(tmp_path / "ledger.db")
    init_db(db_path)
    old = (datetime.now() - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
    recent = (datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
    old_id, _ = enqueue_case_followup(
        db_path, "us_store_004", "BE", "DEMO_JADE", "19999999963", old, old
    )
    recent_id, _ = enqueue_case_followup(
        db_path, "us_store_004", "BE", "DEMO_VISTA", "19999999964", recent, recent
    )
    claim_due_case_followups(db_path, due_at=old, limit=1, account_id="us_store_004", marketplace="BE")
    claim_due_case_followups(db_path, limit=1, followup_ids=[recent_id])

    stale_before = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    requeued = requeue_stale_case_followups(db_path, stale_before, pid_alive=lambda pid: False)

    assert requeued == 1
    rows = {r["id"]: r for r in list_case_followups(db_path)}
    assert rows[old_id]["status"] == "retry"
    assert rows[recent_id]["status"] == "running"


def test_due_same_site_followups_share_one_browser_session(monkeypatch, tmp_path):
    db_path = str(tmp_path / "ledger.db")
    init_db(db_path)
    due = (datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
    first_id, _ = enqueue_case_followup(
        db_path, "us_store_002", "MX", "DEMO_JADE", "19999999987", due, due
    )
    second_id, _ = enqueue_case_followup(
        db_path, "us_store_002", "MX", "DEMO_ORBIT", "19999999986", due, due
    )
    sessions = []
    processed_sessions = []

    class FakeSession:
        def __init__(self, _settings, account_id, site):
            self.account_id = account_id
            self.site = site
            self.close_calls = 0
            sessions.append(self)

        def close(self):
            self.close_calls += 1

    def fake_process(_settings, _task, update_records=True, browser_session=None):
        processed_sessions.append(browser_session)
        return {"result": "pending", "decision_reason": "still under review"}

    monkeypatch.setattr(case_followup, "CaseFollowupBrowserSession", FakeSession)
    monkeypatch.setattr(case_followup, "process_claimed_followup", fake_process)
    settings = {"paths": {"db_path": db_path}, "case_followup": {}}

    results = case_followup.process_due_case_followups(
        settings=settings,
        limit=2,
        update_records=False,
        followup_ids=[first_id, second_id],
    )

    assert len(results) == 2
    assert len(sessions) == 1
    assert processed_sessions == [sessions[0], sessions[0]]
    assert sessions[0].account_id == "us_store_002"
    assert sessions[0].site == "MX"
    assert sessions[0].close_calls == 1


def test_followup_session_close_stops_adspower_profile():
    calls = []

    class FakePage:
        def close(self):
            calls.append(("page_close", None))

    class FakeManager:
        def close_tabs_except_hosts(self, hosts):
            calls.append(("tab_cleanup", hosts))

        def close(self, *, stop_profile=False):
            calls.append(("manager_close", stop_profile))

    session = case_followup.CaseFollowupBrowserSession({}, "example-account", "US")
    session.page = FakePage()
    session.manager = FakeManager()
    session.ready = True

    session.close()

    assert calls[-1] == ("manager_close", True)
    assert session.manager is None
    assert session.page is None
    assert session.ready is False


def test_effectively_approved_brand_skips_case_browser_check(monkeypatch, tmp_path):
    db_path = str(tmp_path / "ledger.db")
    init_db(db_path)
    due = (datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
    followup_id, _ = enqueue_case_followup(
        db_path, "us_store_002", "MX", "DEMO_JADE", "19999999985", due, due
    )
    upsert_case_outcome_status(
        db_path,
        "us_store_002",
        "MX",
        "demo_jade",
        "approved",
        method="case_reply_and_effective_approval",
    )
    monkeypatch.setattr(
        case_followup,
        "check_case_detail",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("approved brand must not open Case detail")
        ),
    )

    results = case_followup.process_due_case_followups(
        settings={"paths": {"db_path": db_path}, "case_followup": {}},
        limit=1,
        update_records=False,
        followup_ids=[followup_id],
    )
    row = next(item for item in list_case_followups(db_path) if item["id"] == followup_id)

    assert results[0]["result"] == "approved"
    assert results[0]["skipped"] is True
    assert results[0]["skip_reason"] == "already_effectively_approved"
    assert row["status"] == "completed"
    assert row["final_result"] == "approved"


def test_false_approved_brand_is_not_skipped(monkeypatch, tmp_path):
    db_path = str(tmp_path / "ledger.db")
    init_db(db_path)
    due = (datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
    followup_id, _ = enqueue_case_followup(
        db_path, "us_store_002", "MX", "DEMO_ORBIT", "19999999984", due, due
    )
    upsert_case_outcome_status(
        db_path,
        "us_store_002",
        "MX",
        "DEMO_ORBIT",
        "false_approved",
        method="case_reply_and_effective_approval",
    )
    browser_checks = []
    monkeypatch.setattr(
        case_followup,
        "check_case_detail",
        lambda *_args, **_kwargs: browser_checks.append(True)
        or {
            "result": "pending",
            "case_status": "Open",
            "decision_reason": "still under review",
            "evidence_dir": "",
        },
    )

    results = case_followup.process_due_case_followups(
        settings={
            "paths": {"db_path": db_path},
            "case_followup": {"retry_interval_hours": 1, "max_attempts": 3},
        },
        limit=1,
        update_records=False,
        followup_ids=[followup_id],
    )

    assert results[0]["result"] == "pending"
    assert browser_checks == [True]


def test_connection_circuit_defers_other_due_tasks_for_same_account(monkeypatch, tmp_path):
    db_path = str(tmp_path / "ledger.db")
    init_db(db_path)
    due = (datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
    first_id, _ = enqueue_case_followup(
        db_path, "us_store_002", "BE", "DEMO_HOME", "19999999991", due, due
    )
    second_id, _ = enqueue_case_followup(
        db_path, "us_store_002", "BE", "DEMO_JADE", "19999999990", due, due
    )
    third_id, _ = enqueue_case_followup(
        db_path, "us_store_007", "BE", "DEMO_WILL", "19999999989", due, due
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
            "max_attempts": 6,
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


def test_pending_result_stops_after_configured_followup_limit(monkeypatch, tmp_path):
    db_path = str(tmp_path / "ledger.db")
    init_db(db_path)
    timestamp = (datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
    followup_id, _ = enqueue_case_followup(
        db_path, "us_store_000", "US", "ExampleBrand", "19999999991", timestamp, timestamp
    )
    task = claim_due_case_followups(db_path, limit=1)[0]
    settings = {
        "paths": {"db_path": db_path},
        "case_followup": {
            "retry_interval_hours": 1,
            "error_retry_interval_hours": 1,
            "max_attempts": 1,
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

    assert result["automatic_followup_exhausted"] is True
    assert "1 次上限" in result["decision_reason"]
    row = next(item for item in list_case_followups(db_path) if item["id"] == followup_id)
    assert row["status"] == "manual_review"
    assert row["final_result"] == "pending"
    assert row["completed_at"] is not None
    assert row["error"] == "automatic_followup_attempt_limit_reached"


def test_ai_timeout_retries_once_then_moves_to_manual_review(monkeypatch, tmp_path):
    db_path = str(tmp_path / "ledger.db")
    init_db(db_path)
    timestamp = (datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
    followup_id, _ = enqueue_case_followup(
        db_path, "us_store_000", "US", "ExampleBrand", "19999999995", timestamp, timestamp
    )
    settings = {
        "paths": {"db_path": db_path},
        "case_followup": {
            "retry_interval_hours": 1,
            "error_retry_interval_hours": 1,
            "max_attempts": 12,
            "registration_path": str(tmp_path / "registry.xlsx"),
            "ai_reply_classification": {
                "max_attempts": 2,
                "retry_interval_minutes": 5,
            },
        },
    }
    monkeypatch.setattr(
        case_followup,
        "check_case_detail",
        lambda *args, **kwargs: {
            "result": "answered_unknown",
            "case_status": "Answered",
            "decision_reason": "Codex AI timed out",
            "evidence_dir": "runtime/evidence/example",
            "ai_classification": {"status": "timeout"},
        },
    )

    first_task = claim_due_case_followups(db_path, limit=1)[0]
    first = case_followup.process_claimed_followup(settings, first_task, update_records=False)
    first_row = next(item for item in list_case_followups(db_path) if item["id"] == followup_id)
    assert first_row["status"] == "retry"
    assert first_row["ai_attempt_count"] == 1
    assert first["ai_attempt_count"] == 1
    assert first.get("rescheduled_at")

    second_task = claim_due_case_followups(db_path, due_at="2999-01-01 00:00:00", limit=1)[0]
    second = case_followup.process_claimed_followup(settings, second_task, update_records=False)
    second_row = next(item for item in list_case_followups(db_path) if item["id"] == followup_id)
    assert second_row["status"] == "manual_review"
    assert second_row["ai_attempt_count"] == 2
    assert second["ai_attempt_count"] == 2


def test_ai_forbidden_uses_longer_retry_cooldown(monkeypatch, tmp_path):
    db_path = str(tmp_path / "ledger.db")
    init_db(db_path)
    timestamp = (datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
    followup_id, _ = enqueue_case_followup(
        db_path, "us_store_000", "US", "ExampleBrand", "19999999993", timestamp, timestamp
    )
    task = claim_due_case_followups(db_path, limit=1)[0]
    settings = {
        "paths": {"db_path": db_path},
        "case_followup": {
            "max_attempts": 12,
            "registration_path": str(tmp_path / "registry.xlsx"),
            "ai_reply_classification": {
                "max_attempts": 2,
                "retry_interval_minutes": 5,
                "forbidden_retry_interval_minutes": 30,
            },
        },
    }
    monkeypatch.setattr(
        case_followup,
        "check_case_detail",
        lambda *args, **kwargs: {
            "result": "answered_unknown",
            "case_status": "Answered",
            "decision_reason": "Codex request forbidden",
            "evidence_dir": "runtime/evidence/example",
            "ai_classification": {"status": "forbidden"},
        },
    )

    before = datetime.now() + timedelta(minutes=29)
    case_followup.process_claimed_followup(settings, task, update_records=False)
    row = next(item for item in list_case_followups(db_path) if item["id"] == followup_id)

    assert row["status"] == "retry"
    assert row["error"] == "codex_ai_forbidden"
    assert row["scheduled_at"] > before.strftime("%Y-%m-%d %H:%M:%S")


def test_operator_can_reopen_manual_followup_without_duplicate(tmp_path):
    db_path = str(tmp_path / "ledger.db")
    init_db(db_path)
    timestamp = (datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
    followup_id, _ = enqueue_case_followup(
        db_path, "us_store_000", "US", "ExampleBrand", "19999999994", timestamp, timestamp
    )
    finish_case_followup(
        db_path,
        followup_id,
        "manual_review",
        "answered_unknown",
        "Answered",
        "AI timeout",
        "runtime/evidence/example",
    )

    assert reopen_case_followup_for_retry(db_path, followup_id) is True
    row = next(item for item in list_case_followups(db_path) if item["id"] == followup_id)
    assert row["status"] == "retry"
    assert row["completed_at"] is None


def test_operator_can_reopen_completed_false_approval_without_duplicate(tmp_path):
    db_path = str(tmp_path / "ledger.db")
    init_db(db_path)
    timestamp = (datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
    followup_id, _ = enqueue_case_followup(
        db_path, "us_store_000", "US", "ExampleBrand", "19999999992", timestamp, timestamp
    )
    finish_case_followup(
        db_path,
        followup_id,
        "completed",
        "false_approved",
        "Answered",
        "approval was not effective",
        "runtime/evidence/example",
    )

    assert reopen_case_followup_for_retry(db_path, followup_id) is True
    row = next(item for item in list_case_followups(db_path) if item["id"] == followup_id)
    assert row["status"] == "retry"
    assert row["final_result"] == "false_approved"
    assert row["completed_at"] is None


def test_false_approved_is_registered_and_rescheduled(monkeypatch, tmp_path):
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
        lambda _settings, _task, result, **kwargs: recorded.append(
            (result["result"], kwargs.get("transition_reapplication"))
        ),
    )

    result = case_followup.process_claimed_followup(settings, task, update_records=True)

    assert result["result"] == "false_approved"
    assert recorded == [("false_approved", False)]
    row = next(item for item in list_case_followups(db_path) if item["id"] == followup_id)
    assert row["status"] == "retry"
    assert row["final_result"] == "false_approved"
    assert row["completed_at"] is None
    assert row["scheduled_at"] > datetime.now().strftime("%Y-%m-%d %H:%M:%S")


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
