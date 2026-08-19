"""
StateLoopExecutor -- core state-machine-driven execution loop.

Replaces the hard-coded _run_core() logic with a configurable state loop:
  1. Navigate to entry_url
  2. Loop (max MAX_STEPS):
     a. Capture evidence (PageCapture)
     b. Match state (StateMachine)
     c. Execute action (ActionRunner / LegacyBridge)
     d. Verify state advanced
     e. If stuck, archive error sample and report
  3. Terminate on terminal conditions
"""

import json
import os
import random
import shutil
import time
from datetime import datetime
from pathlib import Path

from ..capture.page_capture import PageCapture
from ..codex_signal import write_pending_signal
from ..knowledge.selector_registry import SelectorRegistry
from ..knowledge.state_machine import StateMachine
from .action_runner import ActionRunner
from .legacy_bridge import LegacyBridge

# ---------------------------------------------------------------------------
# State -> Action mapping
# ---------------------------------------------------------------------------
# (action_type, action_arg, expected_next_state)
STATE_ACTIONS = {
    # Add-product flow
    "add_product_start": ("legacy", "fill_product_identity", "product_identity_filled"),
    "product_identity_filled": ("legacy", "click_next_and_detect", "5461_triggered"),
    "5461_triggered": ("legacy", "click_apply_to_sell", "form_loading"),
    "gtin_exemption": ("legacy", "click_apply_to_sell", "form_loading"),
    "connect_brand": ("legacy", "connect_brand", "form_loading"),
    "application_type_selection": ("legacy", "select_create_new_asins", "form_loading"),
    "application_sell_only": ("stop", "human_review_sell_products_only", None),
    "already_approved": ("done", None, "done"),
    "declined_case_shown": ("legacy", "handle_declined", "form_loading"),
    # 5461 form flow
    "form_loading": ("wait", 5, "form_ready"),
    "form_ready": ("legacy", "fill_5461_form", "case_created"),
    "form_filled": ("legacy", "fill_5461_form", "case_created"),
    "case_created": ("done", None, "done"),
    # Terminal / error
    "login_expired": ("stop", "login_required", None),
    "error_410001": ("retry", {"wait": 30, "max": 3}, None),
    "error_429": ("retry", {"wait": 60, "max": 2}, None),
    "blank_page": ("retry", {"wait": 10, "max": 3}, None),
    "server_error": ("retry", {"wait": 60, "max": 2}, None),
    "server_error_on_submit": ("retry", {"wait": 60, "max": 2}, None),
    "unknown": ("wait", 3, None),
}


# 映射 check_page_state() 的 page_type 到状态机状态名
PAGE_TYPE_TO_STATE = {
    "PRODUCT_IDENTITY": "add_product_start",
    "PRODUCT_IDENTITY_NEW_UI": "add_product_start",
    "GTIN_EXEMPTION": "gtin_exemption",
    "AUTH_REQUIRED": "5461_triggered",
    "NEEDS_APPROVAL_NEW_UI": "5461_triggered",
    "APPLICATION_TYPE_SELECTION": "application_type_selection",
    "APPLICATION_SELL_ONLY": "application_sell_only",
    "APPLICATION_TYPE_UNKNOWN": "unknown",
    # 5461_FORM_OPEN 只表示右侧 panel 壳子打开；字段未加载时仍应停在 form_loading，
    # 由 _match_state() 用 _probe_5461_form() 动态判定，避免空壳 panel 被误判为可填表。
    "5461_FORM": "form_ready",
    "BRAND_SELECTION": "connect_brand",
    "DESCRIPTION": "already_approved",
    "DESCRIPTION_NEEDS_AUTH": "already_approved",
    "BRAND_BLOCKED": "server_error",
    # UNKNOWN 和 None 回落 StateMachine
}

# old flow 失败后，这些错误类型适合升级到 state loop
STATE_LOOP_WORTHY_ERRORS = {
    "stuck_at_unknown",
    "stuck_at_UNKNOWN",
    "unexpected_page",
    "no_action_mapping",
    "max_steps_exceeded",
}


class StateLoopExecutor:
    """
    State-loop executor for Amazon 5461 submission automation.

    Usage:
        executor = StateLoopExecutor(page, brand_data, evidence_root="runtime/evidence")
        result = executor.run()
        # -> {"result": "success", "case_id": "...", "steps": [...], "error": ""}
    """

    MAX_STEPS = 25
    MAX_RETRIES_PER_STATE = 3

    def __init__(self, page, brand_data: dict, evidence_root: str = "runtime/evidence"):
        self.page = page
        self.brand_data = brand_data
        self.evidence_root = Path(evidence_root)
        account_id = self.brand_data.get("account_id", "unknown")
        brand_name = self.brand_data.get("brand_name", "unknown")
        run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._run_evidence_dir = self.evidence_root / f"{account_id}_{brand_name}_{run_timestamp}"

        # Sub-components
        project_root = Path(__file__).parent.parent.parent
        self.project_root = project_root
        self.sm_add_product = StateMachine(str(project_root / "knowledge" / "pages" / "add-product" / "states.md"))
        self.sm_5461_form = StateMachine(str(project_root / "knowledge" / "pages" / "5461-application" / "states.md"))
        self.registry = SelectorRegistry(str(project_root / "knowledge" / "pages" / "add-product" / "selectors.json"))
        self.capture_inst = PageCapture(page)
        self.runner = ActionRunner(page, self.registry, evidence_dir=str(self._evidence_dir()))
        self.bridge = LegacyBridge(page, brand_data, str(self._evidence_dir()))

        self.history: list[dict] = []
        self._retry_counts: dict[str, int] = {}
        self._auth_state = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> dict:
        """
        Execute the full state loop.

        Returns:
            {
                "result": "success" | "failed" | "stopped",
                "case_id": str | None,
                "steps": list,
                "error": str,
            }
        """
        print("=" * 70)
        print("[INFO] StateLoopExecutor started")
        print(f"[INFO] brand: {self.brand_data.get('brand_name', 'N/A')}")
        print("=" * 70)

        if not self._navigate_to_entry():
            return self._build_result("failed", error="navigation_failed")

        prev_state = None
        prev_state_count = 0
        last_evidence: dict = {}

        for step in range(1, self.MAX_STEPS + 1):
            print(f"\n[INFO] --- Step {step}/{self.MAX_STEPS} ---")

            # a) Capture evidence
            evidence = self._capture(f"step_{step:02d}")
            last_evidence = evidence

            # b) Match state
            state = self._match_state(evidence)
            print(f"[INFO] matched state: {state}")

            # c) Record history
            self.history.append(
                {
                    "step": step,
                    "state": state,
                    "timestamp": datetime.now().isoformat(),
                }
            )

            if state == "auth_blocked":
                stop_status = self._persist_auth_block()
                self.history[-1]["action"] = "auth_block"
                self.history[-1]["action_result"] = False
                return self._build_result("stopped", error=stop_status)

            # d) Detect stuck
            # Skip stuck check for retry states — they manage their own exhaust logic
            action_type = STATE_ACTIONS.get(state, ("wait", 3, None))[0]
            if state == prev_state and action_type != "retry":
                prev_state_count += 1
                if prev_state_count >= self.MAX_RETRIES_PER_STATE:
                    print(f"[WARN] state '{state}' stuck for {prev_state_count} steps")
                    if not self._handle_stuck(state, step, evidence):
                        return self._build_result("failed", error=f"stuck_at_{state}")
            elif state != prev_state:
                prev_state = state
                prev_state_count = 1

            # e) Execute action
            action_result = self._execute(state, evidence)
            self.history[-1]["action"] = action_result.get("action")
            self.history[-1]["action_result"] = action_result.get("ok")

            # f) Check terminal
            if action_result.get("done"):
                case_id = action_result.get("case_id")
                if action_result.get("stopped"):
                    return self._build_result("stopped", case_id=case_id, error=action_result.get("error", ""))
                return self._build_result("success", case_id=case_id)

            # g) Small safety sleep between steps
            time.sleep(1)

        self._emit_codex_signal("max_steps_exceeded", self.MAX_STEPS, last_evidence)
        return self._build_result("failed", error="max_steps_exceeded")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _evidence_dir(self) -> Path:
        """Return the stable evidence directory for this executor run."""
        return self._run_evidence_dir

    def _navigate_to_entry(self) -> bool:
        """Navigate to entry_url, handling account-switcher trap."""
        entry_url = self.brand_data.get("entry_url", "")
        if not entry_url:
            print("[NG] no entry_url in brand_data")
            return False
        return self.runner.navigate(entry_url, timeout=60000)

    def _capture(self, name: str) -> dict:
        """Capture evidence; return minimal dict on failure."""
        out_dir = self._evidence_dir()
        try:
            evidence = self.capture_inst.capture(name, out_dir)
            return {
                "url": evidence.url,
                "title": evidence.title,
                "body_text": evidence.body_text,
                "body_length": evidence.body_length,
                "alerts": evidence.alerts,
                "interactives": evidence.interactives,
                "detected_states": evidence.detected_states,
                "screenshot_path": evidence.screenshot_path,
                "timestamp": evidence.timestamp,
            }
        except Exception as e:
            print(f"[WARN] capture failed: {e}")
            # Minimal fallback evidence
            try:
                return {
                    "url": self.page.url,
                    "title": "",
                    "body_text": "",
                    "body_length": 0,
                    "alerts": [],
                    "interactives": [],
                    "detected_states": [],
                    "screenshot_path": None,
                    "timestamp": datetime.now().isoformat(),
                }
            except Exception:
                return {
                    "url": "",
                    "title": "",
                    "body_text": "",
                    "body_length": 0,
                    "alerts": [],
                    "interactives": [],
                    "detected_states": [],
                    "screenshot_path": None,
                    "timestamp": datetime.now().isoformat(),
                }

    def _match_state(self, evidence: dict) -> str:
        """
        Match page state using check_page_state() first (battle-tested, handles
        Amazon SPA where URL doesn't change after Next), then fall back to StateMachine.
        """
        from ..auth_guard import detect_auth_state

        auth_state = detect_auth_state(self.page)
        if auth_state.blocked:
            self._auth_state = auth_state
            return "auth_blocked"

        # Primary: check_page_state() - accurate for all Amazon SPA transitions
        try:
            from ..flow_submit_5461 import check_page_state

            ps = check_page_state(self.page)
            page_type = ps.get("page_type", "UNKNOWN")
            if page_type == "5461_FORM_OPEN":
                probe = self._probe_5461_form()
                if probe.get("fields_ready"):
                    print(f"[INFO] page_type=5461_FORM_OPEN + fields_ready -> state=form_ready ({probe})")
                    return "form_ready"
                print(f"[INFO] page_type=5461_FORM_OPEN but fields not ready -> state=form_loading ({probe})")
                return "form_loading"
            mapped = PAGE_TYPE_TO_STATE.get(page_type)
            if mapped:
                print(f"[INFO] page_type={page_type} -> state={mapped}")
                return mapped
            # UNKNOWN: fall through
        except Exception as e:
            print(f"[WARN] check_page_state failed: {e}")

        # Fallback: StateMachine (URL/text based)
        state = self.sm_add_product.match(evidence)
        if state:
            return state
        state = self.sm_5461_form.match(evidence)
        if state:
            return state
        return "unknown"

    def _persist_auth_block(self) -> str:
        from ..auth_recovery import (
            capture_auth_evidence,
            create_auth_block,
            ensure_submission_checkpoint,
        )

        brand_name = str(self.brand_data.get("brand_name") or "")
        account_id = str(self.brand_data.get("account_id") or "")
        marketplace = str(self.brand_data.get("marketplace") or "")
        db_path = str(
            self.brand_data.get("checkpoint_db_path")
            or os.getenv("AMAZON5461_DB_PATH")
            or self.project_root / "runtime" / "state" / "ledger.db"
        )
        owner_type = str(self.brand_data.get("submission_owner_type") or "direct_run")
        owner_id = str(
            self.brand_data.get("submission_owner_id")
            or os.getenv("AMAZON5461_RUN_ID")
            or f"state-loop:{account_id}:{brand_name}"
        )
        checkpoint = ensure_submission_checkpoint(
            db_path,
            owner_type=owner_type,
            owner_id=owner_id,
            account_id=account_id,
            marketplace=marketplace,
            brand_name=brand_name,
            phase="state_loop_auth",
        )
        fenced = bool(checkpoint.get("submit_click_fenced_at"))
        auth_settings = {
            "paths": {
                "db_path": db_path,
                "evidence_root": str(self.evidence_root),
            },
            "auth_recovery": {"poll_interval_seconds": 300},
        }
        evidence_path = capture_auth_evidence(
            self.page,
            auth_settings,
            account_id=account_id,
            phase="state_loop_auth",
        )
        auth_block = create_auth_block(
            auth_settings,
            account_id=account_id,
            marketplace=marketplace,
            brand_name=brand_name,
            block_type=str(getattr(self._auth_state, "state", "unknown_auth_state")),
            phase="state_loop_auth",
            source_type=owner_type,
            source_id=owner_id,
            submit_fenced=fenced,
            evidence_path=evidence_path,
            detail=str(getattr(self._auth_state, "reason", "Authentication required")),
            checkpoint_id=int(checkpoint["id"]),
        )
        self.history[-1]["auth_block_id"] = int(auth_block["id"])
        return "waiting_reconciliation" if fenced else "waiting_login"

    def _execute(self, state: str, evidence: dict) -> dict:
        """
        Execute the action mapped to the current state.

        Returns dict with keys:
            ok: bool
            done: bool   -- True if terminal (success/stop)
            stopped: bool -- True if stopped (not success)
            case_id: str | None
            error: str
            action: str
            next_expected: str | None
        """
        mapping = STATE_ACTIONS.get(state)
        if not mapping:
            print(f"[WARN] no action mapping for state '{state}'")
            return {
                "ok": False,
                "done": False,
                "stopped": False,
                "case_id": None,
                "error": "",
                "action": "noop",
                "next_expected": None,
            }

        action_type, action_arg, expected_next = mapping

        # -- done --------------------------------------------------------
        if action_type == "done":
            print(f"[INFO] terminal state '{state}' reached")
            # Try to extract case_id if available
            case_id = None
            try:
                cid_result = self.bridge.call("extract_case_id")
                if cid_result.get("ok"):
                    case_id = cid_result.get("result", {}).get("case_id")
            except Exception:
                pass
            return {
                "ok": True,
                "done": True,
                "stopped": False,
                "case_id": case_id,
                "error": "",
                "action": "done",
                "next_expected": expected_next,
            }

        # -- stop --------------------------------------------------------
        if action_type == "stop":
            print(f"[INFO] stop state '{state}' reached: {action_arg}")
            return {
                "ok": False,
                "done": True,
                "stopped": True,
                "case_id": None,
                "error": str(action_arg),
                "action": "stop",
                "next_expected": expected_next,
            }

        # -- wait --------------------------------------------------------
        if action_type == "wait":
            seconds = action_arg if isinstance(action_arg, (int, float)) else 3
            self.runner.wait_seconds(seconds)
            return {
                "ok": True,
                "done": False,
                "stopped": False,
                "case_id": None,
                "error": "",
                "action": f"wait_{seconds}s",
                "next_expected": expected_next,
            }

        # -- retry -------------------------------------------------------
        if action_type == "retry":
            cfg = action_arg if isinstance(action_arg, dict) else {"wait": 10, "max": 3}
            wait_sec = cfg.get("wait", 10)
            max_retries = cfg.get("max", 3)
            current = self._retry_counts.get(state, 0) + 1
            self._retry_counts[state] = current
            print(f"[INFO] retry state '{state}' ({current}/{max_retries}), wait {wait_sec}s")
            self.runner.wait_seconds(wait_sec)
            if current >= max_retries:
                return {
                    "ok": False,
                    "done": True,
                    "stopped": True,
                    "case_id": None,
                    "error": f"paused_{state}",
                    "action": "retry_exhausted",
                    "next_expected": expected_next,
                }
            # 410001/429 常出现在右侧表单/弹窗里；优先关闭面板再重新 Apply，
            # 不立即刷新整页，避免破坏 Add Product 当前状态或制造更多请求。
            if state in {"error_410001", "error_429"}:
                self._close_panel_wait_reapply(reason=state, attempt=current)
            elif state != "blank_page":
                try:
                    self.page.reload(wait_until="domcontentloaded", timeout=30000)
                    time.sleep(3)
                except Exception as e:
                    print(f"[WARN] reload failed: {e}")
            return {
                "ok": True,
                "done": False,
                "stopped": False,
                "case_id": None,
                "error": "",
                "action": f"retry_{current}",
                "next_expected": expected_next,
            }

        # -- legacy ------------------------------------------------------
        if action_type == "legacy":
            action_name = action_arg
            print(f"[INFO] executing legacy action: {action_name}")

            # Special handling for click_next_and_detect
            if action_name == "click_next_and_detect":
                from ..form_filler import KatalFormFiller

                filler = KatalFormFiller(self.page)
                ok = filler.click_next(check_enabled=True, timeout=10)
                # After clicking next, wait and detect what state we land in
                time.sleep(5)
                return {
                    "ok": ok,
                    "done": False,
                    "stopped": False,
                    "case_id": None,
                    "error": "",
                    "action": action_name,
                    "next_expected": expected_next,
                }

            result = self.bridge.call(action_name)
            ok = result.get("ok", False)
            print(f"[INFO] legacy action '{action_name}' ok={ok}")
            if not ok:
                err = result.get("error", "") or "legacy_action_failed"
                print(f"[WARN] legacy action '{action_name}' failed: {err}")
                return {
                    "ok": False,
                    "done": bool(result.get("stop")),
                    "stopped": bool(result.get("stop")),
                    "case_id": None,
                    "error": err,
                    "action": action_name,
                    "next_expected": expected_next,
                }

            # 对于可能导致页面跳转的 action，额外等待页面载入
            page_transition_actions = {
                "fill_product_identity",
                "click_apply_to_sell",
                "connect_brand",
                "handle_declined",
            }
            if action_name in page_transition_actions and ok:
                print(f"[INFO] waiting 8s for page transition after '{action_name}'...")
                time.sleep(8)

            # Check if we already got a case_id
            case_id = None
            if ok and isinstance(result.get("result"), dict):
                case_id = result["result"].get("case_id")

            return {
                "ok": ok,
                "done": False,
                "stopped": False,
                "case_id": case_id,
                "error": result.get("error", ""),
                "action": action_name,
                "next_expected": expected_next,
            }

        # Fallback
        return {
            "ok": False,
            "done": False,
            "stopped": False,
            "case_id": None,
            "error": "",
            "action": "unknown",
            "next_expected": None,
        }

    def _handle_stuck(self, state: str, step: int, evidence: dict) -> bool:
        """
        Called when state hasn't changed for MAX_RETRIES_PER_STATE steps.

        Archives error sample and returns False (stop execution).
        Override to implement recovery logic.
        """
        print(f"[NG] handling stuck state '{state}' at step {step}")
        self._archive_error_sample(state, evidence)

        # 右侧表单空壳/字段未加载时，执行昨天确认的恢复策略：
        # 关闭右侧 panel -> 随机等待 -> 重新 Apply to sell。
        if state in {"form_loading", "form_ready", "gtin_exemption", "5461_triggered"}:
            current = self._retry_counts.get(f"stuck_{state}", 0) + 1
            self._retry_counts[f"stuck_{state}"] = current
            if current <= 3:
                print(f"[INFO] stuck recovery for {state}: close panel + re-Apply ({current}/3)")
                self._close_panel_wait_reapply(reason=f"stuck_{state}", attempt=current)
                return True
            print(f"[WARN] stuck recovery exhausted for {state}")

        self._emit_codex_signal(f"stuck_at_{state}", step, evidence)
        return False

    def _emit_codex_signal(self, reason: str, step: int, evidence: dict) -> None:
        """Create a local audit signal only after deterministic recovery is exhausted."""
        try:
            signal_path = self.project_root / "runtime" / "codex_signal.json"
            signal = write_pending_signal(
                reason=reason,
                brand_data=self.brand_data,
                evidence=evidence,
                step=step,
                signal_path=signal_path,
                db_path=str(self.project_root / "runtime" / "state" / "ledger.db"),
            )
            print(f"[SIGNAL] Codex diagnosis pending: {signal_path} ({signal['signal_id']})")
        except Exception as exc:
            print(f"[WARN] failed to write Codex signal: {exc}")

    def _probe_5461_form(self) -> dict:
        """Return whether the 5461 form is genuinely fillable, not just a visible shell."""
        try:
            return self.page.evaluate(r"""
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
                    const rateLimitText = text.includes('429') || text.includes('too many requests') || text.includes('410001');
                    const loadingText = text.includes('loading') || text.includes('please wait');
                    return {
                        panel_found: !!panel,
                        old_fields: oldFields,
                        sq_fields: sqFields,
                        file_input: fileInput,
                        submit_button: submitButton,
                        rate_limit_text: rateLimitText,
                        loading_text: loadingText,
                        fields_ready: (oldFields >= 2 || sqFields >= 2) && !rateLimitText
                    };
                }
            """)
        except Exception as e:
            return {"fields_ready": False, "error": str(e)}

    def _close_panel_wait_reapply(self, reason: str, attempt: int) -> bool:
        """Close/hide the right-side 5461 panel, wait randomly, then click Apply to sell again."""
        try:
            close_result = self.page.evaluate(r"""
                () => {
                    let method = '';
                    const buttons = document.querySelectorAll('button, kat-button, [role="button"]');
                    for (const btn of buttons) {
                        const t = (btn.textContent || btn.getAttribute('label') || btn.getAttribute('aria-label') || '').trim().toLowerCase();
                        if (t === 'close' || t.includes('close') || t === 'x' || t === '×') {
                            try { btn.click(); method = 'button'; break; } catch (_) {}
                        }
                    }
                    if (!method) {
                        const cb = document.querySelector('button[aria-label="close"], button[part="panel-close-button"]');
                        if (cb) { try { cb.click(); method = 'aria/part'; } catch (_) {} }
                    }
                    document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', keyCode: 27, bubbles: true}));
                    const panels = document.querySelectorAll('kat-panel-wrapper');
                    panels.forEach(p => {
                        p.removeAttribute('panel-visible');
                        p.setAttribute('panel-visible', 'false');
                        p.style.display = 'none';
                        p.style.visibility = 'hidden';
                    });
                    return {closed: true, method: method || 'force-hide'};
                }
            """)
            print(f"[INFO] closed 5461 panel for {reason}: {close_result}")
        except Exception as e:
            print(f"[WARN] close panel failed for {reason}: {e}")

        wait_sec = random.randint(18, 45)
        print(f"[INFO] waiting {wait_sec}s before re-Apply ({reason}, attempt {attempt})")
        time.sleep(wait_sec)
        try:
            from ..form_filler import KatalFormFiller

            ok = KatalFormFiller(self.page).click_apply_to_sell()
            print(f"[INFO] re-Apply result after {reason}: {ok}")
            return bool(ok)
        except Exception as e:
            print(f"[WARN] re-Apply failed after {reason}: {e}")
            return False

    def _archive_error_sample(self, state: str, evidence: dict):
        """Copy a private error sample under the Git-ignored runtime tree."""
        try:
            page_kind = "5461-application" if state in self.sm_5461_form.list_states() else "add-product"
            samples_dir = self.project_root / "runtime" / "evidence" / "error-samples" / page_kind / state
            samples_dir.mkdir(parents=True, exist_ok=True)

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            dest_dir = samples_dir / ts
            evidence_dir = self._evidence_dir()
            if evidence_dir.exists():
                shutil.copytree(evidence_dir, dest_dir, dirs_exist_ok=True)
                print(f"[INFO] archived private error sample to {dest_dir}")

            # Save only a minimal summary alongside the ignored evidence.
            summary = {
                "state": state,
                "page_kind": page_kind,
                "timestamp": datetime.now().isoformat(),
                "brand_data": {
                    "account_id": self.brand_data.get("account_id"),
                    "brand_name": self.brand_data.get("brand_name"),
                },
                "evidence": {
                    "url": evidence.get("url", ""),
                    "title": evidence.get("title", ""),
                    "body_length": evidence.get("body_length", 0),
                    "detected_states": evidence.get("detected_states", []),
                },
            }
            summary_path = samples_dir / f"{ts}_summary.json"
            with open(summary_path, "w", encoding="utf-8") as f:
                json.dump(summary, f, ensure_ascii=False, indent=2)

        except Exception as e:
            print(f"[WARN] archive_error_sample failed: {e}")

    def _build_result(self, result: str, case_id: str = None, error: str = "") -> dict:
        """Build final result dict."""
        return {
            "result": result,
            "case_id": case_id,
            "steps": self.history,
            "error": error,
        }
