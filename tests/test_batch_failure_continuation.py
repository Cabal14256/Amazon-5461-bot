"""批处理失败继续策略。

验证真实提交中某个品牌落入 Draft/未确认状态时，当前品牌仍保持
failed，但批次会继续处理后续品牌。测试只使用伪造浏览器，不连接
AdsPower/Seller Central，也不会点击真实提交。
"""

from __future__ import annotations

import io
import sys
from types import SimpleNamespace


def _import_batch_module():
    class _Sink:
        def __init__(self):
            self.buffer = io.BytesIO()

        def write(self, *args):
            return 0

        def flush(self):
            pass

    real_stdout, real_stderr = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = _Sink(), _Sink()
    try:
        from scripts import run_full_5461_batch as batch_module
    finally:
        sys.stdout, sys.stderr = real_stdout, real_stderr
    return batch_module


class _FakePage:
    def __init__(self, context):
        self._context = context
        self._closed = False
        self.url = "about:blank"

    def goto(self, url, **kwargs):
        self.url = url

    def wait_for_load_state(self, *args, **kwargs):
        return None

    def set_default_timeout(self, timeout):
        return None

    def close(self):
        self._closed = True

    def is_closed(self):
        return self._closed


class _FakeContext:
    def __init__(self):
        self.pages = []

    def new_page(self):
        page = _FakePage(self)
        self.pages.append(page)
        return page


class _FakePlaywright:
    def start(self):
        return self

    def stop(self):
        return None


def test_draft_failure_keeps_failed_state_and_continues_next_brand(monkeypatch, capsys):
    batch = _import_batch_module()
    context = _FakeContext()
    browser = SimpleNamespace(contexts=[context])
    fake_playwright = _FakePlaywright()

    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: fake_playwright)
    monkeypatch.setattr(batch, "_cdp_connect", lambda *args, **kwargs: browser)
    monkeypatch.setattr(
        "scripts._flow_cli_common.load_runtime",
        lambda *args, **kwargs: (
            {"browser": {}},
            {"adspower_profile_id": "profile-test"},
            {},
            "MX",
            {},
            "ws://fake",
        ),
    )
    monkeypatch.setattr(batch, "schedule_followup_for_result", lambda *args, **kwargs: None)
    monkeypatch.setattr(batch, "schedule_case_id_recovery_for_result", lambda *args, **kwargs: None)
    monkeypatch.setattr(batch, "_record_batch_failure_incident", lambda *args, **kwargs: None)
    monkeypatch.setattr(batch, "stop_file_requested", lambda: False)

    results = iter([
        {
            "status": "draft",
            "note": "Dashboard found Draft",
            "dashboard_check": {"status": "draft"},
            "marketplace_switched": True,
            "actual_marketplace": "MX",
        },
        {
            "status": "success",
            "case_id": "12345678901",
            "marketplace_switched": True,
            "actual_marketplace": "MX",
        },
    ])
    calls = []

    def _run_single_item(item, *args, **kwargs):
        calls.append(item["brand_name"])
        return next(results)

    monkeypatch.setattr(batch, "run_single_item", _run_single_item)

    state = {
        "config": {
            "delay_between_items_min": 0,
            "delay_between_items_max": 0,
        },
        "summary": {"total": 2, "completed": 0, "failed": 0, "pending": 2},
        "batches": [{
            "batch_no": 1,
            "status": "pending",
            "items": [
                {"account_id": "us_store_test", "brand_name": "FIRST", "site": "MX", "status": "pending"},
                {"account_id": "us_store_test", "brand_name": "SECOND", "site": "MX", "status": "pending"},
            ],
        }],
    }

    result_state = batch.run_batch(state, 1, dry_run=False, enable_monitor=False)

    assert calls == ["FIRST", "SECOND"]
    assert result_state["batches"][0]["items"][0]["status"] == "failed"
    assert result_state["batches"][0]["items"][1]["status"] == "completed"
    assert result_state["summary"] == {"total": 2, "completed": 1, "failed": 1, "pending": 0}
    assert "自动跳过当前品牌" in capsys.readouterr().out
