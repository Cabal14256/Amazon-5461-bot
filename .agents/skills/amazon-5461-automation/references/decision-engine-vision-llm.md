# decision_engine.py — Vision LLM 实现与测试（2026-06-15）

## Provider 选择逻辑

`analyze_page_with_llm()` 按以下优先级选 provider：

1. **`OPENROUTER_API_KEY`** 环境变量 → 调 OpenRouter，模型默认 `anthropic/claude-sonnet-4-5`（有截图）/ `anthropic/claude-haiku-4-5`（无截图）
2. **Hermes config.yaml `custom_providers`** → `_load_hermes_provider()` 自动读取激活的 custom provider，支持 vision，**零配置**
3. **`LLM_API_KEY` + `LLM_ENABLED=true`** → Moonshot 回退，仅文本无截图

### `_load_hermes_provider()` 查找路径

```python
candidates = [
    Path(os.environ.get("HERMES_HOME", "__missing__")) / "config.yaml",
    pathlib.Path.home() / "AppData" / "Local" / "hermes" / "config.yaml",   # Windows
    Path(os.environ.get("LOCALAPPDATA", "__missing__")) / "hermes" / "config.yaml",
]
```

优先匹配 `model.provider` 字段指向的 custom provider（形如 `custom:lmuai-claude`），
否则取第一个有 `api_key` + `base_url` 的 provider。

### 当前环境（Windows，lmuai-claude）

- `OPENROUTER_API_KEY`：未配置
- Hermes provider：`custom:lmuai-claude` → `https://api.lmuai.com/v1`，模型 `claude-sonnet-4-6`
- 实际走路径 2，**不需要在 .env 里加任何 key**
- Vision 测试：1×1 PNG → 正确识别颜色；模拟 Amazon 页面截图 → confidence=0.95，正确识别 `approval_required`

## 截图处理

- 截图路径从 `page_context["screenshot_path"]` 读取
- 读取后转 base64 编码，作为 `image_url` 消息块发送
- **超过 2MB 的截图自动跳过**，降级为纯文本分析（避免 token 爆炸）
- 格式：`data:image/png;base64,<b64>`

## 端到端测试方法

必须在项目 venv 里运行，否则 src 包的相对导入会失败：

```bash
cd projects/amazon-5461-bot/amazon-5461-bot
.venv/Scripts/python _scratch/test_vision_llm.py
```

测试脚本位置：`_scratch/test_vision_llm.py`

测试流程：
1. 用 PIL（或纯字节 PNG fallback）生成临时截图
2. 调 `_load_hermes_provider()` 确认 provider 可加载
3. 调 `analyze_page_with_llm(page_context, task_context)` 含截图
4. 调 `analyze_page_with_llm(page_context_no_img, task_context)` 无截图
5. 清理临时截图文件

### 已知 SyntaxWarning

`src/capture/page_capture.py` 的 `_EXTRACT_SCRIPT` 包含 JS 正则（`\s`、`\n` 等），
Python 会在普通字符串里警告非法转义。**Fix**：声明为 raw string：

```python
# 错误
_EXTRACT_SCRIPT = """
    ...replace(/\s+/g, ' ')...
"""

# 正确
_EXTRACT_SCRIPT = r"""
    ...replace(/\s+/g, ' ')...
"""
```

已于 2026-06-15 修复。如果 SyntaxWarning 再次出现，检查同文件中是否有其他
含 `\s`、`\w`、`\d` 等 JS 转义的字符串字面量未加 `r` 前缀。

## `_call_hermes_provider()` 接口

```python
def _call_hermes_provider(provider: Dict[str, str], messages: list) -> Dict[str, Any]:
    # provider = {"api_key": ..., "base_url": ..., "model": ...}
    # 标准 OpenAI 兼容接口，supports image_url content blocks
    resp = requests.post(
        f"{provider['base_url']}/chat/completions",
        headers={"Authorization": f"Bearer {provider['api_key']}"},
        json={"model": provider["model"], "messages": messages,
              "temperature": 0.2, "max_tokens": 1024},
        timeout=45,
    )
```

## 模型可覆盖

设置 `HERMES_LLM_MODEL` 环境变量覆盖默认模型（仅对 OpenRouter 路径生效）：

```bash
HERMES_LLM_MODEL=anthropic/claude-opus-4 python scripts/run_full_5461_batch.py ...
```

Hermes config 路径不支持此覆盖（直接用 config 里的 model 字段）。
