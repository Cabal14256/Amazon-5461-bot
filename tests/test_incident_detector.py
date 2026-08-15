"""Stage-5 failure detector and signature tests."""

from src.incidents import (
    REPAIR_CLASSES,
    classify_failure,
    compute_signature,
    initial_confidence,
    should_record,
)
from src.incidents.signature import normalize_url_pattern

CLASSIFICATION_CASES = [
    # Human-handling classes.
    ({"console_text": "Failed to load resource: the server responded with a status of 429"}, "rate_limit"),
    ({"error_text": "410001 Too Many Requests from cloudfront"}, "rate_limit"),
    ({"error_text": "Please solve the CAPTCHA to continue"}, "captcha"),
    ({"error_text": "Enter the OTP from your authenticator app"}, "two_fa"),
    ({"error_text": "two-factor verification required"}, "two_fa"),
    ({"error_text": "登录过期，请重新登录"}, "login_expired"),
    ({"error_text": "login expired, authenticate again"}, "login_expired"),
    ({"error_text": "账号风险：触发风控"}, "account_risk"),
    ({"error_text": "account risk control checkpoint"}, "account_risk"),
    ({"error_text": "brand hard block for this account"}, "brand_block"),
    ({"error_text": "missing_adspower_profile for account"}, "config_missing"),
    ({"error_text": "缺少品牌包配置"}, "config_missing"),
    ({"error_text": "unknown_account us_store_000"}, "config_missing"),
    ({"error_text": "Amazon SERVER_ERROR, retry later"}, "amazon_platform_error"),
    # Fix-candidate classes.
    ({"reason": "stuck_at_form_ready"}, "flow_loop_exhausted"),
    ({"reason": "max_steps_exceeded"}, "flow_loop_exhausted"),
    ({"error_text": "页面半加载，恢复策略耗尽"}, "flow_loop_exhausted"),
    ({"error_text": "selector_missing: kat-input#question-title"}, "selector_missing"),
    ({"error_text": "选择器缺失：提交按钮"}, "selector_missing"),
    ({"page_state": "state_unknown"}, "state_unknown"),
    ({"error_text": "UNKNOWN 页面，无法分类"}, "state_unknown"),
    ({"error_text": "dom_contract violated: landmark 缺失"}, "dom_contract_changed"),
    ({"error_text": "navigation_changed after submit"}, "navigation_changed"),
    ({"error_text": "result_contract mismatch: 解析器不兼容"}, "result_contract_changed"),
    ({"error_text": "semantic_control submit_button not found"}, "semantic_control_missing"),
    # Fallback.
    ({"error_text": "brand approved already, nothing to do"}, "business_uncertain"),
    ({}, "business_uncertain"),
]


def test_all_classification_rules():
    for kwargs, expected in CLASSIFICATION_CASES:
        got = classify_failure(**kwargs)
        assert got == expected, f"{kwargs}: expected {expected}, got {got}"


def test_human_class_beats_repair_candidate():
    # 429 storm text that also mentions a missing selector → rate_limit.
    got = classify_failure(
        console_text="status of 429; selector_missing kat-input#question-title"
    )
    assert got == "rate_limit"


def test_telemetry_429_does_not_hide_semantic_control_failure():
    got = classify_failure(
        error_text="无法进入5461表单 | Dashboard found Draft",
        console_text="Failed to load resource: the server responded with a status of 429",
        network_errors=[
            {
                "type": "response",
                "status": 429,
                "url": "https://sellercentral.amazon.com/quicklist/katalLogs",
            }
        ],
    )
    assert got == "semantic_control_missing"


def test_non_telemetry_429_still_beats_semantic_control_failure():
    got = classify_failure(
        error_text="无法进入5461表单 | Dashboard found Draft",
        console_text="Failed to load resource: the server responded with a status of 429",
        network_errors=[
            {
                "type": "response",
                "status": 429,
                "url": "https://sellercentral.amazon.com/abis/ajax/getRestrictionEndpoint",
            }
        ],
    )
    assert got == "rate_limit"


def test_generic_missing_5461_entry_is_repair_candidate():
    assert classify_failure(error_text="无法进入5461表单") == "semantic_control_missing"


def test_captcha_beats_selector_missing():
    got = classify_failure(error_text="captcha shown; selector_missing panel")
    assert got == "captcha"


def test_initial_confidence():
    for classification in REPAIR_CLASSES:
        assert initial_confidence(classification) == 0.50
    assert initial_confidence("captcha") == 0.10
    assert initial_confidence("business_uncertain") == 0.10


def test_should_record():
    assert should_record("business_uncertain", dry_run=True) is False
    assert should_record("business_uncertain", dry_run=False) is True
    assert should_record("selector_missing", dry_run=True) is True
    assert should_record("rate_limit", dry_run=False) is True


def test_signature_stable_for_same_cause():
    sig1 = compute_signature(flow_type="5461", state_loop_reason="stuck_at_form_ready")
    sig2 = compute_signature(flow_type="5461", state_loop_reason="stuck_at_form_ready")
    assert sig1 == sig2
    assert len(sig1) == 16
    int(sig1, 16)  # hex


def test_signature_differs_for_different_cause():
    sig1 = compute_signature(flow_type="5461", state_loop_reason="stuck_at_form_ready")
    sig2 = compute_signature(flow_type="5461", state_loop_reason="stuck_at_gtin_exemption")
    assert sig1 != sig2


def test_signature_strips_account_brand_case_id():
    clean = compute_signature(flow_type="5461", error_class="submit timeout")
    tainted = compute_signature(
        flow_type="5461",
        error_class="us_store_003 DEMO-SHIELD 19999999999 submit timeout",
    )
    assert clean == tainted


def test_signature_strips_email_and_long_numbers():
    clean = compute_signature(flow_type="5461", error_class="failed after redirect")
    tainted = compute_signature(
        flow_type="5461",
        error_class="failed after redirect seller@example.com 20260811083000123",
    )
    assert clean == tainted


def test_normalize_url_pattern_strips_account_segments():
    assert normalize_url_pattern(
        "https://sellercentral.amazon.com/applications/us_store_003/detail?mkid=abc123&x=1"
    ) == "sellercentral.amazon.com/applications/*/detail"
    assert normalize_url_pattern("not a url") == ""
    assert normalize_url_pattern("") == ""
