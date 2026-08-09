import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.flow_submit_5461 import extract_case_id
from src.case_dashboard_checker import analyze_dashboard_text, _navigation_landed


class FakePage:
    def __init__(self, text):
        self.text = text

    def evaluate(self, script):
        return self.text

    def inner_text(self, selector):
        return self.text


def test_extract_case_id_allows_uk_case_starting_with_1_near_case_label():
    page = FakePage(
        "Application to create new ASINs for HOMEMO\n"
        "Under review - Decision expected by 2 Jun 2026\n"
        "Case ID - 19999999999\n"
    )
    assert extract_case_id(page) == "19999999999"


def test_extract_case_id_still_ignores_1_prefixed_phone_without_case_label():
    page = FakePage("Contact phone 19999999999 for support. No application identifier is shown here.")
    assert extract_case_id(page) is None


def test_dashboard_text_extracts_under_review_case_id_starting_with_1():
    text = "Application to create new ASINs for HOMEMO Under review Case ID - 19999999999"
    result = analyze_dashboard_text(text, "HOMEMO")
    assert result["status"] == "under_review"
    assert result["case_id"] == "19999999999"


def test_navigation_landed_rejects_add_product_when_target_dashboard_or_apps():
    add_product = "https://sellercentral.amazon.co.uk/abis/listing/create/product_identity?x=1"
    assert not _navigation_landed(add_product, "https://sellercentral.amazon.co.uk/hz/myqdashboard/ref=xx")
    assert not _navigation_landed(add_product, "https://sellercentral.amazon.co.uk/abis/approval/search")
    assert _navigation_landed("https://sellercentral.amazon.co.uk/hz/myqdashboard/ref=xx", "https://sellercentral.amazon.co.uk/hz/myqdashboard/ref=xx")
    assert _navigation_landed("https://sellercentral.amazon.co.uk/abis/approval/search", "https://sellercentral.amazon.co.uk/abis/approval/search")


def test_dashboard_parses_belgium_catalogue_authorisation_rows_independently():
    text = """
Application name
Case ID
Application type
Changed
Status
JavoYion
Catalogue Authorisation
6 Aug 2026
Draft
WILLONE
Catalogue Authorisation
6 Aug 2026
Under review
Expected decision date: 9 Aug 2026
uShield
Catalogue Authorisation
6 Aug 2026
Draft
"""

    assert analyze_dashboard_text(text, "JavoYion")["status"] == "draft"
    assert analyze_dashboard_text(text, "WILLONE")["status"] == "under_review"
    assert analyze_dashboard_text(text, "uShield")["status"] == "draft"
