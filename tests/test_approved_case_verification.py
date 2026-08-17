import json

import pytest

import src.approved_case_verification as approved_verification
from src.approved_case_verification import (
    _connect_approval_message_matches,
    _connect_brand_config_for_brand,
    _manage_brands_direct_url,
    choose_connect_brand_candidate,
    combine_approved_verification,
    get_approved_verification_config,
    verify_manage_brand,
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


@pytest.mark.parametrize(
    "manage_result",
    ["found", "not_found", "connect_approved", "connect_failed", "connect_unknown", "unknown"],
)
def test_add_product_only_brand_pass_is_decisive(manage_result):
    result = combine_approved_verification(
        _check(manage_result),
        _check("pass"),
        brand_name="moshieldwish",
        add_product_only_brands=["MoShieldwish"],
    )
    assert result["result"] == "approved"
    assert result["is_success"] is True
    assert "品牌例外规则" in result["reason"]


def test_add_product_only_brand_without_add_product_pass_keeps_existing_logic():
    result = combine_approved_verification(
        _check("connect_unknown"),
        _check("fail"),
        brand_name="MoShieldwish",
        add_product_only_brands=["MoShieldwish"],
    )
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
    assert config["add_product_only_brands"] == []


def test_add_product_only_brand_configuration_is_trimmed():
    config = get_approved_verification_config(
        {
            "case_followup": {
                "approved_verification": {
                    "add_product_only_brands": [" MoShieldwish ", ""],
                }
            }
        }
    )
    assert config["add_product_only_brands"] == ["MoShieldwish"]


def test_brand_manifest_description_overrides_generic_connect_keywords(tmp_path):
    brand_dir = tmp_path / "DEMO_WILL"
    brand_dir.mkdir()
    description = "Brand offering toys, sporting goods, and household items."
    (brand_dir / "manifest.json").write_text(
        json.dumps({"brand_selection_keywords": [description]}),
        encoding="utf-8",
    )

    config = _connect_brand_config_for_brand(
        {"category_keywords": ["screen protector"]}, "DEMO_WILL", tmp_path
    )

    assert config["category_keywords"] == [description]


@pytest.mark.parametrize(
    ("home_url", "expected"),
    [
        (
            "https://sellercentral.amazon.com/home",
            "https://sellercentral.amazon.com/manage-your-brands?ref_=xx_myb_favb_xx",
        ),
        (
            "https://sellercentral.amazon.co.uk/home",
            "https://sellercentral.amazon.co.uk/manage-your-brands?ref_=xx_myb_favb_xx",
        ),
    ],
)
def test_manage_brands_direct_url_uses_the_seller_central_region(home_url, expected):
    assert _manage_brands_direct_url(home_url) == expected


def test_manage_brand_navigation_falls_back_to_direct_url(monkeypatch, tmp_path):
    class FakePage:
        url = "https://sellercentral.amazon.co.uk/home"

    page = FakePage()
    visited: list[str] = []

    def fake_goto(target_page, url, _timeout_ms):
        visited.append(url)
        target_page.url = url

    monkeypatch.setattr(approved_verification, "_goto", fake_goto)
    monkeypatch.setattr(approved_verification, "_page_text", lambda _page: "Manage Your Brands")
    monkeypatch.setattr(approved_verification, "_auth_block_reason", lambda _page, _text="": None)
    monkeypatch.setattr(
        approved_verification,
        "_click_manage_brands_link",
        lambda _page, _labels, _timeout_ms: {"opened": False, "method": "not_found"},
    )
    monkeypatch.setattr(approved_verification, "_save_evidence", lambda *_args: [])
    monkeypatch.setattr(approved_verification, "_brand_visible_on_manage_page", lambda *_args: True)

    result = verify_manage_brand(
        page,
        "TEST-BRAND",
        "https://sellercentral.amazon.co.uk/home",
        {
            "menu_labels": ["Manage Your Brands"],
            "page_markers": ["Manage Your Brands"],
            "settle_seconds": 0,
        },
        tmp_path,
        page_timeout_ms=45_000,
    )

    assert result["result"] == "found"
    assert result["navigation"]["method"] == "direct_url_fallback"
    assert visited[-1] == "https://sellercentral.amazon.co.uk/manage-your-brands?ref_=xx_myb_favb_xx"


def test_connect_candidate_requires_exact_brand_and_screen_protector_context():
    candidates = [
        {
            "index": 0,
            "name": "DEMO_WILL",
            "description": "Shoes and apparel",
            "text": "DEMO_WILL\nShoes and apparel",
        },
        {
            "index": 1,
            "name": "DEMO_WILL",
            "description": "Phone screen protectors and protective accessories",
            "text": "DEMO_WILL\nPhone screen protectors and protective accessories",
        },
        {
            "index": 2,
            "name": "DEMO_WILL PRO",
            "description": "Screen protectors",
            "text": "DEMO_WILL PRO\nScreen protectors",
        },
    ]
    result = choose_connect_brand_candidate(
        candidates, "DEMO_WILL", ["screen protector", "screen protectors"]
    )
    assert result["status"] == "matched"
    assert result["candidate"]["index"] == 1


def test_connect_candidate_refuses_multiple_relevant_exact_matches():
    candidates = [
        {"index": 0, "name": "DEMO_JADE", "text": "DEMO_JADE\nScreen protectors"},
        {"index": 1, "name": "DEMO_JADE", "text": "DEMO_JADE\nMobile screen protection"},
    ]
    result = choose_connect_brand_candidate(
        candidates, "DEMO_JADE", ["screen protectors", "screen protection"]
    )
    assert result["status"] == "ambiguous"


def test_connect_approval_message_must_name_the_expected_brand():
    text = (
        "You are approved to list DEMO_VISTA products. "
        "You can close this panel and continue listing."
    )
    assert _connect_approval_message_matches(text, "DEMO_VISTA")
    assert not _connect_approval_message_matches(text, "DEMO_WILL")
