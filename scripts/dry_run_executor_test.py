#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Dry-run executor tests -- no browser / AdsPower required.

Uses Mock Page objects to test:
  1. ActionRunner (click/fill fallback chains)
  2. LegacyBridge (unified call() interface)
  3. StateLoopExecutor (full state loop scenarios)

Run:
    $env:PYTHONUTF8="1"; python scripts/dry_run_executor_test.py
"""

import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)

import json
import time
import shutil
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.executor import ActionRunner, LegacyBridge, StateLoopExecutor
from src.executor.state_loop import STATE_ACTIONS
from src.knowledge.selector_registry import SelectorRegistry
from src.knowledge.state_machine import StateMachine


# ---------------------------------------------------------------------------
# Mock Page
# ---------------------------------------------------------------------------
class MockPage:
    """Minimal Playwright Page mock for dry-run testing."""

    def __init__(self, url="about:blank"):
        self._url = url
        self._selectors = {}   # selector -> exists?
        self._values = {}      # selector -> current value
        self._calls = []       # record of all calls

    # -- navigation --
    @property
    def url(self):
        return self._url

    def goto(self, url, **kwargs):
        self._calls.append(("goto", url))
        self._url = url
        return None

    def reload(self, **kwargs):
        self._calls.append(("reload",))
        return None

    # -- selectors --
    def wait_for_selector(self, selector, timeout=3000):
        if not self._selectors.get(selector, False):
            raise Exception(f"Timeout: {selector}")
        return MagicMock()

    def is_visible(self, selector):
        return self._selectors.get(selector, False)

    def query_selector(self, selector):
        if self._selectors.get(selector, False):
            return MagicMock()
        return None

    def locator(self, selector):
        m = MagicMock()
        m.count.return_value = 1 if self._selectors.get(selector, False) else 0
        m.first = m
        m.all.return_value = [m] if self._selectors.get(selector, False) else []
        return m

    # -- interactions --
    def click(self, selector, **kwargs):
        self._calls.append(("click", selector))

    def fill(self, selector, value, **kwargs):
        self._calls.append(("fill", selector, value))
        self._values[selector] = value

    def evaluate(self, script, *args):
        self._calls.append(("evaluate", script[:60], args))
        return None

    def screenshot(self, **kwargs):
        self._calls.append(("screenshot", kwargs.get("path")))
        return None

    def inner_text(self, selector):
        return ""

    def on(self, event, handler):
        pass

    def set_default_timeout(self, timeout):
        pass

    def keyboard(self):
        return MagicMock()

    def wait_for_load_state(self, state, timeout=30000):
        self._calls.append(("wait_for_load_state", state))
        return None

    # -- helpers for test setup --
    def set_selector_exists(self, selector, exists=True):
        self._selectors[selector] = exists


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def make_temp_selectors_json(tmp_path: Path) -> Path:
    data = {
        "brand_input": {
            "preferred": ["kat-input[data-cy='brand-input']"],
            "fallback": ["kat-input[aria-label*='Brand']", "kat-input[name='brand']"],
            "risk": "low",
        },
        "submit_button": {
            "preferred": ["kat-button#submit"],
            "fallback": ["kat-button[data-cy='submit']"],
            "risk": "low",
        },
    }
    p = tmp_path / "selectors.json"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# 1. ActionRunner tests
# ---------------------------------------------------------------------------
class TestActionRunner(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(project_root / "_test_tmp")
        self.tmp.mkdir(exist_ok=True)
        self.selectors_path = make_temp_selectors_json(self.tmp)
        self.registry = SelectorRegistry(str(self.selectors_path))

    def tearDown(self):
        if self.tmp.exists():
            shutil.rmtree(self.tmp, ignore_errors=True)

    def test_click_first_selector_success(self):
        page = MockPage()
        page.set_selector_exists("kat-input[data-cy='brand-input']", True)
        runner = ActionRunner(page, self.registry)

        # ActionRunner.click uses wait_for_selector + click
        # Our mock raises if selector not registered as existing
        ok = runner.click("brand_input")
        self.assertTrue(ok)
        self.assertIn(("click", "kat-input[data-cy='brand-input']"), page._calls)

    def test_click_fallback_chain(self):
        """First selector fails, second succeeds."""
        page = MockPage()
        # Only fallback exists
        page.set_selector_exists("kat-input[aria-label*='Brand']", True)
        runner = ActionRunner(page, self.registry)

        ok = runner.click("brand_input")
        self.assertTrue(ok)
        # Should have tried preferred first (timeout), then fallback (success)
        calls = [c for c in page._calls if c[0] == "click"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], "kat-input[aria-label*='Brand']")

    def test_click_all_fail(self):
        """All selectors in chain fail."""
        page = MockPage()
        runner = ActionRunner(page, self.registry)
        ok = runner.click("brand_input")
        self.assertFalse(ok)

    def test_navigate_success(self):
        page = MockPage()
        runner = ActionRunner(page, self.registry)
        ok = runner.navigate("https://example.com")
        self.assertTrue(ok)
        self.assertEqual(page._url, "https://example.com")

    def test_take_screenshot(self):
        page = MockPage()
        runner = ActionRunner(page, self.registry, evidence_dir=str(self.tmp / "ev"))
        path = runner.take_screenshot("test_shot")
        self.assertIsNotNone(path)
        self.assertIn("test_shot.png", path)


# ---------------------------------------------------------------------------
# 2. LegacyBridge tests
# ---------------------------------------------------------------------------
class TestLegacyBridge(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(project_root / "_test_tmp_bridge")
        self.tmp.mkdir(exist_ok=True)

    def tearDown(self):
        if self.tmp.exists():
            shutil.rmtree(self.tmp, ignore_errors=True)

    @patch("src.form_filler.KatalFormFiller")
    @patch("src.flow_submit_5461.fill_5461_form")
    @patch("src.flow_submit_5461.connect_brand_in_5461_panel")
    @patch("src.flow_submit_5461.handle_declined_case_application")
    @patch("src.flow_submit_5461.extract_case_id")
    def test_call_interface_uniformity(
        self,
        mock_extract_case_id,
        mock_handle_declined,
        mock_connect_brand,
        mock_fill_5461_form,
        mock_filler_cls,
    ):
        """All call() results have the same shape {ok, result, error}."""
        page = MockPage()
        brand_data = {
            "brand_name": "TESTBRAND",
            "statement_text": "Item name: Test Item\nSome statement",
            "upload_files": [],
            "email": "test@example.com",
        }
        bridge = LegacyBridge(page, brand_data, str(self.tmp))

        # Mock returns
        mock_filler = MagicMock()
        mock_filler.fill_product_identity_form.return_value = {"item_name": True}
        mock_filler.click_next.return_value = True
        mock_filler.click_apply_to_sell.return_value = True
        mock_filler_cls.return_value = mock_filler

        mock_fill_5461_form.return_value = {"steps": ["product_title"]}
        mock_connect_brand.return_value = {"found": True, "selected": True}
        mock_handle_declined.return_value = {"success": True}
        mock_extract_case_id.return_value = "1234567890"

        actions = [
            "fill_product_identity",
            "click_apply_to_sell",
            "connect_brand",
            "fill_5461_form",
            "handle_declined",
            "extract_case_id",
        ]

        for action in actions:
            with self.subTest(action=action):
                result = bridge.call(action)
                self.assertIn("ok", result)
                self.assertIn("result", result)
                self.assertIn("error", result)
                self.assertIsInstance(result["ok"], bool)

    def test_unknown_action(self):
        page = MockPage()
        bridge = LegacyBridge(page, {}, str(self.tmp))
        result = bridge.call("nonexistent_action")
        self.assertFalse(result["ok"])
        self.assertIn("Unknown action", result["error"])


# ---------------------------------------------------------------------------
# 3. StateLoopExecutor tests
# ---------------------------------------------------------------------------
class TestStateLoopExecutor(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(project_root / "_test_tmp_loop")
        self.tmp.mkdir(exist_ok=True)

    def tearDown(self):
        if self.tmp.exists():
            shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_executor(self, page, brand_data=None):
        data = brand_data or {
            "account_id": "test_001",
            "brand_name": "MOCKBRAND",
            "entry_url": "https://sellercentral.amazon.com/abis/listing/create/product_identity",
            "marketplace": "US",
            "statement_text": "Item name: Mock Item\nStatement here",
            "upload_files": [],
            "email": "mock@example.com",
            "brand_keywords": None,
        }
        return StateLoopExecutor(page, data, evidence_root=str(self.tmp))

    @patch("src.executor.state_loop.PageCapture")
    @patch("src.executor.state_loop.StateMachine")
    def test_scenario_a_normal_flow(self, mock_sm_cls, mock_capture_cls):
        """Scenario A: add_product_start -> 5461_triggered -> form_ready -> case_created"""
        page = MockPage(url="https://sellercentral.amazon.com/abis/listing/create/product_identity")
        executor = self._make_executor(page)

        # Mock capture
        mock_evidence = MagicMock()
        mock_evidence.url = page._url
        mock_evidence.body_text = "Add a Product"
        mock_evidence.body_length = 5000
        mock_evidence.alerts = []
        mock_evidence.interactives = []
        mock_evidence.detected_states = []
        mock_evidence.screenshot_path = None
        mock_evidence.timestamp = ""
        mock_capture_inst = MagicMock()
        mock_capture_inst.capture.return_value = mock_evidence
        mock_capture_cls.return_value = mock_capture_inst

        # Mock state machines
        sm_add = MagicMock()
        sm_5461 = MagicMock()

        # Sequence of states -- use an index so both SMs can read without consuming
        state_seq = [
            "add_product_start",
            "5461_triggered",
            "form_loading",
            "form_ready",
            "case_created",
        ]
        idx = [0]

        def match_add(ev):
            i = idx[0]
            if i < len(state_seq) and state_seq[i] not in ("form_ready", "case_created"):
                idx[0] += 1
                return state_seq[i]
            return None

        def match_5461(ev):
            i = idx[0]
            if i < len(state_seq) and state_seq[i] in ("form_ready", "case_created"):
                idx[0] += 1
                return state_seq[i]
            return None

        sm_add.match.side_effect = match_add
        sm_5461.match.side_effect = match_5461

        sm_add.list_states.return_value = ["add_product_start", "5461_triggered", "form_loading"]
        sm_5461.list_states.return_value = ["form_ready", "form_filled", "case_created"]

        executor.sm_add_product = sm_add
        executor.sm_5461_form = sm_5461

        # Mock legacy bridge
        executor.bridge = MagicMock()
        executor.bridge.call.return_value = {"ok": True, "result": {}, "error": ""}

        executor._navigate_to_entry = lambda: True
        result = executor.run()
        self.assertEqual(result["result"], "success")
        self.assertTrue(any(s.get("state") == "case_created" for s in result["steps"]))

    @patch("src.executor.state_loop.PageCapture")
    @patch("src.executor.state_loop.StateMachine")
    def test_scenario_b_error_410001_then_success(self, mock_sm_cls, mock_capture_cls):
        """Scenario B: error_410001 -> wait -> retry -> success"""
        page = MockPage()
        executor = self._make_executor(page)

        mock_evidence = MagicMock()
        mock_evidence.url = page._url
        mock_evidence.body_text = ""
        mock_evidence.body_length = 5000
        mock_evidence.alerts = []
        mock_evidence.interactives = []
        mock_evidence.detected_states = []
        mock_evidence.screenshot_path = None
        mock_evidence.timestamp = ""
        mock_capture_inst = MagicMock()
        mock_capture_inst.capture.return_value = mock_evidence
        mock_capture_cls.return_value = mock_capture_inst

        sm_add = MagicMock()
        sm_5461 = MagicMock()

        state_sequence = [
            "add_product_start",
            "error_410001",
            "error_410001",
            "add_product_start",
            "5461_triggered",
            "form_ready",
            "case_created",
        ]

        def match_add(ev):
            if not state_sequence:
                return None
            s = state_sequence[0]
            if s in ("add_product_start", "error_410001", "5461_triggered"):
                return state_sequence.pop(0)
            return None

        def match_5461(ev):
            if not state_sequence:
                return None
            s = state_sequence[0]
            if s in ("form_ready", "case_created"):
                return state_sequence.pop(0)
            return None

        sm_add.match.side_effect = match_add
        sm_5461.match.side_effect = match_5461
        sm_add.list_states.return_value = ["add_product_start", "error_410001", "5461_triggered"]
        sm_5461.list_states.return_value = ["form_ready", "case_created"]

        executor.sm_add_product = sm_add
        executor.sm_5461_form = sm_5461
        executor.bridge = MagicMock()
        executor.bridge.call.return_value = {"ok": True, "result": {}, "error": ""}

        # Override _navigate_to_entry to skip navigation
        executor._navigate_to_entry = lambda: True
        # Patch wait_seconds to avoid long sleeps
        executor.runner.wait_seconds = lambda s: None
        # Patch time.sleep in state_loop module to avoid waits
        import src.executor.state_loop as _sl
        original_sleep = _sl.time.sleep
        _sl.time.sleep = lambda s: None
        try:
            result = executor.run()
        finally:
            _sl.time.sleep = original_sleep
        self.assertEqual(result["result"], "success")

    @patch("src.executor.state_loop.PageCapture")
    @patch("src.executor.state_loop.StateMachine")
    def test_scenario_c_login_expired_stop(self, mock_sm_cls, mock_capture_cls):
        """Scenario C: login_expired -> immediate stop"""
        page = MockPage(url="https://sellercentral.amazon.com/ap/signin")
        executor = self._make_executor(page)

        mock_evidence = MagicMock()
        mock_evidence.url = page._url
        mock_evidence.body_text = "Sign in"
        mock_evidence.body_length = 3000
        mock_evidence.alerts = []
        mock_evidence.interactives = []
        mock_evidence.detected_states = []
        mock_evidence.screenshot_path = None
        mock_evidence.timestamp = ""
        mock_capture_inst = MagicMock()
        mock_capture_inst.capture.return_value = mock_evidence
        mock_capture_cls.return_value = mock_capture_inst

        sm_add = MagicMock()
        sm_add.match.return_value = "login_expired"
        sm_add.list_states.return_value = ["login_expired"]
        sm_5461 = MagicMock()
        sm_5461.list_states.return_value = []

        executor.sm_add_product = sm_add
        executor.sm_5461_form = sm_5461
        # Override _navigate_to_entry to bypass navigation
        executor._navigate_to_entry = lambda: True

        result = executor.run()
        self.assertEqual(result["result"], "stopped")
        self.assertIn("login_required", result["error"])

    @patch("src.executor.state_loop.PageCapture")
    @patch("src.executor.state_loop.StateMachine")
    def test_scenario_d_connect_brand_flow(self, mock_sm_cls, mock_capture_cls):
        """Scenario D: connect_brand -> form_ready -> case_created"""
        page = MockPage()
        executor = self._make_executor(page)

        mock_evidence = MagicMock()
        mock_evidence.url = page._url
        mock_evidence.body_text = "Clarify which brand"
        mock_evidence.body_length = 5000
        mock_evidence.alerts = []
        mock_evidence.interactives = []
        mock_evidence.detected_states = []
        mock_evidence.screenshot_path = None
        mock_evidence.timestamp = ""
        mock_capture_inst = MagicMock()
        mock_capture_inst.capture.return_value = mock_evidence
        mock_capture_cls.return_value = mock_capture_inst

        sm_add = MagicMock()
        sm_5461 = MagicMock()

        state_sequence = [
            "connect_brand",
            "form_loading",
            "form_ready",
            "case_created",
        ]

        def match_add(ev):
            if not state_sequence:
                return None
            s = state_sequence[0]
            if s == "connect_brand":
                return state_sequence.pop(0)
            return None

        def match_5461(ev):
            if not state_sequence:
                return None
            s = state_sequence[0]
            if s in ("form_loading", "form_ready", "case_created"):
                return state_sequence.pop(0)
            return None

        sm_add.match.side_effect = match_add
        sm_5461.match.side_effect = match_5461
        sm_add.list_states.return_value = ["connect_brand"]
        sm_5461.list_states.return_value = ["form_loading", "form_ready", "case_created"]

        executor.sm_add_product = sm_add
        executor.sm_5461_form = sm_5461
        executor.bridge = MagicMock()
        executor.bridge.call.return_value = {"ok": True, "result": {}, "error": ""}

        # Speed up waits
        original_max_retries = StateLoopExecutor.MAX_RETRIES_PER_STATE
        StateLoopExecutor.MAX_RETRIES_PER_STATE = 2
        executor._navigate_to_entry = lambda: True
        executor.runner.wait_seconds = lambda s: None
        import src.executor.state_loop as _sl2
        orig_sleep2 = _sl2.time.sleep
        _sl2.time.sleep = lambda s: None
        try:
            result = executor.run()
        finally:
            StateLoopExecutor.MAX_RETRIES_PER_STATE = original_max_retries
            _sl2.time.sleep = orig_sleep2
        self.assertEqual(result["result"], "success")
        states_visited = [s.get("state") for s in result["steps"]]
        self.assertIn("connect_brand", states_visited)
        self.assertIn("case_created", states_visited)

    def test_archive_error_sample(self):
        """Test _archive_error_sample copies evidence to ignored runtime storage."""
        page = MockPage()
        executor = self._make_executor(page)

        # Create fake evidence dir with a file
        ev_dir = executor._evidence_dir()
        ev_dir.mkdir(parents=True, exist_ok=True)
        (ev_dir / "dummy.txt").write_text("test evidence", encoding="utf-8")

        evidence = {
            "url": "https://example.com",
            "title": "Test",
            "body_length": 1000,
            "detected_states": [],
        }

        executor._archive_error_sample("test_state", evidence)

        # Check that the private runtime samples directory was created
        examples_dir = project_root / "runtime" / "evidence" / "error-samples" / "add-product" / "test_state"
        self.assertTrue(examples_dir.exists())

        # Cleanup
        if examples_dir.exists():
            shutil.rmtree(examples_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# 4. STATE_ACTIONS consistency test
# ---------------------------------------------------------------------------
class TestStateActionsMapping(unittest.TestCase):

    def test_all_actions_have_valid_type(self):
        valid_types = {"legacy", "done", "stop", "wait", "retry", "noop"}
        for state, (action_type, arg, expected) in STATE_ACTIONS.items():
            with self.subTest(state=state):
                self.assertIn(
                    action_type, valid_types,
                    f"State '{state}' has invalid action_type '{action_type}'"
                )

    def test_done_and_stop_have_none_or_string_action_arg(self):
        for state, (action_type, arg, expected) in STATE_ACTIONS.items():
            if action_type == "done":
                with self.subTest(state=state):
                    self.assertIsNone(arg, f"State '{state}' arg should be None")
            elif action_type == "stop":
                with self.subTest(state=state):
                    self.assertIsInstance(arg, str, f"State '{state}' arg should be a string reason")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    unittest.main(verbosity=2)
