"""Stage-7 diff scanning rule table (src/repair/diff_scan.py, blueprint §18.1).

Every must-reject rule has at least one positive case; clean selector
patches must not false-positive.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.repair.diff_scan import (  # noqa: E402
    backend_risk_level,
    classify_file_risk,
    parse_unified_diff,
    scan_diff,
    stricter_risk,
)

ALLOWED = ["config/selectors/", "src/executor/", "src/capture/", "tests/"]


def make_diff(path, added=(), removed=()) -> str:
    lines = [
        f"diff --git a/{path} b/{path}",
        f"--- a/{path}",
        f"+++ b/{path}",
        "@@ -1,2 +1,2 @@",
    ]
    lines += [f"-{line}" for line in removed]
    lines += [f"+{line}" for line in added]
    return "\n".join(lines) + "\n"


def rule_ids(report):
    return {violation.rule_id for violation in report.violations}


# ---------------------------------------------------------------------------
# Clean diffs must pass
# ---------------------------------------------------------------------------

def test_clean_selector_patch_passes():
    diff = make_diff(
        "config/selectors/us.yaml",
        added=["submit_button: '#submit-button-v2'"],
        removed=["submit_button: '#submit-button-v1'"],
    ) + make_diff(
        "tests/test_selector_regression.py",
        added=["def test_submit_selector():", "    assert '#submit-button-v2'"],
    )
    report = scan_diff(diff, allowed_paths=ALLOWED)
    assert report.passed, report.violations
    assert report.files == ["config/selectors/us.yaml", "tests/test_selector_regression.py"]


def test_clean_src_patch_passes():
    diff = make_diff(
        "src/executor/probe.py",
        added=["    return page.locator('[data-cy=submit]').count()"],
        removed=["    return page.locator('#old').count()"],
    )
    assert scan_diff(diff, allowed_paths=ALLOWED).passed


def test_parse_unified_diff_handles_new_file_and_dev_null():
    diff = (
        "diff --git a/tests/test_new.py b/tests/test_new.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/tests/test_new.py\n"
        "@@ -0,0 +1 @@\n"
        "+def test_x():\n"
    )
    files = parse_unified_diff(diff)
    assert [f.path for f in files] == ["tests/test_new.py"]
    assert files[0].added == ["def test_x():"]


# ---------------------------------------------------------------------------
# Allowed scope
# ---------------------------------------------------------------------------

def test_file_outside_allowed_paths_rejected():
    diff = make_diff("src/web/app.py", added=["# touched"])
    report = scan_diff(diff, allowed_paths=ALLOWED)
    assert not report.passed
    assert "path_outside_allowed" in rule_ids(report)


def test_allowed_path_prefix_must_end_on_component_boundary():
    diff = make_diff("tests_evil/test_bypass.py", added=["def test_bypass():", "    assert True"])
    report = scan_diff(diff, allowed_paths=ALLOWED)
    assert not report.passed
    assert "path_outside_allowed" in rule_ids(report)


# ---------------------------------------------------------------------------
# §18.1 rule 1-2: private paths and sensitive content
# ---------------------------------------------------------------------------

def test_private_path_file_rejected():
    diff = make_diff("runtime/private/accounts.json", added=["{}"])
    report = scan_diff(diff, allowed_paths=ALLOWED + ["runtime/"])
    assert "private_path_reference" in rule_ids(report)


def test_private_path_reference_in_added_line_rejected():
    diff = make_diff(
        "src/capture/probe.py", added=['PRIVATE = "runtime/private/accounts.json"']
    )
    assert "private_path_reference" in rule_ids(scan_diff(diff, allowed_paths=ALLOWED))


def test_sensitive_email_in_added_line_rejected():
    diff = make_diff(
        "config/selectors/us.yaml", added=["contact: seller-ops@example.com"]
    )
    assert "sensitive_content" in rule_ids(scan_diff(diff, allowed_paths=ALLOWED))


def test_sensitive_long_digit_string_rejected():
    diff = make_diff(
        "src/executor/probe.py", added=['CASE_ID = "1234567890123"  # hardcoded']
    )
    assert "sensitive_content" in rule_ids(scan_diff(diff, allowed_paths=ALLOWED))


def test_sensitive_adspower_profile_rejected():
    diff = make_diff(
        "src/capture/probe.py", added=['ADSPOWER_PROFILE_ID = "k123abc"']
    )
    assert "sensitive_content" in rule_ids(scan_diff(diff, allowed_paths=ALLOWED))


def test_removed_secret_is_not_a_violation():
    """Removing a secret is fine — only added lines are scanned."""
    diff = make_diff(
        "config/selectors/us.yaml",
        removed=["contact: seller-ops@example.com"],
        added=["contact: ''"],
    )
    assert "sensitive_content" not in rule_ids(scan_diff(diff, allowed_paths=ALLOWED))


# ---------------------------------------------------------------------------
# §18.1 rule 3: --submit gate removal
# ---------------------------------------------------------------------------

def test_submit_gate_removal_rejected():
    diff = make_diff(
        "src/executor/guard.py",
        removed=["REQUIRE_FLAG = '--submit'"],
    )
    assert "submit_gate_removed" in rule_ids(scan_diff(diff, allowed_paths=ALLOWED))


def test_submit_gate_reworded_not_rejected():
    """Keyword survives in an added line — no net removal."""
    diff = make_diff(
        "src/executor/guard.py",
        removed=["REQUIRE_FLAG = '--submit'  # old"],
        added=["REQUIRE_FLAG = '--submit'  # kept"],
    )
    assert "submit_gate_removed" not in rule_ids(scan_diff(diff, allowed_paths=ALLOWED))


# ---------------------------------------------------------------------------
# §18.1 rule 4: CAPTCHA / 2FA / rate-limit bypass
# ---------------------------------------------------------------------------

def test_captcha_bypass_rejected():
    diff = make_diff(
        "src/executor/probe.py", added=["def bypass_captcha(page):  # auto-solve"]
    )
    assert "safety_bypass_introduced" in rule_ids(scan_diff(diff, allowed_paths=ALLOWED))


def test_pause_on_captcha_removal_rejected():
    diff = make_diff(
        "src/executor/state.py",
        removed=["if settings.pause_on_captcha_or_2fa:"],
    )
    assert "safety_bypass_introduced" in rule_ids(scan_diff(diff, allowed_paths=ALLOWED))


# ---------------------------------------------------------------------------
# §18.1 rule 5: Draft / missing Case ID mapped to terminal outcome
# ---------------------------------------------------------------------------

def test_draft_mapped_to_failed_rejected():
    diff = make_diff(
        "src/executor/state.py", added=['    if state == "Draft": return "failed"']
    )
    assert "draft_mapped_to_terminal" in rule_ids(scan_diff(diff, allowed_paths=ALLOWED))


def test_draft_mapped_to_success_rejected():
    diff = make_diff(
        "src/capture/probe.py", added=['OUTCOME = {"Draft": "success"}']
    )
    assert "draft_mapped_to_terminal" in rule_ids(scan_diff(diff, allowed_paths=ALLOWED))


# ---------------------------------------------------------------------------
# §18.1 rule 6: evidence / dashboard / human-review logic removal
# ---------------------------------------------------------------------------

def test_evidence_saving_removal_rejected():
    diff = make_diff(
        "src/capture/probe.py", removed=["save_evidence(page, 'state')"]
    )
    assert "evidence_logic_removed" in rule_ids(scan_diff(diff, allowed_paths=ALLOWED))


def test_dashboard_check_removal_rejected():
    diff = make_diff(
        "src/executor/verify.py", removed=["run_dashboard_check(account)"]
    )
    assert "evidence_logic_removed" in rule_ids(scan_diff(diff, allowed_paths=ALLOWED))


def test_evidence_keyword_readded_not_rejected():
    diff = make_diff(
        "src/capture/probe.py",
        removed=["save_evidence(page, 'state')"],
        added=["save_evidence(page, 'state', redact=True)"],
    )
    report = scan_diff(diff, allowed_paths=ALLOWED)
    assert "evidence_logic_removed" not in rule_ids(report)


# ---------------------------------------------------------------------------
# §18.1 rule 7: unbounded retry / weakened cooldown
# ---------------------------------------------------------------------------

def test_unbounded_retry_loop_rejected():
    diff = make_diff("src/executor/poller.py", added=["while True:", "    poll()"])
    assert "retry_or_cooldown_weakened" in rule_ids(scan_diff(diff, allowed_paths=ALLOWED))


def test_cooldown_keyword_removal_rejected():
    diff = make_diff(
        "src/executor/poller.py", removed=["time.sleep(rate_limit_cooldown)"]
    )
    assert "retry_or_cooldown_weakened" in rule_ids(scan_diff(diff, allowed_paths=ALLOWED))


def test_while_true_in_tests_not_rejected():
    """The loop heuristic targets production code only."""
    diff = make_diff("tests/test_poller.py", added=["while True:", "    break"])
    report = scan_diff(diff, allowed_paths=ALLOWED)
    assert "retry_or_cooldown_weakened" not in rule_ids(report)


# ---------------------------------------------------------------------------
# §18.1 rule 8: live Seller Central calls from tests
# ---------------------------------------------------------------------------

def test_live_seller_central_in_tests_rejected():
    diff = make_diff(
        "tests/test_live.py",
        added=['page.goto("https://sellercentral.amazon.com/applications")'],
    )
    assert "live_seller_central_in_tests" in rule_ids(scan_diff(diff, allowed_paths=ALLOWED))


def test_plain_domain_string_in_tests_not_rejected():
    diff = make_diff("tests/test_live.py", added=['domain = "amazon.com"  # fixture'])
    report = scan_diff(diff, allowed_paths=ALLOWED)
    assert "live_seller_central_in_tests" not in rule_ids(report)


# ---------------------------------------------------------------------------
# Backend risk re-classification (blueprint §17.3)
# ---------------------------------------------------------------------------

def test_classify_file_risk_levels():
    assert classify_file_risk("config/selectors/us.yaml", ALLOWED) == "R0"
    assert classify_file_risk("tests/test_x.py", ALLOWED) == "R0"
    assert classify_file_risk("src/executor/probe.py", ALLOWED) == "R1"
    assert classify_file_risk("src/capture/dom.py", ALLOWED) == "R1"
    # Navigation / form-step semantics -> R2.
    assert classify_file_risk("src/executor/navigation.py", ALLOWED) == "R2"
    # Out of scope is never below R2.
    assert classify_file_risk("src/web/app.py", ALLOWED) == "R2"
    # Submit / legal / authorization semantics -> R3.
    assert classify_file_risk("src/flow_submit_5461.py", ALLOWED) == "R3"
    assert classify_file_risk("src/executor/legal_statement.py", ALLOWED) == "R3"
    # "format" must not trip the form-step rule.
    assert classify_file_risk("src/capture/formatter.py", ALLOWED) == "R1"


def test_backend_risk_level_takes_worst_file():
    files = ["config/selectors/us.yaml", "src/executor/probe.py"]
    assert backend_risk_level(files, ALLOWED) == "R1"
    assert backend_risk_level([], ALLOWED) == "R0"


def test_stricter_risk_combines_codex_and_backend():
    assert stricter_risk("R0", "R1") == "R1"
    assert stricter_risk("R2", "R1") == "R2"
    assert stricter_risk("R0", "R0") == "R0"
    assert stricter_risk("", "R3") == "R3"
    assert stricter_risk("garbage") == "R1"  # unknown self-report degrades safely
