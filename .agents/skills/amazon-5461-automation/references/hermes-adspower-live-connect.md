# Hermes ↔ AdsPower 实时连接 & 未知页面分析

> 设计时间: 2026-06-15  
> 目标: (1) 让 Hermes 能实时接入 AdsPower Chrome 看未知页面；(2) 脚本内加系统化页面识别逻辑。两者互补，同时实施。

---

## 现有连接链路（项目已有）

```
accounts.json (adspower_profile_id)
       ↓
AdsPowerClient.ensure_profile_started() → POST /api/v2/browser-profile/start
       ↓ 返回 ws_endpoint (CDP WebSocket URL)
playwright.chromium.connect_over_cdp(ws_url)
       ↓
browser.contexts[0].pages[0]  → Playwright Page 对象
```

关键文件：
- `src/adspower_client.py` — 封装 AdsPower Local API，含 v1/v2 自动兼容
- `src/browser_manager.py` — `connect_by_account(account_id)` 一步返回 `(Page, config)`
- `src/capture/page_capture.py` — 结构化 DOM 证据采集（headings / alerts / interactives）
- `src/knowledge/state_machine.py` — 基于 states.md 规则匹配页面状态
- `src/decision_engine.py` — LLM 分析框架（默认关闭，需 `LLM_ENABLED=true`）
- `src/executor/state_loop.py` — 状态机驱动执行循环，`unknown` 状态目前只 wait 3s

---

## Part 1 — Hermes 实时接入（被动兜底）

### Step 1: `scripts/hermes_connect.py`

给定 account_id，打印 ws_endpoint，可选写入 `runtime/hermes_connect.json`：

```python
# 用法: python scripts/hermes_connect.py us_store_580
from src.adspower_client import AdsPowerClient
from src.config_loader import load_accounts

def get_ws_endpoint(account_id: str) -> str:
    accounts = load_accounts()
    profile_id = next(
        a["adspower_profile_id"] for a in accounts["accounts"]
        if a["account_id"] == account_id
    )
    client = AdsPowerClient()
    result = client.ensure_profile_started(profile_id=profile_id)
    return result["ws_endpoint"]
```

### Step 2: state_loop 写入联动信号文件

在 `state_loop.py` 的 `unknown` 状态处理中，把现场信息写入 `runtime/hermes_connect.json`：

```python
# 当 unknown 状态触发超过阈值次数时
hermes_signal = {
    "account_id": self.brand_data.get("account_id"),
    "profile_id": self.brand_data.get("adspower_profile_id"),
    "ws_endpoint": self._ws_endpoint,   # 从 browser_manager 传入
    "page_url": page.url,
    "reason": "unknown_page_after_3_retries",
    "timestamp": datetime.now().isoformat(),
}
Path("runtime/hermes_connect.json").write_text(
    json.dumps(hermes_signal, ensure_ascii=False, indent=2)
)
```

### Step 3: Hermes 读取并接入

```
# Hermes 发现 runtime/hermes_connect.json 后：
/browser ws://127.0.0.1:9222/devtools/browser/xxxx

# 然后：
browser_snapshot(full=True)        # 读 accessibility tree
browser_vision(question="...")     # 视觉分析页面状态
browser_console()                  # 看 JS 错误
```

---

## Part 2 — 脚本内 LLM 页面识别（主动防御）

### 现状缺口

| 模块 | 已有 | 缺口 |
|---|---|---|
| `decision_engine.py` | LLM 分析框架 | 硬编码 Moonshot API，默认关闭 |
| `state_loop.py` | 状态机执行 | `unknown` 只 wait 3s，无 LLM fallback |
| `page_capture.py` | 结构化证据 | 没有截图 base64 转 vision 接口 |

### 改造 decision_engine.py

核心改动：传截图 base64 给支持 vision 的模型：

```python
def analyze_page_with_llm(page_context, task_context="", screenshot_path=None):
    """
    page_context: PageEvidence dict (来自 PageCapture.capture())
    screenshot_path: 截图文件路径，有则转 base64 做 vision 分析
    """
    messages = [{"role": "user", "content": []}]
    
    if screenshot_path and Path(screenshot_path).exists():
        import base64
        img_b64 = base64.b64encode(Path(screenshot_path).read_bytes()).decode()
        messages[0]["content"].append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{img_b64}"}
        })
    
    messages[0]["content"].append({"type": "text", "text": build_prompt(page_context, task_context)})
    
    # 调用支持 vision 的模型（claude-sonnet / gpt-4o / gemini-flash）
    # 使用项目 .env 里已有的 API key
```

### state_loop unknown 分支改造

```python
# 现在:
"unknown": ("wait", 3, None),

# 改后:
# 1. PageCapture 采集证据包
# 2. decision_engine.analyze_page_with_llm(evidence, screenshot_path=...)
# 3. confidence > 0.7 → auto_handle
# 4. confidence ≤ 0.7 → insert_unknown_case(db) + 写 hermes_connect.json
```

---

## 实施顺序

```
Step 1: scripts/hermes_connect.py        ← 5 分钟，立刻让 Hermes 能接入任意账号
Step 2: decision_engine 接 vision LLM   ← 让脚本自己能分析未知页面
Step 3: state_loop unknown 分支改造     ← 联通两侧
Step 4: unknown_cases 积累 → states.md  ← 减少未来的 unknown
```

---

## 注意事项

- `ensure_profile_started()` 已处理"已开启但 ws 端口未就绪"的情况（socket 探测 + 重启）
- Hermes `/browser` 命令连接的是已有浏览器上下文，不会影响正在运行的自动化脚本
- `runtime/hermes_connect.json` 应在批次开始时清空，避免残留旧信号误导
- LLM vision 分析成本较高，只在 `unknown` 状态重复触发（≥2次）时才调用
