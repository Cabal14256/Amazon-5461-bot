"""Deterministic failure classification.

Human-handling classifications (CAPTCHA, 2FA, expired login, account risk,
rate limit, brand block, missing config, Amazon platform errors, uncertain
business outcomes) always win over fix-candidate classifications — a page
that mentions a missing selector inside a 429 storm is a rate-limit
problem, not a selector defect.
"""

from __future__ import annotations

import re

REPAIR_CLASSES = {
    "selector_missing",
    "state_unknown",
    "dom_contract_changed",
    "navigation_changed",
    "semantic_control_missing",
    "flow_loop_exhausted",
    "result_contract_changed",
}

HUMAN_CLASSES = {
    "captcha",
    "two_fa",
    "login_expired",
    "account_risk",
    "rate_limit",
    "brand_block",
    "config_missing",
    "amazon_platform_error",
    "business_uncertain",
}

# Ordered: human-handling rules first (higher priority), then fix candidates.
# Rate limits are handled separately because bare browser-console 429 messages
# are ambiguous without the structured request URL that produced them.
_RULES: list[tuple[str, re.Pattern]] = [
    ("captcha", re.compile(r"captcha")),
    ("two_fa", re.compile(r"2fa|otp|two-factor|two_factor")),
    ("login_expired", re.compile(r"登录过期|会话失效|login expired|session expired|auth.*expired")),
    ("account_risk", re.compile(r"账号风险|account risk|risk control")),
    ("brand_block", re.compile(r"brand hard block|brand_block")),
    (
        "config_missing",
        re.compile(r"缺少.*(profile|配置|品牌包)|missing_adspower_profile|unknown_(account|brand|site)|config.*missing"),
    ),
    ("amazon_platform_error", re.compile(r"server_error|amazon 服务端")),
    ("flow_loop_exhausted", re.compile(r"stuck_at_|max_steps_exceeded|半加载|recover.*exhausted|恢复.*耗尽")),
    ("selector_missing", re.compile(r"selector.*missing|选择器.*缺失")),
    ("state_unknown", re.compile(r"state_unknown|unknown 页面|无法分类")),
    ("dom_contract_changed", re.compile(r"dom_contract|landmark.*(缺失|改变)")),
    ("navigation_changed", re.compile(r"navigation_changed")),
    ("result_contract_changed", re.compile(r"result_contract|解析器不兼容")),
    (
        "semantic_control_missing",
        re.compile(
            r"semantic_control|无法进入\s*5461\s*表单|"
            r"未检测到\s*5461\s*(?:入口|表单)|apply to sell.*(?:not found|未找到)"
        ),
    ),
]

_RATE_LIMIT_RE = re.compile(r"429|410001|too many requests|rate[ -]?limit|cloudfront")
_TELEMETRY_URL_MARKERS = (
    "/quicklist/katallogs",
    "/mons/",
)


def is_telemetry_network_event(event: object) -> bool:
    """Return True for observability traffic that cannot block the workflow."""
    if not isinstance(event, dict):
        return False
    url = str(event.get("url") or "").lower()
    return any(marker in url for marker in _TELEMETRY_URL_MARKERS)


def has_actionable_rate_limit(
    *,
    status: str = "",
    error_text: str = "",
    console_text: str = "",
    page_state: str = "",
    reason: str = "",
    network_errors: list[dict] | None = None,
) -> bool:
    """Distinguish workflow rate limits from noisy telemetry failures.

    Explicit flow/page errors remain authoritative.  When structured network
    metadata is available, a generic console ``status of 429`` line is only
    actionable if a non-telemetry request was actually rate-limited.
    """
    explicit_text = " ".join(
        str(part or "") for part in (status, error_text, page_state, reason)
    ).lower()
    if _RATE_LIMIT_RE.search(explicit_text):
        return True

    events = [event for event in (network_errors or []) if isinstance(event, dict)]
    actionable_event = False
    for event in events:
        if is_telemetry_network_event(event):
            continue
        status_code = event.get("status")
        event_text = " ".join(
            str(event.get(key) or "") for key in ("url", "error_text")
        ).lower()
        if status_code == 429 or "410001" in event_text or "too many requests" in event_text:
            actionable_event = True
            break
    if actionable_event:
        return True

    console_lower = str(console_text or "").lower()
    if "410001" in console_lower or "too many requests" in console_lower:
        return True
    if not events and _RATE_LIMIT_RE.search(console_lower):
        return True
    return False


def classify_failure(
    *,
    status: str = "",
    error_text: str = "",
    console_text: str = "",
    page_state: str = "",
    reason: str = "",
    network_errors: list[dict] | None = None,
    dry_run: bool = False,
) -> str:
    """Classify one failure into a human-handling or fix-candidate class."""
    del dry_run  # recording policy lives in should_record; classification is context-free
    if has_actionable_rate_limit(
        status=status,
        error_text=error_text,
        console_text=console_text,
        page_state=page_state,
        reason=reason,
        network_errors=network_errors,
    ):
        return "rate_limit"
    combined = " ".join(
        str(part or "") for part in (status, error_text, console_text, page_state, reason)
    ).lower()
    for classification, pattern in _RULES:
        if pattern.search(combined):
            return classification
    return "business_uncertain"


def initial_confidence(classification: str) -> float:
    """Fix candidates start at 0.50 (likely code defect); human-handling
    classes start at 0.10."""
    return 0.50 if classification in REPAIR_CLASSES else 0.10


def should_record(classification: str, dry_run: bool) -> bool:
    """Dry-run business-uncertain failures are noise; everything else is
    recorded."""
    if classification == "business_uncertain" and dry_run:
        return False
    return True
