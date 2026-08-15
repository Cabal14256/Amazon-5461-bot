import re

import pytest

import src.application_type_selection as application_types
import src.flow_submit_5461 as flow
from src.executor.state_loop import PAGE_TYPE_TO_STATE, STATE_ACTIONS
from src.form_filler import KatalFormFiller


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Apply to sell Application to create new ASINs for XDesign "
            "Application to sell XDesign products",
            "create_new_asins_available",
        ),
        ("Apply to sell Application to sell XDesign products", "sell_products_only"),
        ("Apply to sell Application to verify ownership", "unknown_application_options"),
        ("Listing approval Product title Manufacturer", "none"),
        (
            "Application to create new ASINs for XDesign Under review Case ID - 19999999999",
            "existing_application",
        ),
    ],
)
def test_application_type_text_classification(text, expected):
    result = application_types.classify_application_type_text(text)

    assert result["status"] == expected


class _MissingLocator:
    @property
    def first(self):
        return self

    def filter(self, **_kwargs):
        return self

    def count(self):
        return 0

    def is_visible(self):
        return False


class _ClickableLocator(_MissingLocator):
    def count(self):
        return 1

    def is_visible(self):
        return True


class _ApplicationCardPage:
    def __init__(self, clickable=True):
        self.clickable = clickable
        self.requested_pattern = None

    def get_by_text(self, pattern):
        self.requested_pattern = pattern
        return _ClickableLocator() if self.clickable else _MissingLocator()

    def locator(self, _selector):
        return _MissingLocator()

    def evaluate(self, _script, *_args):
        return {"clicked": False, "reason": "not found"}


def _create_card_probe():
    return {
        "status": "create_new_asins_available",
        "panel_found": True,
        "has_create_new_asins": True,
        "has_sell_products": True,
        "has_any_application_option": True,
        "has_existing_application": False,
    }


def test_exact_create_new_asins_card_is_selected_for_target_brand(monkeypatch):
    page = _ApplicationCardPage()
    filler = KatalFormFiller(page, brand_name="XDesign")
    clicks = []
    monkeypatch.setattr(application_types, "probe_application_type_options", lambda _page: _create_card_probe())
    monkeypatch.setattr(
        filler,
        "_human_click_locator",
        lambda _locator, label, timeout: clicks.append((label, timeout)) or True,
    )
    monkeypatch.setattr(filler, "_wait_for_5461_real_fields", lambda **_kwargs: True)

    result = filler.select_create_new_asins_application()

    assert result["status"] == "create_card_selected"
    assert result["clicked"] is True
    assert result["fields_ready"] is True
    assert clicks == [("Application to create new ASINs for XDesign", 10000)]
    assert isinstance(page.requested_pattern, re.Pattern)
    assert page.requested_pattern.fullmatch("Application to create new ASINs for XDesign")
    assert not page.requested_pattern.fullmatch("Application to sell XDesign products")


def test_create_card_for_nonmatching_brand_is_not_clicked(monkeypatch):
    page = _ApplicationCardPage(clickable=False)
    filler = KatalFormFiller(page, brand_name="TargetBrand")
    monkeypatch.setattr(application_types, "probe_application_type_options", lambda _page: _create_card_probe())

    result = filler.select_create_new_asins_application()

    assert result["status"] == "create_card_click_failed"
    assert result["clicked"] is False


def test_sell_products_only_stops_before_any_apply_click(monkeypatch):
    filler = KatalFormFiller(object(), brand_name="XDesign")
    monkeypatch.setattr(filler, "_probe_5461_panel", lambda: {"hasRealFields": False})
    monkeypatch.setattr(
        filler,
        "select_create_new_asins_application",
        lambda: {"status": "sell_products_only", "panel_found": True, "clicked": False},
    )
    monkeypatch.setattr(
        filler,
        "_click_apply_to_sell_human",
        lambda: (_ for _ in ()).throw(AssertionError("sell-products-only must not be clicked")),
    )

    assert filler.click_apply_to_sell() is False


def test_direct_form_skips_card_detection_and_apply_click(monkeypatch):
    filler = KatalFormFiller(object(), brand_name="XDesign")
    monkeypatch.setattr(filler, "_probe_5461_panel", lambda: {"hasRealFields": True})
    monkeypatch.setattr(
        filler,
        "select_create_new_asins_application",
        lambda: (_ for _ in ()).throw(AssertionError("direct form needs no card selection")),
    )

    assert filler.click_apply_to_sell() is True


def test_chooser_is_selected_when_legacy_panel_probe_misses_it(monkeypatch):
    filler = KatalFormFiller(object(), brand_name="MP-MALL")
    selections = [
        {"status": "none", "panel_found": False, "clicked": False},
        {
            "status": "create_card_selected",
            "panel_found": True,
            "clicked": True,
            "fields_ready": True,
        },
    ]
    monkeypatch.setattr(filler, "_probe_5461_panel", lambda: {"found": False, "hasRealFields": False})
    monkeypatch.setattr(filler, "select_create_new_asins_application", lambda: selections.pop(0))
    monkeypatch.setattr(
        filler,
        "_click_apply_to_sell_human",
        lambda: {"clicked": True, "location": "kat-link-view-playwright"},
    )
    monkeypatch.setattr("src.form_filler.time.sleep", lambda _seconds: None)

    assert filler.click_apply_to_sell() is True
    assert selections == []


class _PanelStatePage:
    url = "https://sellercentral.amazon.com/interactive/listing/workflow/create/product_identity"

    def __init__(self, panel_text):
        self.panel_text = panel_text

    def evaluate(self, script):
        if "return getAllText(document.body)" in script:
            return self.panel_text
        if "seller-qualification-auto-approval-modal" in script:
            return {"found": False}
        if "let panel = document.querySelector" in script:
            return {
                "found": True,
                "visible": True,
                "display": True,
                "width": 400,
                "height": 900,
                "text": self.panel_text,
            }
        raise AssertionError("unexpected evaluate call")

    def locator(self, _selector):
        return _MissingLocator()


def test_page_state_recognises_application_type_selection(monkeypatch):
    monkeypatch.setattr(flow, "has_5461_form_fields", lambda _page: False)
    page = _PanelStatePage(
        "Apply to sell Application to create new ASINs for XDesign "
        "Application to sell XDesign products"
    )

    state = flow.check_page_state(page)

    assert state["page_type"] == "APPLICATION_TYPE_SELECTION"
    assert state["needs_auth"] is True


def test_page_state_marks_sell_only_as_human_review(monkeypatch):
    monkeypatch.setattr(flow, "has_5461_form_fields", lambda _page: False)
    state = flow.check_page_state(
        _PanelStatePage("Apply to sell Application to sell XDesign products")
    )

    assert state["page_type"] == "APPLICATION_SELL_ONLY"
    assert state["requires_human_review"] is True


def test_state_loop_routes_application_type_states_conservatively():
    assert PAGE_TYPE_TO_STATE["APPLICATION_TYPE_SELECTION"] == "application_type_selection"
    assert STATE_ACTIONS["application_type_selection"][:2] == (
        "legacy",
        "select_create_new_asins",
    )
    assert PAGE_TYPE_TO_STATE["APPLICATION_SELL_ONLY"] == "application_sell_only"
    assert STATE_ACTIONS["application_sell_only"][:2] == (
        "stop",
        "human_review_sell_products_only",
    )
    assert PAGE_TYPE_TO_STATE["APPLICATION_TYPE_UNKNOWN"] == "unknown"
