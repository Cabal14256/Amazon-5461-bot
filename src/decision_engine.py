import base64
import json
import os
import re
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .db import insert_unknown_case, list_candidate_rules, mark_rule_hit
from .page_matcher import match_conditions

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_project_provider() -> dict[str, str] | None:
    """Load the repo-local OpenAI-compatible provider from ``.env``/environment."""
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    enabled = os.getenv("LLM_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    api_key = os.getenv("LLM_API_KEY", "").strip()
    provider_name = os.getenv("LLM_PROVIDER", "openai_compatible").strip().lower()
    base_url = os.getenv("LLM_BASE_URL", "").strip()

    if not enabled or not api_key:
        return None
    if not base_url and provider_name == "openai":
        base_url = "https://api.openai.com/v1"
    elif not base_url and provider_name == "moonshot":
        base_url = "https://api.moonshot.cn/v1"
    if not base_url:
        return None

    return {
        "api_key": api_key,
        "base_url": base_url,
        "model": os.getenv("LLM_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini",
        "provider": provider_name,
    }


def _chat_completions_url(base_url: str) -> str:
    """Normalize an OpenAI-compatible base URL without duplicating ``/v1``."""
    normalized = base_url.strip().rstrip("/")
    if normalized.endswith("/chat/completions"):
        return normalized
    return f"{normalized}/chat/completions"


def analyze_page_with_llm(
    page_context: dict[str, Any],
    task_context: str = "",
    model: str | None = None,
) -> dict[str, Any]:
    """
    使用大模型（支持 vision）分析页面状态并给出决策建议。

    Provider 选择优先级：
    1. OPENROUTER_API_KEY 环境变量
    2. 项目 .env 中的 LLM_ENABLED/LLM_API_KEY/LLM_BASE_URL

    模型选择优先级：
    1. model 参数（运行时传入）
    2. AMAZON5461_LLM_MODEL 环境变量
    3. provider 配置里的默认模型

    page_context 支持字段：
        url, title, visible_text, alerts, interactives, screenshot_path
    """
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    openrouter_key = os.getenv("OPENROUTER_API_KEY", "")
    project_provider = _load_project_provider()

    use_openrouter = bool(openrouter_key)
    use_project_provider = bool(project_provider)

    if not use_openrouter and not use_project_provider:
        return _conservative_result("LLM 未配置（未找到可用的 provider）")

    # -----------------------------------------------------------------------
    # 构建消息（含可选截图）
    # -----------------------------------------------------------------------
    screenshot_b64 = _load_screenshot_b64(page_context.get("screenshot_path"))
    alerts_text = _fmt_alerts(page_context.get("alerts", []))
    interactives_text = _fmt_interactives(page_context.get("interactives", []))

    system_msg = (
        "你是 Amazon Seller Central 自动化专家。"
        "你的任务是分析页面截图和文本，判断当前状态，给出自动化脚本的处理建议。"
        "只关注与 Amazon 卖家操作相关的内容，忽略无关广告和导航。"
    )

    text_content = f"""任务上下文: {task_context}

页面信息:
- URL: {page_context.get("url", "N/A")}
- 标题: {page_context.get("title", "N/A")}
- 可见文本（前1500字）:
{(page_context.get("visible_text") or "")[:1500]}

页面错误/提示:
{alerts_text or "（无）"}

可交互元素:
{interactives_text or "（无）"}

请分析：
1. 当前页面是什么状态（用英文短标识，如 add_product_form / 5461_form / brand_selection / login_expired / 429_error / unknown_amazon_page 等）
2. 存在什么风险
3. 自动化脚本应该如何处理

以 JSON 格式返回，不要包含任何 JSON 之外的文字：
{{
    "page_state": "状态标识（英文）",
    "risk_level": "low/medium/high",
    "confidence": 0.0,
    "decision": "auto_handle/human_confirm/human_takeover",
    "analysis": "详细分析（中文）",
    "suggested_actions": [{{"type": "wait/refresh/click/navigate", "selector": "...", "seconds": 5, "url": "...", "reason": "..."}}],
    "questions_for_human": ["需要人工确认的问题"],
    "reason": "决策理由（中文）"
}}"""

    # 构建消息列表
    if screenshot_b64:
        user_content = [
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{screenshot_b64}"},
            },
            {"type": "text", "text": text_content},
        ]
    else:
        user_content = text_content

    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_content},
    ]

    # -----------------------------------------------------------------------
    # -----------------------------------------------------------------------
    # 解析最终使用的模型（三层优先级）
    # -----------------------------------------------------------------------
    # model 参数 > AMAZON5461_LLM_MODEL 环境变量 > provider 默认（各处 fallback）
    override_model = model or os.getenv("AMAZON5461_LLM_MODEL", "")

    # -----------------------------------------------------------------------
    # 调用 OpenRouter（首选，支持 vision）
    # -----------------------------------------------------------------------
    if use_openrouter:
        or_model = override_model or ("anthropic/claude-sonnet-4-5" if screenshot_b64 else "anthropic/claude-haiku-4-5")
        try:
            return _call_openrouter(openrouter_key, or_model, messages)
        except Exception as e:
            print(f"[decision_engine] OpenRouter 调用失败: {e}")
            if not use_project_provider:
                return _conservative_result(f"OpenRouter 调用失败: {e}")

    # -----------------------------------------------------------------------
    # 项目本地 OpenAI-compatible provider
    # -----------------------------------------------------------------------
    if use_project_provider:
        provider_name = project_provider.get("provider", "openai_compatible")
        default_vision = "false" if provider_name == "moonshot" else "true"
        supports_vision = os.getenv("LLM_SUPPORTS_VISION", default_vision).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        provider_messages = messages
        if screenshot_b64 and not supports_vision:
            provider_messages = [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": text_content},
            ]
        try:
            return _call_openai_compatible(project_provider, provider_messages, override_model=override_model)
        except Exception as e:
            print(f"[decision_engine] {provider_name} provider 调用失败: {e}")
            return _conservative_result(f"{provider_name} provider 调用失败: {e}")

    return _conservative_result("所有 LLM provider 均失败")


# ---------------------------------------------------------------------------
# Provider 调用
# ---------------------------------------------------------------------------


def _call_openai_compatible(provider: dict[str, str], messages: list, override_model: str = "") -> dict[str, Any]:
    """Call the repo-configured OpenAI-compatible chat-completions endpoint."""
    import requests

    model = override_model or provider["model"]
    resp = requests.post(
        _chat_completions_url(provider["base_url"]),
        headers={
            "Authorization": f"Bearer {provider['api_key']}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 1024,
        },
        timeout=45,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    return _parse_llm_json(content)


def _call_openrouter(api_key: str, model: str, messages: list) -> dict[str, Any]:
    import requests

    resp = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://github.com/amazon-5461-bot",
            "X-Title": "Amazon5461Bot",
        },
        json={
            "model": model,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 1024,
        },
        timeout=45,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    return _parse_llm_json(content)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _parse_llm_json(content: str) -> dict[str, Any]:
    """从 LLM 返回文本中提取 JSON，兼容 markdown 代码块包装。"""
    # 去掉 ```json ... ``` 包装
    content = re.sub(r"^```(?:json)?\s*", "", content.strip(), flags=re.IGNORECASE)
    content = re.sub(r"\s*```$", "", content.strip())
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if m:
            return json.loads(m.group())
        raise ValueError(f"无法从 LLM 响应中提取 JSON: {content[:300]}") from exc


def _load_screenshot_b64(screenshot_path: str | None) -> str | None:
    """读取截图文件并转为 base64，失败返回 None。"""
    if not screenshot_path:
        return None
    try:
        p = Path(screenshot_path)
        if not p.exists():
            return None
        data = p.read_bytes()
        # 限制截图大小：超过 2MB 则跳过（避免 token 爆炸）
        if len(data) > 2 * 1024 * 1024:
            print(f"[decision_engine] 截图过大 ({len(data) // 1024}KB)，跳过 vision")
            return None
        return base64.b64encode(data).decode("utf-8")
    except Exception as e:
        print(f"[decision_engine] 截图读取失败: {e}")
        return None


def _fmt_alerts(alerts: list) -> str:
    if not alerts:
        return ""
    lines = []
    for a in alerts[:5]:
        if isinstance(a, dict):
            lines.append(f"  - [{a.get('tag', '')}] {a.get('text', '')[:200]}")
        else:
            lines.append(f"  - {str(a)[:200]}")
    return "\n".join(lines)


def _fmt_interactives(interactives: list) -> str:
    if not interactives:
        return ""
    lines = []
    for el in interactives[:10]:
        if isinstance(el, dict):
            tag = el.get("tag", "")
            text = el.get("text", el.get("label", ""))[:80]
            sel = el.get("selector", el.get("id", ""))[:80]
            lines.append(f"  - <{tag}> {text!r}  [{sel}]")
        else:
            lines.append(f"  - {str(el)[:100]}")
    return "\n".join(lines)


def _conservative_result(reason: str) -> dict[str, Any]:
    return {
        "page_state": "unknown",
        "risk_level": "high",
        "confidence": 0.0,
        "decision": "human_takeover",
        "analysis": reason,
        "suggested_actions": [],
        "questions_for_human": ["请判断当前页面状态并决定如何处理"],
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# 规则匹配（原有逻辑，保持不变）
# ---------------------------------------------------------------------------


def match_decision_rule(
    db_path: str,
    flow_type: str,
    marketplace: str,
    account_id: str,
    brand_name: str,
    page_ctx: dict[str, Any],
):
    rows = list_candidate_rules(db_path, flow_type, marketplace, account_id, brand_name)
    for r in rows:
        cond = json.loads(r["match_conditions_json"])
        if match_conditions(page_ctx, cond):
            return {
                "rule_id": r["id"],
                "name": r["name"],
                "risk_level": r["risk_level"],
                "requires_confirm": bool(r["requires_confirm"]),
                "action_plan": json.loads(r["action_plan_json"]),
                "raw": dict(r),
            }
    return None


def record_rule_hit(db_path: str, rule_id: int):
    mark_rule_hit(db_path, rule_id)


def create_unknown_case_and_pause(
    db_path: str,
    flow_type: str,
    account_id: str,
    marketplace: str,
    brand_name: str,
    page_ctx: dict[str, Any],
    ai_result: dict[str, Any],
    screenshot_path: str,
) -> int:
    dom_summary = json.dumps({"dom_markers": page_ctx.get("dom_markers", [])}, ensure_ascii=False)
    return insert_unknown_case(
        db_path=db_path,
        flow_type=flow_type,
        account_id=account_id,
        marketplace=marketplace,
        brand_name=brand_name,
        page_url=page_ctx.get("url"),
        page_title=page_ctx.get("title"),
        screenshot_path=screenshot_path,
        page_text_summary=(page_ctx.get("visible_text") or "")[:2000],
        dom_summary=dom_summary,
        ai_page_state=ai_result.get("page_state"),
        ai_confidence=float(ai_result.get("confidence", 0.0)),
        ai_reason=ai_result.get("reason"),
    )
