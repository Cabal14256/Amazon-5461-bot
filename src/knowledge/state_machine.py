"""
状态机解析器 -- 解析 states.md 并基于证据包匹配页面状态

用法:
    sm = StateMachine("knowledge/pages/add-product/states.md")
    state = sm.match(evidence_dict)
    action = sm.get_recommended_action(state)
"""

import re
from pathlib import Path
from typing import Optional, List, Dict, Any


class StateMachine:
    """
    解析 states.md 文件，提供基于证据包的状态匹配能力。

    状态匹配优先级:
    1. 系统异常状态 (priority >= 800) 优先于业务状态
    2. 同一优先级下按定义顺序匹配
    """

    def __init__(self, states_md_path: str):
        self.states_md_path = Path(states_md_path)
        self.states = self.load_states()

    def load_states(self) -> dict:
        """解析 states.md 文件，返回状态定义字典。"""
        if not self.states_md_path.exists():
            raise FileNotFoundError(f"states.md not found: {self.states_md_path}")

        content = self.states_md_path.read_text(encoding="utf-8")
        return self._parse_content(content)

    def _parse_content(self, content: str) -> dict:
        """解析 markdown 内容提取状态定义。"""
        states = {}
        current_state = None
        current_block = {}

        lines = content.splitlines()
        i = 0
        last_field_key = None
        while i < len(lines):
            line = lines[i].strip()

            # 匹配 ## state: xxx
            if line.startswith("## state:"):
                if current_state:
                    states[current_state] = current_block
                current_state = line.split(":", 1)[1].strip()
                current_block = {"name": current_state}
                last_field_key = None
                i += 1
                continue

            if current_state is None:
                i += 1
                continue

            # 匹配字段行: - **字段名**: 值
            if line.startswith("- **") and "**" in line:
                # 用正则提取 key 和 value
                m = re.match(r'- \*\*(.+?)\*\*:\s*(.*)', line)
                if m:
                    key = m.group(1).strip()
                    value = m.group(2).strip()
                else:
                    i += 1
                    continue
                current_block[key] = value
                last_field_key = key
                i += 1
                continue

            # 匹配缩进列表项（检测方法、下一状态等）
            if line.startswith("- ") and not line.startswith("- **"):
                text = line[2:].strip()
                # 尝试解析 key: value 格式 (如 "url: 包含 ...")
                if ":" in text and not text.startswith("http"):
                    parts = text.split(":", 1)
                    sub_key = parts[0].strip().lower().replace(" ", "_")
                    sub_value = parts[1].strip()
                    if sub_key not in current_block:
                        current_block[sub_key] = []
                    if isinstance(current_block[sub_key], list):
                        current_block[sub_key].append(sub_value)
                    else:
                        current_block[sub_key] = [current_block[sub_key], sub_value]
                else:
                    # 普通列表项，根据 last_field_key 推断归入哪个字段
                    list_key = self._infer_list_key(current_block, last_field_key)
                    if list_key not in current_block:
                        current_block[list_key] = []
                    if isinstance(current_block[list_key], list):
                        current_block[list_key].append(text)
                i += 1
                continue

            # 空行或分隔线，重置 last_field_key
            if line == "" or line.startswith("---"):
                last_field_key = None
                i += 1
                continue

            i += 1

        if current_state:
            states[current_state] = current_block

        # 后处理：解析优先级、布尔字段
        for name, block in states.items():
            # 支持中文 key "优先级" 或英文 key "priority"
            priority_val = block.get("优先级", block.get("priority", "100"))
            block["priority"] = self._parse_priority(priority_val)
            block["is_terminal"] = self._parse_bool(block.get("是否终止", "否"))
            block["is_error"] = name.startswith("error_") or name in ("login_expired", "blank_page", "server_error")
            block["next_states"] = self._parse_next_states(block.get("下一状态列表", ""))
            block["signals"] = self._parse_signals(block)

        return states

    def _infer_list_key(self, block: dict, current_key_hint: str = None) -> str:
        """根据当前已解析的字段推断列表项应归入哪个 key。"""
        if current_key_hint == "检测方法":
            return "detection_methods"
        if "检测方法" in block or "detection_methods" in block:
            return "detection_methods"
        return "items"

    def _parse_priority(self, value) -> int:
        """解析优先级字段。"""
        if isinstance(value, int):
            return value
        text = str(value)
        # 提取数字
        match = re.search(r'(\d+)', text)
        if match:
            return int(match.group(1))
        return 100

    def _parse_bool(self, value) -> bool:
        """解析布尔字段。"""
        text = str(value).lower()
        return "是" in text or "true" in text or "yes" in text

    def _parse_next_states(self, value) -> List[str]:
        """解析下一状态列表。"""
        if not value:
            return []
        text = str(value)
        # 移除括号内容
        text = re.sub(r'\([^)]*\)', '', text)
        states = [s.strip() for s in text.replace("/", ",").split(",") if s.strip()]
        return states

    def _parse_signals(self, block: dict) -> Dict[str, List[str]]:
        """从检测方法中提取信号关键词。

        支持两种格式:
        1. 解析后的子 key: url, text, element, body_length, alerts (列表)
        2. 原始 detection_methods 列表中的 "url: xxx" 字符串
        """
        signals = {
            "url": [],
            "text": [],
            "element": [],
            "body_length": [],
            "alerts": [],
        }

        # 方式1: 直接从解析后的子 key 读取
        for key in signals.keys():
            val = block.get(key)
            if isinstance(val, list):
                signals[key] = val
            elif isinstance(val, str):
                signals[key] = [val]

        # 方式2: 从 detection_methods 列表解析 (兼容旧格式)
        methods = block.get("检测方法", block.get("detection_methods", []))
        if isinstance(methods, str):
            methods = [methods]

        for method in methods:
            method = str(method).strip()
            if method.startswith("url:"):
                signals["url"].append(method[4:].strip())
            elif method.startswith("text:"):
                signals["text"].append(method[5:].strip())
            elif method.startswith("element:"):
                signals["element"].append(method[8:].strip())
            elif method.startswith("body_length:"):
                signals["body_length"].append(method[12:].strip())
            elif method.startswith("alerts:"):
                signals["alerts"].append(method[7:].strip())

        return signals

    def match(self, evidence: dict) -> Optional[str]:
        """
        基于证据包匹配状态，返回最匹配的状态名。

        匹配逻辑:
        1. 先检查 url 匹配
        2. 再检查 body_text 关键词
        3. 再检查 alerts 关键词
        4. 再检查 interactives 中的元素
        5. 系统异常状态优先级高于业务状态
        """
        matches = self.match_all(evidence)
        return matches[0] if matches else None

    def match_all(self, evidence: dict) -> List[str]:
        """
        返回所有匹配的状态（按优先级排序）。
        系统异常状态优先级高于业务状态。
        """
        matched = []
        url = evidence.get("url", "").lower()
        body_text = evidence.get("body_text", "").lower()
        body_length = evidence.get("body_length", 0)
        alerts = evidence.get("alerts", [])
        interactives = evidence.get("interactives", [])

        alerts_text = " ".join(a.get("text", "").lower() for a in alerts)
        interactives_text = " ".join(
            f"{i.get('tag', '')} {i.get('text', '')} {i.get('dataCy', '')} {i.get('dataTestid', '')} {i.get('ariaLabel', '')}"
            .lower()
            for i in interactives
        )

        for state_name, state_def in self.states.items():
            score = self._score_state(state_def, url, body_text, alerts_text, interactives_text, body_length)
            if score > 0:
                matched.append((state_name, score, state_def.get("priority", 100)))

        # 排序: 优先级降序 -> 分数降序
        matched.sort(key=lambda x: (-x[2], -x[1]))
        return [m[0] for m in matched]

    def _score_state(self, state_def: dict, url: str, body_text: str, alerts_text: str,
                     interactives_text: str, body_length: int) -> int:
        """计算状态匹配分数。"""
        score = 0
        signals = state_def.get("signals", {})

        # URL 匹配 (权重最高)
        # URL 匹配 (权重最高)
        for pattern in signals.get("url", []):
            pattern = pattern.lower()
            # 信号可能是描述性文字，提取其中的 URL 片段
            url_parts = re.findall(r'[`"\']?([a-z0-9_\-/]+)[`"\']?', pattern)
            for part in url_parts:
                if part in url:
                    score += 10
                    break
            else:
                # 直接匹配
                if pattern in url:
                    score += 10

        # 文本匹配
        for pattern in signals.get("text", []):
            pattern_lower = pattern.lower()
            # 信号可能是描述性文字，提取引号内的关键词
            quoted = re.findall(r'"([^"]+)"', pattern)
            if quoted:
                match_count = 0
                for q in quoted:
                    if q.lower() in body_text or q.lower() in alerts_text:
                        match_count += 1
                # 多个关键词匹配时增加分数
                score += 5 * min(match_count, 3)
            else:
                if pattern_lower in body_text or pattern_lower in alerts_text:
                    score += 5

        # Alert 匹配
        for pattern in signals.get("alerts", []):
            pattern_lower = pattern.lower()
            quoted = re.findall(r'"([^"]+)"', pattern)
            if quoted:
                for q in quoted:
                    if q.lower() in alerts_text:
                        score += 8
                        break
            else:
                if pattern_lower in alerts_text:
                    score += 8

        # 元素匹配
        for pattern in signals.get("element", []):
            pattern_lower = pattern.lower()
            quoted = re.findall(r'[`"\']([^`"\']+)[`"\']', pattern)
            if quoted:
                for q in quoted:
                    if q.lower() in interactives_text:
                        score += 6
                        break
            else:
                if pattern_lower in interactives_text:
                    score += 6

        # body_length 匹配
        for condition in signals.get("body_length", []):
            condition = condition.strip()
            if condition.startswith("<"):
                try:
                    threshold = int(re.search(r'\d+', condition).group())
                    if body_length < threshold:
                        score += 7
                except (ValueError, AttributeError):
                    pass
            elif condition.startswith(">"):
                try:
                    threshold = int(re.search(r'\d+', condition).group())
                    if body_length > threshold:
                        score += 7
                except (ValueError, AttributeError):
                    pass

        # 特殊状态: blank_page
        if state_def["name"] == "blank_page" and body_length < 2000:
            score += 10

        # 特殊状态: login_expired
        if state_def["name"] == "login_expired":
            if "/signin" in url or "/ap/signin" in url:
                score += 15
            if "sign in" in body_text or "login" in body_text:
                score += 5

        # 特殊状态: error_410001
        if state_def["name"] == "error_410001":
            if "410001" in body_text or "410001" in alerts_text:
                score += 15

        # 特殊状态: error_429
        if state_def["name"] == "error_429":
            if "429" in body_text or "too many requests" in body_text:
                score += 15

        # 特殊状态: server_error
        if state_def["name"] == "server_error":
            if "an error occurred" in body_text or "an error has occurred" in body_text:
                # 排除更具体的错误码
                if "410001" not in body_text:
                    score += 12

        # 特殊状态: server_error_on_submit (5461 表单提交后)
        if state_def["name"] == "server_error_on_submit":
            if "an error has occurred" in body_text or "an error occurred" in body_text:
                # 排除 case_created 和 case_declined
                if "case id" not in body_text and "declined" not in body_text:
                    score += 12
            else:
                # 如果没有错误文本，不应匹配此状态
                score = 0

        return score

    def get_next_states(self, state: str) -> List[str]:
        """获取指定状态的下一状态列表。"""
        state_def = self.states.get(state)
        if not state_def:
            return []
        return state_def.get("next_states", [])

    def get_recommended_action(self, state: str) -> dict:
        """获取指定状态的推荐动作。"""
        state_def = self.states.get(state)
        if not state_def:
            return {"action": "unknown", "details": "State not found"}

        action_text = state_def.get("推荐动作", state_def.get("recommended_action", ""))
        return {
            "action": state,
            "details": action_text,
            "is_terminal": state_def.get("is_terminal", False),
            "is_error": state_def.get("is_error", False),
        }

    def is_terminal(self, state: str) -> bool:
        """判断是否为终止状态 (login_expired/blank_page_stop 等)。"""
        state_def = self.states.get(state)
        if not state_def:
            return False
        return state_def.get("is_terminal", False)

    def is_error(self, state: str) -> bool:
        """判断是否为错误状态。"""
        state_def = self.states.get(state)
        if not state_def:
            return False
        return state_def.get("is_error", False)

    def list_states(self) -> List[str]:
        """返回所有已知状态名。"""
        return list(self.states.keys())

    def get_state_info(self, state: str) -> Optional[dict]:
        """获取状态的完整定义。"""
        return self.states.get(state)
