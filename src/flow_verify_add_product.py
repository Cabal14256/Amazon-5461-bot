"""
品牌验证专用流程 - flow_verify_add_product.py
硬编码品牌验证的完整逻辑，包括页面复用优化
"""
import json
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from playwright.sync_api import Page, sync_playwright

from .db import (
    upsert_procedure_flow,
    replace_procedure_step,
    insert_procedure_run,
    insert_procedure_run_step,
    update_procedure_run_status,
    set_procedure_flow_lifecycle,
    save_decision_rule,
)
from .evidence import build_evidence_dir, write_text
from .page_utils import summarize_page
from .decision_engine import analyze_page_with_llm, create_unknown_case_and_pause


# 品牌验证专用选择器（可配置覆盖）
DEFAULT_SELECTORS = {
    "brand_input": [
        # Amazon 使用 kat-input Web Component
        'kat-input[name*="brand"]',
        'kat-input[placeholder*="Sony"]',
        '[name*="brand-0-value"]',
        'input[name*="brand" i]',
        'input[placeholder*="Brand" i]',
        'input[placeholder*="Sony"]',
        'input[aria-label*="Brand" i]',
        '[data-testid*="brand"] input',
    ],
    "item_name_input": [
        # Amazon 使用 kat-textarea Web Component
        'kat-textarea[name*="item_name"]',
        'kat-textarea[placeholder*="Adidas"]',
        '[name*="item_name-0-value"]',
        'textarea[placeholder*="Item" i]',
        'textarea[placeholder*="Name" i]',
        'textarea[name*="itemName" i]',
        '[data-testid*="itemName"] textarea',
    ],
    "no_product_id_checkbox": [
        # Amazon 使用 kat-checkbox Web Component
        'kat-checkbox[data-cy*="upc-exemption-checkbox"]',
        'kat-checkbox:has-text("does not have a Product ID")',
        'kat-checkbox.product-id-checkbox',
        '[data-cy*="upc-exemption-checkbox"]',
        'text=This product does not have a Product ID',
        'input[type="checkbox"][name*="noProductId" i]',
        '[data-testid*="noProductId"]',
        'label:has-text("does not have a Product ID")',
    ],
    "next_button": [
        'kat-button[kat-aria-label="Next"]',
        'kat-button:has-text("Next")',
        'button:has-text("Next")',
        'button[type="submit"]',
        '[data-testid*="next"]',
        'button:has-text("Continue")',
    ],
    "item_type_keyword_dropdown": [
        'select[name*="itemTypeKeyword" i]',
        '[data-testid*="itemTypeKeyword"]',
        'button:has-text("Item Type Keyword")',
    ],
}

# 成功/失败标记
SUCCESS_MARKERS = {
    "url_contains": ["description"],
    "text_contains": ["Description", "Item Type Keyword"],
    "elements_present": ["textarea[name*='description']"],
}

FAIL_MARKERS = {
    "text_contains": [
        "Brand Authorization Required",
        "restricted to authorized sellers",
        "Apply to sell",
        "not authorized",
    ],
    "elements_present": [
        'button:has-text("Apply to sell")',
    ],
}

PAUSE_MARKERS = {
    "text_contains": [
        "Enter the characters you see",  # CAPTCHA
        "Two-Step Verification",  # 2FA
        "Authentication Required",
    ],
    "elements_present": [
        'input[name*="otp" i]',
        'input[name*="captcha" i]',
    ],
}


class BrandVerifyFlow:
    """品牌验证专用流程执行器"""

    def __init__(self, page: Page, db_path: str, run_id: int, evidence_dir: Path):
        self.page = page
        self.db_path = db_path
        self.run_id = run_id
        self.evidence_dir = evidence_dir
        self.step_no = 0
        self.selectors = DEFAULT_SELECTORS.copy()
        self.page_reuse_count = 0  # 页面复用计数
        self.current_brand = None
        self.results = []  # 验证结果列表

    def _screenshot(self, name: str) -> str:
        """截图并返回路径"""
        self.step_no += 1
        path = self.evidence_dir / f"step_{self.step_no:02d}_{name}.png"
        try:
            # 增加截图超时到 30 秒
            self.page.screenshot(path=str(path), full_page=True, timeout=30000)
        except Exception as e:
            # 截图失败也继续，记录错误
            print(f"截图警告 ({name}): {e}")
            # 尝试普通截图（非全页面）
            try:
                self.page.screenshot(path=str(path), timeout=10000)
            except:
                pass
        return str(path)

    def _log_step(self, step_name: str, action: str, result: str = "", note: str = ""):
        """记录步骤到数据库"""
        url = self.page.url
        insert_procedure_run_step(
            self.db_path, self.run_id, self.step_no, step_name,
            url, "", {"action": action}, result, note
        )

    def _try_selectors(self, selectors: List[str], action: str = "find", **kwargs) -> Optional[Any]:
        """尝试多个选择器，返回第一个成功的"""
        for sel in selectors:
            try:
                locator = self.page.locator(sel)
                count = locator.count()
                if count == 0:
                    continue

                if action == "find":
                    return locator.first
                elif action == "fill":
                    text = kwargs.get("text", "")
                    # 处理 Web Component (kat-input, kat-textarea)
                    if sel.startswith('kat-'):
                        # 对于 Web Component，使用 evaluate 直接设置值
                        elem = locator.first
                        # 尝试通过 JavaScript 设置值
                        elem.evaluate(f"(el, value) => {{ el.value = value; el.dispatchEvent(new Event('input')); }}", text)
                        print(f"    [OK] 填写成功(Web Component): {sel[:40]}...")
                    else:
                        locator.first.fill(text)
                        print(f"    [OK] 填写成功: {sel[:40]}...")
                    return True
                elif action == "click":
                    # 对于 kat-button，使用 JS 点击避免 visibility 问题
                    if sel.startswith('kat-'):
                        locator.first.evaluate("(el) => { el.click(); }")
                        print(f"    [OK] 点击成功(Web Component): {sel[:40]}...")
                    else:
                        locator.first.click()
                        print(f"    [OK] 点击成功: {sel[:40]}...")
                    return True
                elif action == "check":
                    locator.first.check()
                    print(f"    [OK] 勾选成功: {sel[:40]}...")
                    return True
                elif action == "count":
                    return count
            except Exception as e:
                # 调试时打印失败信息
                print(f"  选择器失败 {sel[:30]}...: {e}")
                continue
        return None

    def _analyze_page_state(self, page_ctx: Dict) -> Dict[str, Any]:
        """分析当前页面状态"""
        state = {
            "is_product_identity_page": False,
            "is_description_page": False,
            "is_fail_page": False,
            "is_captcha_page": False,
            "is_2fa_page": False,
            "item_type_keyword_empty": False,
        }

        url = page_ctx.get("url", "")
        text = page_ctx.get("visible_text", "")
        title = page_ctx.get("title", "")

        # 判断页面类型
        if "product_identity" in url or "Add Product" in text:
            state["is_product_identity_page"] = True
        if "description" in url:
            state["is_description_page"] = True
        if any(marker.lower() in text.lower() for marker in FAIL_MARKERS["text_contains"]):
            state["is_fail_page"] = True
        if "Enter the characters you see" in text:
            state["is_captcha_page"] = True
        if "Two-Step Verification" in text or "2FA" in text:
            state["is_2fa_page"] = True

        return state

    def _smart_select_item_type_keyword(self, keywords: List[str] = None) -> bool:
        """
        智能选择 Item Type Keyword
        优先匹配: screen protector, screen protectors, protector
        """
        keywords = keywords or ["screen protector", "screen protectors", "protector"]

        try:
            # 点击下拉框展开选项
            dropdown_selectors = self.selectors["item_type_keyword_dropdown"]
            for sel in dropdown_selectors:
                try:
                    self.page.locator(sel).click()
                    time.sleep(0.5)
                    break
                except:
                    continue

            # 获取所有选项
            options = self.page.locator('option, [role="option"], .dropdown-item').all()
            option_texts = []
            for opt in options:
                try:
                    text = opt.text_content() or opt.get_attribute("value") or ""
                    option_texts.append(text.strip())
                except:
                    continue

            # 智能匹配
            for keyword in keywords:
                for i, text in enumerate(option_texts):
                    if keyword.lower() in text.lower():
                        options[i].click()
                        self._log_step("选择 Item Type Keyword", "click", note=f"选中: {text}")
                        return True

            # 未找到匹配项，截图记录
            self._screenshot("item_type_keyword_not_found")
            return False

        except Exception as e:
            self._log_step("选择 Item Type Keyword", "error", note=str(e))
            return False

    def navigate_to_add_product(self, entry_url: str) -> bool:
        """导航到 Add Product 页面"""
        print(f"  导航到: {entry_url[:60]}...")
        self._screenshot("navigate_start")

        # 导航到入口URL
        try:
            self.page.goto(entry_url, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            print(f"  ⚠️ 导航超时: {e}")
            # 检查当前页面
            current_url = self.page.url
            print(f"  当前URL: {current_url}")

        # 等待页面稳定
        time.sleep(3)

        # 检查页面是否加载成功
        current_url = self.page.url
        print(f"  当前页面: {current_url}")

        if current_url == "about:blank" or not current_url:
            print("  ⚠️ 页面未正确加载，尝试重新导航...")
            # 可能是需要登录，打开 Seller Central 主页
            self.page.goto("https://sellercentral.amazon.com/", wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)
            print(f"  重定向后: {self.page.url}")
            # 再次尝试导航到目标页面
            self.page.goto(entry_url, wait_until="domcontentloaded", timeout=45000)
            time.sleep(3)

        self._screenshot("navigate_complete")

        # 检查是否为 Amazon 错误页
        if "An error occurred" in self.page.content():
            self._log_step("检查页面状态", "wait", note="Amazon 错误页，等待30秒后刷新")
            time.sleep(30)
            self.page.reload()
            time.sleep(2)
            self._screenshot("after_refresh")

        # 检查是否需要选择 Item Type Keyword
        page_ctx = summarize_page(self.page)
        state = self._analyze_page_state(page_ctx)

        if state["is_product_identity_page"]:
            # 检查 Item Type Keyword 是否为空
            try:
                # 尝试找到 Item Type Keyword 字段并检查是否已填充
                item_type_selectors = self.selectors["item_type_keyword_dropdown"]
                for sel in item_type_selectors:
                    try:
                        value = self.page.locator(sel).input_value()
                        if not value or value.strip() == "":
                            self._log_step("检查 Item Type Keyword", "info", note="为空，需要智能选择")
                            self._smart_select_item_type_keyword()
                        break
                    except:
                        continue
            except:
                pass

        self._log_step("导航到 Add Product", "navigate", "ok")
        return True

    def fill_form(self, brand_name: str, item_name: str = "screen protector") -> bool:
        """填写表单（Brand + Item Name + No Product ID）"""
        self.current_brand = brand_name

        # 等待页面加载完成
        print(f"  等待页面加载...")
        time.sleep(2)

        # 1. 清空并填写 Brand Name
        self._screenshot("before_fill_brand")
        print(f"  正在填写 Brand: {brand_name}")
        brand_filled = self._try_selectors(self.selectors["brand_input"], "fill", text=brand_name)
        if not brand_filled:
            print(f"  ⚠️ 无法找到 Brand 输入框，尝试智能查找...")
            # 尝试更智能的查找
            page_ctx = summarize_page(self.page)
            print(f"  当前页面: {page_ctx.get('url', 'N/A')}")
            print(f"  页面标题: {page_ctx.get('title', 'N/A')}")
            self._screenshot("brand_fill_failed")
            raise RuntimeError(f"无法找到 Brand 输入框，品牌: {brand_name}")
        self._log_step("填写 Brand Name", "fill", note=f"品牌: {brand_name}")
        time.sleep(0.5)

        # 2. 填写 Item Name
        self._screenshot("before_fill_item_name")
        item_filled = self._try_selectors(self.selectors["item_name_input"], "fill", text=item_name)
        if not item_filled:
            # Item Name 可能不是必填，继续
            self._log_step("填写 Item Name", "skip", note="未找到输入框，可能已预填充")
        else:
            self._log_step("填写 Item Name", "fill", note=f"内容: {item_name}")
        time.sleep(0.5)

        # 3. 勾选 "No Product ID"
        self._screenshot("before_check_no_product_id")
        print(f"  正在勾选 No Product ID...")
        try:
            checkbox_selectors = self.selectors["no_product_id_checkbox"]
            for sel in checkbox_selectors:
                try:
                    locator = self.page.locator(sel)
                    if locator.count() == 0:
                        continue

                    print(f"    尝试选择器: {sel[:40]}...")

                    # 处理 kat-checkbox Web Component
                    if sel.startswith('kat-checkbox') or 'kat-' in sel:
                        # 使用 JavaScript 点击
                        locator.first.evaluate("(el) => { el.click(); }")
                        print(f"    [OK] 勾选成功(Web Component): {sel[:40]}...")
                    elif "text=" in sel:
                        # 文本选择器，直接点击
                        locator.click()
                        print(f"    [OK] 点击成功: {sel[:40]}...")
                    else:
                        # 普通 checkbox
                        is_checked = locator.is_checked()
                        if not is_checked:
                            locator.check()
                        print(f"    [OK] 勾选成功: {sel[:40]}...")

                    self._log_step("勾选 No Product ID", "check", "ok")
                    break
                except Exception as e:
                    print(f"    选择器失败: {e}")
                    continue
        except Exception as e:
            self._log_step("勾选 No Product ID", "skip", note=str(e))
        time.sleep(1)  # 等待按钮状态更新

        self._screenshot("form_filled")
        return True

    def click_next(self) -> Dict[str, Any]:
        """点击 Next 按钮并分析结果"""
        self._screenshot("before_click_next")

        # 点击 Next
        next_clicked = self._try_selectors(self.selectors["next_button"], "click")
        if not next_clicked:
            raise RuntimeError("无法找到 Next 按钮")

        self._log_step("点击 Next", "click", "ok")
        time.sleep(3)  # 等待页面响应
        self._screenshot("after_click_next")

        # 分析结果
        page_ctx = summarize_page(self.page)
        state = self._analyze_page_state(page_ctx)

        result = {
            "success": False,
            "fail": False,
            "need_pause": False,
            "state": state,
            "page_ctx": page_ctx,
        }

        # 检查各种状态
        if state["is_description_page"]:
            result["success"] = True
            result["reason"] = "进入 Description 页面，品牌验证通过"
        elif state["is_fail_page"]:
            result["fail"] = True
            result["reason"] = "Brand Authorization Required，品牌未授权"
        elif state["is_captcha_page"]:
            result["need_pause"] = True
            result["reason"] = "遇到 CAPTCHA 验证码，需要人工处理"
        elif state["is_2fa_page"]:
            result["need_pause"] = True
            result["reason"] = "遇到 Two-Step Verification，需要人工处理"
        else:
            # 未知状态，需要进一步分析
            result["need_analysis"] = True
            result["reason"] = "页面状态未知，需要 AI 分析"

        return result

    def verify_brand(self, brand_name: str, use_ai_analysis: bool = True) -> Dict[str, Any]:
        """
        验证单个品牌
        返回: {"result": "pass"|"fail"|"unknown", "reason": str, "can_reuse_page": bool}
        """
        self._log_step(f"开始验证品牌: {brand_name}", "start")

        # 1. 填写表单
        self.fill_form(brand_name)

        # 2. 点击 Next
        click_result = self.click_next()

        # 3. 处理结果
        if click_result.get("success"):
            self._log_step("品牌验证", "pass", note=click_result["reason"])
            self.results.append({"brand": brand_name, "result": "pass"})
            return {
                "result": "pass",
                "reason": click_result["reason"],
                "can_reuse_page": False,  # 通过时需要新开页面
            }

        elif click_result.get("fail"):
            self._log_step("品牌验证", "fail", note=click_result["reason"])
            self.results.append({"brand": brand_name, "result": "fail", "reason": click_result["reason"]})
            return {
                "result": "fail",
                "reason": click_result["reason"],
                "can_reuse_page": True,  # 失败时可以复用页面
            }

        elif click_result.get("need_pause"):
            self._log_step("品牌验证", "pause", note=click_result["reason"])
            return {
                "result": "pause",
                "reason": click_result["reason"],
                "can_reuse_page": False,
            }

        elif click_result.get("need_analysis") and use_ai_analysis:
            # 使用 AI 分析页面
            return self._ai_analyze_and_decide(brand_name, click_result["page_ctx"])

        else:
            self._log_step("品牌验证", "unknown", note="无法判定结果")
            return {
                "result": "unknown",
                "reason": "无法判定页面状态",
                "can_reuse_page": False,
            }

    def _ai_analyze_and_decide(self, brand_name: str, page_ctx: Dict) -> Dict[str, Any]:
        """
        使用 AI 分析页面并决策
        风险分层决策策略:
        - 低风险 + 高置信度(≥0.8) → 自动处理
        - 中风险 → 显示分析，人工确认
        - 高风险 或 低置信度(<0.5) → 人工接管
        """
        self._screenshot("ai_analysis")

        # 调用 LLM 分析
        ai_result = analyze_page_with_llm(
            page_context=page_ctx,
            task_context=f"品牌验证: {brand_name}",
        )

        risk_level = ai_result.get("risk_level", "high")
        confidence = ai_result.get("confidence", 0.0)
        decision = ai_result.get("decision", "human_takeover")
        suggested_actions = ai_result.get("suggested_actions", [])

        self._log_step("AI 页面分析", "analyze", note=f"风险: {risk_level}, 置信度: {confidence}, 决策: {decision}")

        # 风险分层决策
        if risk_level == "low" and confidence >= 0.8 and decision == "auto_handle":
            # 低风险自动处理
            self._log_step("AI 决策", "auto_handle", note="低风险高置信度，自动执行")
            # 执行建议动作
            for action in suggested_actions:
                self._execute_ai_action(action)
            # 重新验证
            return self.verify_brand(brand_name, use_ai_analysis=False)

        elif risk_level == "medium":
            # 中风险，人工确认
            self._log_step("AI 决策", "human_confirm", note="中风险，需要人工确认")
            return {
                "result": "human_confirm",
                "reason": f"AI 分析: {ai_result.get('analysis', '')}",
                "suggested_actions": suggested_actions,
                "ai_result": ai_result,
                "can_reuse_page": False,
            }

        else:
            # 高风险或低置信度，人工接管
            self._log_step("AI 决策", "human_takeover", note="高风险或低置信度，人工接管")

            # 创建 unknown_case 记录
            unknown_case_id = create_unknown_case_and_pause(
                db_path=self.db_path,
                flow_type="verify_add_product",
                account_id="",  # 从外部传入
                marketplace="",  # 从外部传入
                brand_name=brand_name,
                page_ctx=page_ctx,
                ai_result=ai_result,
                screenshot_path=self._screenshot("unknown_case"),
            )

            return {
                "result": "human_takeover",
                "reason": f"AI 分析: {ai_result.get('analysis', '')}",
                "unknown_case_id": unknown_case_id,
                "ai_result": ai_result,
                "can_reuse_page": False,
            }

    def _execute_ai_action(self, action: Dict):
        """执行 AI 建议的动作"""
        action_type = action.get("type")
        try:
            if action_type == "click":
                selector = action.get("selector")
                self.page.locator(selector).click()
                self._log_step("AI 执行", "click", note=selector)
            elif action_type == "wait":
                ms = action.get("ms", 2000)
                time.sleep(ms / 1000)
                self._log_step("AI 执行", "wait", note=f"{ms}ms")
            elif action_type == "refresh":
                self.page.reload()
                time.sleep(2)
                self._log_step("AI 执行", "refresh")
            elif action_type == "fill":
                selector = action.get("selector")
                text = action.get("text", "")
                self.page.locator(selector).fill(text)
                self._log_step("AI 执行", "fill", note=f"{selector} = {text}")
        except Exception as e:
            self._log_step("AI 执行", "error", note=str(e))

    def prepare_for_next_brand(self, current_result: str) -> bool:
        """
        为验证下一个品牌准备页面
        - 如果当前失败（还在 Product Identity 页面）→ 清空 Brand 字段，复用页面
        - 如果当前通过（在 Description 页面）→ 返回 False，需要新开页面
        """
        if current_result == "fail":
            # 清空 Brand 字段，准备复用
            try:
                for sel in self.selectors["brand_input"]:
                    try:
                        self.page.locator(sel).first.fill("")
                        self.page.locator(sel).first.clear()
                        self._log_step("清空 Brand 字段", "clear", "ok")
                        self.page_reuse_count += 1
                        return True
                    except:
                        continue
            except Exception as e:
                self._log_step("清空 Brand 字段", "error", note=str(e))
                return False

        elif current_result == "pass":
            # 验证通过，页面已跳转到 Description，不能复用
            self._log_step("页面复用检查", "info", note="验证通过，需要新开页面")
            return False

        return False

    def run_batch_verify(self, brand_names: List[str], entry_url: str) -> Dict[str, Any]:
        """
        批量验证多个品牌
        """
        all_results = []
        need_new_page = True

        for i, brand in enumerate(brand_names):
            self._log_step(f"批量验证 [{i+1}/{len(brand_names)}]", "batch", note=f"品牌: {brand}")

            if need_new_page:
                # 需要新开页面
                if i > 0:
                    # 关闭旧页面（如果不是第一个）
                    try:
                        self.page.close()
                    except:
                        pass
                    # 新开页面
                    self.page = self.page.context.new_page()

                # 导航到 Add Product
                self.navigate_to_add_product(entry_url)
                need_new_page = False

            # 验证当前品牌
            result = self.verify_brand(brand)
            all_results.append({"brand": brand, **result})

            # 决定下一步
            if result["result"] == "pass":
                need_new_page = True  # 通过，下次需要新开页面
            elif result["result"] == "fail":
                # 失败，尝试复用页面
                can_reuse = self.prepare_for_next_brand("fail")
                need_new_page = not can_reuse
            else:
                # 暂停/未知/人工接管，停止批量
                break

        return {
            "results": all_results,
            "total": len(brand_names),
            "passed": sum(1 for r in all_results if r["result"] == "pass"),
            "failed": sum(1 for r in all_results if r["result"] == "fail"),
            "page_reuse_count": self.page_reuse_count,
        }


def run_verify_add_product(
    cdp_url: str,
    db_path: str,
    account_id: str,
    marketplace: str,
    brand_name: str,  # 单个品牌或逗号分隔的多个品牌
    entry_url: str,
    evidence_root: str,
    flow_code: str = "verify_add_product_auto",
    use_ai: bool = True,
) -> Dict[str, Any]:
    """
    运行品牌验证专用流程
    """
    # 解析品牌列表
    brand_names = [b.strip() for b in brand_name.split(",") if b.strip()]
    if not brand_names:
        raise ValueError("必须提供至少一个品牌名称")

    # 创建/更新流程记录
    flow_id = upsert_procedure_flow(
        db_path=db_path,
        flow_code=flow_code,
        flow_type="verify_add_product",
        name="品牌验证专用流程",
        marketplace=marketplace,
        brand_name=brand_names[0],
    )
    set_procedure_flow_lifecycle(db_path, flow_code, "approved")  # 专用流程自动批准

    # 创建运行记录
    run_id = insert_procedure_run(
        db_path, flow_id, account_id, marketplace, brand_names[0], run_mode="auto"
    )

    # 创建证据目录
    evidence_dir = build_evidence_dir(evidence_root, account_id, brand_names[0], "verify_add_product")

    # 执行流程
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()
        page.set_default_timeout(20000)

        try:
            flow = BrandVerifyFlow(page, db_path, run_id, evidence_dir)

            # 先导航到 Add Product 页面
            print("  正在导航到 Add Product 页面...")
            flow.navigate_to_add_product(entry_url)
            print("  [OK] 页面加载完成")

            if len(brand_names) == 1:
                # 单个品牌
                result = flow.verify_brand(brand_names[0], use_ai_analysis=use_ai)
                update_procedure_run_status(db_path, run_id, result["result"], note=result.get("reason", ""))
            else:
                # 批量验证
                batch_result = flow.run_batch_verify(brand_names, entry_url)
                update_procedure_run_status(
                    db_path, run_id,
                    "completed",
                    note=f"批量完成: {batch_result['passed']}通过, {batch_result['failed']}失败"
                )
                result = batch_result

            return {
                "ok": True,
                "flow_code": flow_code,
                "run_id": run_id,
                "result": result,
            }

        except Exception as e:
            update_procedure_run_status(db_path, run_id, "error", note=str(e))
            raise

        finally:
            browser.close()
