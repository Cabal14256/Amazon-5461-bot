from typing import Dict, Any


def summarize_page(page) -> Dict[str, Any]:
    url = page.url
    try:
        title = page.title()
    except Exception:
        title = ""

    try:
        visible_text = page.locator("body").inner_text(timeout=5000) or ""
    except Exception:
        visible_text = ""

    visible_text = visible_text.strip()

    dom_markers = []
    try:
        if page.locator("button").count() > 0:
            dom_markers.append("has_button")
        if page.locator("input").count() > 0:
            dom_markers.append("has_input")
        if page.locator("textarea").count() > 0:
            dom_markers.append("has_textarea")
        if "description" in visible_text.lower():
            dom_markers.append("text_description")
        if "two-step verification" in visible_text.lower():
            dom_markers.append("two_factor")
        if "captcha" in visible_text.lower() or "characters you see" in visible_text.lower():
            dom_markers.append("captcha")
    except Exception:
        pass

    return {
        "url": url,
        "title": title,
        "visible_text": visible_text[:4000],
        "dom_markers": dom_markers,
    }
