"""Stage-5 evidence bundle tests."""

import json

from src.incidents import build_evidence_bundle
from src.incidents.evidence_bundle import UNTRUSTED_BEGIN, UNTRUSTED_END

FAKE_EMAIL = "fake-seller@example.com"
FAKE_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
FAKE_BEARER = "abcdefghijklmnopqrstuvwxyz1234567890"


def _page_evidence():
    return {
        "url": "https://sellercentral.amazon.com/applications/detail",
        "title": "Sell Your Application",
        "visible_text": (
            f"Contact {FAKE_EMAIL} for help. "
            f"debug aws key {FAKE_AWS_KEY} bearer {FAKE_BEARER} "
            "form ready, please fill manufacturer"
        ),
        "steps": [{"step": 1, "state": "form_ready"}],
    }


def _run_context():
    return {
        "account_id": "us_store_999",
        "site": "US",
        "brand_name": "TESTBRAND",
        "detector_type": "batch",
        "email": FAKE_EMAIL,
        "username": "fake-user",
        "password": "fake-password-123",
    }


def test_full_bundle_generates_eight_artifacts(tmp_path):
    bundle = build_evidence_bundle(
        42,
        tmp_path,
        page_evidence=_page_evidence(),
        run_context=_run_context(),
        screenshot_path="runtime/evidence/private/shot-42.png",
        selectors=["kat-input#question-title", "kat-button#submit"],
        dom_contract={"landmarks": ["form", "submit"]},
        previous_success={"run_id": "batch-1"},
    )
    assert bundle == tmp_path / "incidents" / "42"
    expected = {
        "manifest.json",
        "page-summary.redacted.json",
        "visible-text.redacted.txt",
        "relevant-selectors.json",
        "run-context.redacted.json",
        "screenshot-withheld.json",
        "dom-contract.json",
        "previous-success.json",
    }
    assert {p.name for p in bundle.iterdir()} == expected

    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["incident_id"] == 42
    assert set(manifest["files"]) == expected - {"manifest.json"}
    assert all(entry["absent"] is False for entry in manifest["files"].values())


def test_optional_artifacts_absent(tmp_path):
    bundle = build_evidence_bundle(
        7,
        tmp_path,
        page_evidence=_page_evidence(),
        run_context=_run_context(),
    )
    assert not (bundle / "dom-contract.json").exists()
    assert not (bundle / "previous-success.json").exists()
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["files"]["dom-contract.json"]["absent"] is True
    assert manifest["files"]["previous-success.json"]["absent"] is True
    assert manifest["files"]["screenshot-withheld.json"]["absent"] is True


def test_screenshot_withheld_marker(tmp_path):
    bundle = build_evidence_bundle(
        9,
        tmp_path,
        page_evidence=_page_evidence(),
        run_context=_run_context(),
        screenshot_path="runtime/evidence/private/shot-9.png",
    )
    withheld = json.loads((bundle / "screenshot-withheld.json").read_text(encoding="utf-8"))
    assert withheld == {
        "withheld": True,
        "reason": "stage-5 default",
        "original_private_path": "runtime/evidence/private/shot-9.png",
    }
    # The raw screenshot is never copied into the bundle.
    assert not any(p.suffix.lower() == ".png" for p in bundle.iterdir())


def test_visible_text_wrapped_as_untrusted_and_redacted(tmp_path):
    bundle = build_evidence_bundle(
        11, tmp_path, page_evidence=_page_evidence(), run_context=_run_context()
    )
    text = (bundle / "visible-text.redacted.txt").read_text(encoding="utf-8")
    assert text.startswith(UNTRUSTED_BEGIN)
    assert text.rstrip().endswith(UNTRUSTED_END)
    assert FAKE_EMAIL not in text
    assert len(text.encode("utf-8")) <= 8192 + 200  # cap + marker overhead


def test_run_context_drops_credential_keys(tmp_path):
    bundle = build_evidence_bundle(
        13, tmp_path, page_evidence=_page_evidence(), run_context=_run_context()
    )
    context = json.loads((bundle / "run-context.redacted.json").read_text(encoding="utf-8"))
    assert "email" not in context
    assert "username" not in context
    assert "password" not in context
    assert context["account_id"] == "us_store_999"


def test_no_secret_leaks_anywhere_in_bundle(tmp_path):
    bundle = build_evidence_bundle(
        17,
        tmp_path,
        page_evidence=_page_evidence(),
        run_context=_run_context(),
        screenshot_path="runtime/evidence/private/shot-17.png",
        selectors=["kat-input#question-title"],
        dom_contract={"landmarks": ["form"]},
        previous_success={"note": f"contact {FAKE_EMAIL}"},
    )
    for path in bundle.iterdir():
        content = path.read_text(encoding="utf-8")
        assert FAKE_EMAIL not in content, f"{path.name} leaked the fixture email"
        assert FAKE_AWS_KEY not in content, f"{path.name} leaked the fixture AWS key"
        assert FAKE_BEARER not in content, f"{path.name} leaked the fixture bearer token"
        assert "fake-password-123" not in content


def test_minimal_bundle_without_page_evidence(tmp_path):
    bundle = build_evidence_bundle(23, tmp_path)
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["files"]["page-summary.redacted.json"]["absent"] is True
    assert manifest["files"]["visible-text.redacted.txt"]["absent"] is True
    assert (bundle / "manifest.json").exists()
