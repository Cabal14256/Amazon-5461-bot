"""方案 2 dry-run（no_submit）守卫测试。

覆盖：
- submit_5461_from_add_product(no_submit=True) 真实走完填表流程但在提交按钮
  点击前短路（主路径 阶段3/3 + Connect brand 早路径 阶段1.5），返回 dry_run；
- 批次脚本层：dry_run 时向 submit_5461_from_add_product 透传 no_submit，
  且下游登记（case followup / case id recovery）不触发。

所有 page 均为 MagicMock，不启动真实浏览器；evidence 写入 tmp_path。
"""
from unittest.mock import MagicMock

import src.config_loader
import src.form_filler
from src import flow_submit_5461 as flow


def _import_batch_module():
    """导入批次脚本。其模块顶部会重包装 sys.stdout/stderr（utf-8），
    这里用临时 sink 接住包装，导入后恢复真实 stdout/stderr，
    避免包装器 GC 时关闭 pytest/终端的底层 buffer。"""
    import io
    import sys

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


SUBMIT_SELECTOR = "kat-button#submit_button"


def _base_state(page_type="5461_FORM_OPEN", has_form=True):
    return {
        "page_type": page_type,
        "url": "https://sellercentral.amazon.com/abis/listing/create/product_identity",
        "brand_blocked": False,
        "has_permission": False,
        "needs_auth": False,
        "has_5461_form": has_form,
    }


def _fake_evaluate(js, *args, **kwargs):
    """按 JS 内容给出最小可控返回值，避免 MagicMock 参与比较运算。"""
    text = js if isinstance(js, str) else ""
    low = text.lower()
    if "apply to sell" in low or "application required" in low:
        return {"found": False}
    return None


def _make_page():
    page = MagicMock()
    page.url = "https://sellercentral.amazon.com/abis/listing/create/product_identity"
    page.evaluate.side_effect = _fake_evaluate
    page.locator.return_value.first.count.return_value = 0
    return page


def _install_harness(monkeypatch, tmp_path, states):
    """patch 掉市场切换/导航/填表等重操作，返回各 mock 句柄。"""
    seq = list(states)

    def _state(page):
        if len(seq) > 1:
            return dict(seq.pop(0))
        return dict(seq[0])

    click_mock = MagicMock()
    fill_mock = MagicMock(return_value={"filled": True})
    case_id_mock = MagicMock(return_value=None)

    filler = MagicMock()
    filler.under_review_case_id = None
    filler.declined_case_id = None
    filler.detect_add_product_ui.return_value = "legacy"

    def _fake_shot(page, path, full_page=False, timeout=10000):
        with open(path, "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n")  # 仅占位，证明留证步骤真实执行

    monkeypatch.setattr(flow, "check_page_state", _state)
    monkeypatch.setattr(src.form_filler, "KatalFormFiller", lambda page: filler)
    monkeypatch.setattr(flow, "capture_evidence_safe", lambda *a, **k: None)
    monkeypatch.setattr(flow, "_async_screenshot", _fake_shot)
    monkeypatch.setattr(flow, "fill_5461_form", fill_mock)
    monkeypatch.setattr(flow, "click_katal_button", click_mock)
    monkeypatch.setattr(flow, "extract_case_id", case_id_mock)
    monkeypatch.setattr(flow, "wait_for_5461_form_fields", lambda *a, **k: True)
    monkeypatch.setattr(flow, "connect_brand_in_5461_panel", lambda *a, **k: {"found": False})
    monkeypatch.setattr(flow, "handle_brand_selection", lambda *a, **k: True)
    monkeypatch.setattr(
        flow,
        "resolve_connect_brand_loading_state",
        lambda page, filler, brand, state: (state, True),
    )
    monkeypatch.setattr(flow, "ensure_page_ready", lambda **k: {"success": True, "retries": 0})
    monkeypatch.setattr(src.config_loader, "load_account", lambda *a, **k: {})
    monkeypatch.setattr("time.sleep", lambda *a, **k: None)

    return {"click": click_mock, "fill": fill_mock, "case_id": case_id_mock}


def _run_no_submit(tmp_path):
    return flow.submit_5461_from_add_product(
        cdp_url="ws://fake",
        account_id="us_store_test",
        marketplace="UK",
        brand_name="TESTBRAND",
        add_product_url="https://sellercentral.amazon.co.uk/abis/listing/create/product_identity",
        statement_text="5461 statement for TESTBRAND",
        upload_files=[],
        evidence_root=str(tmp_path),
        no_submit=True,
        page=_make_page(),
        skip_market_switch=True,
    )


def _assert_dry_run_common(result, mocks, expected_phase):
    assert result["submit_result"] == "dry_run"
    assert "未点击提交" in result["note"]

    # 1) 提交按钮从未被点击（本路径下 click_katal_button 应完全未被调用）
    submit_clicks = [
        c for c in mocks["click"].call_args_list
        if len(c.args) > 1 and c.args[1] == SUBMIT_SELECTOR
    ]
    assert not submit_clicks
    assert mocks["click"].call_count == 0

    # 2) 表单确实填过（流程真实走到了提交前一步）
    mocks["fill"].assert_called_once()

    # 3) 跳过 Case ID 轮询
    mocks["case_id"].assert_not_called()

    # 4) steps 记录 skipped_no_submit
    skipped = [
        s for s in result["steps"]
        if s.get("action") == "click_submit" and s.get("status") == "skipped_no_submit"
    ]
    assert skipped, "steps 中缺少 skipped_no_submit 记录"
    assert skipped[0]["phase"] == expected_phase

    # 5) 留证截图非空，且含 no_submit_before_submit.png
    assert result["evidence_files"]
    assert any("no_submit_before_submit" in f for f in result["evidence_files"])


def test_no_submit_main_path_stops_before_submit_click(monkeypatch, tmp_path):
    """主路径 阶段3/3：填完 5461 表单、03_before_submit 截图后，在点击提交前短路。"""
    mocks = _install_harness(monkeypatch, tmp_path, [_base_state("5461_FORM_OPEN")])
    result = _run_no_submit(tmp_path)
    _assert_dry_run_common(result, mocks, expected_phase=3)


def test_no_submit_connect_brand_early_path(monkeypatch, tmp_path):
    """早路径 阶段1.5（Connect brand 后 panel 直接打开）：同样在点击提交前短路。"""
    mocks = _install_harness(
        monkeypatch,
        tmp_path,
        [_base_state("BRAND_SELECTION", has_form=False), _base_state("5461_FORM_OPEN")],
    )
    result = _run_no_submit(tmp_path)
    _assert_dry_run_common(result, mocks, expected_phase=1.5)


# ---------------------------------------------------------------------------
# 批次脚本层：dry_run 透传 no_submit，下游登记不触发
# ---------------------------------------------------------------------------


def test_followup_not_scheduled_in_dry_run(monkeypatch):
    """schedule_followup_for_result(dry_run=True) 直接返回 None，不触碰调度器。"""
    batch = _import_batch_module()
    from src import case_followup

    schedule_mock = MagicMock()
    monkeypatch.setattr(case_followup, "schedule_case_followup", schedule_mock)

    item = {"account_id": "us_store_test", "brand_name": "TESTBRAND", "site": "UK"}
    result = {  # 即便是完全满足登记条件的 success + case_id，也必须被 dry_run 拦截
        "status": "success",
        "case_id": "12345678901",
        "synced_sku": "SKU-1",
    }
    assert batch.schedule_followup_for_result(item, result, {}, dry_run=True) is None
    schedule_mock.assert_not_called()


def test_case_id_recovery_not_scheduled_in_dry_run(monkeypatch):
    """schedule_case_id_recovery_for_result(dry_run=True) 直接返回 None。"""
    batch = _import_batch_module()
    from src import case_id_recovery

    schedule_mock = MagicMock(return_value={"created": True})
    monkeypatch.setattr(case_id_recovery, "schedule_case_id_recovery", schedule_mock)
    monkeypatch.setattr(case_id_recovery, "is_case_id_recovery_candidate", lambda r: True)

    item = {"account_id": "us_store_test", "brand_name": "TESTBRAND", "site": "UK"}
    result = {"status": "submitted_no_case_id_pending_dashboard", "synced_sku": "SKU-1"}
    assert batch.schedule_case_id_recovery_for_result(item, result, {}, dry_run=True) is None
    schedule_mock.assert_not_called()


def test_dry_run_status_alone_not_eligible_for_followup():
    """双保险：即使不带 dry_run 标志，status=dry_run 也不满足 followup 登记条件。"""
    batch = _import_batch_module()

    item = {"account_id": "us_store_test", "brand_name": "TESTBRAND", "site": "UK"}
    result = {"status": "dry_run", "case_id": "12345678901", "synced_sku": "SKU-1"}
    assert batch.schedule_followup_for_result(item, result, {}, dry_run=False) is None


def test_run_single_item_passes_no_submit_and_maps_dry_run_status(monkeypatch, tmp_path):
    """run_single_item(dry_run=True)：向 submit_5461_from_add_product 透传 no_submit=True，
    并把 submit_result=dry_run 映射为批次 status=dry_run。"""
    batch = _import_batch_module()

    account = {
        "account_id": "us_store_test",
        "adspower_profile_id": "profile-1",
        "email": "",
        "marketplace_configs": {},
    }
    monkeypatch.setattr(
        batch,
        "ensure_account_exists",
        lambda *a, **k: {"created": False, "matched_profile": True, "account": account},
    )
    monkeypatch.setattr(batch, "sync_brand_pack_for_account", lambda *a, **k: {"sku": "SKU-1"})
    monkeypatch.setattr(batch, "resolve_account_email", lambda acc: "")
    monkeypatch.setattr(
        batch,
        "load_runtime",
        lambda *a, **k: (
            {"paths": {"evidence_root": str(tmp_path)}},
            account,
            {},
            "UK",
            {},
            "ws://fake",
        ),
    )
    monkeypatch.setattr(batch, "resolve_statement_text", lambda *a, **k: ("stmt text", None))
    monkeypatch.setattr(
        batch,
        "extract_statement_payload",
        lambda text: {"title": "t", "sku": "s", "content": "c"},
    )
    monkeypatch.setattr(
        batch,
        "resolve_account_specific_uk_payload",
        lambda *a, **k: {"title": "", "sku": "", "content": ""},
    )
    monkeypatch.setattr(
        batch,
        "resolve_account_specific_site_payload",
        lambda *a, **k: {"title": "", "sku": "", "content": ""},
    )

    captured = {}

    def _fake_submit(**kwargs):
        captured.update(kwargs)
        return {
            "submit_result": "dry_run",
            "note": "dry-run：已走到提交前一步，未点击提交",
            "case_id": None,
            "evidence_files": ["no_submit_before_submit.png"],
            "steps": [{"phase": 3, "action": "click_submit", "status": "skipped_no_submit"}],
            "marketplace_switched": True,
            "actual_marketplace": "UK",
        }

    monkeypatch.setattr(batch, "submit_5461_from_add_product", _fake_submit)

    item = {"account_id": "us_store_test", "brand_name": "TESTBRAND", "site": None}
    result = batch.run_single_item(item, {}, dry_run=True, page=None)

    assert captured.get("no_submit") is True
    assert result["status"] == "dry_run"
    assert "未点击提交" in result["note"]


def test_run_single_item_real_run_passes_no_submit_false(monkeypatch, tmp_path):
    """对照：非 dry_run 时 no_submit=False（真实提交语义不变）。"""
    batch = _import_batch_module()

    account = {
        "account_id": "us_store_test",
        "adspower_profile_id": "profile-1",
        "email": "",
        "marketplace_configs": {},
    }
    monkeypatch.setattr(
        batch,
        "ensure_account_exists",
        lambda *a, **k: {"created": False, "matched_profile": True, "account": account},
    )
    monkeypatch.setattr(batch, "sync_brand_pack_for_account", lambda *a, **k: {"sku": "SKU-1"})
    monkeypatch.setattr(batch, "resolve_account_email", lambda acc: "")
    monkeypatch.setattr(
        batch,
        "load_runtime",
        lambda *a, **k: (
            {"paths": {"evidence_root": str(tmp_path)}},
            account,
            {},
            "UK",
            {},
            "ws://fake",
        ),
    )
    monkeypatch.setattr(batch, "resolve_statement_text", lambda *a, **k: ("stmt text", None))
    monkeypatch.setattr(
        batch,
        "extract_statement_payload",
        lambda text: {"title": "t", "sku": "s", "content": "c"},
    )
    monkeypatch.setattr(
        batch,
        "resolve_account_specific_uk_payload",
        lambda *a, **k: {"title": "", "sku": "", "content": ""},
    )
    monkeypatch.setattr(
        batch,
        "resolve_account_specific_site_payload",
        lambda *a, **k: {"title": "", "sku": "", "content": ""},
    )

    captured = {}

    def _fake_submit(**kwargs):
        captured.update(kwargs)
        return {"submit_result": "success", "case_id": "12345678901", "note": "ok"}

    monkeypatch.setattr(batch, "submit_5461_from_add_product", _fake_submit)

    item = {"account_id": "us_store_test", "brand_name": "TESTBRAND", "site": None}
    result = batch.run_single_item(item, {}, dry_run=False, page=None)

    assert captured.get("no_submit") is False
    assert result["status"] == "success"
