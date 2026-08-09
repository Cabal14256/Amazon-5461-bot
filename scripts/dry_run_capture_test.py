"""
Dry-run 测试 — 验证阶段 1 证据采集基础设施

不需要真实浏览器，使用 Mock Page 对象测试所有核心功能：
1. 模块导入
2. 脱敏模块 (redact)
3. PageCapture（Mock Page）
4. capture_evidence / capture_evidence_safe / capture_error_evidence
5. 证据文件输出验证
6. 状态信号检测
7. flow_submit_5461 导入（不运行）

运行方式:
    cd projects/amazon-5461-bot
    python scripts/dry_run_capture_test.py
"""

import sys
import json
import shutil
import traceback
from pathlib import Path
from datetime import datetime

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

PASS = "✅"
FAIL = "❌"
WARN = "⚠️ "

results = []

def test(name, fn):
    try:
        fn()
        results.append((PASS, name, None))
        print(f"  {PASS} {name}")
    except Exception as e:
        results.append((FAIL, name, str(e)))
        print(f"  {FAIL} {name}: {e}")
        traceback.print_exc()

# ─── Mock Page ────────────────────────────────────────────────
class MockPage:
    """模拟 Playwright Page 对象（sync API）。"""

    def __init__(self, scenario="add_product_start"):
        self.scenario = scenario
        self._url = "https://sellercentral.amazon.com/productsearch/search"
        self._title = "Add a Product - Amazon Seller Central"

    def evaluate(self, script):
        """返回模拟的 DOM 数据，根据 scenario 变化。"""
        data = {
            "url": self._url,
            "title": self._title,
            "bodyText": self._make_body_text(),
            "bodyLength": 5000,
            "headings": [
                {"tag": "H1", "text": "Add a Product", "id": "", "class": ""},
                {"tag": "H2", "text": "Select a Category", "id": "", "class": ""},
            ],
            "alerts": [],
            "interactives": [
                {
                    "tag": "button",
                    "text": "Next",
                    "id": "next-btn",
                    "class": "a-button-primary",
                    "name": "",
                    "type": "button",
                    "disabled": False,
                    "ariaLabel": "",
                    "ariaPressed": "",
                    "dataCy": "",
                    "dataTestid": "",
                    "href": "",
                    "placeholder": "",
                    "value": "",
                },
                {
                    "tag": "input",
                    "text": "",
                    "id": "product-title",
                    "class": "kat-input__input",
                    "name": "product-title",
                    "type": "text",
                    "disabled": False,
                    "ariaLabel": "Product Title",
                    "ariaPressed": "",
                    "dataCy": "",
                    "dataTestid": "product-title",
                    "href": "",
                    "placeholder": "Enter product title",
                    "value": "",
                },
            ],
        }
        if self.scenario == "error_410001":
            data["alerts"] = [{"tag": "DIV", "text": "410001 Error occurred", "class": "a-alert-error", "testid": "", "cy": ""}]
            data["bodyText"] = "An error occurred: 410001. Please try again later."
        elif self.scenario == "5461_triggered":
            data["alerts"] = [{"tag": "DIV", "text": "You are not approved to create ASINs for this brand. Apply to sell.", "class": "a-alert-warning", "testid": "", "cy": ""}]
            data["bodyText"] = "5461 Brand Authorization Required. Request approval to sell this product."
            data["title"] = "Apply to Sell - Amazon Seller Central"
        elif self.scenario == "login_expired":
            data["bodyText"] = "Please sign in to continue."
            data["title"] = "Sign In - Amazon"
            data["url"] = "https://www.amazon.com/ap/signin"
        return data

    def _make_body_text(self):
        if self.scenario == "add_product_start":
            return "Add a Product. I'm adding a product not sold on Amazon. Product type category."
        elif self.scenario == "5461_triggered":
            return "5461 Brand Authorization. Apply to sell. Not approved to create ASINs."
        return "Amazon Seller Central."

    def screenshot(self, path=None, full_page=True):
        """模拟截图：写一个 1x1 PNG 文件。"""
        # PNG 最小有效文件：1x1 透明像素
        png_1x1 = bytes([
            0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A,  # PNG signature
            0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44, 0x52,  # IHDR length + type
            0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,  # width=1, height=1
            0x08, 0x02, 0x00, 0x00, 0x00, 0x90, 0x77, 0x53,  # bit depth=8, color type=2
            0xDE, 0x00, 0x00, 0x00, 0x0C, 0x49, 0x44, 0x41,  # IDAT length + type
            0x54, 0x08, 0xD7, 0x63, 0xF8, 0xCF, 0xC0, 0x00,  # compressed data
            0x00, 0x00, 0x02, 0x00, 0x01, 0xE2, 0x21, 0xBC,
            0x33, 0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4E,  # IEND
            0x44, 0xAE, 0x42, 0x60, 0x82
        ])
        if path:
            with open(path, "wb") as f:
                f.write(png_1x1)


# ─── 准备输出目录 ──────────────────────────────────────────────
OUT_DIR = Path("evidence/dry_run_test") / datetime.now().strftime("%Y%m%d-%H%M%S")
OUT_DIR.mkdir(parents=True, exist_ok=True)
print(f"\n{'='*60}")
print(f"  证据采集 Dry-run 测试")
print(f"  输出目录: {OUT_DIR}")
print(f"{'='*60}\n")

# ─── Test 1: 模块导入 ──────────────────────────────────────────
print("[ 1 ] 模块导入")

def test_import_capture():
    from src.capture import PageCapture, PageEvidence, PageMonitor, NetworkCapture, redact_text, default_redact
test("src.capture 全部导出", test_import_capture)

def test_import_evidence():
    from src.evidence import (
        capture_evidence, capture_evidence_safe, capture_error_evidence,
        build_evidence_dir, EVIDENCE_NODES
    )
test("src.evidence 全部导出", test_import_evidence)

def test_import_flow():
    import src.flow_submit_5461
test("src.flow_submit_5461 导入（不运行）", test_import_flow)

# ─── Test 2: 脱敏模块 ─────────────────────────────────────────
print("\n[ 2 ] 脱敏模块 (redact)")
from src.capture.redact import redact_text, default_redact

def test_redact_token():
    text = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.test.signature"
    result = redact_text(text)
    assert "eyJ" not in result, f"Token 未被脱敏: {result}"
test("Bearer token 脱敏", test_redact_token)

def test_redact_email():
    text = "Email: user@example.com sent the request"
    result = redact_text(text)
    assert "@example.com" not in result or "***" in result or "redacted" in result.lower() or "user@" not in result, \
        f"Email 未被脱敏: {result}"
    print(f"    → '{text}' => '{result}'")
test("Email 脱敏", test_redact_email)

def test_redact_cookie():
    text = "Cookie: session-id=abc123xyz; x-main=testtoken"
    result = redact_text(text)
    assert "abc123xyz" not in result, f"Cookie 未被脱敏: {result}"
test("Cookie 脱敏", test_redact_cookie)

def test_redact_safe_text():
    text = "Add a Product - Amazon Seller Central"
    result = redact_text(text)
    assert result == text, f"普通文字被意外脱敏: {result}"
test("普通文字不被脱敏", test_redact_safe_text)

# ─── Test 3: PageCapture（Mock Page）──────────────────────────
print("\n[ 3 ] PageCapture（Mock Page）")
from src.capture import PageCapture, PageEvidence

def test_page_capture_normal():
    page = MockPage("add_product_start")
    capture = PageCapture(page)
    out_dir = OUT_DIR / "scenario_normal"
    evidence = capture.capture("01_add_product_start", out_dir)
    assert isinstance(evidence, PageEvidence), "返回类型应为 PageEvidence"
    assert evidence.url, "URL 应非空"
    assert evidence.title, "Title 应非空"
    assert evidence.body_length > 0, "body_length 应 > 0"
    print(f"    → URL: {evidence.url}")
    print(f"    → Title: {evidence.title}")
    print(f"    → Detected States: {evidence.detected_states}")
test("正常页面采集", test_page_capture_normal)

def test_page_capture_screenshot():
    page = MockPage("add_product_start")
    capture = PageCapture(page)
    out_dir = OUT_DIR / "scenario_screenshot"
    evidence = capture.capture("screenshot_test", out_dir)
    assert evidence.screenshot_path is not None, "截图路径应非空"
    assert Path(evidence.screenshot_path).exists(), f"截图文件应存在: {evidence.screenshot_path}"
    print(f"    → Screenshot: {evidence.screenshot_path}")
test("截图文件生成", test_page_capture_screenshot)

def test_page_capture_files():
    page = MockPage("add_product_start")
    capture = PageCapture(page)
    out_dir = OUT_DIR / "scenario_files"
    evidence = capture.capture("file_check", out_dir)
    expected_files = [
        "file_check_dom_snapshot.json",
        "file_check_page_text.txt",
        "file_check_summary.json",
    ]
    for fname in expected_files:
        fpath = out_dir / fname
        assert fpath.exists(), f"缺少文件: {fname}"
        assert fpath.stat().st_size > 0, f"文件为空: {fname}"
    print(f"    → 所有文件生成: {expected_files}")
test("所有证据文件生成", test_page_capture_files)

def test_state_detection_5461():
    page = MockPage("5461_triggered")
    capture = PageCapture(page)
    out_dir = OUT_DIR / "scenario_5461"
    evidence = capture.capture("04_5461_triggered", out_dir)
    assert "5461_triggered" in evidence.detected_states, \
        f"应检测到 5461_triggered，实际: {evidence.detected_states}"
    print(f"    → Detected: {evidence.detected_states}")
test("状态检测 - 5461 触发", test_state_detection_5461)

def test_state_detection_error():
    page = MockPage("error_410001")
    capture = PageCapture(page)
    out_dir = OUT_DIR / "scenario_410001"
    evidence = capture.capture("error_test", out_dir)
    assert "error_410001" in evidence.detected_states, \
        f"应检测到 error_410001，实际: {evidence.detected_states}"
    print(f"    → Detected: {evidence.detected_states}")
test("状态检测 - 410001 错误", test_state_detection_error)

def test_state_detection_login():
    page = MockPage("login_expired")
    capture = PageCapture(page)
    out_dir = OUT_DIR / "scenario_login"
    evidence = capture.capture("login_test", out_dir)
    assert "login_expired" in evidence.detected_states, \
        f"应检测到 login_expired，实际: {evidence.detected_states}"
    print(f"    → Detected: {evidence.detected_states}")
test("状态检测 - 登录过期", test_state_detection_login)

# ─── Test 4: capture_evidence API ─────────────────────────────
print("\n[ 4 ] capture_evidence API")
from src.evidence import (
    capture_evidence, capture_evidence_safe, capture_error_evidence,
    build_evidence_dir, EVIDENCE_NODES
)

def test_capture_evidence():
    page = MockPage("add_product_start")
    result = capture_evidence(
        page,
        name="01_add_product_start",
        root=str(OUT_DIR / "evidence_api"),
        account="us_store_578",
        brand="HOMEMO",
    )
    assert isinstance(result, dict), "返回类型应为 dict"
    assert "url" in result, "result 应包含 url"
    assert result["url"], "URL 应非空"
    print(f"    → Keys: {list(result.keys())[:6]}")
test("capture_evidence() 正常调用", test_capture_evidence)

def test_capture_evidence_safe_ok():
    page = MockPage("5461_triggered")
    result = capture_evidence_safe(
        page,
        name="04_5461_triggered",
        root=str(OUT_DIR / "evidence_safe"),
        account="us_store_578",
        brand="WILLONE",
    )
    assert result is not None, "正常情况下不应返回 None"
    assert "detected_states" in result
    print(f"    → Detected: {result['detected_states']}")
test("capture_evidence_safe() 正常返回", test_capture_evidence_safe_ok)

def test_capture_evidence_safe_fail():
    """验证 safe 版本在失败时不抛异常，返回 None。"""
    class BrokenPage:
        def evaluate(self, _): raise RuntimeError("模拟 Playwright 崩溃")
        def screenshot(self, **kw): raise RuntimeError("模拟截图失败")

    result = capture_evidence_safe(
        BrokenPage(),
        name="broken_test",
        root=str(OUT_DIR / "evidence_broken"),
        account="test",
        brand="test",
    )
    assert result is None, f"失败时应返回 None，实际: {result}"
    print(f"    → 异常被吞掉，返回 None ✓")
test("capture_evidence_safe() 失败不抛异常", test_capture_evidence_safe_fail)

def test_capture_error_evidence():
    page = MockPage("error_410001")
    result = capture_error_evidence(
        page,
        error_name="410001",
        root=str(OUT_DIR / "evidence_error"),
        account="us_store_578",
        brand="IKABO",
    )
    assert result is not None, "error_evidence 不应返回 None"
    assert "timestamp" in result
    print(f"    → Error evidence timestamp: {result.get('timestamp', '')[:19]}")
test("capture_error_evidence() 正常调用", test_capture_error_evidence)

def test_evidence_dir_format():
    """验证 build_evidence_dir 路径格式正确（修复前为 Y%-%m-%d）。"""
    path = build_evidence_dir(str(OUT_DIR / "dir_test"), "us_store_578", "HOMEMO", "01_add_product_start")
    assert path.exists(), "目录应已创建"
    # 检查日期格式是否正确（应为 2026-05-19，不是 Y2026-05-19）
    parts = str(path).replace("\\", "/").split("/")
    date_part = [p for p in parts if len(p) == 10 and p.count("-") == 2]
    assert date_part, f"路径中未找到 YYYY-MM-DD 格式日期，完整路径: {path}"
    assert not date_part[0].startswith("Y"), f"日期格式错误（修复前遗留）: {date_part[0]}"
    print(f"    → 日期路径: {date_part[0]} ✓")
test("build_evidence_dir 路径格式正确（bug fix 验证）", test_evidence_dir_format)

def test_evidence_nodes_constants():
    assert len(EVIDENCE_NODES) >= 9, f"EVIDENCE_NODES 应有 ≥9 项，实际: {len(EVIDENCE_NODES)}"
    for k, v in EVIDENCE_NODES.items():
        assert v.startswith(("0", "1")), f"节点名称格式异常: {v}"
    print(f"    → 节点数量: {len(EVIDENCE_NODES)}, keys: {list(EVIDENCE_NODES.keys())}")
test("EVIDENCE_NODES 常量完整性", test_evidence_nodes_constants)

# ─── Test 5: DOM 快照内容验证 ─────────────────────────────────
print("\n[ 5 ] 证据文件内容验证")

def test_summary_json_parseable():
    out_dir = OUT_DIR / "scenario_files"
    summary_file = out_dir / "file_check_summary.json"
    with open(summary_file, encoding="utf-8") as f:
        data = json.load(f)
    required_keys = ["url", "title", "body_length", "headings", "alerts", "interactives", "detected_states", "timestamp"]
    for key in required_keys:
        assert key in data, f"summary.json 缺少字段: {key}"
    print(f"    → summary.json 字段完整: {required_keys}")
test("summary.json 可解析且字段完整", test_summary_json_parseable)

def test_page_text_readable():
    out_dir = OUT_DIR / "scenario_files"
    text_file = out_dir / "file_check_page_text.txt"
    content = text_file.read_text(encoding="utf-8")
    assert "URL:" in content
    assert "Title:" in content
    assert "HEADINGS:" in content
    assert "INTERACTIVE ELEMENTS" in content
    print(f"    → page_text.txt 包含所有必要段落")
test("page_text.txt 格式正确", test_page_text_readable)

# ─── 汇总 ─────────────────────────────────────────────────────
total = len(results)
passed = sum(1 for r in results if r[0] == PASS)
failed = sum(1 for r in results if r[0] == FAIL)

print(f"\n{'='*60}")
print(f"  结果: {passed}/{total} 通过  |  {failed} 失败")
if failed > 0:
    print(f"\n  失败项:")
    for icon, name, err in results:
        if icon == FAIL:
            print(f"    {icon} {name}")
            print(f"       {err}")
print(f"\n  证据文件位置: {OUT_DIR.resolve()}")
print(f"{'='*60}\n")

sys.exit(0 if failed == 0 else 1)
