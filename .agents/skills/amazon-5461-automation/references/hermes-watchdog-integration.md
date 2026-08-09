# Hermes Watchdog Integration (2026-06-15)

实时监控架构：脚本卡住 → 信号文件 → Hermes Cron → CDP 接入 → 用户通知。

---

## 信号文件 Schema

路径：`runtime/hermes_signal.json`（相对项目根，即 `amazon-5461-bot/`）

```json
{
  "status": "pending",           // "pending" | "handled"
  "reason": "unknown_page_step5_confidence0.30",
  "account_id": "us_store_580",
  "brand_name": "HOMEMO",
  "site": "BE",
  "ws_endpoint": "ws://127.0.0.1:9222/devtools/browser/xxx",
  "page_url": "https://sellercentral.amazon.com/...",
  "page_title": "Add a Product",
  "screenshot_path": "evidence/us_store_580_HOMEMO_20260615/step_05/screenshot.png",
  "alerts": [...],               // 最多 5 条页面 alert
  "llm_result": {                // 脚本内 LLM 的初步分析，可能为 null
    "page_state": "unknown_amazon_page",
    "confidence": 0.28,
    "decision": "human_takeover",
    "analysis": "..."
  },
  "timestamp": "2026-06-15T10:23:45.123456",
  "handled_at": "2026-06-15T10:24:12.000000"   // Hermes 处理后写入
}
```

**写入时机**（`_write_hermes_signal` 调用场景）：
- `unknown` 状态：LLM confidence < 0.65 或 decision == human_takeover
- 其他 stuck 状态（form_loading/form_ready 恢复耗尽后）：`stuck_at_<state>`

**Hermes 处理后**：将 `status` 改为 `"handled"` + 写入 `handled_at`，防止重复触发。

---

## Hermes Cron Job

- **Job ID**: `a4019db5f0ab`
- **Name**: `amazon-5461-hermes-watchdog`
- **Schedule**: every 1m
- **Toolsets**: browser, vision, file, terminal

轮询逻辑（cron prompt 核心）：
1. 读 signal 文件，status != "pending" → 静默退出（无输出 = 不发消息）
2. status == "pending" → 读取所有字段
3. ws_endpoint 非空 → `browser_navigate(ws_endpoint)` → `browser_snapshot(full=True)` + `browser_vision`
4. ws_endpoint 为空 → `vision_analyze(screenshot_path)`
5. 综合脚本 LLM 结论 → 生成报告
6. `patch` 信号文件 status → "handled" + handled_at
7. 输出报告（发给用户）

---

## `StateLoopExecutor` 修改点

**`__init__` 新增参数**：
```python
def __init__(self, page, brand_data: dict, evidence_root: str = "evidence",
             ws_endpoint: str = ""):
    ...
    self._ws_endpoint = ws_endpoint
```

**调用方传入 ws_endpoint**（示例，位于 flow_submit_5461.py 或 batch 脚本中）：
```python
result = client.ensure_profile_started(profile_id=profile_id)
ws_endpoint = result.get("ws_endpoint", "")
executor = StateLoopExecutor(page, brand_data, evidence_root=..., ws_endpoint=ws_endpoint)
```

**新增方法**：
- `_handle_unknown_state(step, evidence)` — LLM 分析 + 信号写入 + 等待逻辑
- `_write_hermes_signal(reason, evidence, llm_result)` — 写 JSON 信号文件

---

## `decision_engine.py` 架构

```
analyze_page_with_llm(page_context, task_context)
  │
  ├── OPENROUTER_API_KEY 存在？
  │     ├── 有截图 → claude-sonnet-4-5（vision）
  │     └── 无截图 → claude-haiku-4-5（纯文本）
  │     └── 模型可由 HERMES_LLM_MODEL 覆盖
  │
  ├── 失败 or 无 OR key → 回退 LLM_API_KEY + LLM_ENABLED=true
  │     └── moonshot-v1-8k（纯文本，不支持 vision）
  │
  └── 全部失败 → _conservative_result（human_takeover, confidence=0.0）
```

截图限制：> 2MB 自动跳过 vision，降级纯文本分析。

---

## `scripts/hermes_connect.py` 用法

```bash
# 启动账号浏览器并获取 CDP 地址（已开启则复用）
python scripts/hermes_connect.py us_store_580
python scripts/hermes_connect.py 580        # 数字简写也支持

# 列出所有已打开的浏览器
python scripts/hermes_connect.py --list-open

# JSON 输出（供脚本调用）
python scripts/hermes_connect.py 580 --json
```

输出示例：
```
账号       : us_store_580 (580) （已在运行，复用）
profile_id : abc123xyz
ws_endpoint: ws://127.0.0.1:9222/devtools/browser/xxx...

Hermes 连接命令：
  /browser ws://127.0.0.1:9222/devtools/browser/xxx...
```

---

## ⚠️ Watchdog 实际触发前提：StateLoopExecutor 必须启用（2026-07-01 确认）

**watchdog 依赖 `_write_hermes_signal()`，而该方法只存在于 `StateLoopExecutor` 中。**

`StateLoopExecutor` 默认**关闭**，正常批处理不经过它，因此信号文件永远不会写入，watchdog 永远 SILENT。

### 触发路径

| 路径 | 条件 | 默认值 |
|---|---|---|
| 显式启用 | `--use-state-loop` 参数 或 config `use_state_loop: true` | **关闭** |
| 自动 fallback | 老流程失败 + `auto_state_loop_fallback: true` + 错误类型匹配 | **关闭** |

代码注释（`run_full_5461_batch.py`）明确说明：
```
# StateLoopExecutor is still production-experimental. Keep fallback opt-in only.
# Enable explicitly with config.auto_state_loop_fallback=true or --use-state-loop.
```

### 如何快速诊断 watchdog 是否真正工作

1. **检查 cron 输出文件** — 所有输出应该是 `[SILENT]`：
   ```bash
   cat "C:/Users/Admin/AppData/Local/hermes/cron/output/a4019db5f0ab/$(ls -t C:/Users/Admin/AppData/Local/hermes/cron/output/a4019db5f0ab/ | head -1)"
   ```
   如果最近 N 条全是 `[SILENT]`，说明信号文件从没被写过或 status 不是 `pending`。

2. **检查信号文件是否存在**：
   ```bash
   cat projects/amazon-5461-bot/amazon-5461-bot/runtime/hermes_signal.json 2>/dev/null || echo "FILE_NOT_FOUND"
   ```
   `FILE_NOT_FOUND` = StateLoopExecutor 从未运行（或从未卡到写信号的代码路径）。

3. **确认 StateLoopExecutor 是否在跑**：在批处理日志中搜索：
   ```
   [INFO] StateLoopExecutor started
   ```
   没有这行 = 批处理走的是老流程，watchdog 不会触发。

### 让 watchdog 真正发挥作用的选项

**选项 A：批处理加 `--use-state-loop`（全程状态机）**
```bash
./.venv/Scripts/python scripts/run_full_5461_batch.py \
  --accounts 621 --brands "HOMEMO,JZG" --site US --use-state-loop
```
适合：想让 StateLoopExecutor 作为主执行路径时。

**选项 B：config 开 `auto_state_loop_fallback`（老流程失败后自动升级）**
```json
{
  "auto_state_loop_fallback": true
}
```
适合：老流程基本够用，只想在特定失败类型时升级。

**选项 C：维持现状（watchdog 空转）**
当前 watchdog 每分钟跑一次但始终 SILENT，因为主流程不走 StateLoopExecutor。如果不打算启用 state loop，可以将 watchdog 暂停以减少无效 session 积累：
```bash
hermes cron pause a4019db5f0ab
```

---

## 注意事项

- 信号文件是单文件覆盖写入，并发批次中多个品牌同时卡住时，后写的会覆盖先写的。当前设计适合顺序批次（`run_full_5461_batch.py` 默认串行）。
- Hermes cron 静默退出（无 stdout）= 不触发消息推送。只有找到 pending 信号才发消息。这是正常行为。
- `ws_endpoint` 在 AdsPower 浏览器关闭后失效。Hermes watchdog 连 CDP 失败时应回退到分析截图文件。

---

## Watchdog Lifecycle — Session Accumulation Problem (2026-06-15)

**Problem**: the cron job runs every minute unconditionally. Each tick spawns a Hermes session (~1,440/day). Over a multi-day run this fills the session DB with idle watchdog sessions, slowing session search and wasting disk.

**Solution**: keep the job **paused** by default. `run_full_5461_batch.py` auto-resumes it at start and pauses it at end via `try/finally`.

**Implementation in `run_full_5461_batch.py`**:
```python
WATCHDOG_JOB_ID = "a4019db5f0ab"

def _watchdog(action: str) -> None:
    try:
        result = subprocess.run(
            ["hermes", "cron", action, WATCHDOG_JOB_ID],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            print(f"[watchdog] {action} ok")
        else:
            print(f"[watchdog] {action} failed: {result.stderr.strip()}")
    except Exception as e:
        print(f"[watchdog] {action} error: {e}")
```

In `main()`, after `args = parser.parse_args()`:
```python
_watchdog("resume")
try:
    # ... entire batch body (state_path setup, all loops, print_summary) ...
finally:
    _watchdog("pause")
```

**Why `try/finally`**: ensures pause even on crash, `sys.exit()`, or keyboard interrupt — not just on clean completion.

**Manual control** (if needed without running a batch):
```bash
hermes cron resume a4019db5f0ab   # turn on monitoring
hermes cron pause  a4019db5f0ab   # turn off
hermes cron list                  # check state (enabled: true/false)
```
