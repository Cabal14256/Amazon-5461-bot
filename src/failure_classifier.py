"""Failure / health classification for Amazon 5461 runs."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List


def _join_texts(*values: Any) -> str:
    parts: List[str] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, dict):
            parts.append(_join_texts(*value.values()))
        elif isinstance(value, (list, tuple, set)):
            parts.append(_join_texts(*value))
        else:
            parts.append(str(value))
    return "\n".join(p for p in parts if p)


def classify_failure(result: Dict[str, Any] | None = None, network_errors: list[dict[str, Any]] | None = None, console_text: str = "") -> Dict[str, Any]:
    """Return stable machine-readable health/failure tags.

    Intended tags include:
    - restriction_endpoint_429
    - cloudfront_chunk_failed
    - panel_shell_only
    - form_fields_missing
    - upload_input_missing
    - submit_no_case_id
    - dashboard_navigation_failed
    - dashboard_not_found
    - draft_detected
    - account_rate_limited
    """
    result = result or {}
    network_errors = network_errors or result.get("network_errors") or []
    text = _join_texts(
        result.get("status"), result.get("submit_result"), result.get("note"), result.get("error"),
        result.get("dashboard_check"), result.get("steps"), console_text,
    ).lower()
    net_text = _join_texts(network_errors).lower()
    combined = text + "\n" + net_text

    tags: list[str] = []
    reasons: list[str] = []

    def add(tag: str, reason: str):
        if tag not in tags:
            tags.append(tag)
            reasons.append(reason)

    if "getrestrictionendpoint" in combined and "429" in combined:
        add("restriction_endpoint_429", "getRestrictionEndpoint returned 429")
    if "cloudfront" in combined and ("err_failed" in combined or "429" in combined or "chunk" in combined):
        add("cloudfront_chunk_failed", "CloudFront JS chunk/resource failed")
    if "panel shell" in combined or "仅 shell" in combined or "空壳" in combined or "半加载" in combined:
        add("panel_shell_only", "5461 panel shell/half-loaded")
    if "字段缺失" in combined or "字段仍未加载" in combined or "form fields" in combined or "字段/上传控件" in combined:
        add("form_fields_missing", "5461 real fields missing")
    if "upload input" in combined or "file input" in combined or "上传控件" in combined:
        add("upload_input_missing", "upload input missing")
    if "no case id" in combined or "未提取" in combined or "未获取 case" in combined or "case id 超时" in combined:
        add("submit_no_case_id", "submitted/ambiguous but no Case ID extracted")
    dash = result.get("dashboard_check") or {}
    if isinstance(dash, dict):
        if dash.get("status") == "draft":
            add("draft_detected", "Dashboard detected draft")
        if dash.get("status") == "not_found":
            add("dashboard_not_found", "Dashboard found no case/draft")
        if dash.get("status") == "error" or "dashboard_navigation_failed" in str(dash).lower() or "err_aborted" in str(dash).lower():
            add("dashboard_navigation_failed", "Dashboard navigation/check failed")
    if "draft" in combined:
        add("draft_detected", "draft mentioned")
    if "429" in combined or "too many requests" in combined or "rate limit" in combined:
        add("account_rate_limited", "429/rate limit evidence")
    if "410001" in combined:
        add("amazon_410001", "Amazon 410001 error")
    if "login" in combined or "signin" in combined or "not authorized" in combined:
        add("login_or_permission_issue", "login/permission issue evidence")

    severity = "ok"
    if any(t in tags for t in ["account_rate_limited", "restriction_endpoint_429", "cloudfront_chunk_failed"]):
        severity = "rate_limited"
    elif any(t in tags for t in ["draft_detected"]):
        severity = "draft"
    elif tags:
        severity = "needs_review"

    return {"severity": severity, "tags": tags, "reasons": reasons}
