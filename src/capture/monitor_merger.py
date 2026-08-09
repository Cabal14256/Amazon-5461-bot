"""
MonitorMerger -- 合并/分析多个品牌的监控数据。

用于事后诊断和批量分析旁路监控产出的事件流。
"""

import json
from pathlib import Path
from typing import List, Dict, Optional


class MonitorMerger:
    """合并/分析多个品牌的监控数据。"""

    def find_all_monitor_dirs(self, evidence_root: str) -> List[str]:
        """
        查找 evidence_root 下所有包含 monitor/ 子目录的路径。
        """
        root = Path(evidence_root)
        monitor_dirs = []
        if not root.exists():
            return monitor_dirs

        for path in root.rglob("monitor"):
            if path.is_dir():
                monitor_dirs.append(str(path))
        return monitor_dirs

    def get_errors_summary(self, monitor_dir: str) -> dict:
        """
        读取 network.jsonl，返回错误汇总。

        Returns:
            {
                "total_requests": int,
                "errors_4xx": int,
                "errors_5xx": int,
                "rate_limits_429": int,
                "failed_requests": int,
                "error_details": list,
            }
        """
        monitor_dir = Path(monitor_dir)
        network_path = monitor_dir / "network.jsonl"

        summary = {
            "total_requests": 0,
            "errors_4xx": 0,
            "errors_5xx": 0,
            "rate_limits_429": 0,
            "failed_requests": 0,
            "error_details": [],
        }

        if not network_path.exists():
            return summary

        try:
            with open(network_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    etype = event.get("type", "")
                    status = event.get("status")

                    if etype == "request":
                        summary["total_requests"] += 1
                    elif etype == "requestfailed":
                        summary["failed_requests"] += 1
                        summary["error_details"].append({
                            "timestamp": event.get("timestamp"),
                            "type": etype,
                            "method": event.get("method"),
                            "url": event.get("url"),
                            "error_text": event.get("error_text"),
                        })
                    elif etype == "response" and status is not None:
                        summary["total_requests"] += 1
                        if status == 429:
                            summary["rate_limits_429"] += 1
                            summary["errors_4xx"] += 1
                            summary["error_details"].append({
                                "timestamp": event.get("timestamp"),
                                "type": etype,
                                "method": event.get("method"),
                                "url": event.get("url"),
                                "status": status,
                            })
                        elif 400 <= status < 500:
                            summary["errors_4xx"] += 1
                            summary["error_details"].append({
                                "timestamp": event.get("timestamp"),
                                "type": etype,
                                "method": event.get("method"),
                                "url": event.get("url"),
                                "status": status,
                            })
                        elif 500 <= status < 600:
                            summary["errors_5xx"] += 1
                            summary["error_details"].append({
                                "timestamp": event.get("timestamp"),
                                "type": etype,
                                "method": event.get("method"),
                                "url": event.get("url"),
                                "status": status,
                            })
        except Exception as e:
            print(f"[WARN] get_errors_summary failed for {monitor_dir}: {e}")

        return summary

    def get_timeline(self, monitor_dir: str) -> List[dict]:
        """
        返回时间线：DOM 事件 + 网络错误 + console 错误按时间排序。
        """
        monitor_dir = Path(monitor_dir)
        timeline = []

        # 读取 events.jsonl（DOM 变化）
        events_path = monitor_dir / "events.jsonl"
        if events_path.exists():
            try:
                with open(events_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        timeline.append({
                            "timestamp": event.get("timestamp", 0),
                            "source": "dom",
                            "type": event.get("type", "unknown"),
                            "detail": event.get("detail", {}),
                        })
            except Exception as e:
                print(f"[WARN] read events.jsonl failed: {e}")

        # 读取 network.jsonl（只取错误）
        network_path = monitor_dir / "network.jsonl"
        if network_path.exists():
            try:
                with open(network_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        etype = event.get("type", "")
                        status = event.get("status")
                        is_error = (etype == "requestfailed") or (status and status >= 400)
                        if is_error:
                            timeline.append({
                                "timestamp": event.get("timestamp", 0),
                                "source": "network",
                                "type": etype,
                                "detail": {
                                    "method": event.get("method"),
                                    "url": event.get("url"),
                                    "status": status,
                                    "error_text": event.get("error_text"),
                                },
                            })
            except Exception as e:
                print(f"[WARN] read network.jsonl failed: {e}")

        # 读取 console.jsonl
        console_path = monitor_dir / "console.jsonl"
        if console_path.exists():
            try:
                with open(console_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        timeline.append({
                            "timestamp": event.get("timestamp", 0),
                            "source": "console",
                            "type": event.get("type", "console"),
                            "detail": {
                                "level": event.get("level"),
                                "message": event.get("message", ""),
                            },
                        })
            except Exception as e:
                print(f"[WARN] read console.jsonl failed: {e}")

        # 按时间排序
        timeline.sort(key=lambda x: x["timestamp"])
        return timeline

    def diagnose(self, monitor_dir: str) -> dict:
        """
        自动诊断常见问题：
        - 410001/429 出现在哪个时间点
        - DOM 在点击后多少秒有变化
        - 是否有 login/redirect 信号
        """
        monitor_dir = Path(monitor_dir)
        diagnosis = {
            "monitor_dir": str(monitor_dir),
            "has_429": False,
            "has_410001": False,
            "has_login_redirect": False,
            "rate_limit_events": [],
            "error_410001_events": [],
            "login_redirect_signals": [],
            "dom_mutation_after_click_sec": None,
            "recommendation": "",
        }

        # 读取 network.jsonl 诊断网络问题
        network_path = monitor_dir / "network.jsonl"
        if network_path.exists():
            try:
                with open(network_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        status = event.get("status")
                        url = event.get("url", "")
                        etype = event.get("type", "")

                        if status == 429:
                            diagnosis["has_429"] = True
                            diagnosis["rate_limit_events"].append({
                                "timestamp": event.get("timestamp"),
                                "url": url,
                            })

                        if "410001" in url or (event.get("error_text") and "410001" in event.get("error_text")):
                            diagnosis["has_410001"] = True
                            diagnosis["error_410001_events"].append({
                                "timestamp": event.get("timestamp"),
                                "url": url,
                            })

                        # 检测 login/redirect 信号
                        url_lower = url.lower()
                        if any(k in url_lower for k in ("signin", "login", "auth", "oauth")):
                            diagnosis["has_login_redirect"] = True
                            diagnosis["login_redirect_signals"].append({
                                "timestamp": event.get("timestamp"),
                                "url": url,
                                "type": etype,
                            })
            except Exception as e:
                print(f"[WARN] diagnose network read failed: {e}")

        # 读取 events.jsonl 诊断 DOM 变化
        events_path = monitor_dir / "events.jsonl"
        if events_path.exists():
            try:
                mutation_timestamps = []
                with open(events_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if event.get("type") == "mutation":
                            mutation_timestamps.append(event.get("timestamp", 0))

                if mutation_timestamps:
                    first_mutation = min(mutation_timestamps)
                    # 如果有 start_time 信息可以计算相对时间
                    # 这里简单记录最早 mutation 时间戳
                    diagnosis["dom_mutation_after_click_sec"] = round(first_mutation, 2)
            except Exception as e:
                print(f"[WARN] diagnose events read failed: {e}")

        # 生成建议
        recommendations = []
        if diagnosis["has_429"]:
            recommendations.append("检测到 429 限流，建议增加品牌间延迟或降低并发。")
        if diagnosis["has_410001"]:
            recommendations.append("检测到 410001 错误，建议检查账号权限或品牌关联状态。")
        if diagnosis["has_login_redirect"]:
            recommendations.append("检测到登录/重定向信号，建议检查账号 Cookie 是否过期。")
        if not recommendations:
            recommendations.append("未检测到明显异常。")

        diagnosis["recommendation"] = " ".join(recommendations)
        return diagnosis

    def batch_diagnose(self, evidence_root: str) -> Dict[str, dict]:
        """
        对 evidence_root 下所有 monitor 目录进行批量诊断。
        Returns:
            {monitor_dir: diagnosis_dict, ...}
        """
        results = {}
        for monitor_dir in self.find_all_monitor_dirs(evidence_root):
            results[monitor_dir] = self.diagnose(monitor_dir)
        return results
