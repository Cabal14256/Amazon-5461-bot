"""Effective approval checks for an Amazon Case that says it was approved.

An explicit Case approval is only a trigger for these checks.  It is not an
effective approval by itself.  Unless a brand is explicitly configured for
the Add Product-only rule, a real approval requires either an explicit Connect
brand approval, or both of the following:

* the brand is visible in Seller Central's Manage Your Brands page; and
* Add Product can advance from Product Identity to Description.

The module never clicks Apply to sell or submits a 5461 application.  Login,
CAPTCHA, 2FA, navigation failures, and unrecognised UI states remain unknown or
blocked; they must not be converted into a false approval.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from .form_filler import KatalFormFiller

MANAGE_FOUND = "found"
MANAGE_NOT_FOUND = "not_found"
MANAGE_CONNECT_APPROVED = "connect_approved"
MANAGE_CONNECT_FAILED = "connect_failed"
MANAGE_CONNECT_UNKNOWN = "connect_unknown"
ADD_PRODUCT_PASS = "pass"
ADD_PRODUCT_FAIL = "fail"

_AUTH_TEXT_MARKERS = {
    "captcha_required": ("enter the characters you see", "captcha"),
    "two_factor_authentication_required": (
        "two-step verification",
        "two factor authentication",
        "verification code",
    ),
    "seller_central_login_required": ("sign in to your account",),
}

_ADD_PRODUCT_RESTRICTION_MARKERS = (
    "brand authorization required",
    "brand authorisation required",
    "restricted to authorized sellers",
    "restricted to authorised sellers",
    "you need approval to list this product",
    "listing approval",
    "apply to sell",
    "application required",
)

_ADD_PRODUCT_BRAND_SELECTION_MARKERS = (
    "could not associate",
    "couldn't associate",
    "clarify which brand",
    "select brand",
)

_CONNECT_BRAND_FAILURE_MARKERS = (
    "you are not approved to list",
    "unable to connect this brand",
    "could not connect this brand",
    "couldn't connect this brand",
    "no brands found",
    "no brand found",
)

_MANAGE_BRANDS_PATH = "/manage-your-brands?ref_=xx_myb_favb_xx"
_BRAND_PACKS_ROOT = Path(__file__).resolve().parent.parent / "brand_packs"


def get_approved_verification_config(settings: dict[str, Any]) -> dict[str, Any]:
    """Return a shallowly merged, conservative verification configuration."""
    configured = dict(
        (settings.get("case_followup") or {}).get("approved_verification") or {}
    )
    manage_config = {
        "menu_labels": ["Manage Your Brands", "Manage your brands", "Manage Brands"],
        "page_markers": ["Manage Your Brands", "Brand Portfolio", "Your brands"],
        "manage_brands_url": "",
        "settle_seconds": 2.0,
    }
    configured_manage = dict(configured.get("manage_brand") or {})
    connect_brand_config = {
        "enabled": True,
        "add_brand_labels": ["Add brand", "Add Brand"],
        "panel_markers": ["Connect brand", "Search for the brand you want to list with"],
        "search_placeholders": ["Enter brand name"],
        "category_keywords": [
            "screen protector",
            "screen protectors",
            "screen protection",
            "protective screen",
            "mobile screen",
            "cellphone screen",
            "phone screen",
            "手机屏幕保护膜",
            "protections d'écran",
        ],
        "panel_timeout_ms": 10_000,
        "search_timeout_ms": 15_000,
        "result_timeout_ms": 20_000,
    }
    connect_brand_config.update(configured_manage.pop("connect_brand", {}) or {})
    manage_config.update(configured_manage)
    manage_config["connect_brand"] = connect_brand_config
    add_product_config = {
        "item_name_template": "{brand} Screen Protector",
        "transition_timeout_ms": 15_000,
        # Amazon's new listing workflow calls its Product Identity validation CTA
        # "Submit".  The selector below advances/validates the step; this module
        # still refuses Apply to sell and every 5461/application Submit button.
        "allow_new_ui_submit_as_continue": True,
    }
    add_product_config.update(configured.get("add_product") or {})
    configured_add_product_only_brands = configured.get("add_product_only_brands") or []
    if isinstance(configured_add_product_only_brands, str):
        configured_add_product_only_brands = [configured_add_product_only_brands]
    add_product_only_brands = [
        str(brand_name).strip()
        for brand_name in configured_add_product_only_brands
        if str(brand_name).strip()
    ]
    return {
        "enabled": bool(configured.get("enabled", True)),
        "add_product_only_brands": add_product_only_brands,
        "manage_brand": manage_config,
        "add_product": add_product_config,
    }


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "")
    return re.sub(r"\s+", " ", normalized).strip().casefold()


def _load_brand_selection_keywords(
    brand_name: str, brand_packs_root: Path | None = None
) -> list[str]:
    """Load the human-maintained exact Add brand descriptions for one brand."""
    if not brand_name or Path(brand_name).name != brand_name:
        return []
    root = brand_packs_root or _BRAND_PACKS_ROOT
    manifest_path = root / brand_name / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    return [
        str(keyword).strip()
        for keyword in manifest.get("brand_selection_keywords") or []
        if str(keyword).strip()
    ]


def _connect_brand_config_for_brand(
    config: dict[str, Any], brand_name: str, brand_packs_root: Path | None = None
) -> dict[str, Any]:
    """Prefer exact per-brand descriptions over generic category keywords."""
    resolved = dict(config)
    brand_keywords = _load_brand_selection_keywords(brand_name, brand_packs_root)
    if brand_keywords:
        resolved["category_keywords"] = brand_keywords
    return resolved


def _page_text(page) -> str:
    """Read visible-ish light/shadow DOM text without depending on one UI version."""
    try:
        return page.evaluate(
            r"""() => {
              const parts = [document.body ? document.body.innerText : ''];
              const roots = [document];
              for (let index = 0; index < roots.length; index++) {
                const root = roots[index];
                for (const node of root.querySelectorAll('*')) {
                  if (!node.shadowRoot) continue;
                  roots.push(node.shadowRoot);
                  for (const child of node.shadowRoot.querySelectorAll('*')) {
                    const style = window.getComputedStyle(child);
                    if (style.display === 'none' || style.visibility === 'hidden') continue;
                    if (child.children.length > 0) continue;
                    const text = (child.innerText || child.textContent || '').trim();
                    if (text) parts.push(text);
                  }
                }
              }
              return parts.join('\n');
            }"""
        ) or ""
    except Exception:
        try:
            return page.locator("body").inner_text(timeout=10_000) or ""
        except Exception:
            return ""


def _auth_block_reason(page, text: str = "") -> str | None:
    url = (getattr(page, "url", "") or "").casefold()
    text_lower = (text or _page_text(page)).casefold()
    if "/ap/signin" in url:
        return "seller_central_login_required"
    if "captcha" in url:
        return "captcha_required"
    for reason, markers in _AUTH_TEXT_MARKERS.items():
        if any(marker in text_lower for marker in markers):
            return reason
    return None


def _save_evidence(page, out_dir: Path, prefix: str) -> list[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    files: list[str] = []
    writers = (
        ("url.txt", lambda: getattr(page, "url", "") or ""),
        ("text.txt", lambda: _page_text(page)),
        ("html", page.content),
    )
    for suffix, producer in writers:
        try:
            path = out_dir / f"{prefix}.{suffix}"
            path.write_text(producer(), encoding="utf-8")
            files.append(str(path))
        except Exception:
            pass
    try:
        screenshot = out_dir / f"{prefix}.png"
        page.screenshot(path=str(screenshot), full_page=True, timeout=20_000)
        files.append(str(screenshot))
    except Exception:
        pass
    return files


def _goto(page, url: str, timeout_ms: int) -> None:
    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    try:
        page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 8_000))
    except Exception:
        pass


def _manage_brands_direct_url(sellercentral_home_url: str) -> str:
    """Return Amazon's stable regional Manage Your Brands destination."""
    hostname = (urlsplit(sellercentral_home_url).hostname or "").casefold()
    domain = "sellercentral.amazon.co.uk" if hostname.endswith("amazon.co.uk") else "sellercentral.amazon.com"
    return f"https://{domain}{_MANAGE_BRANDS_PATH}"


def _click_manage_brands_link(page, labels: list[str], timeout_ms: int) -> dict[str, Any]:
    """Open the Manage Your Brands destination from Seller Central navigation."""
    label_patterns = [re.compile(rf"^{re.escape(label)}$", re.IGNORECASE) for label in labels]

    def candidates():
        for pattern in label_patterns:
            yield page.get_by_role("link", name=pattern).first
            yield page.get_by_text(pattern).first
        yield page.locator('a[href*="brand" i]').filter(
            has_text=re.compile(r"manage|portfolio", re.IGNORECASE)
        ).first

    def use_first_candidate() -> dict[str, Any] | None:
        for locator in candidates():
            try:
                if locator.count() == 0 or not locator.is_visible(timeout=1_000):
                    continue
                href = locator.get_attribute("href")
                label = (locator.inner_text(timeout=2_000) or "").strip()
                if href and not href.casefold().startswith("javascript:"):
                    destination = urljoin(page.url, href)
                    _goto(page, destination, timeout_ms)
                    return {"opened": True, "method": "href", "label": label}
                locator.click(timeout=5_000)
                return {"opened": True, "method": "click", "label": label}
            except Exception:
                continue
        return None

    direct = use_first_candidate()
    if direct:
        return direct

    menu_selectors = (
        "#sc-m-nav-hamburger",
        '[data-testid*="hamburger" i]',
        'kat-button[aria-label*="menu" i]',
        'button[aria-label*="menu" i]',
        '[role="button"][aria-label*="menu" i]',
    )
    menu_opened = False
    for selector in menu_selectors:
        try:
            locator = page.locator(selector).first
            if locator.count() and locator.is_visible(timeout=1_000):
                locator.click(timeout=5_000)
                menu_opened = True
                break
        except Exception:
            continue
    if menu_opened:
        try:
            page.wait_for_timeout(750)
        except Exception:
            time.sleep(0.75)
        after_menu = use_first_candidate()
        if after_menu:
            after_menu["menu_opened"] = True
            return after_menu
    return {"opened": False, "menu_opened": menu_opened, "method": "not_found"}


def _brand_visible_on_manage_page(page, brand_name: str) -> bool:
    target = _normalize_text(brand_name)
    if not target:
        return False
    try:
        candidates = page.evaluate(
            r"""() => {
              const values = [];
              const roots = [document];
              for (let index = 0; index < roots.length; index++) {
                const root = roots[index];
                for (const el of root.querySelectorAll('*')) {
                  if (el.shadowRoot) roots.push(el.shadowRoot);
                  const style = window.getComputedStyle(el);
                  if (style.display === 'none' || style.visibility === 'hidden') continue;
                  const text = (el.innerText || el.textContent || '').trim();
                  if (!text || text.length > 300) continue;
                  values.push(text);
                }
              }
              return values.slice(0, 10000);
            }"""
        ) or []
    except Exception:
        candidates = []
    for candidate in candidates:
        if _normalize_text(str(candidate)) == target:
            return True
        if any(_normalize_text(line) == target for line in str(candidate).splitlines()):
            return True
    return any(_normalize_text(line) == target for line in _page_text(page).splitlines())


def _click_exact_button(page, labels: list[str], timeout_ms: int = 5_000) -> dict[str, Any]:
    """Click one visible button by an exact accessible/text label."""
    for label in labels:
        pattern = re.compile(rf"^{re.escape(str(label))}$", re.IGNORECASE)
        candidates = (
            page.get_by_role("button", name=pattern).first,
            page.get_by_text(pattern).first,
            page.locator("kat-button").filter(has_text=pattern).first,
        )
        for locator in candidates:
            try:
                if locator.count() == 0 or not locator.is_visible(timeout=750):
                    continue
                locator.click(timeout=timeout_ms)
                return {"clicked": True, "label": str(label)}
            except Exception:
                continue
    return {"clicked": False, "label": ""}


def _connect_approval_message_matches(text: str, brand_name: str) -> bool:
    expected = _normalize_text(
        f"You are approved to list {brand_name} products. "
        "You can close this panel and continue listing."
    )
    return bool(expected and expected in _normalize_text(text))


def _candidate_has_exact_brand(candidate: dict[str, Any], brand_name: str) -> bool:
    target = _normalize_text(brand_name)
    name = _normalize_text(str(candidate.get("name") or ""))
    if name == target:
        return True
    lines = str(candidate.get("text") or "").splitlines()
    return any(_normalize_text(line) == target for line in lines)


def choose_connect_brand_candidate(
    candidates: list[dict[str, Any]],
    brand_name: str,
    category_keywords: list[str],
) -> dict[str, Any]:
    """Choose one exact-brand, screen-protector-related search result."""
    normalized_keywords = [
        _normalize_text(keyword) for keyword in category_keywords if _normalize_text(keyword)
    ]
    matches: list[dict[str, Any]] = []
    for candidate in candidates:
        if not _candidate_has_exact_brand(candidate, brand_name):
            continue
        candidate_text = _normalize_text(str(candidate.get("text") or ""))
        matched_keyword = next(
            (keyword for keyword in normalized_keywords if keyword in candidate_text), ""
        )
        if matched_keyword:
            matches.append({**candidate, "matched_keyword": matched_keyword})

    if len(matches) == 1:
        return {"status": "matched", "candidate": matches[0]}
    if len(matches) > 1:
        return {
            "status": "ambiguous",
            "reason": "搜索结果中有多个同品牌的屏幕保护膜相关选项，拒绝猜测",
            "candidate_count": len(matches),
        }
    exact_brand_count = sum(
        1 for candidate in candidates if _candidate_has_exact_brand(candidate, brand_name)
    )
    return {
        "status": "not_found",
        "reason": (
            "搜索结果中找到同名品牌，但没有屏幕保护膜相关选项"
            if exact_brand_count
            else "搜索结果中没有找到精确匹配的品牌"
        ),
        "candidate_count": len(candidates),
    }


def _collect_connect_brand_candidates(page) -> list[dict[str, Any]]:
    """Read the DOM contract proven by connect_brand_in_5461_panel().

    The selector sequence is intentionally copied from the existing 5461
    ConnectBrand flow, then tightened for approval verification: scope to the
    current brex widget when available and never fall back to a generic radio.
    """
    return page.evaluate(
        r"""() => {
        const widget = document.querySelector('div[data-cy="brex-widget"]');
        const root = widget || document;
        return Array.from(
          root.querySelectorAll('kat-box[data-testid="brand-info-option"]')
        ).map((box, index) => {
          const nameNode = box.querySelector('[data-testid="brand-name"], p');
          const descriptionNode = box.querySelector(
            '[data-testid="brand-description-content"]'
          );
          const name = (nameNode ? nameNode.textContent : '').trim();
          const description = (descriptionNode ? descriptionNode.textContent : '').trim();
          const text = (box.innerText || box.textContent || '').trim();
          return {index, name, description, text};
        });
        }"""
    ) or []


def _fill_connect_brand_search(page, brand_name: str, placeholders: list[str]) -> dict[str, Any]:
    candidates = []
    for placeholder in placeholders:
        pattern = re.compile(rf"^{re.escape(str(placeholder))}$", re.IGNORECASE)
        candidates.append(page.get_by_placeholder(pattern).first)
    candidates.append(page.locator('input[placeholder*="brand" i]').last)

    for locator in candidates:
        try:
            if locator.count() == 0 or not locator.is_visible(timeout=1_000):
                continue
            locator.click(timeout=3_000)
            locator.fill(brand_name, timeout=5_000)
            locator.press("Enter", timeout=3_000)
            return {"filled": True, "submitted_by": "enter"}
        except Exception:
            continue
    return {"filled": False, "submitted_by": ""}


def _click_rightmost_search_button(page) -> bool:
    candidates = (
        page.get_by_role("button", name=re.compile(r"search", re.IGNORECASE)),
        page.locator('button[aria-label*="search" i]'),
        page.locator('kat-button[aria-label*="search" i]'),
    )
    visible = []
    for locator in candidates:
        try:
            for index in range(locator.count()):
                item = locator.nth(index)
                if not item.is_visible(timeout=500):
                    continue
                box = item.bounding_box() or {}
                visible.append((float(box.get("x") or 0), item))
        except Exception:
            continue
    for _, locator in sorted(visible, key=lambda item: item[0], reverse=True):
        try:
            locator.click(timeout=3_000)
            return True
        except Exception:
            continue
    return False


def _select_connect_brand_candidate(page, index: int) -> bool:
    boxes = page.locator(
        'div[data-cy="brex-widget"] kat-box[data-testid="brand-info-option"]'
    )
    try:
        if boxes.count() == 0:
            boxes = page.locator('kat-box[data-testid="brand-info-option"]')
    except Exception:
        boxes = page.locator('kat-box[data-testid="brand-info-option"]')
    box = boxes.nth(index)
    candidates = (
        box.locator(
            'input[type="radio"][name="brand-record-selection-radio-button"]'
        ).first,
        box.get_by_role("radio").first,
        box,
    )
    for locator in candidates:
        try:
            if locator.count() == 0 or not locator.is_visible(timeout=1_000):
                continue
            locator.click(timeout=5_000)
            return True
        except Exception:
            continue
    return False


def _connect_button_disabled(locator) -> bool:
    try:
        return bool(
            locator.evaluate(
                r"""element => {
                  const inner = element.shadowRoot
                    ? element.shadowRoot.querySelector('button') : null;
                  return element.disabled === true || element.hasAttribute('disabled') ||
                    element.getAttribute('state') === 'disabled' ||
                    element.getAttribute('aria-disabled') === 'true' ||
                    Boolean(inner && (inner.disabled || inner.hasAttribute('disabled')));
                }"""
            )
        )
    except Exception:
        return True


def _click_connect_brand_confirmation(page, timeout_ms: int) -> dict[str, Any]:
    deadline = time.monotonic() + max(1.0, timeout_ms / 1_000)
    while time.monotonic() < deadline:
        candidates = (
            page.locator('kat-button[data-testid="connect-brand-button"]').first,
            page.get_by_role(
                "button", name=re.compile(r"^connect(?:ing)? this brand$", re.IGNORECASE)
            ).first,
        )
        for locator in candidates:
            try:
                if locator.count() == 0 or not locator.is_visible(timeout=500):
                    continue
                if _connect_button_disabled(locator):
                    continue
                locator.click(timeout=5_000)
                return {"clicked": True}
            except Exception:
                continue
        time.sleep(0.25)
    return {"clicked": False, "reason": "Connect this brand 按钮未出现或一直不可用"}


def verify_connect_brand_authorization(
    page,
    brand_name: str,
    config: dict[str, Any],
    evidence_dir: Path,
) -> dict[str, Any]:
    """Use Add brand as the decisive fallback when the portfolio omits a brand."""
    evidence_files: list[str] = []
    candidate_summary: dict[str, Any] = {}
    try:
        if not bool(config.get("enabled", True)):
            return {
                "result": "unknown",
                "reason": "Connect brand 兜底验证被配置为关闭",
                "evidence_files": [],
            }

        add_action = _click_exact_button(
            page,
            [str(label) for label in config.get("add_brand_labels") or []],
        )
        if not add_action.get("clicked"):
            evidence_files.extend(_save_evidence(page, evidence_dir, "connect_brand_add_button_missing"))
            return {
                "result": "unknown",
                "reason": "Manage Your Brands 已确认，但未找到可点击的 Add brand",
                "evidence_files": evidence_files,
            }

        panel_timeout_ms = int(config.get("panel_timeout_ms") or 10_000)
        panel_markers = [_normalize_text(str(value)) for value in config.get("panel_markers") or []]
        deadline = time.monotonic() + max(1.0, panel_timeout_ms / 1_000)
        panel_text = ""
        while time.monotonic() < deadline:
            panel_text = _page_text(page)
            block = _auth_block_reason(page, panel_text)
            if block:
                evidence_files.extend(_save_evidence(page, evidence_dir, "connect_brand_blocked"))
                return {"result": "blocked", "reason": block, "evidence_files": evidence_files}
            normalized = _normalize_text(panel_text)
            if any(marker and marker in normalized for marker in panel_markers):
                break
            time.sleep(0.25)
        else:
            evidence_files.extend(_save_evidence(page, evidence_dir, "connect_brand_panel_unknown"))
            return {
                "result": "unknown",
                "reason": "点击 Add brand 后未确认 Connect brand 侧栏已打开",
                "evidence_files": evidence_files,
            }

        evidence_files.extend(_save_evidence(page, evidence_dir, "connect_brand_panel"))
        search = _fill_connect_brand_search(
            page,
            brand_name,
            [str(value) for value in config.get("search_placeholders") or []],
        )
        if not search.get("filled"):
            evidence_files.extend(_save_evidence(page, evidence_dir, "connect_brand_search_input_missing"))
            return {
                "result": "unknown",
                "reason": "Connect brand 侧栏中未找到品牌搜索输入框",
                "evidence_files": evidence_files,
            }

        search_timeout_ms = int(config.get("search_timeout_ms") or 15_000)
        deadline = time.monotonic() + max(1.0, search_timeout_ms / 1_000)
        candidates: list[dict[str, Any]] = []
        search_button_retried = False
        while time.monotonic() < deadline:
            candidates = _collect_connect_brand_candidates(page)
            if candidates:
                break
            text = _page_text(page)
            block = _auth_block_reason(page, text)
            if block:
                evidence_files.extend(_save_evidence(page, evidence_dir, "connect_brand_blocked"))
                return {"result": "blocked", "reason": block, "evidence_files": evidence_files}
            failure = next(
                (marker for marker in _CONNECT_BRAND_FAILURE_MARKERS if marker in text.casefold()),
                "",
            )
            if failure:
                evidence_files.extend(_save_evidence(page, evidence_dir, "connect_brand_no_result"))
                return {
                    "result": "failed",
                    "reason": f"Connect brand 搜索明确失败: {failure}",
                    "evidence_files": evidence_files,
                }
            if not search_button_retried and time.monotonic() > deadline - max(
                1.0, search_timeout_ms / 2_000
            ):
                search_button_retried = _click_rightmost_search_button(page)
            time.sleep(0.5)

        evidence_files.extend(_save_evidence(page, evidence_dir, "connect_brand_search_results"))
        if not candidates:
            return {
                "result": "unknown",
                "reason": "品牌搜索超时，未出现可判定的结果或明确失败提示",
                "evidence_files": evidence_files,
            }

        candidate_summary = choose_connect_brand_candidate(
            candidates,
            brand_name,
            [str(value) for value in config.get("category_keywords") or []],
        )
        if candidate_summary.get("status") == "ambiguous":
            return {
                "result": "unknown",
                "reason": str(candidate_summary.get("reason") or "Connect brand 结果不唯一"),
                "candidate_count": candidate_summary.get("candidate_count", 0),
                "evidence_files": evidence_files,
            }
        if candidate_summary.get("status") != "matched":
            return {
                "result": "failed",
                "reason": str(candidate_summary.get("reason") or "没有匹配的 Connect brand 结果"),
                "candidate_count": candidate_summary.get("candidate_count", 0),
                "evidence_files": evidence_files,
            }

        candidate = dict(candidate_summary["candidate"])
        candidate_index = int(candidate.get("index") or 0)
        if not _select_connect_brand_candidate(page, candidate_index):
            evidence_files.extend(_save_evidence(page, evidence_dir, "connect_brand_candidate_click_failed"))
            return {
                "result": "unknown",
                "reason": "已找到精确品牌和屏幕保护膜相关选项，但无法可靠选中",
                "evidence_files": evidence_files,
            }
        evidence_files.extend(_save_evidence(page, evidence_dir, "connect_brand_candidate_selected"))

        text = _page_text(page)
        if not _connect_approval_message_matches(text, brand_name):
            confirm = _click_connect_brand_confirmation(page, timeout_ms=5_000)
            if not confirm.get("clicked"):
                evidence_files.extend(_save_evidence(page, evidence_dir, "connect_brand_confirm_unknown"))
                return {
                    "result": "unknown",
                    "reason": str(confirm.get("reason") or "无法确认 Connect this brand 操作"),
                    "evidence_files": evidence_files,
                }

        result_timeout_ms = int(config.get("result_timeout_ms") or 20_000)
        deadline = time.monotonic() + max(1.0, result_timeout_ms / 1_000)
        while time.monotonic() < deadline:
            text = _page_text(page)
            block = _auth_block_reason(page, text)
            if block:
                evidence_files.extend(_save_evidence(page, evidence_dir, "connect_brand_blocked"))
                return {"result": "blocked", "reason": block, "evidence_files": evidence_files}
            if _connect_approval_message_matches(text, brand_name):
                evidence_files.extend(_save_evidence(page, evidence_dir, "connect_brand_approved"))
                return {
                    "result": "approved",
                    "reason": (
                        f"Connect brand 明确显示 You are approved to list {brand_name} products"
                    ),
                    "matched_keyword": candidate.get("matched_keyword") or "",
                    "evidence_files": evidence_files,
                }
            failure = next(
                (marker for marker in _CONNECT_BRAND_FAILURE_MARKERS if marker in text.casefold()),
                "",
            )
            if failure:
                evidence_files.extend(_save_evidence(page, evidence_dir, "connect_brand_failed"))
                return {
                    "result": "failed",
                    "reason": f"Connect brand 明确未批准: {failure}",
                    "evidence_files": evidence_files,
                }
            time.sleep(0.5)

        evidence_files.extend(_save_evidence(page, evidence_dir, "connect_brand_result_unknown"))
        return {
            "result": "unknown",
            "reason": "Connect brand 已执行，但未出现对应品牌的明确批准或失败文案",
            "evidence_files": evidence_files,
        }
    except Exception as exc:
        evidence_files.extend(_save_evidence(page, evidence_dir, "connect_brand_error"))
        return {
            "result": "unknown",
            "reason": f"Connect brand 检查异常: {type(exc).__name__}: {exc}",
            "candidate": candidate_summary,
            "evidence_files": evidence_files,
        }


def verify_manage_brand(
    page,
    brand_name: str,
    sellercentral_home_url: str,
    config: dict[str, Any],
    evidence_dir: Path,
    *,
    page_timeout_ms: int,
) -> dict[str, Any]:
    """Verify that an exact brand is visible on Manage Your Brands."""
    evidence_files: list[str] = []
    navigation: dict[str, Any] = {}
    try:
        configured_url = str(config.get("manage_brands_url") or "").strip()
        if configured_url:
            destination = configured_url.format(home_url=sellercentral_home_url.rstrip("/"))
            _goto(page, destination, page_timeout_ms)
            navigation = {"opened": True, "method": "configured_url"}
        else:
            _goto(page, sellercentral_home_url, page_timeout_ms)
            text = _page_text(page)
            block = _auth_block_reason(page, text)
            if block:
                evidence_files.extend(_save_evidence(page, evidence_dir, "manage_brand_blocked"))
                return {
                    "result": "blocked",
                    "reason": block,
                    "url": page.url,
                    "evidence_files": evidence_files,
                }
            navigation = _click_manage_brands_link(
                page, list(config.get("menu_labels") or []), page_timeout_ms
            )
            if not navigation.get("opened"):
                destination = _manage_brands_direct_url(sellercentral_home_url)
                _goto(page, destination, page_timeout_ms)
                navigation = {
                    **navigation,
                    "opened": True,
                    "method": "direct_url_fallback",
                    "destination": destination,
                }

        settle_seconds = max(0.0, float(config.get("settle_seconds") or 0.0))
        if settle_seconds:
            time.sleep(settle_seconds)
        text = _page_text(page)
        block = _auth_block_reason(page, text)
        evidence_files.extend(_save_evidence(page, evidence_dir, "manage_brand"))
        if block:
            return {
                "result": "blocked",
                "reason": block,
                "url": page.url,
                "navigation": navigation,
                "evidence_files": evidence_files,
            }

        page_markers = [str(item).casefold() for item in config.get("page_markers") or []]
        text_lower = text.casefold()
        page_confirmed = any(marker in text_lower for marker in page_markers)
        page_confirmed = page_confirmed or (
            bool(navigation.get("opened")) and "brand" in (page.url or "").casefold()
        )
        if not page_confirmed:
            return {
                "result": "unknown",
                "reason": "页面已打开，但无法确认这是 Manage Your Brands 品牌列表",
                "url": page.url,
                "navigation": navigation,
                "evidence_files": evidence_files,
            }
        if _brand_visible_on_manage_page(page, brand_name):
            return {
                "result": MANAGE_FOUND,
                "reason": f"Manage Your Brands 中找到品牌 {brand_name}",
                "url": page.url,
                "navigation": navigation,
                "evidence_files": evidence_files,
            }
        if any(marker in text_lower for marker in ("loading...", "please wait")):
            return {
                "result": "unknown",
                "reason": "Manage Your Brands 仍处于加载状态，不能判定品牌不存在",
                "url": page.url,
                "navigation": navigation,
                "evidence_files": evidence_files,
            }
        connect_result = verify_connect_brand_authorization(
            page,
            brand_name,
            _connect_brand_config_for_brand(
                dict(config.get("connect_brand") or {}), brand_name
            ),
            evidence_dir / "connect_brand",
        )
        evidence_files.extend(connect_result.get("evidence_files") or [])
        connect_outcome = str(connect_result.get("result") or "unknown")
        if connect_outcome == "approved":
            manage_outcome = MANAGE_CONNECT_APPROVED
        elif connect_outcome == "failed":
            manage_outcome = MANAGE_CONNECT_FAILED
        elif connect_outcome == "blocked":
            manage_outcome = "blocked"
        else:
            manage_outcome = MANAGE_CONNECT_UNKNOWN
        return {
            "result": manage_outcome,
            "reason": (
                f"Manage Your Brands 未显示品牌 {brand_name}；"
                f"Connect brand: {connect_result.get('reason') or connect_outcome}"
            ),
            "url": page.url,
            "navigation": navigation,
            "connect_brand": connect_result,
            "evidence_files": evidence_files,
        }
    except Exception as exc:
        evidence_files.extend(_save_evidence(page, evidence_dir, "manage_brand_error"))
        return {
            "result": "unknown",
            "reason": f"Manage Your Brands 检查异常: {type(exc).__name__}: {exc}",
            "url": getattr(page, "url", "") or "",
            "navigation": navigation,
            "evidence_files": evidence_files,
        }


def _find_add_product_action(page, allow_new_ui_submit: bool) -> dict[str, Any]:
    return page.evaluate(
        r"""allowNewUiSubmit => {
          const selectors = [
            'kat-button[data-testid="continue-button"]',
            'kat-button#continue-to-description-button',
            'kat-button#next-button',
            'kat-button[kat-aria-label="Next"]',
            'kat-button[kat-aria-label="Continue to Description"]',
            'button#next-button'
          ];
          if (allowNewUiSubmit && location.href.includes('interactive/listing/workflow/create')) {
            selectors.push('kat-button#form-submit-button[data-testid="submit-button"]');
          }
          let button = null;
          let selector = '';
          for (const candidate of selectors) {
            button = document.querySelector(candidate);
            if (button) { selector = candidate; break; }
          }
          if (!button) {
            button = Array.from(document.querySelectorAll('kat-button,button')).find(node => {
              const text = (node.textContent || node.getAttribute('label') || '').trim();
              return /^(continue|next|continue to description)$/i.test(text);
            });
            if (button) selector = 'text-fallback';
          }
          if (!button) return {found: false, clicked: false};
          const inner = button.shadowRoot ? button.shadowRoot.querySelector('button') : null;
          const attr = button.getAttribute('disabled');
          const disabled = attr === '' || attr === 'true' || button.disabled === true ||
            button.getAttribute('state') === 'disabled' || button.getAttribute('aria-disabled') === 'true' ||
            (inner && (inner.disabled || inner.hasAttribute('disabled')));
          const visible = button.offsetParent !== null;
          if (disabled || !visible) {
            return {
              found: true, clicked: false, disabled, visible, selector,
              text: (button.textContent || button.getAttribute('label') || '').trim()
            };
          }
          button.click();
          return {
            found: true, clicked: true, disabled: false, visible: true, selector,
            text: (button.textContent || button.getAttribute('label') || '').trim()
          };
        }""",
        bool(allow_new_ui_submit),
    )


def _is_description_url(url: str) -> bool:
    value = (url or "").casefold()
    return bool(re.search(r"(?:/|#)description(?:[/?#]|$)", value))


def verify_add_product(
    page,
    brand_name: str,
    entry_url: str,
    item_type_hint: str,
    config: dict[str, Any],
    evidence_dir: Path,
    *,
    page_timeout_ms: int,
) -> dict[str, Any]:
    """Fill Product Identity and verify that its validation CTA reaches Description."""
    evidence_files: list[str] = []
    fill_results: dict[str, bool] = {}
    ready_results: dict[str, bool] = {}
    action: dict[str, Any] = {}
    try:
        if not entry_url:
            return {
                "result": "unknown",
                "reason": "账号站点配置缺少 Add Product entry_url",
                "evidence_files": [],
            }
        _goto(page, entry_url, page_timeout_ms)
        time.sleep(2)
        text = _page_text(page)
        block = _auth_block_reason(page, text)
        if block:
            evidence_files.extend(_save_evidence(page, evidence_dir, "add_product_blocked"))
            return {
                "result": "blocked",
                "reason": block,
                "url": page.url,
                "evidence_files": evidence_files,
            }

        filler = KatalFormFiller(page)
        item_name = str(config.get("item_name_template") or "{brand} Screen Protector").format(
            brand=brand_name
        )
        fill_results = filler.fill_product_identity_form(
            brand_name,
            item_name=item_name,
            item_type_hint=item_type_hint,
        )
        ready_results = filler.ensure_add_product_ready_for_continue(
            filler.detect_add_product_ui(), item_type_hint=item_type_hint
        )
        evidence_files.extend(_save_evidence(page, evidence_dir, "add_product_filled"))

        core_fields_ready = bool(fill_results.get("brand_name")) and bool(
            fill_results.get("item_name")
        )
        action = _find_add_product_action(
            page, bool(config.get("allow_new_ui_submit_as_continue", True))
        )
        if not action.get("found"):
            return {
                "result": "unknown",
                "reason": "Add Product 表单填写后未找到 Continue/Next 验证按钮",
                "url": page.url,
                "fill_results": fill_results,
                "ready_results": ready_results,
                "action": action,
                "evidence_files": evidence_files,
            }
        if action.get("disabled"):
            result = ADD_PRODUCT_FAIL if core_fields_ready else "unknown"
            reason = (
                "Add Product 必要字段已填写，但 Continue/Next 仍不可用"
                if result == ADD_PRODUCT_FAIL
                else "Add Product 表单字段未能可靠填写，不能把按钮不可用判定为假过"
            )
            return {
                "result": result,
                "reason": reason,
                "url": page.url,
                "fill_results": fill_results,
                "ready_results": ready_results,
                "action": action,
                "evidence_files": evidence_files,
            }
        if not action.get("clicked"):
            return {
                "result": "unknown",
                "reason": "找到 Add Product Continue/Next，但点击未生效",
                "url": page.url,
                "fill_results": fill_results,
                "ready_results": ready_results,
                "action": action,
                "evidence_files": evidence_files,
            }

        transition_timeout_ms = int(config.get("transition_timeout_ms") or 15_000)
        try:
            page.wait_for_function(
                r"""() => {
                  const url = location.href.toLowerCase();
                  const text = (document.body ? document.body.innerText : '').toLowerCase();
                  return /(?:\/|#)description(?:[/?#]|$)/.test(url) ||
                    url.includes('seller-qualification') ||
                    text.includes('brand authorization required') ||
                    text.includes('brand authorisation required') ||
                    text.includes('you need approval to list this product') ||
                    text.includes('application required') ||
                    text.includes('clarify which brand') ||
                    document.querySelector('kat-panel-wrapper[panel-visible="true"]') !== null;
                }""",
                timeout=transition_timeout_ms,
            )
        except Exception:
            pass
        time.sleep(1)
        text = _page_text(page)
        text_lower = text.casefold()
        evidence_files.extend(_save_evidence(page, evidence_dir, "add_product_after_continue"))
        block = _auth_block_reason(page, text)
        if block:
            return {
                "result": "blocked",
                "reason": block,
                "url": page.url,
                "fill_results": fill_results,
                "ready_results": ready_results,
                "action": action,
                "evidence_files": evidence_files,
            }
        restriction = next(
            (marker for marker in _ADD_PRODUCT_RESTRICTION_MARKERS if marker in text_lower),
            "",
        )
        brand_selection = next(
            (
                marker
                for marker in _ADD_PRODUCT_BRAND_SELECTION_MARKERS
                if marker in text_lower
            ),
            "",
        )
        if _is_description_url(page.url) and not restriction:
            return {
                "result": ADD_PRODUCT_PASS,
                "reason": "Add Product 已从 Product Identity 进入 Description",
                "url": page.url,
                "fill_results": fill_results,
                "ready_results": ready_results,
                "action": action,
                "evidence_files": evidence_files,
            }
        if (
            restriction
            or (brand_selection and not _is_description_url(page.url))
            or "seller-qualification" in (page.url or "").casefold()
        ):
            detail = restriction or brand_selection or "seller-qualification"
            return {
                "result": ADD_PRODUCT_FAIL,
                "reason": f"Add Product 未进入可用的 Description，检测到授权限制: {detail}",
                "url": page.url,
                "fill_results": fill_results,
                "ready_results": ready_results,
                "action": action,
                "evidence_files": evidence_files,
            }
        return {
            "result": "unknown",
            "reason": "Continue/Next 已点击，但页面既未进入 Description，也未出现明确授权失败",
            "url": page.url,
            "fill_results": fill_results,
            "ready_results": ready_results,
            "action": action,
            "evidence_files": evidence_files,
        }
    except Exception as exc:
        evidence_files.extend(_save_evidence(page, evidence_dir, "add_product_error"))
        return {
            "result": "unknown",
            "reason": f"Add Product 检查异常: {type(exc).__name__}: {exc}",
            "url": getattr(page, "url", "") or "",
            "fill_results": fill_results,
            "ready_results": ready_results,
            "action": action,
            "evidence_files": evidence_files,
        }


def combine_approved_verification(
    manage_brand: dict[str, Any],
    add_product: dict[str, Any],
    *,
    brand_name: str = "",
    add_product_only_brands: list[str] | None = None,
) -> dict[str, Any]:
    """Combine checks without treating a stale brand portfolio as a failure."""
    manage_result = str(manage_brand.get("result") or "unknown")
    add_result = str(add_product.get("result") or "unknown")
    reason = (
        f"Manage Brand: {manage_brand.get('reason') or manage_result}; "
        f"Add Product: {add_product.get('reason') or add_result}"
    )
    normalized_brand = _normalize_text(brand_name)
    add_product_only = normalized_brand and normalized_brand in {
        _normalize_text(value) for value in (add_product_only_brands or [])
    }
    if add_product_only and add_result == ADD_PRODUCT_PASS:
        return {
            "result": "approved",
            "is_success": True,
            "reason": (
                f"{reason}; 品牌例外规则: {brand_name} 以 Add Product "
                "进入 Description 为通过依据"
            ),
        }

    # The explicit Connect brand panel is decisive when the portfolio omitted
    # the brand.  It intentionally overrides any earlier Add Product result.
    if manage_result == MANAGE_CONNECT_APPROVED:
        return {"result": "approved", "is_success": True, "reason": reason}
    if manage_result == MANAGE_CONNECT_FAILED:
        return {"result": "false_approved", "is_success": False, "reason": reason}

    # A missing/stale portfolio without a completed Connect brand probe cannot
    # establish a fake approval, even if Add Product produced a conflicting UI.
    if manage_result in {MANAGE_NOT_FOUND, MANAGE_CONNECT_UNKNOWN}:
        return {"result": "verification_pending", "is_success": None, "reason": reason}

    # Outside the stale-portfolio branch, an explicit Add Product restriction
    # remains a real business failure.
    if add_result == ADD_PRODUCT_FAIL:
        return {"result": "false_approved", "is_success": False, "reason": reason}
    if manage_result == "blocked" or add_result == "blocked":
        return {"result": "blocked", "is_success": None, "reason": reason}
    if manage_result == MANAGE_FOUND and add_result == ADD_PRODUCT_PASS:
        return {"result": "approved", "is_success": True, "reason": reason}
    return {"result": "verification_pending", "is_success": None, "reason": reason}


def verify_approved_case(
    case_page,
    account: dict[str, Any],
    site: str,
    brand_name: str,
    sellercentral_home_url: str,
    settings: dict[str, Any],
    evidence_dir: Path,
) -> dict[str, Any]:
    """Run both approval checks in new tabs of the already selected marketplace."""
    config = get_approved_verification_config(settings)
    if not config["enabled"]:
        return {
            "result": "verification_pending",
            "is_success": None,
            "decision_reason": "Case 已批准，但批准后二次验证功能被配置为关闭",
            "manage_brand": {"result": "not_run"},
            "add_product": {"result": "not_run"},
            "evidence_files": [],
        }

    site_code = (site or "").upper()
    site_config = dict((account.get("marketplace_configs") or {}).get(site_code) or {})
    entry_url = str(site_config.get("entry_url") or account.get("entry_url") or "")
    item_type_hint = str(
        site_config.get("item_type_keyword")
        or account.get("item_type_keyword")
        or "cell-phone-screen-protectors"
    )
    page_timeout_ms = int((settings.get("browser") or {}).get("page_timeout_ms") or 45_000)
    manage_dir = evidence_dir / "approved_verification" / "manage_brand"
    add_dir = evidence_dir / "approved_verification" / "add_product"
    manage_page = None
    add_page = None
    try:
        manage_page = case_page.context.new_page()
        manage_page.set_default_timeout(
            int((settings.get("browser") or {}).get("action_timeout_ms") or 20_000)
        )
        manage_result = verify_manage_brand(
            manage_page,
            brand_name,
            sellercentral_home_url,
            config["manage_brand"],
            manage_dir,
            page_timeout_ms=page_timeout_ms,
        )
    except Exception as exc:
        manage_result = {
            "result": "unknown",
            "reason": f"Manage Brand 标签页创建/检查异常: {type(exc).__name__}: {exc}",
            "evidence_files": [],
        }
    finally:
        if manage_page is not None:
            try:
                manage_page.close()
            except Exception:
                pass

    try:
        add_page = case_page.context.new_page()
        add_page.set_default_timeout(
            int((settings.get("browser") or {}).get("action_timeout_ms") or 20_000)
        )
        add_result = verify_add_product(
            add_page,
            brand_name,
            entry_url,
            item_type_hint,
            config["add_product"],
            add_dir,
            page_timeout_ms=page_timeout_ms,
        )
    except Exception as exc:
        add_result = {
            "result": "unknown",
            "reason": f"Add Product 标签页创建/检查异常: {type(exc).__name__}: {exc}",
            "evidence_files": [],
        }
    finally:
        if add_page is not None:
            try:
                add_page.close()
            except Exception:
                pass

    combined = combine_approved_verification(
        manage_result,
        add_result,
        brand_name=brand_name,
        add_product_only_brands=config["add_product_only_brands"],
    )
    evidence_files = list(manage_result.get("evidence_files") or []) + list(
        add_result.get("evidence_files") or []
    )
    result = {
        "result": combined["result"],
        "is_success": combined["is_success"],
        "decision_reason": combined["reason"],
        "manage_brand": manage_result,
        "add_product": add_result,
        "evidence_files": evidence_files,
    }
    evidence_dir.mkdir(parents=True, exist_ok=True)
    result_path = evidence_dir / "approved_verification_result.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["evidence_files"].append(str(result_path))
    return result
