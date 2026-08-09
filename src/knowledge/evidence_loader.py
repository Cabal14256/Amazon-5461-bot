"""
证据加载器 -- 加载和管理历史证据包

用法:
    loader = EvidenceLoader("evidence")
    latest = loader.load_latest("us_store_570", "MP-MALL", "01_add_product_start")
    all_evidences = loader.load_all("us_store_570", "MP-MALL")
"""

import json
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta


class EvidenceLoader:
    """
    证据包加载器。

    证据目录结构:
        evidence/{YYYY-MM-DD}/{account}/{brand}/{node_name}/
            {node_name}_summary.json
            {node_name}_dom_snapshot.json
            {node_name}_page_text.txt
            {node_name}_screenshot.png
    """

    def __init__(self, evidence_root: str = "evidence"):
        self.evidence_root = Path(evidence_root)

    def load_latest(self, account: str, brand: str, node: str) -> Optional[dict]:
        """
        加载最新的某节点证据。

        Args:
            account: 账号标识
            brand: 品牌名称
            node: 节点名称，如 "01_add_product_start"

        Returns:
            证据 summary.json 的 dict，或 None
        """
        all_evidences = self._load_node_evidences(account, brand, node)
        if not all_evidences:
            return None
        # 按时间排序，返回最新的
        all_evidences.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return all_evidences[0]

    def load_all(self, account: str, brand: str) -> List[dict]:
        """
        加载某账号+品牌的所有证据（按时间排序）。

        Returns:
            所有证据 summary.json 的列表
        """
        results = []
        for day_dir in sorted(self.evidence_root.iterdir(), reverse=True):
            if not day_dir.is_dir():
                continue
            brand_dir = day_dir / account / brand
            if not brand_dir.exists():
                continue
            for node_dir in brand_dir.iterdir():
                if not node_dir.is_dir():
                    continue
                summary_file = self._find_summary_file(node_dir)
                if summary_file and summary_file.exists():
                    try:
                        with open(summary_file, "r", encoding="utf-8") as f:
                            data = json.load(f)
                            results.append(data)
                    except (json.JSONDecodeError, IOError):
                        continue
        # 按时间排序
        results.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return results

    def list_accounts(self) -> List[str]:
        """列出所有账号标识。"""
        accounts = set()
        for day_dir in self.evidence_root.iterdir():
            if not day_dir.is_dir():
                continue
            # 只处理 YYYY-MM-DD 格式的日期目录
            try:
                datetime.strptime(day_dir.name, "%Y-%m-%d")
            except ValueError:
                continue
            for account_dir in day_dir.iterdir():
                if account_dir.is_dir():
                    accounts.add(account_dir.name)
        return sorted(list(accounts))

    def list_brands(self, account: str) -> List[str]:
        """列出某账号下的所有品牌。"""
        brands = set()
        for day_dir in self.evidence_root.iterdir():
            if not day_dir.is_dir():
                continue
            account_dir = day_dir / account
            if not account_dir.exists():
                continue
            for brand_dir in account_dir.iterdir():
                if brand_dir.is_dir():
                    brands.add(brand_dir.name)
        return sorted(list(brands))

    def find_errors(self, since_days: int = 7) -> List[dict]:
        """
        找近期所有 error_ 开头的证据包。

        Args:
            since_days: 最近 N 天

        Returns:
            错误证据列表
        """
        cutoff = datetime.now() - timedelta(days=since_days)
        results = []

        for day_dir in self.evidence_root.iterdir():
            if not day_dir.is_dir():
                continue
            # 解析日期目录名
            try:
                day_date = datetime.strptime(day_dir.name, "%Y-%m-%d")
            except ValueError:
                continue
            if day_date < cutoff:
                continue

            for account_dir in day_dir.iterdir():
                if not account_dir.is_dir():
                    continue
                for brand_dir in account_dir.iterdir():
                    if not brand_dir.is_dir():
                        continue
                    for node_dir in brand_dir.iterdir():
                        if not node_dir.is_dir():
                            continue
                        # 只收集 error_ 开头的节点
                        if not node_dir.name.startswith("error_"):
                            continue
                        summary_file = self._find_summary_file(node_dir)
                        if summary_file and summary_file.exists():
                            try:
                                with open(summary_file, "r", encoding="utf-8") as f:
                                    data = json.load(f)
                                    data["_meta"] = {
                                        "date": day_dir.name,
                                        "account": account_dir.name,
                                        "brand": brand_dir.name,
                                        "node": node_dir.name,
                                    }
                                    results.append(data)
                            except (json.JSONDecodeError, IOError):
                                continue

        # 按时间排序
        results.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return results

    def _load_node_evidences(self, account: str, brand: str, node: str) -> List[dict]:
        """加载某节点在所有日期的证据。"""
        results = []
        for day_dir in self.evidence_root.iterdir():
            if not day_dir.is_dir():
                continue
            node_dir = day_dir / account / brand / node
            if not node_dir.exists():
                continue
            summary_file = self._find_summary_file(node_dir)
            if summary_file and summary_file.exists():
                try:
                    with open(summary_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        results.append(data)
                except (json.JSONDecodeError, IOError):
                    continue
        return results

    def _find_summary_file(self, node_dir: Path) -> Optional[Path]:
        """在节点目录中查找 summary.json 文件。"""
        # 尝试标准命名: {node_name}_summary.json
        node_name = node_dir.name
        candidates = [
            node_dir / f"{node_name}_summary.json",
        ]
        # 也尝试通配匹配
        for pattern in node_dir.glob("*_summary.json"):
            candidates.append(pattern)

        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    def list_nodes(self, account: str, brand: str, date: str = None) -> List[str]:
        """
        列出某账号+品牌在某日期的所有节点。

        Args:
            date: 日期字符串 YYYY-MM-DD，默认最新日期
        """
        if date:
            day_dir = self.evidence_root / date
            if not day_dir.exists():
                return []
        else:
            # 找最新日期
            day_dirs = sorted([d for d in self.evidence_root.iterdir() if d.is_dir()], reverse=True)
            if not day_dirs:
                return []
            day_dir = day_dirs[0]

        brand_dir = day_dir / account / brand
        if not brand_dir.exists():
            return []

        nodes = []
        for node_dir in brand_dir.iterdir():
            if node_dir.is_dir():
                nodes.append(node_dir.name)
        return sorted(nodes)
