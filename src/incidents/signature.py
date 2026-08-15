"""Incident signature computation.

A signature is a 16-hex-char short SHA-256 over normalized failure
components.  Normalization MUST strip anything account- or brand-specific
(account ids, brand names, Case IDs, long digit runs, emails) before
hashing so the same defect aggregates across accounts and no PII can be
reconstructed from the signature inputs.
"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlsplit

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_ACCOUNT_ID_RE = re.compile(r"\b[a-z]{2,}_store_\d+\b", re.IGNORECASE)
_LONG_DIGITS_RE = re.compile(r"\b\d{6,}\b")
# Brand-name samples: long UPPERCASE tokens (optionally with dashes/digits),
# e.g. DEMO-SHIELD.  Applied before lowercasing.
_BRAND_TOKEN_RE = re.compile(r"\b[A-Z][A-Z0-9_-]{3,}\b")
_WS_RE = re.compile(r"\s+")


def normalize_url_pattern(url: str) -> str:
    """Reduce a URL to a stable pattern: no query/fragment, no
    account-specific path segments (mkid, pure digits, *_store_*, long
    opaque tokens)."""
    text = str(url or "").strip()
    if not text:
        return ""
    try:
        parts = urlsplit(text)
    except ValueError:
        return ""
    host = parts.netloc.lower()
    if not host:
        # Not an absolute URL — nothing stable to pattern-match on.
        return ""
    segments = []
    for seg in parts.path.split("/"):
        if not seg:
            continue
        lowered = seg.lower()
        if (
            "mkid" in lowered
            or "_store_" in lowered
            or seg.isdigit()
            or len(seg) >= 16
        ):
            segments.append("*")
        else:
            segments.append(lowered)
    return host + "/" + "/".join(segments)


def normalize_error_class(text: str) -> str:
    """Lowercased, whitespace-collapsed error text with PII/volatile data
    (emails, account ids, brand-name samples, Case IDs and other long digit
    runs) stripped out entirely so signatures stay account/brand-neutral."""
    result = str(text or "")
    result = _EMAIL_RE.sub(" ", result)
    result = _ACCOUNT_ID_RE.sub(" ", result)
    result = _BRAND_TOKEN_RE.sub(" ", result)
    result = _LONG_DIGITS_RE.sub(" ", result)
    result = _WS_RE.sub(" ", result.lower()).strip()
    return result


def _normalize_list(values) -> str:
    items = sorted(normalize_error_class(str(v)) for v in (values or []) if str(v).strip())
    return ",".join(items)


def compute_signature(
    flow_type: str = "",
    page_family: str = "",
    url_pattern: str = "",
    missing_landmarks=None,
    component_types=None,
    state_loop_reason: str = "",
    error_class: str = "",
) -> str:
    """16-hex-char SHA-256 short hash over normalized failure components."""
    parts = [
        normalize_error_class(flow_type),
        normalize_error_class(page_family),
        normalize_url_pattern(url_pattern),
        _normalize_list(missing_landmarks),
        _normalize_list(component_types),
        normalize_error_class(state_loop_reason),
        normalize_error_class(error_class),
    ]
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:16]
