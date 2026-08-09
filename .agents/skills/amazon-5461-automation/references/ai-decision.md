# AI Decision Reference

> 2026-06-01 status note: This is historical design/reference material. The active project tree currently does not contain src/ai_analyzer.py; verify current code before following commands here.

> 历史位置: `projects/amazon-5461-bot/src/ai_analyzer.py`
> 当前状态: 文件不在当前项目树；本文仅作历史设计参考。
> 最后更新: 2026-04-12

## 分层决策机制

```
L1: 本地规则 (零 Token)
    ↓ 未命中
L2: 决策规则 (零 Token)
    ↓ 未命中
L3: 大模型分析 (消耗 Token)
    ↓ 不确定
L4: 人工确认/接管
```

## 本地规则 (L1)

`_local_page_analyzer()` 实现零 Token 快速判断:

```python
# 高置信度规则
if "two-step verification" in text:
    return {
        "page_state": "two_factor",
        "confidence": 0.99,
        "decision": "human_required",
        "risk_level": "high"
    }

if "description" in url.lower():
    return {
        "page_state": "known_success",
        "confidence": 0.95,
        "decision": "auto_handle",
        "risk_level": "low"
    }
```

**支持的本地规则**:
- ✅ 二步验证 (2FA)
- ✅ 验证码 (CAPTCHA)
- ✅ 品牌授权失败
- ✅ 成功跳转
- ✅ 系统错误页面
- ✅ 加载中状态

## 决策规则 (L2)

`decision_engine.py` 中的 `match_decision_rule()`:

```python
matched = match_decision_rule(
    db_path,
    flow_type="verify_add_product",
    marketplace="US",
    account_id="us_store_517",
    brand_name="HOMEMO",
    page_ctx=page_context
)
```

规则来源: `resolve_unknown_case.py add <id>`

## 大模型分析 (L3)

### 支持的 Provider

| Provider | 状态 |
|----------|------|
| Moonshot | ✅ 已实现 |
| OpenAI | ⚠️ 部分实现 (Vision 有 TODO) |
| Anthropic | ❌ 未实现 |

### 配置

```bash
# .env
LLM_ENABLED=true
LLM_PROVIDER=moonshot
LLM_MODEL=moonshot-v1-8k
LLM_API_KEY=sk-your-key
LLM_BASE_URL=https://api.moonshot.cn/v1
```

### 决策逻辑

```python
if risk_level == "low" and confidence >= 0.8:
    decision = "auto_handle"
elif risk_level == "high" or confidence < 0.5:
    decision = "human_takeover"
else:
    decision = "human_confirm"
```

### 输出格式

```json
{
    "page_state": "form_error | success | blocked | captcha | unknown",
    "confidence": 0.85,
    "risk_level": "low | medium | high",
    "decision": "auto_handle | human_confirm | human_takeover",
    "reason": "分析理由",
    "suggested_actions": [...]
}
```

## Token 成本估算

| 模型 | 单次分析 | 预估成本 |
|------|----------|----------|
| Moonshot v1-8k | ~2K + ~500 | ~¥0.015/次 |
| GPT-4o-mini | ~2K + ~500 | ~$0.003/次 |

## 最佳实践

1. **首次使用**: 先不启用 LLM，观察本地规则覆盖率
2. **逐步启用**: 遇到不确定情况时启用 LLM
3. **固化规则**: 将常见场景固化为 `decision_rules`
4. **调整阈值**: 根据业务需求调整 `confidence` 阈值

