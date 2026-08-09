"""Human-like Playwright interaction helpers.

These helpers operate inside Playwright/browser context only. They do not move the
real OS mouse or steal the user's physical keyboard.
"""

from __future__ import annotations

import random
import time
from typing import Any

from playwright.sync_api import Locator, Page


def human_delay(min_sec: float = 0.3, max_sec: float = 1.5, label: str = "") -> float:
    delay = random.uniform(min_sec, max_sec)
    if label:
        print(f"[HumanPacer] {label}: {delay:.1f}s")
    time.sleep(delay)
    return delay


def human_click(locator: Locator, label: str = "element", timeout: int = 10000, force: bool = False) -> bool:
    try:
        locator.wait_for(state="attached", timeout=timeout)
        try:
            locator.scroll_into_view_if_needed(timeout=timeout)
        except Exception:
            pass
        human_delay(0.25, 1.0, f"准备点击 {label}")
        try:
            locator.hover(timeout=min(3000, timeout))
            human_delay(0.15, 0.7, f"hover {label}")
        except Exception:
            pass
        locator.click(timeout=timeout, force=force)
        human_delay(0.6, 1.8, f"点击后等待 {label}")
        return True
    except Exception as e:
        print(f"[HumanPacer] 点击 {label} 失败: {e}")
        return False


def human_type(page: Page, locator: Locator, text: str, label: str = "field", timeout: int = 10000, clear: bool = True) -> bool:
    try:
        locator.wait_for(state="attached", timeout=timeout)
        try:
            locator.scroll_into_view_if_needed(timeout=timeout)
        except Exception:
            pass
        human_click(locator, label, timeout=timeout, force=False)
        if clear:
            page.keyboard.press("Control+A")
            human_delay(0.08, 0.25)
            page.keyboard.press("Delete")
            human_delay(0.08, 0.25)
        page.keyboard.type(text, delay=random.randint(25, 90))
        human_delay(0.25, 0.9, f"输入后等待 {label}")
        page.keyboard.press("Tab")
        human_delay(0.25, 0.8, f"blur {label}")
        return True
    except Exception as e:
        print(f"[HumanPacer] 输入 {label} 失败: {e}")
        return False


def safe_checkbox_click(page: Page, selector: str, desired: bool = True, label: str = "checkbox") -> bool:
    """Prefer locator click for visible checkbox-like controls; JS fallback is left to callers."""
    try:
        loc = page.locator(selector).first
        if loc.count() == 0:
            return False
        current = page.evaluate("""(sel) => {
            const el = document.querySelector(sel);
            if (!el) return null;
            const inner = el.shadowRoot && el.shadowRoot.querySelector('input[type="checkbox"], [role="checkbox"], [role="switch"]');
            if (inner && 'checked' in inner) return !!inner.checked;
            const aria = el.getAttribute('aria-checked') || el.getAttribute('aria-pressed');
            if (aria !== null) return aria === 'true';
            return !!el.checked || el.hasAttribute('checked');
        }""", selector)
        if current is desired:
            return True
        return human_click(loc, label=label, timeout=8000, force=False)
    except Exception as e:
        print(f"[HumanPacer] checkbox {label} 失败: {e}")
        return False
