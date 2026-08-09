import pytest

from src.approved_case_verification import (
    _connect_approval_message_matches,
    choose_connect_brand_candidate,
    combine_approved_verification,
    get_approved_verification_config,
)


def _check(result: str, reason: str = "test") -> dict:
    return {"result": result, "reason": reason}


def test_effective_approval_requires_both_checks_to_pass():
    result = combine_approved_verification(_check("found"), _check("pass"))
    assert result["result"] == "approved"
    assert result["is_success"] is True


def test_missing_manage_brand_waits_for_connect_brand_probe():
    result = combine_approved_verification(_check("not_found"), _check("pass"))
    assert result["result"] == "verification_pending"
    assert result["is_success"] is None


@pytest.mark.parametrize("add_result", ["pass", "fail", "unknown", "blocked"])
def test_connect_brand_approval_is_decisive(add_result):
    result = combine_approved_verification(
        _check("connect_approved"), _check(add_result)
    )
    assert result["result"] == "approved"
    assert result["is_success"] is True


@pytest.mark.parametrize("add_result", ["pass", "fail", "unknown", "blocked"])
def test_connect_brand_explicit_failure_is_decisive(add_result):
    result = combine_approved_verification(
        _check("connect_failed"), _check(add_result)
    )
    assert result["result"] == "false_approved"
    assert result["is_success"] is False


def test_add_product_restriction_is_false_approved():
    result = combine_approved_verification(_check("found"), _check("fail"))
    assert result["result"] == "false_approved"
    assert result["is_success"] is False


def test_technical_unknown_is_not_false_approved():
    result = combine_approved_verification(_check("unknown"), _check("pass"))
    assert result["result"] == "verification_pending"
    assert result["is_success"] is None


def test_stale_manage_brand_page_does_not_let_add_product_decide():
    result = combine_approved_verification(_check("not_found"), _check("blocked"))
    assert result["result"] == "verification_pending"


def test_missing_manage_brand_with_add_product_failure_still_needs_connect_probe():
    result = combine_approved_verification(_check("not_found"), _check("fail"))
    assert result["result"] == "verification_pending"


def test_connect_brand_technical_unknown_is_retryable_even_if_add_product_fails():
    result = combine_approved_verification(_check("connect_unknown"), _check("fail"))
    assert result["result"] == "verification_pending"


def test_login_or_captcha_block_stays_blocked_without_business_failure():
    result = combine_approved_verification(_check("found"), _check("blocked"))
    assert result["result"] == "blocked"
    assert result["is_success"] is None


def test_nested_configuration_keeps_safe_defaults():
    config = get_approved_verification_config(
        {
            "case_followup": {
                "approved_verification": {
                    "manage_brand": {"settle_seconds": 0},
                }
            }
        }
    )
    assert config["enabled"] is True
    assert config["manage_brand"]["settle_seconds"] == 0
    assert "Manage Your Brands" in config["manage_brand"]["menu_labels"]
    assert config["manage_brand"]["connect_brand"]["enabled"] is True
    assert "screen protector" in config["manage_brand"]["connect_brand"]["category_keywords"]
    assert config["add_product"]["allow_new_ui_submit_as_continue"] is True


def test_connect_candidate_requires_exact_brand_and_screen_protector_context():
    candidates = [
        {
            "index": 0,
            "name": "WILLONE",
            "description": "Shoes and apparel",
            "text": "WILLONE\nShoes and apparel",
        },
        {
            "index": 1,
            "name": "WILLONE",
            "description": "Phone screen protectors and protective accessories",
            "text": "WILLONE\nPhone screen protectors and protective accessories",
        },
        {
            "index": 2,
            "name": "WILLONE PRO",
            "description": "Screen protectors",
            "text": "WILLONE PRO\nScreen protectors",
        },
    ]
    result = choose_connect_brand_candidate(
        candidates, "WILLONE", ["screen protector", "screen protectors"]
    )
    assert result["status"] == "matched"
    assert result["candidate"]["index"] == 1


def test_connect_candidate_refuses_multiple_relevant_exact_matches():
    candidates = [
        {"index": 0, "name": "JZG", "text": "JZG\nScreen protectors"},
        {"index": 1, "name": "JZG", "text": "JZG\nMobile screen protection"},
    ]
    result = choose_connect_brand_candidate(
        candidates, "JZG", ["screen protectors", "screen protection"]
    )
    assert result["status"] == "ambiguous"


def test_connect_approval_message_must_name_the_expected_brand():
    text = (
        "You are approved to list VASG products. "
        "You can close this panel and continue listing."
    )
    assert _connect_approval_message_matches(text, "VASG")
    assert not _connect_approval_message_matches(text, "WILLONE")
