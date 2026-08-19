"""Strict, non-secret options accepted by managed automation jobs."""

from __future__ import annotations

from typing import Any

NON_SUBMIT_BRAND_LIMIT = 20
CASE_FOLLOWUP_DELAY_MIN_HOURS = 0.1
CASE_FOLLOWUP_DELAY_MAX_HOURS = 168.0

_ALLOWED_JOB_OPTION_KEYS = {
    "case_followup_delay_hours",
    "case_followup_enabled",
}


def normalize_job_options(raw: Any, *, job_type: str) -> dict[str, Any]:
    """Validate and normalize the immutable option snapshot for one job.

    Case follow-up options only make sense for a real submit job.  Keeping the
    validator outside the web layer also protects jobs restored directly from
    SQLite before their command line is built.
    """

    if raw in (None, {}):
        return {}
    if not isinstance(raw, dict):
        raise ValueError("job_options_must_be_object")
    unknown = sorted(set(raw) - _ALLOWED_JOB_OPTION_KEYS)
    if unknown:
        raise ValueError(f"unknown_job_options:{','.join(unknown)}")
    if job_type != "submit":
        raise ValueError("job_options_not_supported")

    normalized: dict[str, Any] = {}
    if "case_followup_delay_hours" in raw:
        value = raw["case_followup_delay_hours"]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("invalid_case_followup_delay_hours")
        delay = float(value)
        if not CASE_FOLLOWUP_DELAY_MIN_HOURS <= delay <= CASE_FOLLOWUP_DELAY_MAX_HOURS:
            raise ValueError("invalid_case_followup_delay_hours")
        normalized["case_followup_delay_hours"] = delay

    if "case_followup_enabled" in raw:
        value = raw["case_followup_enabled"]
        if not isinstance(value, bool):
            raise ValueError("invalid_case_followup_enabled")
        normalized["case_followup_enabled"] = value

    return normalized


__all__ = [
    "CASE_FOLLOWUP_DELAY_MAX_HOURS",
    "CASE_FOLLOWUP_DELAY_MIN_HOURS",
    "NON_SUBMIT_BRAND_LIMIT",
    "normalize_job_options",
]
