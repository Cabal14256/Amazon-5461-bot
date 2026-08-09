"""
统一脱敏规则 — 任何采集模块都必须调用

禁止保存、复述、推测任何 API Key、token、cookie、OAuth secret、私钥、
完整邮箱凭据或其他敏感信息。
"""

import re
from typing import Callable, Optional

# 敏感信息正则模式
SENSITIVE_PATTERNS = [
    # AWS Access Key
    (r"AKIA[0-9A-Z]{16}", "[REDACTED_AWS_KEY]"),
    # Authorization Bearer token (header format)
    (r"(?i)authorization\s*:\s*bearer\s+[a-zA-Z0-9_\-\.]+", "Authorization: Bearer [REDACTED]"),
    # Standalone Bearer token (no header prefix)
    (r"(?i)\bbearer\s+[a-zA-Z0-9_\-\.]{20,}", "Bearer [REDACTED]"),
    # JWT token pattern (three Base64url segments separated by dots)
    (r"\beyJ[a-zA-Z0-9_\-]+\.[a-zA-Z0-9_\-]+\.[a-zA-Z0-9_\-]+\b", "[REDACTED_JWT]"),
    # x-amz-access-token
    (r"(?i)x-amz-access-token\s*[:=]\s*[a-zA-Z0-9_\-\.]+", "x-amz-access-token: [REDACTED]"),
    # CSRF token
    (r"(?i)csrf[-_]?token\s*[:=]\s*[a-zA-Z0-9_\-\.]+", "csrf-token: [REDACTED]"),
    # Session token
    (r"(?i)session[-_]?token\s*[:=]\s*[a-zA-Z0-9_\-\.]+", "session-token: [REDACTED]"),
    # Cookie header
    (r"(?i)cookie\s*:\s*[^\r\n]+", "Cookie: [REDACTED]"),
    # Set-Cookie header
    (r"(?i)set-cookie\s*:\s*[^\r\n]+", "Set-Cookie: [REDACTED]"),
    # Email addresses
    (r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[REDACTED_EMAIL]"),
    # Password fields
    (r"(?i)(password|passwd|pwd)\s*[:=]\s*[^\s&\"\']+", "\\1: [REDACTED]"),
    # OTP / MFA codes
    (r"(?i)(otp|mfa|2fa|verification[-_]?code)\s*[:=]\s*\d{4,8}", "\\1: [REDACTED]"),
    # Credit card numbers (basic Luhn-like pattern)
    (r"\b(?:\d{4}[- ]?){3}\d{4}\b", "[REDACTED_CARD]"),
    # Bank account numbers (long digit sequences)
    (r"\b\d{8,20}\b", "[REDACTED_NUMBER]"),
    # Phone numbers
    (r"\b\+?\d{1,3}[-.\s]?\(?\d{1,4}\)?[-.\s]?\d{1,4}[-.\s]?\d{1,9}\b", "[REDACTED_PHONE]"),
]

# 必须脱敏的敏感关键词（用于额外检查）
MUST_REDACT_KEYWORDS = [
    "cookie", "authorization", "x-amz-access-token",
    "csrf-token", "session-token", "password",
    "otp", "mfa", "credit card", "bank account",
    "tax id", "social security", "passport",
]

# 禁止采集的内容类型
FORBIDDEN = [
    "完整请求头", "response body", "原始 cookie 字符串",
    "银行卡号", "税务 ID", "身份信息", "护照号码",
]

# 高风险按钮标记
HIGH_RISK_ACTIONS = [
    "submit_button",          # 5461 提交
    "connect_brand_button",   # 品牌关联确认
]


def redact_text(text: str, extra_patterns: Optional[list] = None) -> str:
    """
    对文本进行脱敏处理。

    Args:
        text: 原始文本
        extra_patterns: 额外的 (pattern, replacement) 元组列表

    Returns:
        脱敏后的文本
    """
    if not text:
        return text

    result = text

    # 应用标准模式
    for pattern, replacement in SENSITIVE_PATTERNS:
        result = re.sub(pattern, replacement, result)

    # 应用额外模式
    if extra_patterns:
        for pattern, replacement in extra_patterns:
            result = re.sub(pattern, replacement, result)

    return result


def redact_dict(obj: dict, extra_patterns: Optional[list] = None) -> dict:
    """
    递归地对字典中的所有字符串值进行脱敏。
    """
    if isinstance(obj, dict):
        return {
            k: redact_dict(v, extra_patterns)
            for k, v in obj.items()
        }
    elif isinstance(obj, list):
        return [redact_dict(item, extra_patterns) for item in obj]
    elif isinstance(obj, str):
        return redact_text(obj, extra_patterns)
    else:
        return obj


def default_redact(text: str) -> str:
    """默认脱敏函数，供 PageCapture 等使用。"""
    return redact_text(text)


def is_sensitive_key(key: str) -> bool:
    """检查键名是否可能包含敏感信息。"""
    key_lower = key.lower()
    return any(kw in key_lower for kw in MUST_REDACT_KEYWORDS)


def safe_json_dump(obj: dict, **kwargs) -> str:
    """
    安全地序列化 JSON，先脱敏再输出。
    """
    import json
    redacted = redact_dict(obj)
    return json.dumps(redacted, ensure_ascii=False, **kwargs)
