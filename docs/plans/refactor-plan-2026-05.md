# Amazon 5461 自动化项目改造方案

> 版本：v1.1 | 更新：2026-05-19
> 阶段1-4 已全部完成（88/88 测试通过）

## 完成状态

| 阶段 | 状态 | 提交 |
|------|------|------|
| 阶段 1：证据采集基础设施 | ✅ 完成 | 659829b..1f74dd0 |
| 阶段 2：页面知识库 + 状态机 | ✅ 完成 | 53a7047 |
| 阶段 3：执行引擎（状态循环） | ✅ 完成 | 509eb3f..4bdbced |
| 阶段 4：旁路监控常态化 | ✅ 完成 | bb99c24 |
| 阶段 5：清理归档 | ⬜ 待做 | — |

> **待验证**：StateLoopExecutor 真实浏览器链路（Mock 14/14 通过，真实浏览器明天跑 MP-MALL 时验证）

---
## 一、现状诊断（基于 2026-05-18 代码实测）

| 模块 | 当前状态 | 问题 |
|------|---------|------|
| `src/flow_submit_5461.py` | 2983 行，单文件硬编码全流程 | 一个函数走到底，异常时无状态恢复能力；Case ID 提取、Declined 检测、Connect brand 等逻辑全部耦合在一起 |
| `src/form_filler.py` | 1385 行，表单填写专用 | 与流程逻辑耦合，缺乏"页面状态验证后再填写"的闭环 |
| `src/page_matcher.py` | 40 行，纯条件匹配 | 有 URL/text/element 匹配函数，但无状态机定义，条件散落在各处 |
| `src/evidence.py` | 仅建目录 | 不采集 DOM、不记录网络、不保存结构化摘要 |
| `config/selectors/flow_5461.yaml` | 扁平选择器列表 | 无 fallback 链，无风险标记，无 last_verified 时间戳 |
| `src/amazon_error_handler.py` | 已落地（2026-05-18） | 仅覆盖服务端错误页面，未覆盖 DOM 变化监听、网络请求采集 |
| `scripts/run_full_5461_batch.py` | 603 行 | 已支持独立页面 + anchor 页机制，但状态追踪靠日志文本，无结构化证据包 |
| 根目录 | 100+ 临时文件 | 截图、txt、debug log 混杂，无分类归档规则 |
| `knowledge/` | **不存在** | 核心缺口：没有页面知识库 |

**核心矛盾**：脚本每次都在"凭代码猜页面"，而不是"读知识库判状态"。
- 选择器失效 → 直接抛异常 → 需要人工看截图猜原因
- 遇到新页面变体（UK 旧版 UI、Connect brand 弹窗）→ 需要现场改代码
- 异常样本只存在于聊天记录和截图里 → 无法沉淀为知识

---

## 二、改造目标

```
改造前：
  脚本 → 截图 → 人工看 → 改代码 → 再跑

改造后：
  脚本 → 采集证据包 → 匹配状态机 → 查选择器注册表 → 执行 → 验证 → 异常时 AI 分析证据包 → 更新知识库
```

---

## 三、总体架构

```
projects/amazon-5461-bot/
├── src/
│   ├── capture/              # NEW — 页面证据采集层
│   │   ├── __init__.py
│   │   ├── page_capture.py   # DOM 快照 + 截图 + 文本
│   │   ├── monitor.py        # MutationObserver + 实时监听
│   │   ├── network_capture.py# 网络请求摘要
│   │   └── redact.py         # 敏感信息脱敏
│   ├── knowledge/            # NEW — 知识库读取层
│   │   ├── __init__.py
│   │   ├── state_machine.py  # states.md 解析 + 状态匹配
│   │   ├── selector_registry.py # selectors.json 读取 + fallback 链
│   │   └── evidence_loader.py   # 历史证据包加载
│   ├── executor/             # NEW — 执行引擎（渐进迁移）
│   │   ├── __init__.py
│   │   ├── state_loop.py     # 状态循环主控
│   │   └── action_runner.py  # 选择器执行 + 重试 + 验证
│   ├── flow_submit_5461.py   # EXISTING — 逐步拆解到 executor/
│   ├── form_filler.py        # EXISTING — 保留，增加状态校验入口
│   └── ...
├── knowledge/                # NEW — 页面知识库（文本+JSON，可版本控制）
│   ├── pages/
│   │   ├── add-product/
│   │   │   ├── states.md
│   │   │   ├── selectors.json
│   │   │   ├── field-map.json
│   │   │   └── examples/
│   │   │       ├── normal/
│   │   │       ├── 5461/
│   │   │       ├── 429/
│   │   │       ├── 410001/
│   │   │       ├── connect-brand/
│   │   │       └── declined/
│   │   └── 5461-application/
│   │       ├── states.md
│   │       ├── selectors.json
│   │       └── examples/
│   ├── prompts/
│   │   ├── page-analyzer.md
│   │   ├── selector-repair.md
│   │   └── error-classifier.md
│   └── runs/                 # 每次执行的证据归档（gitignored）
├── evidence/                 # EXISTING — 运行时证据产出
│   └── 2026-05-18/
│       └── {account}/{brand}/
│           ├── 01_add_product_start/
│           │   ├── screenshot.png
│           │   ├── dom_snapshot.json
│           │   ├── page_text.txt
│           │   ├── network.jsonl
│           │   └── summary.json
│           ├── 02_5461_triggered/
│           └── ...
├── scripts/
│   ├── run_full_5461_batch.py      # EXISTING — 批量入口
│   ├── monitor_sellercentral.py    # NEW — 旁路监控工具
│   └── capture_page.py             # NEW — 单次页面采集 CLI
└── config/selectors/
    └── flow_5461.yaml              # DEPRECATED → 迁移到 knowledge/pages/*/selectors.json
```

---

## 四、改造阶段（渐进式，不中断现有流程）

### 阶段 1：建立证据采集基础设施（2-3 天）

**目标**：让脚本在每一步关键节点自动产出结构化证据包，替代"只有截图"的现状。

#### 1.1 新建 `src/capture/`

**`page_capture.py`** — 页面快照采集器

```python
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

@dataclass
class PageEvidence:
    url: str
    title: str
    body_text: str          # 脱敏后纯文本
    headings: list          # h1/h2/h3 摘要
    alerts: list            # 错误提示
    interactives: list      # 可交互元素（含 selectorHints）
    screenshot_path: Optional[str] = None
    timestamp: str = ""

class PageCapture:
    def __init__(self, page, redact_fn=None):
        self.page = page
        self.redact = redact_fn or default_redact

    def capture(self, name: str, out_dir: Path) -> PageEvidence:
        """采集一次完整证据包，写入磁盘，返回结构化数据"""
        ...
```

功能要点：
- 复用 `evidence.py` 的目录生成逻辑，但增加 `dom_snapshot.json`
- 通过 `page.evaluate()` 注入 JS 提取 `headings + interactives + alerts + bodyText`
- **强制脱敏**：邮箱、token、cookie、Authorization 头替换为 `[REDACTED]`
- 同时产出：
  - `screenshot.png` — 全页截图
  - `dom_snapshot.json` — 结构化 DOM 摘要
  - `page_text.txt` — 纯文本，方便 AI 直接读
  - `summary.json` — URL + title + alertCount + interactiveCount + detected_states

**`monitor.py`** — 实时 DOM 监听

- 在页面注入 `MutationObserver`，监听 childList/attributes/characterData
- 变化事件收集到 `window.__sellerMonitorEvents`，Python 端定时 `drain`
- 用于检测：按钮突然出现、错误提示弹出、表单加载完成、页面跳转

**`network_capture.py`** — 网络摘要

- 绑定 `page.on("request")` / `page.on("response")` / `page.on("requestfailed")`
- 只记录：method、URL（脱敏）、status、content-type、resourceType
- **不记录**：完整 headers、请求体、response body

**`redact.py`** — 统一脱敏规则

```python
SENSITIVE_PATTERNS = [
    r"AKIA[0-9A-Z]{16}",
    r"(?i)authorization:\s*bearer\s+[a-z0-9._\-]+",
    r"(?i)x-amz-access-token[:=]\s*[a-z0-9._\-]+",
    r"(?i)csrf[-_]?token[:=]\s*[a-z0-9._\-]+",
    r"(?i)cookie[:=].+",
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
]
```

#### 1.2 改造 `src/evidence.py`

从"只建目录"升级为"产出完整证据包"：

```python
def capture_evidence(page, name: str, root: str, account: str, brand: str) -> dict:
    """一键采集当前页面完整证据包"""
    out_dir = build_evidence_dir(root, account, brand, name)
    capture = PageCapture(page)
    evidence = capture.capture(name, out_dir)
    return asdict(evidence)
```

#### 1.3 新建 CLI 工具

**`scripts/capture_page.py`** — 单次采集（用于人工操作时旁路记录）

```bash
python scripts/capture_page.py \
  --cdp http://127.0.0.1:9222 \
  --out evidence/manual \
  --name "uk-old-ui-check"
```

**`scripts/monitor_sellercentral.py`** — 实时监控（参考文档 2 的完整实现）

```bash
python scripts/monitor_sellercentral.py \
  --cdp http://127.0.0.1:9222 \
  --out evidence/live-monitor \
  --interval 2 \
  --screenshot-interval 30
```

产出：
```
evidence/live-monitor/20260518-143022/
├── events.jsonl          # DOM 变化 + 快照变化事件
├── network.jsonl         # 请求/响应/失败
├── console.jsonl         # Console 日志
├── snapshots/            # 每次页面变化时的 dom_snapshot.json
└── screenshots/          # 每 30s 全页截图
```

#### 1.4 接入现有流程

在 `flow_submit_5461.py` 的关键节点插入 `capture_evidence()`：

| 阶段 | 节点 | 证据包名称 |
|------|------|-----------|
| 1 | Add Product 页面加载完成 | `01_add_product_start` |
| 1 | Product Identity 表单填写前 | `02_before_fill` |
| 1 | Product Identity 表单填写后 | `03_after_fill` |
| 2 | 5461 弹窗/提示出现 | `04_5461_triggered` |
| 2 | Apply to sell 点击后 | `05_apply_clicked` |
| 2 | Connect brand 弹窗出现 | `06_connect_brand` |
| 3 | 5461 申请表加载完成 | `07_form_loaded` |
| 3 | 表单填写完成 | `08_form_filled` |
| 3 | 提交后 | `09_after_submit` |
| - | 异常发生时 | `XX_error_{timestamp}` |

**安全边界**：证据采集是"旁路"行为，不阻塞主流程；采集失败不影响业务逻辑。

---

### 阶段 2：建立页面知识库 + 状态机（3-4 天）

**目标**：把"散落在代码里的页面判断逻辑"沉淀为可版本控制的文本知识库。

#### 2.1 新建 `knowledge/pages/`

**`knowledge/pages/add-product/states.md`**

```markdown
# Add Product 页面状态机

## state: add_product_start
- **识别**: URL 含 `/add-products` 或 `/product-type-chooser`
- **信号**: 页面有 "Add a Product" / "I'm adding a product not sold on Amazon"
- **动作**: 填写 Item Name → Brand → 勾选 No Product ID → 选 Browse Node
- **下一状态**: `product_identity_filled` / `5461_triggered` / `login_expired`
- **停止条件**: 429 / 410001 / 空白页

## state: product_identity_filled
- **识别**: Item Name / Brand 已填入，Browse Node 已选择
- **信号**: "Next" 按钮变为可点击
- **动作**: 点击 Next
- **下一状态**: `description_page` / `5461_triggered` / `brand_choice`

## state: 5461_triggered
- **识别**: 页面文字含 "5461" 或 "not approved to create ASINs"
- **信号**: 出现 "Request approval" / "Apply to sell" / "Brand Authorization Required"
- **动作**: 点击 Apply to sell（或 Request approval）
- **下一状态**: `5461_form` / `connect_brand` / `already_approved`
- **禁止**: 高频刷新、重复提交

## state: connect_brand
- **识别**: 出现 "Clarify which brand" / `brex-widget` / `div[data-cy="brex-widget"]`
- **信号**: 多个品牌单选选项
- **动作**: 按 `brand_selection_keywords` 匹配 → 点击 radio → 点击 "Connect this brand"
- **下一状态**: `5461_form`
- **停止条件**: 无匹配品牌 → 人工

## state: already_approved
- **识别**: 直接进入 Description 页面，无 5461 提示
- **信号**: URL 含 `/description`，无 "not approved"
- **动作**: 记录 "已有权限"，跳过
- **下一状态**: `done`

## state: declined_case_shown
- **识别**: 弹窗/页面出现 "Declined" + Case ID
- **信号**: Case ID 附近 30 字符内含 "declined" / "rejected" / "denied"
- **动作**: 标记为 declined → 关闭弹窗 → 随机等待 → 重新点击 Apply to sell
- **下一状态**: `5461_form` / `error_410001`

## state: login_expired
- **识别**: URL 含 `/signin` 或页面有登录表单
- **动作**: 暂停，通知人工

## state: error_410001
- **识别**: 页面/console 含 "410001"
- **动作**: 等待 30-60s 后重试，连续 3 次则暂停该品牌
- **冷却**: 品牌间至少 30s

## state: error_429
- **识别**: 页面/console 含 "429" / "Too Many Requests"
- **动作**: 品牌间冷却 30s，降低并发

## state: blank_page
- **识别**: 页面正文为空或 bodyText 长度 < 2000
- **动作**: 等待 10s 刷新，连续 3 次则暂停
```

**`knowledge/pages/add-product/selectors.json`** — 带 fallback 链的选择器注册表

```json
{
  "brand_input": {
    "preferred": ["kat-input[data-cy='listing-approval-brand-name-input']"],
    "fallback": [
      "kat-input[aria-label*='Brand']",
      "input[name*='brand']",
      "input[placeholder*='Brand']"
    ],
    "fallback_text": ["Brand name", "品牌"],
    "shadow_dom": true,
    "last_verified": "2026-05-18",
    "verified_sites": ["US", "MX", "UK"]
  },
  "item_name_input": {
    "preferred": ["kat-input[data-cy='item-name-input']"],
    "fallback": [
      "kat-input[aria-label*='Item Name']",
      "input[name='itemName']",
      "textarea[aria-label*='Product title']"
    ],
    "fallback_text": ["Item Name", "Product title", "商品名称"],
    "shadow_dom": true,
    "last_verified": "2026-05-18"
  },
  "apply_to_sell_button": {
    "preferred": ["kat-button[data-cy='seller-qualification-path-forward-button']"],
    "fallback": [
      "button:has-text('Apply to sell')",
      "a:has-text('Apply')",
      "button:has-text('Request approval')",
      "kat-button:has-text('Apply')"
    ],
    "last_verified": "2026-05-18"
  },
  "submit_button": {
    "preferred": ["kat-button[data-cy='listing-approval-submit-button']"],
    "fallback": [
      "kat-button:has-text('Submit')",
      "button:has-text('Submit')"
    ],
    "risk": "high",
    "requires_human_confirmation": false,
    "last_verified": "2026-05-18"
  },
  "next_button": {
    "preferred": ["kat-button[data-cy='next-button']"],
    "fallback": [
      "button:has-text('Next')",
      "input[type='submit']",
      "kat-button:has-text('Next')"
    ],
    "shadow_dom_note": "disabled 时需 shadowRoot 内层点击",
    "last_verified": "2026-05-18"
  },
  "no_product_id_checkbox": {
    "preferred": ["input[type='checkbox'][name*='no_product_id']"],
    "fallback": [
      "kat-checkbox[aria-label*='No Product ID']",
      "input[type='checkbox']"
    ],
    "fallback_text": ["No Product ID", "I don't have a product ID", "我没有商品编码"],
    "last_verified": "2026-05-18"
  },
  "connect_brand_radio": {
    "preferred": ["input[type='radio'][name='brand-record-selection-radio-button']"],
    "fallback": [
      "kat-radiobutton"
    ],
    "last_verified": "2026-05-18"
  },
  "connect_brand_button": {
    "preferred": ["kat-button[data-testid='connect-brand-button']"],
    "fallback": [
      "button:has-text('Connect this brand')"
    ],
    "last_verified": "2026-05-18"
  }
}
```

**`knowledge/pages/add-product/field-map.json`** — 页面字段 ↔ 资料库字段映射

```json
{
  "product_title": {
    "page_fields": ["Item Name", "Product title"],
    "brand_pack_key": "item_name",
    "required": true
  },
  "brand_name": {
    "page_fields": ["Brand name"],
    "brand_pack_key": "brand",
    "required": true
  },
  "manufacturer": {
    "page_fields": ["Manufacturer"],
    "brand_pack_key": "manufacturer",
    "required": true
  },
  "description": {
    "page_fields": ["Description", "Product description"],
    "brand_pack_key": "description",
    "required": true
  },
  "email": {
    "page_fields": ["Email", "Contact email"],
    "brand_pack_key": "contact_email",
    "required": true
  },
  "statement": {
    "page_fields": ["Statement", "Brand statement"],
    "brand_pack_key": "statement",
    "required": true
  }
}
```

#### 2.2 新建 `src/knowledge/`

**`state_machine.py`** — 解析 states.md，执行状态匹配

```python
class StateMachine:
    def __init__(self, states_md_path: str):
        self.states = self._parse_states(states_md_path)

    def match(self, evidence: PageEvidence) -> str:
        """根据证据包匹配当前状态，返回状态名"""
        # 按优先级遍历状态定义
        # 先匹配系统异常（login_expired, error_429, error_410001, blank_page）
        # 再匹配业务状态
        ...

    def get_action(self, state: str) -> dict:
        """返回该状态对应的推荐动作"""
        ...
```

**`selector_registry.py`** — 读取 selectors.json，执行 fallback 链

```python
class SelectorRegistry:
    def __init__(self, selectors_json_path: str):
        self.registry = json.load(open(selectors_json_path))

    def find(self, name: str, page) -> Optional[str]:
        """
        按优先级尝试选择器：
        1. preferred（逐个尝试）
        2. fallback（逐个尝试）
        3. fallback_text（通过文本内容匹配）
        4. 全部失败 → 返回 None，触发 AI 修复流程
        """
        ...

    def mark_verified(self, name: str, site: str):
        """当某个选择器在某站点验证成功后，更新 last_verified"""
        ...
```

#### 2.3 三类样本归档规则

```
knowledge/pages/add-product/examples/
├── normal/                 # 正常样本
│   ├── 01_add_product_start/
│   └── 02_submitted/
├── 5461/                   # 预期异常
│   └── 5461_triggered/
├── 429/                    # 系统异常
├── 410001/
├── connect-brand/          # 2026-05-18 新发现的变体
├── declined/               # Declined Case 弹窗
└── uk-old-ui/              # UK 站旧版 UI 变体
```

每个样本是一个完整证据包（screenshot + dom_snapshot.json + page_text.txt + summary.json）。

---

### 阶段 3：改造执行流程为状态循环（5-7 天）

**目标**：`flow_submit_5461.py` 从"一个函数走到底"改为"状态循环"。

#### 3.1 状态循环架构

```python
# src/executor/state_loop.py

class StateLoopExecutor:
    def __init__(self, page, knowledge_base, brand_data):
        self.page = page
        self.kb = knowledge_base
        self.brand_data = brand_data
        self.capture = PageCapture(page)
        self.registry = SelectorRegistry("knowledge/pages/add-product/selectors.json")
        self.state_machine = StateMachine("knowledge/pages/add-product/states.md")

    def run(self) -> dict:
        max_steps = 30
        for step in range(max_steps):
            # 1. 采集证据
            evidence = self.capture.capture(f"step_{step:02d}", self.out_dir)

            # 2. 匹配状态
            state = self.state_machine.match(evidence)
            print(f"[Step {step}] 当前状态: {state}")

            # 3. 检查终止条件
            if state in ("done", "login_expired", "error_429_stop", "blank_page_stop"):
                return {"result": state, "evidence": evidence}

            # 4. 获取动作
            action = self.state_machine.get_action(state)

            # 5. 执行动作
            success = self.execute_action(action, evidence)

            # 6. 验证结果
            evidence_after = self.capture.capture(f"step_{step:02d}_after", self.out_dir)
            new_state = self.state_machine.match(evidence_after)

            if new_state == state:
                # 状态未变化，可能执行失败
                if not self.handle_stuck(state, evidence, evidence_after):
                    return {"result": "stuck", "state": state}

            # 7. 更新知识库（如选择器验证成功）
            self.update_knowledge(state, action, success)

        return {"result": "max_steps_reached"}
```

#### 3.2 与现有代码的衔接策略（渐进迁移）

不一次性重写 2983 行的 `flow_submit_5461.py`，而是：

**Step A：先包装现有函数**

将 `flow_submit_5461.py` 中的大阶段函数标记为"legacy action"：

```python
# flow_submit_5461.py 内部
_LEGACY_ACTIONS = {
    "fill_product_identity": fill_product_identity_form,      # 阶段 1
    "trigger_5461": handle_5461_trigger,                       # 阶段 2 前半
    "connect_brand": connect_brand_in_5461_panel,             # 阶段 2 中
    "fill_5461_form": fill_katal_application_form,            # 阶段 3
    "submit": submit_application,                              # 阶段 3 末
}
```

**Step B：状态循环调用 legacy action**

```python
# executor/state_loop.py 中的 execute_action

def execute_action(self, action, evidence):
    if action["type"] == "legacy":
        # 调用现有函数，但前后自动采集证据
        func = _LEGACY_ACTIONS[action["name"]]
        return func(self.page, self.brand_data)
    elif action["type"] == "selector_click":
        sel = self.registry.find(action["selector_name"], self.page)
        if sel:
            self.page.click(sel)
            return True
        return False
    elif action["type"] == "ai_review":
        # 触发人工/AI 审查
        return self.pause_for_review(evidence)
```

**Step C：逐步拆解 legacy action**

- 第 1 周：`fill_product_identity_form` 拆解为多个 selector_click + verify 小步骤
- 第 2 周：`fill_katal_application_form` 拆解
- 第 3 周：移除 legacy 包装，完全状态化

#### 3.3 失败时的 AI 分析流程

当状态循环卡住时，自动触发：

```python
def handle_stuck(self, state, evidence_before, evidence_after):
    # 1. 保存异常证据包到 knowledge/pages/*/examples/{异常类型}/
    self.archive_error_sample(state, evidence_before, evidence_after)

    # 2. 生成 AI 分析请求
    ai_prompt = self.build_analyzer_prompt(evidence_before, evidence_after, state)

    # 3. 输出到日志/OpenClaw
    print("[AI分析] 请将以下证据包提交给 OpenClaw 分析:")
    print(f"  证据路径: {self.out_dir}")
    print(f"  当前状态: {state}")
    print(f"  预期下一状态: {self.state_machine.get_expected_next(state)}")

    # 4. 等待修复建议（人工或 AI）
    # OpenClaw 可以读取 evidence 目录，分析后输出新的选择器或状态规则
    return False  # 暂停，等待人工介入
```

---

### 阶段 4：旁路监控常态化（2-3 天，可与阶段 1 并行）

**目标**：运行脚本时，同时启动监控进程，记录完整上下文。

#### 4.1 集成到批量脚本

在 `run_full_5461_batch.py` 中：

```python
# 启动品牌前，开启监控
monitor = None
if config.get("enable_monitor", True):
    monitor = start_monitor(cdp_url, out_dir=f"evidence/live-monitor/{run_id}")

try:
    result = submit_5461_from_add_product(...)
finally:
    if monitor:
        monitor.stop()
        # 监控数据与品牌证据合并
        merge_monitor_data(monitor.out_dir, evidence_dir)
```

#### 4.2 监控数据的用途

- **诊断 410001/429**：查看 network.jsonl 中哪个请求返回错误
- **诊断表单未加载**：查看 events.jsonl 中 DOM 是否在点击后有变化
- **诊断 Shadow DOM 问题**：查看 dom_snapshot.json 中 selectorHints
- **事后复盘**：不用再看聊天记录里的截图，直接查 evidence 目录

---

### 阶段 5：清理与归档（1-2 天）

#### 5.1 根目录文件清理

| 文件模式 | 处理方案 |
|---------|---------|
| `_*.png`, `_*.txt`, `debug_*.png` | 有证据价值的移入 `evidence/archive/2026-05-before-refactor/` |
| `*_log.txt` | 有错误样本价值的，提取关键页移至 `knowledge/examples/` |
| `_rewrite_connect.py` | 若已合并到 `flow_submit_5461.py`，删除 |
| 根目录 `.png`/`.txt` | 确认无价值后删除 |

#### 5.2 废弃 `config/selectors/flow_5461.yaml`

迁移路径：
1. 将 `flow_5461.yaml` 内容转换为 `knowledge/pages/add-product/selectors.json`
2. `flow_submit_5461.py` 逐步改为从 `SelectorRegistry` 读取
3. 过渡期保留 `flow_5461.yaml` 作为只读备份，一个月后删除

---

## 五、优先级与时间表

| 周 | 阶段 | 事项 | 产出物 | 影响现有流程？ |
|----|------|------|--------|-------------|
| W1 | 1 | `src/capture/` 模块 + `capture_evidence()` 接入关键节点 | 每个品牌产出 8-10 个结构化证据包 | **无影响**，仅增加旁路采集 |
| W1 | 4 | `scripts/monitor_sellercentral.py` 落地 | 实时监控能力 | **无影响**，独立进程 |
| W2 | 2 | `knowledge/pages/`  states.md + selectors.json + field-map.json | 知识库 V1 | **无影响**，仅读取 |
| W2 | 2 | `src/knowledge/` 解析器 | StateMachine + SelectorRegistry | **无影响**，并行开发 |
| W3 | 3 | `src/executor/state_loop.py` + legacy action 包装 | 状态循环原型 | **有影响**，需测试验证 |
| W3 | 3 | 选择器 fallback 链生效 | 减少选择器失效导致的失败 | **低风险**，失败自动 fallback |
| W4 | 3 | 逐步拆解 legacy action（阶段 1→3） | 完全状态化执行 | **中风险**，需逐个品牌测试 |
| W4 | 5 | 根目录清理 + 旧 selectors.yaml 废弃 | 整洁项目结构 | **无影响** |
| W5+ | 持续 | 收集异常样本 → 更新 knowledge/ | 知识库持续进化 | **无影响** |

---

## 六、关键设计决策

### 6.1 为什么不一次性重写 `flow_submit_5461.py`？

- 2983 行代码包含大量业务细节（UK 旧版 UI、Connect brand、Declined 处理、mons_sel_mkid 修复等），一次性重写风险极高
- 渐进式"legacy action 包装"策略允许：新架构跑通的同时，旧代码继续工作
- 每个 legacy action 拆解时，都有完整的证据包验证正确性

### 6.2 为什么 knowledge/ 用文本文件（markdown + json）而不是数据库？

- 知识库需要版本控制（git diff 可见状态规则变化）
- AI 可以直接读取 markdown/json，不需要查询语言
- 人工可以直接编辑 states.md，不需要写代码
- 运行时加载到内存，性能无影响

### 6.3 为什么证据包要保存到磁盘而不是只存内存？

- 脚本崩溃后，证据保留用于复盘
- AI 分析时可以读取完整证据包，不依赖当前进程状态
- 异常样本可以直接复制到 `knowledge/examples/` 作为知识库素材

### 6.4 安全边界（强制执行）

```python
# src/capture/redact.py — 任何采集模块都必须调用
MUST_REDACT = [
    "cookie", "authorization", "x-amz-access-token",
    "csrf-token", "session-token", "password",
    "otp", "mfa", "credit card", "bank account"
]

# 禁止采集
FORBIDDEN = [
    "完整请求头", "response body", "原始 cookie 字符串",
    "银行卡号", "税务 ID", "身份信息"
]

# 高风险按钮标记
HIGH_RISK_ACTIONS = [
    "submit_button",          # 5461 提交
    "connect_brand_button",   # 品牌关联确认
]
```

---

## 七、衡量改造成功的标准

| 指标 | 改造前 | 改造后目标 |
|------|--------|-----------|
| 遇到新页面变体的响应时间 | 30-60 分钟（现场改代码） | 5-10 分钟（补充 states.md + examples） |
| 选择器失效导致的失败率 | ~20%（无 fallback） | <5%（有 fallback 链 + AI 修复） |
| 异常排查所需信息 | 1 张截图 + 聊天记录 | 完整证据包（DOM + 网络 + 控制台 + 截图） |
| 新增站点适配成本 | 需要现场调试 | 参照知识库 states.md，预先补充选择器 |
| AI 判断页面状态准确率 | "看图猜" | 基于结构化证据 + 状态机，可验证 |
| 代码可维护性 | 2983 行单文件 | 状态机 + 选择器注册表 + 小粒度 action |

---

## 八、下一步行动

1. **立即开始**：创建 `src/capture/` 目录，实现 `PageCapture.capture()` 基础版
2. **本周内**：在 `flow_submit_5461.py` 的 5 个关键节点插入 `capture_evidence()`
3. **本周内**：创建 `knowledge/pages/add-product/states.md` V1（把现有经验写进去）
4. **下周**：创建 `knowledge/pages/add-product/selectors.json`，把 `flow_5461.yaml` 迁移过来并增加 fallback
5. **持续**：每次遇到异常，保存证据包到 `knowledge/pages/*/examples/`，反向丰富知识库

---

*本方案是活文档，执行中根据实际约束调整。*


