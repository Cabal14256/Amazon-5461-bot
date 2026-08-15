"""Detect Amazon's intermediate application-type chooser conservatively.

The chooser can appear between ``Apply to sell`` and the actual 5461 form.
Some brands show both "create new ASINs" and "sell products" cards, while
others go straight to the form.  This module classifies only what is visible;
it never treats the sell-products card as equivalent to the create-ASIN path.
"""

from __future__ import annotations

import re
from typing import Any

_CREATE_NEW_ASINS_RE = re.compile(r"application\s+to\s+create\s+new\s+asins\s+for\b", re.I)
_SELL_PRODUCTS_RE = re.compile(r"application\s+to\s+sell\b.*?\bproducts\b", re.I)
_ANY_APPLICATION_RE = re.compile(r"application\s+to\b", re.I)
_EXISTING_APPLICATION_RE = re.compile(
    r"\bcase\s*id\b|\bunder\s+review\b|\bdeclined\b|\brejected\b|\bdenied\b",
    re.I,
)


def classify_application_type_text(text: str) -> dict[str, Any]:
    """Classify visible chooser text without assuming every brand has cards."""

    normalized = " ".join(str(text or "").split())
    has_create = bool(_CREATE_NEW_ASINS_RE.search(normalized))
    has_sell = bool(_SELL_PRODUCTS_RE.search(normalized))
    has_any = bool(_ANY_APPLICATION_RE.search(normalized))
    has_existing_application = bool(_EXISTING_APPLICATION_RE.search(normalized))

    if has_existing_application:
        status = "existing_application"
    elif has_create:
        status = "create_new_asins_available"
    elif has_sell:
        status = "sell_products_only"
    elif has_any:
        status = "unknown_application_options"
    else:
        status = "none"

    return {
        "status": status,
        "has_create_new_asins": has_create,
        "has_sell_products": has_sell,
        "has_any_application_option": has_any,
        "has_existing_application": has_existing_application,
    }


def probe_application_type_options(page) -> dict[str, Any]:
    """Read the visible right-side panel and classify its application cards."""

    try:
        panel_data = page.evaluate(
            """() => {
                const wrappers = Array.from(document.querySelectorAll('kat-panel-wrapper'));
                const isVisible = (el) => {
                    const style = window.getComputedStyle(el);
                    return el.getAttribute('panel-visible') === 'true' ||
                           (style.display !== 'none' && style.visibility !== 'hidden' &&
                            el.offsetWidth > 0 && el.offsetHeight > 0);
                };
                const textOf = (el) => (el.innerText || el.textContent || '').trim();
                const panel = wrappers.find((el) => {
                    if (!isVisible(el)) return false;
                    const text = textOf(el).toLowerCase();
                    return text.includes('apply to sell') || text.includes('application to');
                });
                if (!panel) return {panel_found: false, text: ''};
                return {panel_found: true, text: textOf(panel)};
            }"""
        ) or {"panel_found": False, "text": ""}
    except Exception as exc:
        return {
            "status": "probe_error",
            "panel_found": False,
            "error": type(exc).__name__,
            "has_create_new_asins": False,
            "has_sell_products": False,
            "has_any_application_option": False,
            "has_existing_application": False,
        }

    result = classify_application_type_text(panel_data.get("text", ""))
    result["panel_found"] = bool(panel_data.get("panel_found"))
    return result
