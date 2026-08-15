"""
LegacyBridge -- wrap existing flow_submit_5461 functions into a unified call() interface.

This allows the StateLoopExecutor to invoke legacy logic without hard-coding
function names, and returns a consistent {ok, result, error} shape.
"""

import time
from pathlib import Path
from typing import Any


class LegacyBridge:
    """
    Bridge from legacy monolithic functions to unified action interface.

    Usage:
        bridge = LegacyBridge(page, brand_data, evidence_dir)
        result = bridge.call("fill_5461_form")
        # -> {"ok": True, "result": {...}, "error": ""}
    """

    ACTIONS = {
        "fill_product_identity": "_fill_product_identity",
        "click_apply_to_sell": "_click_apply_to_sell",
        "select_create_new_asins": "_select_create_new_asins",
        "connect_brand": "_connect_brand",
        "fill_5461_form": "_fill_5461_form",
        "handle_declined": "_handle_declined",
        "extract_case_id": "_extract_case_id",
    }

    def __init__(self, page, brand_data: dict, evidence_dir: str):
        self.page = page
        self.brand_data = brand_data
        self.evidence_dir = Path(evidence_dir)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)

        # Lazy imports to avoid heavy module load
        from ..flow_submit_5461 import (
            fill_5461_form,
            connect_brand_in_5461_panel,
            handle_declined_case_application,
            extract_case_id,
        )
        from ..form_filler import KatalFormFiller

        self._fill_5461_form_fn = fill_5461_form
        self._connect_brand_fn = connect_brand_in_5461_panel
        self._handle_declined_fn = handle_declined_case_application
        self._extract_case_id_fn = extract_case_id
        self._filler_cls = KatalFormFiller

        # Cached filler instance
        self._filler = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def call(self, action_name: str, **kwargs) -> dict:
        """
        Dispatch action by name.

        Returns:
            {"ok": bool, "result": any, "error": str}
        """
        method_name = self.ACTIONS.get(action_name)
        if not method_name:
            return {"ok": False, "result": None, "error": f"Unknown action: {action_name}"}

        method = getattr(self, method_name)
        try:
            result = method(**kwargs)
            ok = self._result_ok(action_name, result)
            return {"ok": ok, "result": result, "error": "" if ok else self._result_error(action_name, result)}
        except Exception as e:
            print(f"[NG] LegacyBridge.{method_name} error: {e}")
            return {"ok": False, "result": None, "error": str(e)}

    # ------------------------------------------------------------------
    # Internal action implementations
    # ------------------------------------------------------------------

    def _filler(self):
        """Lazy-init KatalFormFiller."""
        if self._filler is None:
            self._filler = self._filler_cls(self.page)
        return self._filler

    def _fill_product_identity(self) -> dict:
        """Fill Add Product identity form."""
        from ..form_filler import KatalFormFiller

        brand_name = self.brand_data.get("brand_name", "")
        item_name = self.brand_data.get("item_name", "")
        item_type_keyword = self.brand_data.get("item_type_keyword", "cell-phone-screen-protectors")

        if not item_name:
            # Extract from statement_text if available
            statement = self.brand_data.get("statement_text", "")
            item_name = self._extract_item_name(statement) or f"{brand_name} Screen Protector"

        filler = KatalFormFiller(self.page)
        results = filler.fill_product_identity_form(
            brand_name=brand_name,
            item_name=item_name,
            item_type_hint=item_type_keyword,
        )
        # Also click Next so the state advances
        next_ok = filler.click_next(check_enabled=True, timeout=10)
        return {"filled": results, "next_clicked": next_ok, "item_name": item_name}

    def _result_ok(self, action_name: str, result: Any) -> bool:
        """Translate legacy return shapes into a real success boolean."""
        if action_name in {"click_apply_to_sell", "select_create_new_asins"}:
            return bool(isinstance(result, dict) and result.get("clicked"))
        if action_name == "fill_product_identity":
            if not isinstance(result, dict):
                return False
            return bool(result.get("next_clicked"))
        if action_name == "fill_5461_form":
            if not isinstance(result, dict):
                return False
            if result.get("fields_ready") is False:
                return False
            if result.get("submit_clicked") is False:
                return False
            form_result = result.get("form_result") or {}
            if isinstance(form_result, dict) and form_result.get("status") in {"form_not_ready", "upload_failed"}:
                return False
            return True
        if isinstance(result, dict):
            if result.get("success") is False or result.get("selected") is False:
                return False
            if result.get("ok") is False:
                return False
        return True

    def _result_error(self, action_name: str, result: Any) -> str:
        if isinstance(result, dict):
            for key in ("error", "note", "status", "action"):
                if result.get(key):
                    return f"{action_name}:{result.get(key)}"
        return f"{action_name}_failed"

    def _probe_5461_form_fields(self) -> dict:
        """Check whether 5461 fields really exist before filling/submitting."""
        try:
            return self.page.evaluate(r'''
                () => {
                    const panel = document.querySelector(
                        'kat-panel-wrapper[data-testid*="QualificationWidget"], ' +
                        'kat-panel-wrapper[panel-visible="true"]'
                    );
                    const scope = panel || document;
                    const q = (sel) => !!scope.querySelector(sel);
                    const oldFields = [
                        'kat-input#question-cat_auth_mo_question_string_id_product_title',
                        'kat-input#question-cat_auth_mo_question_string_id_manufacturer',
                        'kat-input#question-cat_auth_mo_question_string_id_product_description'
                    ].filter(q).length;
                    const sqFields = [
                        '[name*="question-1"], [id*="question-1"], [name*="product_title"], [id*="product_title"]',
                        '[name*="question-2"], [id*="question-2"], [name*="manufacturer"], [id*="manufacturer"]',
                        '[name*="question-3"], [id*="question-3"], [name*="description"], [id*="description"]'
                    ].filter(q).length;
                    const fileInput = q('input[type="file"], input[id*="document_upload"][id*="document_input"]');
                    const submitButton = q('kat-button#submit_button, kat-button[label*="Submit"], button[type="submit"]');
                    const text = (scope.innerText || document.body.innerText || '').toLowerCase();
                    return {
                        panel_found: !!panel,
                        old_fields: oldFields,
                        sq_fields: sqFields,
                        file_input: fileInput,
                        submit_button: submitButton,
                        rate_limit_text: text.includes('429') || text.includes('too many requests') || text.includes('410001'),
                        fields_ready: (oldFields >= 2 || sqFields >= 2)
                    };
                }
            ''')
        except Exception as e:
            return {"fields_ready": False, "error": str(e)}

    def _click_apply_to_sell(self) -> dict:
        """Find and click Apply to sell button."""
        from ..form_filler import KatalFormFiller

        filler = KatalFormFiller(self.page)
        filler.brand_name = self.brand_data.get("brand_name", "")
        ok = filler.click_apply_to_sell()
        return {"clicked": ok}

    def _select_create_new_asins(self) -> dict:
        """Select only the exact create-new-ASINs card for the target brand."""
        filler = self._filler_cls(self.page)
        filler.brand_name = self.brand_data.get("brand_name", "")
        return filler.select_create_new_asins_application()

    def _connect_brand(self) -> dict:
        """Handle Connect brand popup in 5461 panel."""
        brand_name = self.brand_data.get("brand_name", "")
        keywords = self.brand_data.get("brand_keywords", None)
        result = self._connect_brand_fn(self.page, brand_name, keywords)
        return result

    def _fill_5461_form(self) -> dict:
        """Fill and submit the 5461 form."""
        brand_name = self.brand_data.get("brand_name", "")
        statement_text = self.brand_data.get("statement_text", "")
        upload_files = self.brand_data.get("upload_files", [])
        email = self.brand_data.get("email", "")

        probe = self._probe_5461_form_fields()
        if not probe.get("fields_ready"):
            print(f"[LegacyBridge] 5461 form fields not ready, skip fill/submit: {probe}")
            return {"status": "form_not_ready", "fields_ready": False, "probe": probe}

        # Fill the form fields
        form_result = self._fill_5461_form_fn(
            self.page, brand_name, statement_text, upload_files, email
        )

        # Click submit
        from ..flow_submit_5461 import click_katal_button
        submit_ok = click_katal_button(self.page, 'kat-button#submit_button')

        # Wait for case ID to appear
        time.sleep(12)
        case_id = self._extract_case_id_fn(self.page)

        return {
            "form_result": form_result,
            "fields_ready": True,
            "submit_clicked": submit_ok,
            "case_id": case_id,
        }

    def _handle_declined(self) -> dict:
        """Handle declined case application."""
        brand_name = self.brand_data.get("brand_name", "")
        evidence_dir = str(self.evidence_dir)
        filler = self._filler()
        result = self._handle_declined_fn(self.page, brand_name, evidence_dir, filler)
        return result

    def _extract_case_id(self) -> dict:
        """Extract Case ID from page."""
        case_id = self._extract_case_id_fn(self.page)
        return {"case_id": case_id}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_item_name(statement_text: str) -> str:
        """Extract Item name from statement text."""
        for line in statement_text.split("\n"):
            if line.startswith("Item name：") or line.startswith("Item name:"):
                return line.split("：", 1)[1].strip() if "：" in line else line.split(":", 1)[1].strip()
        return ""
