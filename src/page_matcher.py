from typing import Dict, Any, List


def _contains_all(haystack: str, keywords: List[str]) -> bool:
    hay = (haystack or "").lower()
    return all((kw or "").lower() in hay for kw in keywords)


def match_conditions(page_ctx: Dict[str, Any], cond: Dict[str, Any]) -> bool:
    url = page_ctx.get("url", "")
    title = page_ctx.get("title", "")
    visible_text = page_ctx.get("visible_text", "")
    dom_markers = page_ctx.get("dom_markers", []) or []

    if cond.get("url_contains") and not _contains_all(url, cond.get("url_contains", [])):
        return False
    if cond.get("title_contains") and not _contains_all(title, cond.get("title_contains", [])):
        return False
    if cond.get("text_contains") and not _contains_all(visible_text, cond.get("text_contains", [])):
        return False

    for marker in cond.get("elements_present", []) or []:
        if marker not in dom_markers:
            return False

    for marker in cond.get("elements_absent", []) or []:
        if marker in dom_markers:
            return False

    return True
