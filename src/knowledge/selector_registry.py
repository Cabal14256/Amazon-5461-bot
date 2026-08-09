"""
选择器注册表 -- 管理页面选择器的 preferred/fallback 链

用法:
    reg = SelectorRegistry("knowledge/pages/add-product/selectors.json")
    selectors = reg.get_ordered_selectors("brand_input")
    # -> ["kat-input[data-cy='...']", "kat-input[aria-label*='Brand']", ...]
"""

import json
from pathlib import Path
from typing import Optional, List, Dict, Any


class SelectorRegistry:
    """
    选择器注册表。

    管理选择器的 preferred/fallback 链，支持:
    - 按名称获取有序选择器列表
    - 高风险标记
    - 验证时间戳追踪
    """

    def __init__(self, selectors_json_path: str):
        self.selectors_json_path = Path(selectors_json_path)
        self.registry = self._load()

    def _load(self) -> dict:
        """加载选择器 JSON 文件。"""
        if not self.selectors_json_path.exists():
            raise FileNotFoundError(f"selectors.json not found: {self.selectors_json_path}")

        with open(self.selectors_json_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def find(self, name: str, page=None) -> Optional[str]:
        """
        返回第一个在页面上找到的选择器（需要 page 参数时）。

        Args:
            name: 选择器名称
            page: Playwright Page 对象（可选），如果提供则按序尝试并返回第一个存在的

        Returns:
            匹配的选择器字符串，或 None
        """
        selectors = self.get_ordered_selectors(name)
        if not selectors:
            return None

        if page is None:
            # 无 page 时返回 preferred
            return selectors[0] if selectors else None

        # 有 page 时按序尝试
        for sel in selectors:
            try:
                # 支持 Playwright 的 is_visible / query_selector
                if hasattr(page, "is_visible"):
                    if page.is_visible(sel):
                        return sel
                elif hasattr(page, "query_selector"):
                    el = page.query_selector(sel)
                    if el:
                        return sel
            except Exception:
                continue
        return None

    def get_all(self, name: str) -> Optional[dict]:
        """返回指定名称的完整配置字典。"""
        return self.registry.get(name)

    def is_high_risk(self, name: str) -> bool:
        """判断选择器是否为高风险。"""
        config = self.registry.get(name)
        if not config:
            return False
        return config.get("risk", "low") == "high"

    def mark_verified(self, name: str, site: str, date: str = None):
        """
        标记选择器在某站点验证成功。

        Args:
            name: 选择器名称
            site: 站点代码 (如 "US", "UK")
            date: 验证日期，默认今天
        """
        if date is None:
            from datetime import datetime
            date = datetime.now().strftime("%Y-%m-%d")

        config = self.registry.get(name)
        if not config:
            return

        config["last_verified"] = date
        sites = config.get("verified_sites", [])
        if site not in sites:
            sites.append(site)
            config["verified_sites"] = sites

        # 写回文件
        self._save()

    def get_fallback_chain(self, name: str) -> List[str]:
        """返回 preferred + fallback 合并列表（兼容旧接口）。"""
        return self.get_ordered_selectors(name)

    def get_ordered_selectors(self, name: str) -> List[str]:
        """
        返回选择器列表供调用方按序尝试。

        顺序: preferred -> fallback
        """
        config = self.registry.get(name)
        if not config:
            return []

        result = []
        preferred = config.get("preferred", [])
        if isinstance(preferred, str):
            preferred = [preferred]
        result.extend(preferred)

        fallback = config.get("fallback", [])
        if isinstance(fallback, str):
            fallback = [fallback]
        result.extend(fallback)

        return result

    def get_fallback_text(self, name: str) -> List[str]:
        """返回 fallback 文字匹配列表。"""
        config = self.registry.get(name)
        if not config:
            return []
        return config.get("fallback_text", [])

    def has_shadow_dom(self, name: str) -> bool:
        """判断选择器是否在 Shadow DOM 内。"""
        config = self.registry.get(name)
        if not config:
            return False
        return config.get("shadow_dom", False)

    def get_notes(self, name: str) -> str:
        """返回选择器备注。"""
        config = self.registry.get(name)
        if not config:
            return ""
        return config.get("notes", "")

    def list_names(self) -> List[str]:
        """返回所有已注册的选择器名称。"""
        return list(self.registry.keys())

    def _save(self):
        """保存注册表到磁盘。"""
        with open(self.selectors_json_path, "w", encoding="utf-8") as f:
            json.dump(self.registry, f, ensure_ascii=False, indent=2)
