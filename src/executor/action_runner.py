"""
ActionRunner -- atomic page actions with selector fallback chains.

Wraps Playwright page operations and SelectorRegistry fallback logic
into reusable, log-friendly methods.
"""

import time
from pathlib import Path
from typing import Optional

from ..knowledge.selector_registry import SelectorRegistry


class ActionRunner:
    """
    Atomic action runner for Playwright page operations.

    Usage:
        runner = ActionRunner(page, registry, evidence_dir="runtime/evidence/001")
        runner.click("submit_button")
        runner.fill("brand_input", "MyBrand")
        runner.navigate("https://sellercentral.amazon.com/...")
    """

    def __init__(self, page, registry: SelectorRegistry, evidence_dir: str = None):
        self.page = page
        self.registry = registry
        self.evidence_dir = Path(evidence_dir) if evidence_dir else None

    # ------------------------------------------------------------------
    # Core actions
    # ------------------------------------------------------------------

    def click(self, selector_name: str) -> bool:
        """
        Click element by selector name, trying registry fallback chain.

        Returns True if any selector succeeded, False otherwise.
        On success, marks the selector as verified in the registry.
        """
        selectors = self.registry.get_ordered_selectors(selector_name)
        if not selectors:
            print(f"[WARN] ActionRunner.click: no selectors for '{selector_name}'")
            return False

        for sel in selectors:
            try:
                self.page.wait_for_selector(sel, timeout=3000)
                self.page.click(sel)
                # Mark verified (site is unknown here; use empty string)
                try:
                    self.registry.mark_verified(selector_name, site="")
                except Exception:
                    pass
                print(f"[OK] click '{selector_name}' via {sel[:60]}")
                return True
            except Exception as e:
                print(f"[NG] click '{selector_name}' failed for {sel[:60]}: {e}")
                continue

        print(f"[NG] click '{selector_name}': all selectors exhausted")
        return False

    def fill(self, selector_name: str, value: str) -> bool:
        """
        Fill input/textarea by selector name, using fill_katal_input for Shadow DOM.

        Returns True if any selector succeeded, False otherwise.
        """
        # Import here to avoid circular dependency at module load time
        from ..flow_submit_5461 import fill_katal_input

        selectors = self.registry.get_ordered_selectors(selector_name)
        if not selectors:
            print(f"[WARN] ActionRunner.fill: no selectors for '{selector_name}'")
            return False

        for sel in selectors:
            try:
                # Use the existing Katal-aware fill function
                if fill_katal_input(self.page, sel, value):
                    try:
                        self.registry.mark_verified(selector_name, site="")
                    except Exception:
                        pass
                    print(f"[OK] fill '{selector_name}' via {sel[:60]}")
                    return True
            except Exception as e:
                print(f"[NG] fill '{selector_name}' failed for {sel[:60]}: {e}")
                continue

        print(f"[NG] fill '{selector_name}': all selectors exhausted")
        return False

    def wait_seconds(self, seconds: float):
        """Sleep with a log line."""
        print(f"[INFO] waiting {seconds}s ...")
        time.sleep(seconds)

    def navigate(self, url: str, timeout: int = 60000) -> bool:
        """
        Navigate to URL. If stuck on account-switcher, goto about:blank first.

        Returns True on success, False on failure.
        """
        try:
            # Handle account-switcher trap
            current_url = self.page.url
            if "account-switcher" in current_url or "merchantMarketplace" in current_url:
                print("[INFO] detected account-switcher, resetting to about:blank first")
                self.page.goto("about:blank", wait_until="commit", timeout=8000)
                time.sleep(2)

            self.page.goto(url, wait_until="commit", timeout=timeout)
            self.page.wait_for_load_state("domcontentloaded", timeout=30000)
            print(f"[OK] navigated to {url[:120]}")
            return True
        except Exception as e:
            print(f"[NG] navigate failed: {e}")
            return False

    def take_screenshot(self, name: str) -> Optional[str]:
        """
        Save screenshot to evidence_dir/{name}.png.

        Returns path on success, None on failure (does not raise).
        """
        if not self.evidence_dir:
            return None
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        path = self.evidence_dir / f"{name}.png"
        try:
            self.page.screenshot(path=str(path), full_page=False)
            print(f"[OK] screenshot saved: {path}")
            return str(path)
        except Exception as e:
            print(f"[WARN] screenshot failed: {e}")
            return None
