import src.flow_submit_5461 as flow
from src.flow_submit_5461 import handle_brand_selection


class _CaptureFirstEvaluate:
    def __init__(self):
        self.script = ""

    def evaluate(self, script):
        self.script = script
        raise RuntimeError("stop after capturing selector script")


def test_brand_selection_entry_scans_kat_link():
    page = _CaptureFirstEvaluate()

    assert handle_brand_selection(page, "JZG") is False
    assert "kat-link, button" in page.script
    assert "select brand" in page.script.lower()


class _MissingLocator:
    def count(self):
        return 0


class _PageWithStaleBrandWarning:
    url = "https://sellercentral.amazon.com/interactive/listing/workflow/create/product_identity"

    def evaluate(self, script):
        if "return getAllText(document.body)" in script:
            return "Select Brand couldn't associate Brand Registry Listing approval Product title"
        if "seller-qualification-auto-approval-modal" in script:
            return {"found": False}
        if "kat-panel-wrapper" in script:
            return {"found": False}
        raise AssertionError("unexpected evaluate call")

    def locator(self, _selector):
        return _MissingLocator()


def test_real_5461_fields_win_over_stale_brand_warning(monkeypatch):
    monkeypatch.setattr(flow, "has_5461_form_fields", lambda _page: True)

    state = flow.check_page_state(_PageWithStaleBrandWarning())

    assert state["page_type"] == "5461_FORM_OPEN"
    assert state["has_5461_form"] is True


def test_connect_brand_skeleton_waits_for_real_fields(monkeypatch):
    waits = []
    monkeypatch.setattr(
        flow,
        "wait_for_5461_form_fields",
        lambda _page, timeout_sec, interval_sec: waits.append((timeout_sec, interval_sec)) or True,
    )
    monkeypatch.setattr(
        flow,
        "recover_half_loaded_5461_panel",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("recovery should not be needed")),
    )
    state = {"page_type": "BRAND_SELECTION", "has_5461_form": False}

    resolved, ready = flow.resolve_connect_brand_loading_state(object(), object(), "V-PORYADKU", state)

    assert ready is True
    assert waits == [(24, 2)]
    assert resolved == {"page_type": "5461_FORM_OPEN", "has_5461_form": True}


class _CountLocator:
    def __init__(self, count):
        self._count = count

    def count(self):
        return self._count


class _PageWithGtinEmail:
    def locator(self, selector):
        return _CountLocator(1 if selector == 'kat-input#contact_info_email_input' else 0)


def test_gtin_variant_fills_required_contact_email(monkeypatch):
    calls = []
    monkeypatch.setattr(flow, "clean_email", lambda value: value)
    monkeypatch.setattr(flow, "fill_katal_input", lambda _page, selector, value: calls.append((selector, value)) or True)

    assert flow.fill_optional_contact_email(_PageWithGtinEmail(), "seller@example.com") is True
    assert calls == [('kat-input#contact_info_email_input', 'seller@example.com')]
