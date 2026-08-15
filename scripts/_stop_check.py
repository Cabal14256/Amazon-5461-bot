"""Web 控制台安全停止哨兵检查（无副作用模块，供批次脚本与单测共用）。

job manager 请求停止时只落一个 STOP_REQUESTED 文件；批次脚本在品牌循环
顶部（天然安全停止点）调用 :func:`stop_file_requested` 检查，存在则用
:func:`apply_stop_request` 把剩余 item 标记 skipped 并正常收尾退出。
"""

import os
from pathlib import Path


def stop_file_requested() -> bool:
    """AMAZON5461_STOP_FILE 环境变量指向的哨兵文件是否存在。"""
    path = os.environ.get("AMAZON5461_STOP_FILE", "").strip()
    if not path:
        return False
    try:
        return Path(path).exists()
    except OSError:
        return False


def apply_stop_request(state: dict, items: list) -> int:
    """把剩余未完成 item 标记为 skipped（安全停止点收尾），返回标记数量。"""
    remaining = [i for i in items if i.get("status") not in ("completed", "failed", "skipped")]
    for pending_item in remaining:
        pending_item["status"] = "skipped"
        pending_item["error"] = "stop_requested"
        summary = state.get("summary") or {}
        if "pending" in summary:
            summary["pending"] = max(0, int(summary.get("pending") or 0) - 1)
    return len(remaining)
