from src.flow_submit_5461 import (
    capture_failure_page_evidence,
    check_page_state,
    has_application_required_entry_text,
)


class _MissingLocator:
    @property
    def first(self):
        return self

    def count(self):
        return 0


class _ProductIdentityPage:
    url = (
        "https://sellercentral.amazon.com/interactive/listing/workflow/"
        "create/product_identity?productType=SCREEN_PROTECTOR#approval"
    )

    def __init__(self, text):
        self.text = text

    def evaluate(self, script):
        if "function getAllText" in script:
            return self.text
        if "seller-qualification-auto-approval-modal" in script:
            return {"found": False}
        if "QualificationWidget" in script:
            return {"found": False}
        if "const selectors = [" in script:
            return False
        if "const deepRoots = [document]" in script:
            return {
                "approval_control_count": 1,
                "approval_control_texts": ["View"],
                "visible_panel_count": 0,
                "form_field_count": 0,
                "restriction_entry_count": 1,
            }
        raise AssertionError(f"unexpected evaluate script: {script[:80]}")

    def locator(self, _selector):
        return _MissingLocator()

    def inner_text(self, selector):
        assert selector == "body"
        return self.text


def test_application_required_entry_accepts_singular_and_plural_counts():
    assert has_application_required_entry_text("1 restriction 1 application required View")
    assert has_application_required_entry_text("2 restrictions 2 applications required View")
    assert has_application_required_entry_text("12applications required")


def test_restriction_view_fallback_accepts_any_count():
    assert has_application_required_entry_text("3 restrictions View")


def test_unrelated_product_identity_text_is_not_approval_entry():
    assert not has_application_required_entry_text("Product Identity Ready Continue to Description")
    assert not has_application_required_entry_text("Restrictions may apply Learn more")


def test_product_identity_state_recognizes_two_applications_required():
    page = _ProductIdentityPage("Approval Required 2 restrictions View 2 applications required")

    state = check_page_state(page)

    assert state["page_type"] == "NEEDS_APPROVAL_NEW_UI"
    assert state["needs_auth"] is True


def test_failure_page_evidence_captures_state_text_and_control_probes():
    page = _ProductIdentityPage("Approval Required 2 restrictions View 2 applications required")

    evidence = capture_failure_page_evidence(page)

    assert evidence["url"] == (
        "https://sellercentral.amazon.com/interactive/listing/workflow/create/product_identity"
    )
    assert evidence["recognized_state"]["page_type"] == "NEEDS_APPROVAL_NEW_UI"
    assert evidence["visible_text"].startswith("Approval Required")
    assert evidence["selector_probes"]["approval_control_texts"] == ["View"]
    assert evidence["selector_probes"]["restriction_entry_count"] == 1
    assert "capture_errors" not in evidence
