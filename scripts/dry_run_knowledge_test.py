"""
Dry-run 测试 -- 验证阶段 2 页面知识库 + 状态机

不需要真实浏览器，使用 Mock 数据测试所有核心功能:
1. 知识库文件可解析 (states.md, selectors.json)
2. StateMachine.match() 对各种 mock evidence 能正确返回状态名
3. SelectorRegistry.get_ordered_selectors() 返回正确的选择器列表
4. EvidenceLoader 能读取 evidence/2026-05-19/ 目录下今天生成的真实证据包

运行方式:
    cd projects/amazon-5461-bot
    python scripts/dry_run_knowledge_test.py
"""

import sys
import json
import shutil
import traceback
from pathlib import Path
from datetime import datetime

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

PASS = "[OK]"
FAIL = "[NG]"
WARN = "[WARN]"

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


# 准备输出目录
OUT_DIR = Path("evidence/dry_run_test") / datetime.now().strftime("%Y%m%d-%H%M%S")
OUT_DIR.mkdir(parents=True, exist_ok=True)
print(f"\n{'='*60}")
print(f"  知识库 Dry-run 测试")
print(f"  输出目录: {OUT_DIR}")
print(f"{'='*60}\n")

# Test 1: 模块导入
print("[ 1 ] 模块导入")


def test_import_knowledge():
    from src.knowledge import StateMachine, SelectorRegistry, EvidenceLoader
    assert StateMachine is not None
    assert SelectorRegistry is not None
    assert EvidenceLoader is not None


test("src.knowledge 全部导出", test_import_knowledge)


def test_import_state_machine():
    from src.knowledge.state_machine import StateMachine
    assert StateMachine is not None


test("StateMachine 可导入", test_import_state_machine)


def test_import_selector_registry():
    from src.knowledge.selector_registry import SelectorRegistry
    assert SelectorRegistry is not None


test("SelectorRegistry 可导入", test_import_selector_registry)


def test_import_evidence_loader():
    from src.knowledge.evidence_loader import EvidenceLoader
    assert EvidenceLoader is not None


test("EvidenceLoader 可导入", test_import_evidence_loader)

# Test 2: 知识库文件存在且可解析
print("\n[ 2 ] 知识库文件解析")

ADD_PRODUCT_STATES_MD = "knowledge/pages/add-product/states.md"
APP_5461_STATES_MD = "knowledge/pages/5461-application/states.md"
ADD_PRODUCT_SELECTORS = "knowledge/pages/add-product/selectors.json"
APP_5461_SELECTORS = "knowledge/pages/5461-application/selectors.json"
FIELD_MAP = "knowledge/pages/add-product/field-map.json"
PROMPT_FILE = "knowledge/prompts/page-analyzer.md"


def test_add_product_states_exists():
    assert Path(ADD_PRODUCT_STATES_MD).exists(), f"文件不存在: {ADD_PRODUCT_STATES_MD}"


test("add-product/states.md 存在", test_add_product_states_exists)


def test_5461_app_states_exists():
    assert Path(APP_5461_STATES_MD).exists(), f"文件不存在: {APP_5461_STATES_MD}"


test("5461-application/states.md 存在", test_5461_app_states_exists)


def test_add_product_selectors_exists():
    assert Path(ADD_PRODUCT_SELECTORS).exists(), f"文件不存在: {ADD_PRODUCT_SELECTORS}"


test("add-product/selectors.json 存在", test_add_product_selectors_exists)


def test_5461_app_selectors_exists():
    assert Path(APP_5461_SELECTORS).exists(), f"文件不存在: {APP_5461_SELECTORS}"


test("5461-application/selectors.json 存在", test_5461_app_selectors_exists)


def test_field_map_exists():
    assert Path(FIELD_MAP).exists(), f"文件不存在: {FIELD_MAP}"


test("add-product/field-map.json 存在", test_field_map_exists)


def test_prompt_exists():
    assert Path(PROMPT_FILE).exists(), f"文件不存在: {PROMPT_FILE}"


test("prompts/page-analyzer.md 存在", test_prompt_exists)

# Test 3: StateMachine 解析 states.md
print("\n[ 3 ] StateMachine 解析")
from src.knowledge.state_machine import StateMachine


def test_parse_add_product_states():
    sm = StateMachine(ADD_PRODUCT_STATES_MD)
    states = sm.list_states()
    assert len(states) >= 10, f"应有 >=10 个状态，实际: {len(states)}"
    print(f"    -> 解析到 {len(states)} 个状态: {states}")


test("解析 add-product states.md", test_parse_add_product_states)


def test_parse_5461_app_states():
    sm = StateMachine(APP_5461_STATES_MD)
    states = sm.list_states()
    assert len(states) >= 6, f"应有 >=6 个状态，实际: {len(states)}"
    print(f"    -> 解析到 {len(states)} 个状态: {states}")


test("解析 5461-application states.md", test_parse_5461_app_states)


def test_state_priorities():
    sm = StateMachine(ADD_PRODUCT_STATES_MD)
    login_state = sm.get_state_info("login_expired")
    assert login_state is not None, "login_expired 状态应存在"
    assert login_state["priority"] >= 800, f"login_expired 优先级应 >=800, 实际: {login_state['priority']}"
    assert login_state["is_terminal"] is True, "login_expired 应为终止状态"
    print(f"    -> login_expired priority={login_state['priority']}, terminal={login_state['is_terminal']}")


test("状态优先级解析正确", test_state_priorities)


def test_next_states_parsed():
    sm = StateMachine(ADD_PRODUCT_STATES_MD)
    state = sm.get_state_info("add_product_start")
    next_states = state.get("next_states", [])
    assert "product_identity_filled" in next_states, "add_product_start 下一状态应包含 product_identity_filled"
    assert "login_expired" in next_states, "add_product_start 下一状态应包含 login_expired"
    print(f"    -> next_states: {next_states}")


test("下一状态列表解析正确", test_next_states_parsed)


def test_recommended_action():
    sm = StateMachine(ADD_PRODUCT_STATES_MD)
    action = sm.get_recommended_action("login_expired")
    assert action["is_terminal"] is True
    assert action["is_error"] is True
    print(f"    -> action: {action}")


test("推荐动作解析正确", test_recommended_action)

# Test 4: StateMachine.match() 状态匹配
print("\n[ 4 ] StateMachine.match() 状态匹配")


def make_evidence(url="", body_text="", body_length=5000, alerts=None, interactives=None):
    return {
        "url": url,
        "body_text": body_text,
        "body_length": body_length,
        "alerts": alerts or [],
        "interactives": interactives or [],
    }


def test_match_login_expired():
    sm = StateMachine(ADD_PRODUCT_STATES_MD)
    evidence = make_evidence(
        url="https://www.amazon.com/ap/signin",
        body_text="Please sign in to continue. Email Password Sign In",
        body_length=3000,
    )
    matched = sm.match(evidence)
    assert matched == "login_expired", f"应匹配 login_expired, 实际: {matched}"


test("匹配 login_expired", test_match_login_expired)


def test_match_5461_triggered():
    sm = StateMachine(ADD_PRODUCT_STATES_MD)
    evidence = make_evidence(
        url="https://sellercentral.amazon.com/abis/listing/create/product_identity",
        body_text="You are not approved to create ASINs for this brand. 5461 Brand Authorization Required. Apply to sell.",
        body_length=8000,
    )
    matched = sm.match(evidence)
    assert matched == "5461_triggered", f"应匹配 5461_triggered, 实际: {matched}"


test("匹配 5461_triggered", test_match_5461_triggered)


def test_match_error_410001():
    sm = StateMachine(ADD_PRODUCT_STATES_MD)
    evidence = make_evidence(
        url="https://sellercentral.amazon.com/abis/listing/create/product_identity",
        body_text="An error occurred: 410001. Please try again later.",
        body_length=5000,
        alerts=[{"text": "Error 410001 occurred"}],
    )
    matched = sm.match(evidence)
    assert matched == "error_410001", f"应匹配 error_410001, 实际: {matched}"


test("匹配 error_410001", test_match_error_410001)


def test_match_error_429():
    sm = StateMachine(ADD_PRODUCT_STATES_MD)
    evidence = make_evidence(
        url="https://sellercentral.amazon.com/abis/listing/create/product_identity",
        body_text="Too Many Requests. Error 429. Please wait.",
        body_length=5000,
    )
    matched = sm.match(evidence)
    assert matched == "error_429", f"应匹配 error_429, 实际: {matched}"


test("匹配 error_429", test_match_error_429)


def test_match_blank_page():
    sm = StateMachine(ADD_PRODUCT_STATES_MD)
    evidence = make_evidence(
        url="https://sellercentral.amazon.com/abis/listing/create/product_identity",
        body_text="",
        body_length=500,
    )
    matched = sm.match(evidence)
    assert matched == "blank_page", f"应匹配 blank_page, 实际: {matched}"


test("匹配 blank_page", test_match_blank_page)


def test_match_add_product_start():
    sm = StateMachine(ADD_PRODUCT_STATES_MD)
    evidence = make_evidence(
        url="https://sellercentral.amazon.com/abis/listing/create/product_identity",
        body_text="Add a Product. I'm adding a product not sold on Amazon. Product type category.",
        body_length=8000,
        interactives=[
            {"tag": "input", "text": "", "dataCy": "", "ariaLabel": "Product Title"},
            {"tag": "button", "text": "Next", "dataCy": "next-button"},
        ],
    )
    matched = sm.match(evidence)
    assert matched == "add_product_start", f"应匹配 add_product_start, 实际: {matched}"


test("匹配 add_product_start", test_match_add_product_start)


def test_match_server_error():
    sm = StateMachine(ADD_PRODUCT_STATES_MD)
    evidence = make_evidence(
        url="https://sellercentral.amazon.com/abis/listing/create/product_identity",
        body_text="An error occurred. We're sorry, something went wrong.",
        body_length=5000,
    )
    matched = sm.match(evidence)
    assert matched == "server_error", f"应匹配 server_error, 实际: {matched}"


test("匹配 server_error", test_match_server_error)


def test_match_all_returns_sorted():
    sm = StateMachine(ADD_PRODUCT_STATES_MD)
    evidence = make_evidence(
        url="https://www.amazon.com/ap/signin",
        body_text="Please sign in. Also 410001 error occurred.",
        body_length=3000,
    )
    all_matches = sm.match_all(evidence)
    assert len(all_matches) >= 2, f"应匹配多个状态，实际: {all_matches}"
    # login_expired 优先级最高，应在第一位
    assert all_matches[0] == "login_expired", f"第一个匹配应为 login_expired, 实际: {all_matches[0]}"
    print(f"    -> all matches: {all_matches}")


test("match_all 按优先级排序", test_match_all_returns_sorted)


def test_match_5461_form_ready():
    sm = StateMachine(APP_5461_STATES_MD)
    evidence = make_evidence(
        url="https://sellercentral.amazon.com/sq/approvalrequest",
        body_text="Listing approval. Brand Authorization. Apply to sell.",
        body_length=10000,
        interactives=[
            {"tag": "kat-input", "text": "", "dataCy": "level-0:question-1:text"},
            {"tag": "kat-input", "text": "", "dataCy": "level-0:question-2:text"},
            {"tag": "kat-input", "text": "", "dataCy": "level-0:question-3:text"},
            {"tag": "kat-button", "text": "Submit", "dataCy": "listing-approval-submit-button"},
        ],
    )
    matched = sm.match(evidence)
    assert matched == "form_ready", f"应匹配 form_ready, 实际: {matched}"


test("匹配 5461 form_ready", test_match_5461_form_ready)


def test_match_5461_case_created():
    sm = StateMachine(APP_5461_STATES_MD)
    evidence = make_evidence(
        url="https://sellercentral.amazon.com/sq/approvalrequest",
        body_text="Your application has been submitted. Case ID: AB-1234567890. Thank you.",
        body_length=5000,
    )
    matched = sm.match(evidence)
    assert matched == "case_created", f"应匹配 case_created, 实际: {matched}"


test("匹配 5461 case_created", test_match_5461_case_created)

# Test 5: SelectorRegistry
print("\n[ 5 ] SelectorRegistry")
from src.knowledge.selector_registry import SelectorRegistry


def test_registry_load():
    reg = SelectorRegistry(ADD_PRODUCT_SELECTORS)
    names = reg.list_names()
    assert len(names) >= 8, f"应有 >=8 个选择器，实际: {len(names)}"
    print(f"    -> 注册选择器: {names}")


test("加载 add-product selectors.json", test_registry_load)


def test_get_ordered_selectors():
    reg = SelectorRegistry(ADD_PRODUCT_SELECTORS)
    selectors = reg.get_ordered_selectors("brand_input")
    assert len(selectors) >= 2, f"brand_input 应有 >=2 个选择器，实际: {selectors}"
    assert "kat-input[data-cy='listing-approval-brand-name-input']" in selectors, "应有主选择器"
    print(f"    -> brand_input: {selectors}")


test("get_ordered_selectors brand_input", test_get_ordered_selectors)


def test_get_ordered_selectors_next_button():
    reg = SelectorRegistry(ADD_PRODUCT_SELECTORS)
    selectors = reg.get_ordered_selectors("next_button")
    assert len(selectors) >= 2, f"next_button 应有 >=2 个选择器，实际: {selectors}"
    print(f"    -> next_button: {selectors}")


test("get_ordered_selectors next_button", test_get_ordered_selectors_next_button)


def test_get_ordered_selectors_apply_to_sell():
    reg = SelectorRegistry(ADD_PRODUCT_SELECTORS)
    selectors = reg.get_ordered_selectors("apply_to_sell_button")
    assert len(selectors) >= 2, f"apply_to_sell_button 应有 >=2 个选择器，实际: {selectors}"
    print(f"    -> apply_to_sell_button: {selectors}")


test("get_ordered_selectors apply_to_sell_button", test_get_ordered_selectors_apply_to_sell)


def test_get_ordered_selectors_connect_brand():
    reg = SelectorRegistry(ADD_PRODUCT_SELECTORS)
    selectors = reg.get_ordered_selectors("connect_brand_radio")
    assert len(selectors) >= 1, f"connect_brand_radio 应有 >=1 个选择器，实际: {selectors}"
    print(f"    -> connect_brand_radio: {selectors}")


test("get_ordered_selectors connect_brand_radio", test_get_ordered_selectors_connect_brand)


def test_get_all_config():
    reg = SelectorRegistry(ADD_PRODUCT_SELECTORS)
    config = reg.get_all("brand_input")
    assert config is not None
    assert config.get("shadow_dom") is True
    assert config.get("risk") == "low"
    print(f"    -> brand_input config: shadow_dom={config['shadow_dom']}, risk={config['risk']}")


test("get_all 返回完整配置", test_get_all_config)


def test_is_high_risk():
    reg = SelectorRegistry(ADD_PRODUCT_SELECTORS)
    assert reg.is_high_risk("submit_button") is False  # add-product 中没有 submit_button
    # 5461 中的 submit_button 是 high risk
    reg2 = SelectorRegistry(APP_5461_SELECTORS)
    assert reg2.is_high_risk("submit_button") is True, "5461 的 submit_button 应为 high risk"


test("is_high_risk 判断", test_is_high_risk)


def test_has_shadow_dom():
    reg = SelectorRegistry(ADD_PRODUCT_SELECTORS)
    assert reg.has_shadow_dom("brand_input") is True
    assert reg.has_shadow_dom("connect_brand_radio") is False


test("has_shadow_dom 判断", test_has_shadow_dom)


def test_5461_selectors_both_flows():
    """验证 5461 selectors.json 同时包含旧 abis 和新 /sq/approvalrequest 的选择器"""
    reg = SelectorRegistry(APP_5461_SELECTORS)
    selectors = reg.get_ordered_selectors("brand_input")
    # 应同时包含旧流程和新流程的选择器
    has_old = any("listing-approval-brand-name-input" in s for s in selectors)
    has_new = any("level-0:question-1" in s for s in selectors)
    assert has_old, "应包含旧 abis 流程选择器"
    assert has_new, "应包含新 /sq/approvalrequest 流程选择器"
    print(f"    -> brand_input (5461): old={has_old}, new={has_new}")


test("5461 selectors 包含两套流程", test_5461_selectors_both_flows)


def test_fallback_text():
    reg = SelectorRegistry(ADD_PRODUCT_SELECTORS)
    texts = reg.get_fallback_text("no_product_id_checkbox")
    assert len(texts) >= 3, f"应有 >=3 个 fallback_text，实际: {texts}"
    assert "No Product ID" in texts
    print(f"    -> fallback_text: {texts}")


test("fallback_text 返回正确", test_fallback_text)

# Test 6: EvidenceLoader 读取真实证据
print("\n[ 6 ] EvidenceLoader 读取真实证据")
from src.knowledge.evidence_loader import EvidenceLoader


def test_loader_init():
    loader = EvidenceLoader("evidence")
    assert loader is not None


test("EvidenceLoader 初始化", test_loader_init)


def test_load_latest_real_evidence():
    """尝试加载今天生成的真实证据包"""
    loader = EvidenceLoader("evidence")
    # 尝试加载 MP-MALL 的证据
    latest = loader.load_latest("us_store_570", "MP-MALL", "01_add_product_start")
    if latest:
        print(f"    -> 加载到证据: url={latest.get('url', 'N/A')[:60]}...")
        assert "url" in latest
        assert "timestamp" in latest
    else:
        # 尝试其他节点
        latest = loader.load_latest("us_store_570", "MP-MALL", "08_form_filled")
        if latest:
            print(f"    -> 加载到证据 (08_form_filled): url={latest.get('url', 'N/A')[:60]}...")
        else:
            print(f"    -> 未找到 MP-MALL 的指定节点证据，尝试列出可用节点")
            nodes = loader.list_nodes("us_store_570", "MP-MALL")
            print(f"    -> 可用节点: {nodes[:10]}")
            if nodes:
                latest = loader.load_latest("us_store_570", "MP-MALL", nodes[0])
                if latest:
                    print(f"    -> 加载到证据 ({nodes[0]}): url={latest.get('url', 'N/A')[:60]}...")


test("加载最新真实证据", test_load_latest_real_evidence)


def test_load_all_for_brand():
    loader = EvidenceLoader("evidence")
    all_ev = loader.load_all("us_store_570", "MP-MALL")
    print(f"    -> MP-MALL 共 {len(all_ev)} 条证据")
    # 不强制断言数量，因为证据可能不在标准目录结构


test("加载品牌所有证据", test_load_all_for_brand)


def test_list_accounts():
    loader = EvidenceLoader("evidence")
    accounts = loader.list_accounts()
    print(f"    -> 账号列表: {accounts[:10]}")
    assert isinstance(accounts, list)


test("列出所有账号", test_list_accounts)


def test_find_errors():
    loader = EvidenceLoader("evidence")
    errors = loader.find_errors(since_days=7)
    print(f"    -> 近7天错误证据: {len(errors)} 条")
    if errors:
        print(f"    -> 最新错误: {errors[0].get('_meta', {})}")


test("查找近期错误证据", test_find_errors)


def test_list_brands():
    loader = EvidenceLoader("evidence")
    accounts = loader.list_accounts()
    if accounts:
        brands = loader.list_brands(accounts[0])
        print(f"    -> 账号 {accounts[0]} 的品牌: {brands[:10]}")


test("列出账号下品牌", test_list_brands)

# Test 7: Field Map
print("\n[ 7 ] Field Map")


def test_field_map_parseable():
    with open(FIELD_MAP, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert "brand_name" in data
    assert "product_title" in data
    assert data["brand_name"]["brand_pack_key"] == "brand"
    print(f"    -> 字段数: {len(data)}")


test("field-map.json 可解析", test_field_map_parseable)

# Test 8: 集成测试 - 端到端
print("\n[ 8 ] 集成测试")


def test_end_to_end_state_flow():
    """模拟完整的 Add Product -> 5461 -> Submit 状态流"""
    sm_add = StateMachine(ADD_PRODUCT_STATES_MD)
    sm_5461 = StateMachine(APP_5461_STATES_MD)
    reg = SelectorRegistry(ADD_PRODUCT_SELECTORS)
    reg_5461 = SelectorRegistry(APP_5461_SELECTORS)

    # Step 1: Add Product 开始
    ev1 = make_evidence(
        url="https://sellercentral.amazon.com/abis/listing/create/product_identity",
        body_text="Add a Product. I'm adding a product not sold on Amazon.",
        body_length=8000,
    )
    state1 = sm_add.match(ev1)
    assert state1 == "add_product_start"

    # Step 2: 填写后 -> 触发 5461
    ev2 = make_evidence(
        url="https://sellercentral.amazon.com/abis/listing/create/product_identity",
        body_text="You are not approved to create ASINs for this brand. 5461 Brand Authorization Required.",
        body_length=8000,
        interactives=[{"tag": "kat-button", "text": "Apply to sell", "dataCy": "seller-qualification-path-forward-button"}],
    )
    state2 = sm_add.match(ev2)
    assert state2 == "5461_triggered"

    # 获取 Apply to sell 选择器
    selectors = reg.get_ordered_selectors("apply_to_sell_button")
    assert len(selectors) > 0

    # Step 3: 5461 表单加载
    ev3 = make_evidence(
        url="https://sellercentral.amazon.com/sq/approvalrequest",
        body_text="Listing approval. Brand Authorization.",
        body_length=10000,
        interactives=[
            {"tag": "kat-input", "text": "", "dataCy": "level-0:question-1:text"},
            {"tag": "kat-input", "text": "", "dataCy": "level-0:question-2:text"},
            {"tag": "kat-button", "text": "Submit", "dataCy": "listing-approval-submit-button"},
        ],
    )
    state3 = sm_5461.match(ev3)
    assert state3 == "form_ready"

    # Step 4: 提交成功
    ev4 = make_evidence(
        url="https://sellercentral.amazon.com/sq/approvalrequest",
        body_text="Your application has been submitted. Case ID: AB-1234567890.",
        body_length=5000,
    )
    state4 = sm_5461.match(ev4)
    assert state4 == "case_created"

    print(f"    -> 状态流: {state1} -> {state2} -> {state3} -> {state4}")


test("端到端状态流", test_end_to_end_state_flow)


def test_terminal_state_handling():
    sm = StateMachine(ADD_PRODUCT_STATES_MD)
    assert sm.is_terminal("login_expired") is True
    assert sm.is_terminal("add_product_start") is False
    assert sm.is_error("error_410001") is True
    assert sm.is_error("error_429") is True
    assert sm.is_error("add_product_start") is False
    print(f"    -> terminal/error 判断正确")


test("终止状态/错误状态判断", test_terminal_state_handling)

# 汇总
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
print(f"\n  输出目录: {OUT_DIR.resolve()}")
print(f"{'='*60}\n")

sys.exit(0 if failed == 0 else 1)
