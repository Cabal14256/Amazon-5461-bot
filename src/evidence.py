"""
证据采集入口 — 从"只建目录"升级为"产出完整证据包"

用法:
    from src.evidence import capture_evidence
    evidence = await capture_evidence(page, "01_add_product_start", "evidence", "us_store_001", "MyBrand")
    # 返回 dict，同时文件已写入 evidence/2026-05-19/us_store_001/MyBrand/01_add_product_start/
"""

from pathlib import Path
from datetime import datetime
from typing import Optional

from .capture import PageCapture, default_redact


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def build_evidence_dir(root: str, account_id: str, brand_name: str, kind: str):
    """
    构建证据目录路径。
    格式: {root}/{YYYY-MM-DD}/{account_id}/{brand_name}/{kind}/
    """
    day = datetime.now().strftime("%Y-%m-%d")
    path = Path(root) / day / account_id / brand_name / kind
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_text(path, text: str):
    """写入文本文件。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def capture_evidence(
    page,
    name: str,
    root: str = "evidence",
    account: str = "unknown",
    brand: str = "unknown",
    redact_fn=None,
) -> dict:
    """
    一键采集当前页面完整证据包。
    【同步版本】兼容 sync Playwright API。

    Args:
        page: Playwright Page 对象（sync API）
        name: 证据包名称，如 "01_add_product_start"
        root: 证据根目录，默认 "evidence"
        account: 账号标识
        brand: 品牌名称
        redact_fn: 自定义脱敏函数，默认使用 default_redact

    Returns:
        dict: PageEvidence 的 asdict 结果
    """
    out_dir = build_evidence_dir(root, account, brand, name)
    capture = PageCapture(page, redact_fn=redact_fn or default_redact)
    evidence = capture.capture(name, out_dir)
    return evidence.__dict__ if hasattr(evidence, "__dict__") else evidence


def capture_evidence_safe(
    page,
    name: str,
    root: str = "evidence",
    account: str = "unknown",
    brand: str = "unknown",
    redact_fn=None,
) -> Optional[dict]:
    """
    安全的证据采集 — 采集失败不抛异常，返回 None。
    【同步版本】兼容 sync Playwright API。

    用于主流程中的旁路采集，确保采集失败不影响业务逻辑。
    """
    try:
        return capture_evidence(page, name, root, account, brand, redact_fn)
    except Exception as e:
        print(f"[Evidence] 采集失败 [{name}]: {e}")
        return None


def capture_error_evidence(
    page,
    error_name: str,
    root: str = "evidence",
    account: str = "unknown",
    brand: str = "unknown",
    extra_meta: Optional[dict] = None,
) -> dict:
    """
    异常时的证据采集 — 自动附加时间戳到名称。
    【同步版本】兼容 sync Playwright API。

    例: capture_error_evidence(page, "410001", ...) -> evidence/.../error_410001_143022/
    """
    timestamp = datetime.now().strftime("%H%M%S")
    name = f"error_{error_name}_{timestamp}"
    result = capture_evidence_safe(page, name, root, account, brand)
    if result and extra_meta:
        result["meta"] = result.get("meta", {})
        result["meta"].update(extra_meta)
    return result


# 关键节点名称常量（用于统一命名）
EVIDENCE_NODES = {
    "add_product_start": "01_add_product_start",
    "before_fill": "02_before_fill",
    "after_fill": "03_after_fill",
    "5461_triggered": "04_5461_triggered",
    "apply_clicked": "05_apply_clicked",
    "connect_brand": "06_connect_brand",
    "form_loaded": "07_form_loaded",
    "form_filled": "08_form_filled",
    "after_submit": "09_after_submit",
}
