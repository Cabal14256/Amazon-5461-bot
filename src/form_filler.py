#!/usr/bin/env python3
"""
Form Filler V2 - 基于 _scratch 脚本的最佳实践

参考:
- run_541_mocodi_fixed.py: 使用 keyboard.press 逐字符输入
- simple_fill.py: 使用 click + keyboard.type
"""
import re
import sys
import time
import random
from typing import Optional, List, Dict, Any
from playwright.sync_api import Page, Locator
from .human_interaction import human_delay, human_click

# Fix Windows GBK encoding issue
if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass


class KatalFormFiller:
    """Katal 表单填写器 V2 - 使用逐字符输入模拟真实用户"""
    
    def __init__(self, page: Page, brand_name: str = ""):
        self.page = page
        self.brand_name = str(brand_name or "").strip()
        self.application_type_result = None
        # New Add Product UI has multiple pre-Continue normalization passes.
        # Keep Product ID exemption idempotent so a second pass never toggles it back off.
        self._new_ui_no_product_id_set = False
        self._new_ui_item_type_keyword_set = False
    
    def _get_active_input_selector(self) -> str:
        """检测当前页面使用的输入框选择器（新旧UI兼容）"""
        # 新UI
        if self.page.locator('kat-textarea[name="item_name-0-value"]').count() > 0:
            return 'kat-textarea[name="item_name-0-value"]'
        # 旧UI
        if self.page.locator('kat-textarea[name="item_name"]').count() > 0:
            return 'kat-textarea[name="item_name"]'
        return 'kat-textarea[name="item_name-0-value"]'
    
    def type_like_human(self, text: str, delay_ms: int = 30) -> None:
        """
        模拟人类打字 - 逐字符输入
        支持特殊字符（如法语 é, è, ê 等）
        兼容新旧页面结构
        """
        selector = self._get_active_input_selector()
        
        for char in text:
            # 检查是否为 ASCII 字符
            if ord(char) < 128:
                # 普通 ASCII 字符使用 keyboard.press
                try:
                    self.page.keyboard.press(char)
                except Exception:
                    # 如果 keyboard.press 失败，使用 JS 插入
                    self.page.evaluate(f'''() => {{
                        const el = document.querySelector('{selector}');
                        if (el && el.shadowRoot) {{
                            const textarea = el.shadowRoot.querySelector('textarea');
                            if (textarea) {{
                                textarea.value += '{char}';
                                textarea.dispatchEvent(new Event('input', {{ bubbles: true }}));
                            }}
                        }}
                    }}''')
            else:
                # 特殊字符（如 é, è, ê）使用 JavaScript 直接插入
                import json
                char_json = json.dumps(char)
                self.page.evaluate(f'''() => {{
                    const el = document.querySelector('{selector}');
                    if (el && el.shadowRoot) {{
                        const textarea = el.shadowRoot.querySelector('textarea');
                        if (textarea) {{
                            textarea.value += {char_json};
                            textarea.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        }}
                    }}
                }}''')
            time.sleep(delay_ms / 1000)
    
    def _human_delay(self, min_sec: float = 0.4, max_sec: float = 1.8, label: str = "") -> None:
        """Small in-browser-action pacing. Does not move the real OS mouse/keyboard."""
        human_delay(min_sec, max_sec, label)

    def _human_click_locator(self, locator: Locator, label: str = "element", timeout: int = 10000, force: bool = False) -> bool:
        """Prefer Playwright-level hover/click over DOM btn.click(); falls back to force only when requested."""
        return human_click(locator, label=label, timeout=timeout, force=force)

    def _find_button_by_text(self, text: str, scope: str = "page"):
        """Return a Playwright locator for a visible button-like element by text/label."""
        pattern = re.compile(text, re.I)
        candidates = [
            self.page.get_by_role("button", name=pattern).first,
            self.page.locator("kat-button").filter(has_text=pattern).first,
            self.page.locator("button").filter(has_text=pattern).first,
            self.page.locator("a[role='button']").filter(has_text=pattern).first,
        ]
        for loc in candidates:
            try:
                if loc.count() > 0:
                    return loc
            except Exception:
                continue
        return None

    def _click_apply_to_sell_human(self) -> dict:
            """Try human-like Playwright click before JS DOM fallback."""
            loc = self._find_button_by_text(r"apply\s+to\s+sell")
            if loc is not None and self._human_click_locator(loc, "Apply to sell", timeout=10000):
                return {"clicked": True, "location": "playwright-text", "text": "apply to sell"}

            # ★ 2026-07-15: 新UI把 "Apply to sell" 替换为 kat-link "View"
            # 位于 "1 restriction / 1 application required" 区域内
            # 优先用 Playwright locator 点击
            try:
                view_loc = self.page.locator('kat-link:has-text("View")')
                if view_loc.count() > 0:
                    view_loc.first.scroll_into_view_if_needed(timeout=3000)
                    self._human_delay(0.3, 0.8, "View链接点击前等待")
                    view_loc.first.click(timeout=5000)
                    return {"clicked": True, "location": "kat-link-view-playwright", "text": "View"}
            except Exception as e:
                print(f"[FormFiller] kat-link View playwright点击失败: {e}，尝试JS fallback")

            # Fallback for Katal/shadow edge cases: still dispatches a browser click, but only after human-like pacing.
            self._human_delay(0.8, 2.0, "Apply to sell JS fallback 前等待")
            return self.page.evaluate('''
                () => {
                    const allButtons = document.querySelectorAll('kat-button, button, a[role="button"]');
                    for (let btn of allButtons) {
                        const text = (btn.getAttribute('label') || btn.textContent || '').toLowerCase();
                        if (text.includes('apply to sell')) {
                            try { btn.scrollIntoView({block:'center', inline:'center'}); } catch(e) {}
                            btn.click();
                            return { clicked: true, location: 'js-text-fallback', text: text };
                        }
                    }
                    // ★ 新UI: kat-link "View" 在 application required 区域
                    const katLinks = document.querySelectorAll('kat-link');
                    for (const lnk of katLinks) {
                        const text = (lnk.textContent || lnk.getAttribute('label') || '').trim().toLowerCase();
                        if (text === 'view') {
                            try { lnk.scrollIntoView({block:'center', inline:'center'}); } catch(e) {}
                            lnk.click();
                            return { clicked: true, location: 'js-kat-link-view', text: 'View' };
                        }
                    }
                    const popups = document.querySelectorAll('[class*="modal"], [class*="panel"], [class*="popup"], aside, div[role="dialog"]');
                    for (const popup of popups) {
                        const btns = popup.querySelectorAll('button, kat-button');
                        for (const btn of btns) {
                            const text = (btn.textContent || btn.getAttribute('label') || '').toLowerCase();
                            if (text.includes('apply to sell')) {
                                try { btn.scrollIntoView({block:'center', inline:'center'}); } catch(e) {}
                                btn.click();
                                return { clicked: true, location: 'js-popup-fallback', text: text };
                            }
                        }
                    }
                    const btn = document.querySelector('kat-button[data-cy="seller-qualification-path-forward-button"]');
                    if (btn) {
                        try { btn.scrollIntoView({block:'center', inline:'center'}); } catch(e) {}
                        btn.click();
                        return { clicked: true, location: 'js-global-fallback' };
                    }
                    return { clicked: false, error: 'Button not found' };
                }
            ''')

    def fill_item_name(self, value: str = "Screen Protector for iPhone 15 Pro Max") -> bool:
        """
        填写 Item Name (kat-textarea)
        兼容新旧页面结构
        """
        print(f"[FormFiller] 填写 Item Name...")
        
        try:
            # 首先等待页面加载完成
            time.sleep(2)
            
            # 尝试新页面结构 (name="item_name-0-value")
            # 然后回退到旧结构 (name="item_name")
            selectors = [
                'kat-textarea[name="item_name-0-value"]',
                'kat-textarea[name="item_name"]',
                'kat-textarea[placeholder*="Item Name" i]',
                'kat-textarea[placeholder*="item name" i]',
                'kat-textarea',
            ]
            
            target_selector = None
            for sel in selectors:
                try:
                    # 增加等待时间
                    self.page.wait_for_selector(sel, timeout=5000)
                    if self.page.locator(sel).count() > 0:
                        target_selector = sel
                        break
                except:
                    pass
            
            if not target_selector:
                # 通过 JavaScript 查找
                js_result = self.page.evaluate("""() => {
                    const el = document.querySelector('kat-textarea[name="item_name-0-value"]') 
                              || document.querySelector('kat-textarea[name="item_name"]')
                              || document.querySelector('kat-textarea');
                    return el ? 'found' : 'not_found';
                }""")
                if js_result == 'found':
                    target_selector = 'kat-textarea'
                else:
                    print("[FormFiller] 未找到 Item Name 输入框")
                    return False
            
            print(f"[FormFiller] 使用选择器: {target_selector}")
            
            # 方法1: 使用 Playwright locator 点击并输入
            try:
                textarea = self.page.locator(target_selector).first
                if textarea.count() > 0:
                    textarea.click(force=True)
                    time.sleep(0.5)
                    # 清空现有内容
                    self.page.keyboard.press('Control+a')
                    time.sleep(0.2)
                    self.page.keyboard.press('Delete')
                    time.sleep(0.2)
                    # 输入文本
                    self.page.keyboard.type(value, delay=30)
                    time.sleep(0.5)
                    self.page.keyboard.press('Tab')
                    time.sleep(0.5)
                    print("[FormFiller] Item Name 已通过 keyboard 填写")
            except Exception as e:
                print(f"[FormFiller] keyboard 填写失败: {e}")
            
            # 方法2: 使用 JavaScript 兜底确保值正确
            self.page.evaluate(f'''(val) => {{
                const el = document.querySelector('{target_selector}');
                if (el) {{
                    el.value = val;
                    el.setAttribute('value', val);
                    el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    const shadow = el.shadowRoot;
                    if (shadow) {{
                        const textarea = shadow.querySelector('textarea');
                        if (textarea) {{
                            textarea.value = val;
                            textarea.dispatchEvent(new Event('input', {{ bubbles: true }}));
                            textarea.dispatchEvent(new Event('blur', {{ bubbles: true }}));
                        }}
                    }}
                    el.dispatchEvent(new Event('blur', {{ bubbles: true }}));
                }}
            }}''', value)
            time.sleep(2)
            
            print("[FormFiller] Item Name 已填写")
            return True
            
        except Exception as e:
            print(f"[FormFiller] 填写 Item Name 失败: {e}")
            return False
    
    def fill_brand_name(self, brand_name: str) -> bool:
        """
        填写 Brand Name (kat-input)
        兼容新旧页面结构
        """
        print(f"[FormFiller] 填写 Brand Name: {brand_name}")
        
        # 检测新旧UI
        selectors = [
            'kat-input[name="brand-0-value"]',  # 新UI
            'kat-input[name="brand"]'            # 旧UI
        ]
        
        target_selector = None
        for sel in selectors:
            if self.page.locator(sel).count() > 0:
                target_selector = sel
                break
        
        if not target_selector:
            print("[FormFiller] 未找到 Brand Name 输入框")
            return False
        
        print(f"[FormFiller] 使用选择器: {target_selector}")
        
        try:
            # Step 1: 先用 JS 强制清空字段
            self.page.evaluate(f"""() => {{
                const el = document.querySelector('{target_selector}');
                if (el) {{
                    el.value = '';
                    const shadow = el.shadowRoot;
                    if (shadow) {{
                        const input = shadow.querySelector('input');
                        if (input) {{
                            input.value = '';
                            input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        }}
                    }}
                    el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                }}
            }}""")
            time.sleep(0.3)
            
            # Step 2: 点击获取焦点
            brand_input = self.page.locator(target_selector).first
            if brand_input.count() > 0:
                brand_input.click(force=True)
                time.sleep(0.5)
                
                # Step 3: 清空
                self.page.keyboard.press('Control+a')
                time.sleep(0.2)
                self.page.keyboard.press('Delete')
                time.sleep(0.2)
                
                # Step 4: 逐字符输入（Brand Name 用 keyboard.type，不用 type_like_human）
                self.page.keyboard.type(brand_name, delay=30)
                time.sleep(0.5)
                
                # Step 5: Tab 键触发 blur
                self.page.keyboard.press('Tab')
                time.sleep(0.5)
            
            # Step 6: JS 兜底确保值正确
            self.page.evaluate(f"""(val) => {{
                const el = document.querySelector('{target_selector}');
                if (el) {{
                    el.value = val;
                    el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    const shadow = el.shadowRoot;
                    if (shadow) {{
                        const input = shadow.querySelector('input');
                        if (input) {{
                            input.value = val;
                            input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                            input.dispatchEvent(new Event('blur', {{ bubbles: true }}));
                        }}
                    }}
                    el.dispatchEvent(new Event('blur', {{ bubbles: true }}));
                }}
            }}""", brand_name)
            time.sleep(2)
            
            # Step 7: 验证结果
            verify = self.page.evaluate(f"""() => {{
                const el = document.querySelector('{target_selector}');
                return el ? {{ value: el.value, state: el.state }} : null;
            }}""")
            print(f"[FormFiller] Brand Name 验证: {verify}")
            
            print("[FormFiller] Brand Name 已填写")
            return True
            
        except Exception as e:
            print(f"[FormFiller] 填写 Brand Name 失败: {e}")
            return False
    
    def select_item_type_keyword(self) -> bool:
        """
        选择 Item Type Keyword (kat-dropdown)
        
        关键发现：551US 页面使用的是 kat-dropdown，ID: item_type_keyword#1.value
        选项值格式: cell-phone-screen-protectors::Cell Phones & Accessories > ...
        新UI也可能使用原生 select[name*='itemTypeKeyword']。
        """
        print("[FormFiller] 选择 Item Type Keyword...")
        
        try:
            # 查找 dropdown/select 并直接设置值；兼容 kat-dropdown 与新UI原生 select。
            result = self.page.evaluate('''() => {
                const candidates = [];
                for (const sel of [
                    'kat-dropdown[id*="item_type_keyword"]',
                    'kat-dropdown[name*="item_type_keyword"]',
                    'select[name*="itemTypeKeyword" i]',
                    'select[name*="item_type_keyword" i]',
                    'select[aria-label*="Item Type Keyword" i]'
                ]) {
                    try { candidates.push(...document.querySelectorAll(sel)); } catch (e) {}
                }
                const byId = document.getElementById('item_type_keyword#1.value');
                if (byId) candidates.unshift(byId);
                const dropdown = candidates.find(el => {
                    const text = ((el.getAttribute('name') || '') + ' ' + (el.id || '') + ' ' + (el.getAttribute('aria-label') || '') + ' ' + (el.textContent || '')).toLowerCase();
                    return text.includes('itemtypekeyword') || text.includes('item_type_keyword') || text.includes('item type keyword') || el.tagName.toLowerCase() === 'select';
                });
                if (!dropdown) return { found: false, reason: 'dropdown_not_found', count: candidates.length };
                const tag = dropdown.tagName.toLowerCase();
                const targetNeedle = 'cell-phone-screen-protectors';

                if (tag === 'select') {
                    const opts = Array.from(dropdown.options || []);
                    let opt = opts.find(o => (o.value || '').toLowerCase().includes(targetNeedle))
                           || opts.find(o => (o.textContent || '').toLowerCase().includes('cell phone screen protector'))
                           || opts.find(o => (o.textContent || '').toLowerCase().includes('screen protector'))
                           || opts.find(o => o.value);
                    if (!opt) return { found: true, tag, set: false, options_count: opts.length };
                    dropdown.value = opt.value;
                    opt.selected = true;
                    dropdown.dispatchEvent(new Event('input', { bubbles: true }));
                    dropdown.dispatchEvent(new Event('change', { bubbles: true }));
                    dropdown.dispatchEvent(new Event('blur', { bubbles: true }));
                    return { found: true, tag, set: true, value: opt.value, name: opt.textContent };
                }

                if (tag !== 'kat-dropdown') {
                    return { found: false, reason: 'not_supported_dropdown', tag };
                }
                
                let options = [];
                try {
                    options = JSON.parse(dropdown.getAttribute('options') || '[]');
                } catch (e) {
                    return { found: true, tag, options_count: 0, error: 'parse_error' };
                }
                
                const targetOption = options.find(opt => opt.value && opt.value.includes(targetNeedle))
                                  || options.find(opt => (opt.name || '').toLowerCase().includes('screen protector'));
                
                if (targetOption) {
                    dropdown.value = targetOption.value;
                    dropdown.setAttribute('value', targetOption.value);
                    dropdown.dispatchEvent(new Event('change', { bubbles: true }));
                    dropdown.dispatchEvent(new Event('input', { bubbles: true }));
                    dropdown.dispatchEvent(new Event('blur', { bubbles: true }));
                    return { found: true, tag, set: true, value: targetOption.value, name: targetOption.name };
                }
                
                if (options.length > 0) {
                    dropdown.value = options[0].value;
                    dropdown.setAttribute('value', options[0].value);
                    dropdown.dispatchEvent(new Event('change', { bubbles: true }));
                    dropdown.dispatchEvent(new Event('input', { bubbles: true }));
                    return { found: true, tag, set: true, value: options[0].value, method: 'first_option' };
                }
                
                return { found: true, tag, set: false, options_count: options.length };
            }''')
            
            if not result.get('found'):
                print(f"[FormFiller]   未找到下拉框: {result.get('reason')}")
                return True  # 如果页面上没有，可能是 URL 已指定
            
            if result.get('set'):
                print(f"[FormFiller]   已设置: {result.get('name', result.get('value', ''))[:80]}")
                time.sleep(1)
                return True
            else:
                print(f"[FormFiller]   设置失败: {result}")
                return False
                
        except Exception as e:
            print(f"[FormFiller] 选择 Item Type Keyword 失败: {e}")
            return False
    
    def uncheck_no_brand_name(self) -> bool:
        """
        取消勾选 "This product does not have a brand name"
        """
        print("[FormFiller] 检查 'no brand name' 复选框...")
        
        try:
            result = self.page.evaluate('''() => {
                const checkboxes = document.querySelectorAll('kat-checkbox');
                for (let cb of checkboxes) {
                    const label = cb.getAttribute('label') || cb.textContent || '';
                    if (label.includes('does not have a brand name')) {
                        // 通过 Shadow DOM 访问内部 checkbox
                        let wasChecked = false;
                        if (cb.shadowRoot) {
                            const nativeCb = cb.shadowRoot.querySelector('div[role="checkbox"]');
                            if (nativeCb && nativeCb.getAttribute('aria-checked') === 'true') {
                                wasChecked = true;
                                nativeCb.click();
                                nativeCb.setAttribute('aria-checked', 'false');
                            }
                        }
                        // 同时设置 kat 元素的 checked
                        if (cb.checked) {
                            cb.checked = false;
                            cb.dispatchEvent(new Event('change', { bubbles: true }));
                        }
                        return { found: true, wasChecked: wasChecked };
                    }
                }
                return { found: false };
            }''')
            
            if result.get('found'):
                if result.get('wasChecked'):
                    print("[FormFiller] 已取消勾选 'no brand name'")
                else:
                    print("[FormFiller] 'no brand name' 已是未勾选状态")
            else:
                print("[FormFiller] 未找到 'no brand name' 复选框")
            
            time.sleep(0.5)
            return True
            
        except Exception as e:
            print(f"[FormFiller] 取消勾选失败: {e}")
            return False
    
    def check_no_product_id(self) -> bool:
        """
        勾选 "This product does not have a Product ID"
        
        使用 data-cy="upc-exemption-checkbox" 选择器
        """
        print("[FormFiller] 勾选 'no product ID' 复选框...")
        
        try:
            result = self.page.evaluate('''() => {
                // 优先使用 data-cy 选择器
                let checkbox = document.querySelector('kat-checkbox[data-cy="upc-exemption-checkbox"]');
                
                // 备选：通过文本查找
                if (!checkbox) {
                    const checkboxes = document.querySelectorAll('kat-checkbox');
                    for (let cb of checkboxes) {
                        const label = cb.getAttribute('label') || cb.textContent || '';
                        if (label.includes('Product ID') || label.includes('does not have a Product ID')) {
                            checkbox = cb;
                            break;
                        }
                    }
                }
                
                if (!checkbox) {
                    return { found: false, error: 'Checkbox not found' };
                }
                
                // 通过 Shadow DOM 访问内部 checkbox
                let wasChecked = false;
                if (checkbox.shadowRoot) {
                    const checkboxDiv = checkbox.shadowRoot.querySelector('[role="checkbox"]');
                    if (checkboxDiv && !checkboxDiv.getAttribute('aria-checked')) {
                        checkboxDiv.click();
                        wasChecked = false;
                    } else if (checkboxDiv) {
                        wasChecked = true;
                    }
                }
                
                // 同时设置 kat 元素的 checked
                wasChecked = checkbox.checked || wasChecked;
                if (!checkbox.checked) {
                    checkbox.checked = true;
                    checkbox.dispatchEvent(new Event('change', { bubbles: true }));
                }
                
                return { found: true, wasChecked: wasChecked };
            }''')
            
            if result.get('found'):
                if result.get('wasChecked'):
                    print("[FormFiller] 'no product ID' 已是勾选状态")
                else:
                    print("[FormFiller] 已勾选 'no product ID'")
            else:
                print(f"[FormFiller] 未找到 'no product ID' 复选框")
            
            time.sleep(0.5)
            return True
            
        except Exception as e:
            print(f"[FormFiller] 勾选失败: {e}")
            return False
    
    def _hide_navbar_overlay(self):
        """隐藏导航栏遮挡层，防止其拦截点击事件"""
        print("[FormFiller] 隐藏导航栏遮挡层...")
        try:
            self.page.evaluate("""() => {
                const navbar = document.querySelector('#nav-bar-component') || document.querySelector('header');
                if (navbar) {
                    navbar.style.pointerEvents = 'none';
                    navbar.style.position = 'static';
                    // Also hide any overlay menus
                    const menus = document.querySelectorAll('.menu-tab-sctn, ._AU8SWG5G337hBtJw8NV');
                    menus.forEach(m => m.style.pointerEvents = 'none');
                }
            }""")
            time.sleep(0.5)
        except Exception as e:
            print(f"[FormFiller] 隐藏导航栏失败（可能页面正在导航）: {e}")
    
    def _restore_navbar_overlay(self):
        """恢复导航栏"""
        self.page.evaluate("""() => {
            const navbar = document.querySelector('#nav-bar-component') || document.querySelector('header');
            if (navbar) {
                navbar.style.pointerEvents = '';
                navbar.style.position = '';
                const menus = document.querySelectorAll('.menu-tab-sctn, ._AU8SWG5G337hBtJw8NV');
                menus.forEach(m => m.style.pointerEvents = '');
            }
        }""")
    
    def detect_add_product_ui(self) -> str:
        """Return 'new_ui', 'legacy', or 'unknown' for the Add Product identity page."""
        # Prefer Playwright locators: they are more reliable on Amazon's dynamic
        # new listing UI than one large querySelector script.
        try:
            if self.page.locator('kat-textarea[name="item_name-0-value"]').count() > 0:
                return "new_ui"
            if self.page.locator('kat-input[name="brand-0-value"]').count() > 0:
                return "new_ui"
            if self.page.locator('text=Classify your product').count() > 0:
                return "new_ui"
            if self.page.locator('text=Item Type Keyword').count() > 0:
                return "new_ui"
            if self.page.locator('text=External Product ID').count() > 0:
                return "new_ui"
            if self.page.locator('kat-textarea[name="item_name"]').count() > 0:
                return "legacy"
            if self.page.locator('kat-input[name="brand"]').count() > 0:
                return "legacy"
        except Exception:
            pass

        try:
            info = self.page.evaluate("""() => {
                const url = location.href || '';
                const bodyText = document.body ? document.body.innerText || '' : '';
                if (url.includes('/interactive/listing/workflow/create/product_identity')) return 'new_ui';
                if (bodyText.includes('Classify your product') || bodyText.includes('Product Identity') || bodyText.includes('Item Type Keyword') || bodyText.includes('External Product ID')) return 'new_ui';
                if (url.includes('/abis/listing/create/product_identity')) return 'legacy';
                return 'unknown';
            }""")
            return info or "unknown"
        except Exception:
            return "unknown"

    def _call_react_on_blur_for_field(self, field_id: str, value: str) -> bool:
        """Call the React onBlur handler used by new UI Mewtwo text fields."""
        try:
            result = self.page.evaluate("""({fieldId, value}) => {
                const el = document.getElementById(fieldId) || document.querySelector(`[name="${fieldId}"]`);
                if (!el) return { ok: false, reason: 'field_not_found' };
                function fire(node) {
                    node.dispatchEvent(new Event('input', { bubbles: true }));
                    node.dispatchEvent(new Event('change', { bubbles: true }));
                    node.dispatchEvent(new Event('blur', { bubbles: true }));
                }
                el.value = value;
                el.setAttribute('value', value);
                el.removeAttribute('state');
                el.removeAttribute('aria-invalid');
                if (el.shadowRoot) {
                    const inner = el.shadowRoot.querySelector('input, textarea');
                    if (inner) {
                        inner.value = value;
                        inner.setAttribute('value', value);
                        fire(inner);
                    }
                }
                fire(el);

                const fiberKey = Object.keys(el).find(k => k.startsWith('__reactFiber$'));
                let called = false;
                if (fiberKey) {
                    let f = el[fiberKey];
                    for (let i = 0; f && i < 12; i++, f = f.return) {
                        const p = f.memoizedProps;
                        if (p && typeof p.onBlur === 'function') {
                            p.onBlur({
                                currentTarget: { value, name: fieldId },
                                target: { value, name: fieldId },
                                preventDefault() {},
                                stopPropagation() {},
                            });
                            called = true;
                            break;
                        }
                    }
                }
                return { ok: true, calledReactOnBlur: called, value: el.value, state: el.getAttribute('state') };
            }""", {"fieldId": field_id, "value": value})
            print(f"[FormFiller:new_ui] React onBlur {field_id}: {result}")
            return bool(result.get('ok'))
        except Exception as e:
            print(f"[FormFiller:new_ui] React onBlur {field_id} 失败: {e}")
            return False

    def _new_ui_item_type_already_satisfied(self, item_type_hint: str = "") -> dict:
        """Detect when Add Product URL/page already carries the desired itemType.

        New UI often preselects productType/itemType from URL, but if Amazon renders
        an Item Type Keyword control in error/empty state, the URL alone is not enough.
        """
        try:
            return self.page.evaluate("""(hint) => {
                const url = new URL(window.location.href);
                const params = (url.search + '&' + url.hash).toLowerCase();
                const desired = 'cell-phone-screen-protectors';
                const hintText = String(hint || '').toLowerCase();
                const body = (document.body && document.body.innerText || '').toLowerCase();
                const control = document.querySelector('kat-dropdown#item_type_keyword-0-value, kat-dropdown[name="item_type_keyword-0-value"]')
                             || document.getElementById('item_type_keyword#1.value')
                             || document.querySelector('kat-dropdown[id*="item_type_keyword"], kat-dropdown[name*="item_type_keyword"], select[name*="itemTypeKeyword" i], select[name*="item_type_keyword" i]');
                const controlState = control ? (control.getAttribute('state') || '') : null;
                const controlValue = control ? String(control.value || control.getAttribute('value') || '') : '';
                const controlOk = !!control && controlValue.toLowerCase().includes(desired) && controlState !== 'error';
                const controlBlocksUrl = !!control && controlState === 'error' && !controlValue;
                const urlOk = params.includes('itemtype=' + desired) || params.includes('itemtypekeyword=' + desired) || params.includes(desired) || params.includes('producttype=screen_protector');
                const hintOk = hintText.includes(desired) || hintText.includes('screen protector');
                const pageOk = (body.includes('item type keyword') && body.includes('screen protector'))
                            || (body.includes('product type') && body.includes('screen protector'))
                            || body.includes('screen protector');
                const ok = controlBlocksUrl ? false : !!(controlOk || urlOk || (hintOk && pageOk));
                return {ok, urlOk, hintOk, pageOk, controlFound: !!control, controlState, controlValue, controlOk, controlBlocksUrl, url: window.location.href};
            }""", item_type_hint)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _select_new_ui_item_type_keyword(self, item_type_hint: str = "") -> bool:
        """Set the new UI Item Type Keyword KAT dropdown with the real option value."""
        print("[FormFiller:new_ui] 设置 Item Type Keyword...")
        satisfied = self._new_ui_item_type_already_satisfied(item_type_hint)
        if satisfied.get("ok"):
            print(f"[FormFiller:new_ui] Item Type Keyword 已由URL/页面满足: {satisfied}")
            self._new_ui_item_type_keyword_set = True
            return True
        try:
            result = self.page.evaluate("""() => {
                const dd = document.querySelector('kat-dropdown#item_type_keyword-0-value, kat-dropdown[name="item_type_keyword-0-value"]')
                        || document.getElementById('item_type_keyword#1.value')
                        || document.querySelector('kat-dropdown[id*="item_type_keyword"], kat-dropdown[name*="item_type_keyword"]');
                if (!dd) return { ok: false, reason: 'dropdown_not_found' };
                const targetValue = 'cell-phone-screen-protectors';
                let options = [];
                if (Array.isArray(dd.options)) options = dd.options;
                if (!options.length) {
                    try { options = JSON.parse(dd.getAttribute('options') || '[]'); } catch(e) { options = []; }
                }
                const target = options.find(o => String(o.value || '').toLowerCase().includes(targetValue))
                            || options.find(o => /cell phone.*screen protect|screen protectors/i.test(o.name || o.text || ''));
                if (!target) return { ok: false, reason: 'target_option_not_found', optionCount: options.length, id: dd.id, name: dd.getAttribute('name') };

                function fire(el) {
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    el.dispatchEvent(new CustomEvent('change', { bubbles: true, detail: { value: target.value, selectedOption: target, selectedOptions: [target] } }));
                    el.dispatchEvent(new CustomEvent('kat-change', { bubbles: true, detail: { value: target.value, selectedOption: target, selectedOptions: [target] } }));
                    el.dispatchEvent(new CustomEvent('katChange', { bubbles: true, detail: { value: target.value, selectedOption: target, selectedOptions: [target] } }));
                    el.dispatchEvent(new Event('blur', { bubbles: true }));
                }

                dd.value = target.value;
                dd.__value = target.value;
                dd.setAttribute('value', target.value);
                dd.removeAttribute('state');
                dd.removeAttribute('aria-invalid');

                try { dd.setSelectedBasedOnValue && dd.setSelectedBasedOnValue(); } catch(e) {}
                try { dd.setOptionSelected && dd.setOptionSelected(target, true); } catch(e) {}
                try { dd.requestUpdateAndEmitValues && dd.requestUpdateAndEmitValues(); } catch(e) {}

                if (dd.shadowRoot) {
                    for (const optEl of dd.shadowRoot.querySelectorAll('kat-option')) {
                        const isTarget = optEl.getAttribute('value') === target.value;
                        optEl.setAttribute('aria-selected', isTarget ? 'true' : 'false');
                        if (isTarget) optEl.setAttribute('selected', ''); else optEl.removeAttribute('selected');
                    }
                    const text = target.name || target.text || target.value;
                    const selection = dd.shadowRoot.querySelector('.selection-text');
                    const placeholder = dd.shadowRoot.querySelector('.placeholder-text');
                    const header = dd.shadowRoot.querySelector('.header-row-text');
                    if (selection) { selection.textContent = text; selection.classList.remove('hidden'); }
                    if (placeholder) { placeholder.textContent = ''; placeholder.classList.add('hidden'); }
                    if (header) { header.textContent = text; header.classList.remove('placeholder'); header.classList.add('value'); }
                }
                fire(dd);
                return { ok: true, method: 'value_property', id: dd.id, name: dd.getAttribute('name'), value: dd.value, attr: dd.getAttribute('value'), text: target.name || target.text || target.value, state: dd.getAttribute('state') };
            }""")
            print(f"[FormFiller:new_ui] Item Type Keyword 结果: {result}")
            if result.get('ok'):
                self._new_ui_item_type_keyword_set = True
                return True
            satisfied_after = self._new_ui_item_type_already_satisfied(item_type_hint)
            if satisfied_after.get('ok'):
                print(f"[FormFiller:new_ui] dropdown未设置但URL/页面已满足: {satisfied_after}")
                self._new_ui_item_type_keyword_set = True
                return True
            return False
        except Exception as e:
            print(f"[FormFiller:new_ui] Item Type Keyword 设置失败: {e}")
            return False

    def _find_new_ui_external_product_id_row_js(self) -> str:
        """JavaScript snippet that finds the new UI External Product ID field group.

        Some Amazon new UI builds do not render the old ResponsiveNoOpDecorator test id.
        Fall back to field names / nearby label text while keeping the scope tight.
        """
        return r"""
                function findExternalProductIdRow() {
                    const byTestId = document.querySelector('[data-testid="ResponsiveNoOpDecorator-externally_assigned_product_identifier"]')
                                  || document.querySelector('[data-testid="NoOpDecorator-externally_assigned_product_identifier"]');
                    if (byTestId) return byTestId;

                    const named = document.querySelector('[name^="externally_assigned_product_identifier"], [id^="externally_assigned_product_identifier"], kat-input[name*="externally_assigned_product_identifier"], kat-dropdown[name*="externally_assigned_product_identifier"]');
                    if (named) {
                        let node = named;
                        for (let depth = 0; depth < 8 && node; depth++, node = node.parentElement) {
                            const text = (node.textContent || '').toLowerCase();
                            const hasToggleText = text.includes('does not have a product id');
                            const hasExternalField = !!node.querySelector('[name^="externally_assigned_product_identifier"], [id^="externally_assigned_product_identifier"], kat-input[name*="externally_assigned_product_identifier"], kat-dropdown[name*="externally_assigned_product_identifier"]');
                            if (hasExternalField && (hasToggleText || node.querySelector('kat-toggle, kat-checkbox, input[type="checkbox"], [role="switch"], [role="checkbox"]'))) return node;
                        }
                    }

                    const candidates = Array.from(document.querySelectorAll('kat-checkbox, kat-toggle, label, span, div, p'))
                        .filter(el => ((el.textContent || el.getAttribute('label') || el.getAttribute('aria-label') || '').toLowerCase()).includes('does not have a product id'));
                    for (const el of candidates) {
                        let node = el;
                        for (let depth = 0; depth < 8 && node; depth++, node = node.parentElement) {
                            if (node.querySelector && node.querySelector('[name^="externally_assigned_product_identifier"], [id^="externally_assigned_product_identifier"], kat-input[name*="externally_assigned_product_identifier"], kat-dropdown[name*="externally_assigned_product_identifier"]')) return node;
                            if (node.querySelector && node.querySelector('kat-toggle, kat-checkbox, input[type="checkbox"], [role="switch"], [role="checkbox"]') && (node.textContent || '').toLowerCase().includes('external product id')) return node;
                        }
                    }
                    return null;
                }
        """

    def _get_new_ui_no_product_id_state(self) -> Dict[str, Any]:
        """Read-only state check for new UI Product ID exemption toggle.

        Amazon's kat-toggle can expose inconsistent outer aria/checked attributes.
        The shadow input checked property is the source of truth when present.
        """
        try:
            result = self.page.evaluate("""() => {
                function findExternalProductIdRow() {
                    const byTestId = document.querySelector('[data-testid="ResponsiveNoOpDecorator-externally_assigned_product_identifier"]')
                                  || document.querySelector('[data-testid="NoOpDecorator-externally_assigned_product_identifier"]');
                    if (byTestId) return byTestId;
                    const named = document.querySelector('[name^="externally_assigned_product_identifier"], [id^="externally_assigned_product_identifier"], kat-input[name*="externally_assigned_product_identifier"], kat-dropdown[name*="externally_assigned_product_identifier"]');
                    if (named) {
                        let node = named;
                        for (let depth = 0; depth < 8 && node; depth++, node = node.parentElement) {
                            const text = (node.textContent || '').toLowerCase();
                            const hasToggleText = text.includes('does not have a product id');
                            const hasExternalField = !!node.querySelector('[name^="externally_assigned_product_identifier"], [id^="externally_assigned_product_identifier"], kat-input[name*="externally_assigned_product_identifier"], kat-dropdown[name*="externally_assigned_product_identifier"]');
                            if (hasExternalField && (hasToggleText || node.querySelector('kat-toggle, kat-checkbox, input[type="checkbox"], [role="switch"], [role="checkbox"]'))) return node;
                        }
                    }
                    return null;
                }
                const row = findExternalProductIdRow();
                if (!row) return { ok: false, found: false, reason: 'external_product_id_row_not_found' };
                const ctrl = row.querySelector('kat-toggle, kat-checkbox, input[type="checkbox"], [role="switch"], [role="checkbox"]');
                if (!ctrl) return { ok: false, found: false, reason: 'toggle_not_found_in_row', rowText: (row.textContent || '').slice(0, 300) };

                let inner = null;
                if (ctrl.shadowRoot) {
                    inner = ctrl.shadowRoot.querySelector('input[type="checkbox"], [role="switch"], [role="checkbox"]');
                }
                let innerChecked = null;
                let innerAria = null;
                if (inner) {
                    if ('checked' in inner) innerChecked = !!inner.checked;
                    innerAria = inner.getAttribute('aria-checked') || inner.getAttribute('aria-pressed');
                }
                const outerAria = ctrl.getAttribute && (ctrl.getAttribute('aria-checked') || ctrl.getAttribute('aria-pressed'));
                const outerChecked = !!ctrl.checked || ctrl.hasAttribute('checked');

                let checked = null;
                let source = 'unknown';
                if (innerChecked !== null) {
                    checked = innerChecked;
                    source = 'shadow_input_checked';
                } else if (innerAria !== null) {
                    checked = innerAria === 'true';
                    source = 'shadow_aria';
                } else if (outerAria !== null) {
                    checked = outerAria === 'true';
                    source = 'outer_aria';
                } else {
                    checked = outerChecked;
                    source = 'outer_checked';
                }

                return {
                    ok: true,
                    found: true,
                    checked,
                    source,
                    tag: ctrl.tagName,
                    outerChecked,
                    outerAria,
                    innerChecked,
                    innerAria,
                    memoConsistent: null
                };
            }""")
            if result.get('ok'):
                result['memoConsistent'] = bool(self._new_ui_no_product_id_set) == bool(result.get('checked'))
            return result
        except Exception as e:
            return {"ok": False, "found": False, "reason": str(e)}

    def _clear_new_ui_external_product_id_errors(self) -> bool:
        """Clear External Product ID field errors without touching the exemption toggle."""
        try:
            result = self.page.evaluate("""() => {
                function findExternalProductIdRow() {
                    const byTestId = document.querySelector('[data-testid="ResponsiveNoOpDecorator-externally_assigned_product_identifier"]')
                                  || document.querySelector('[data-testid="NoOpDecorator-externally_assigned_product_identifier"]');
                    if (byTestId) return byTestId;
                    const named = document.querySelector('[name^="externally_assigned_product_identifier"], [id^="externally_assigned_product_identifier"], kat-input[name*="externally_assigned_product_identifier"], kat-dropdown[name*="externally_assigned_product_identifier"]');
                    if (named) {
                        let node = named;
                        for (let depth = 0; depth < 8 && node; depth++, node = node.parentElement) {
                            const text = (node.textContent || '').toLowerCase();
                            const hasToggleText = text.includes('does not have a product id');
                            const hasExternalField = !!node.querySelector('[name^="externally_assigned_product_identifier"], [id^="externally_assigned_product_identifier"], kat-input[name*="externally_assigned_product_identifier"], kat-dropdown[name*="externally_assigned_product_identifier"]');
                            if (hasExternalField && (hasToggleText || node.querySelector('kat-toggle, kat-checkbox, input[type="checkbox"], [role="switch"], [role="checkbox"]'))) return node;
                        }
                    }
                    return null;
                }
                const row = findExternalProductIdRow();
                if (!row) return { ok: false, reason: 'external_product_id_row_not_found' };

                const touched = [];
                const toggleSet = new Set(Array.from(row.querySelectorAll('kat-toggle, kat-checkbox, input[type="checkbox"], [role="switch"], [role="checkbox"]')));
                function isInsideToggle(el) {
                    for (const t of toggleSet) {
                        if (el === t || (t.contains && t.contains(el))) return true;
                    }
                    return false;
                }

                for (const el of row.querySelectorAll('kat-dropdown, kat-input, input, select')) {
                    if (isInsideToggle(el)) continue;
                    try {
                        if ('value' in el) el.value = '';
                        el.removeAttribute('value');
                        el.removeAttribute('state');
                        el.removeAttribute('aria-invalid');
                        if (el.shadowRoot) {
                            for (const inner of el.shadowRoot.querySelectorAll('input, select')) {
                                if (isInsideToggle(inner)) continue;
                                if ('value' in inner) inner.value = '';
                                inner.removeAttribute('value');
                                inner.removeAttribute('state');
                                inner.removeAttribute('aria-invalid');
                                inner.dispatchEvent(new Event('input', { bubbles: true }));
                                inner.dispatchEvent(new Event('change', { bubbles: true }));
                                inner.dispatchEvent(new Event('blur', { bubbles: true }));
                            }
                        }
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                        el.dispatchEvent(new Event('blur', { bubbles: true }));
                        touched.push(el.tagName + '#' + (el.id || '') + '[' + (el.getAttribute('name') || '') + ']');
                    } catch(e) {}
                }

                for (const err of row.querySelectorAll('[state="error"], [aria-invalid="true"], .error, [class*="error"]')) {
                    if (isInsideToggle(err)) continue;
                    try {
                        err.removeAttribute('state');
                        err.removeAttribute('aria-invalid');
                    } catch(e) {}
                }
                return { ok: true, touched };
            }""")
            print(f"[FormFiller:new_ui] External Product ID error 清理: {result}")
            return bool(result.get('ok'))
        except Exception as e:
            print(f"[FormFiller:new_ui] External Product ID error 清理失败: {e}")
            return False

    def _set_new_ui_no_product_id(self, desired: bool = True) -> bool:
        """Idempotently set the new UI 'This product does not have a Product ID' kat-toggle."""
        print("[FormFiller:new_ui] 幂等设置 no Product ID toggle...")
        try:
            before_state = self._get_new_ui_no_product_id_state()
            print(f"[FormFiller:new_ui] no Product ID 当前状态: {before_state}")

            if desired and (self._new_ui_no_product_id_set or before_state.get('checked') is True):
                self._new_ui_no_product_id_set = True
                self._clear_new_ui_external_product_id_errors()
                print("[FormFiller:new_ui] no Product ID 已开启；跳过点击，仅清理字段错误")
                return True
            if (not desired) and before_state.get('checked') is False:
                self._new_ui_no_product_id_set = False
                print("[FormFiller:new_ui] no Product ID 已关闭；跳过点击")
                return True

            # Prefer a real user-like click over JS state mutation. Amazon's new EU/BE
            # Product Identity React validation only enables Continue after the
            # shadow-input/host toggle is changed by an actual click event sequence.
            try:
                toggle = self.page.locator('kat-toggle[data-testid="HasSelectedProductIdExemptionToggle"]').first
                if toggle.count() > 0:
                    toggle.scroll_into_view_if_needed(timeout=5000)
                    toggle.click(force=True, timeout=10000)
                    time.sleep(1.5)
                    after_real_click = self._get_new_ui_no_product_id_state()
                    print(f"[FormFiller:new_ui] no Product ID 真实点击后状态: {after_real_click}")
                    if bool(after_real_click.get('checked')) == bool(desired):
                        self._new_ui_no_product_id_set = bool(desired)
                        self._clear_new_ui_external_product_id_errors()
                        return True
            except Exception as click_error:
                print(f"[FormFiller:new_ui] no Product ID 真实点击失败，回退 JS: {click_error}")

            result = self.page.evaluate("""(desired) => {
                function findExternalProductIdRow() {
                    const byTestId = document.querySelector('[data-testid="ResponsiveNoOpDecorator-externally_assigned_product_identifier"]')
                                  || document.querySelector('[data-testid="NoOpDecorator-externally_assigned_product_identifier"]');
                    if (byTestId) return byTestId;
                    const named = document.querySelector('[name^="externally_assigned_product_identifier"], [id^="externally_assigned_product_identifier"], kat-input[name*="externally_assigned_product_identifier"], kat-dropdown[name*="externally_assigned_product_identifier"]');
                    if (named) {
                        let node = named;
                        for (let depth = 0; depth < 8 && node; depth++, node = node.parentElement) {
                            const text = (node.textContent || '').toLowerCase();
                            const hasToggleText = text.includes('does not have a product id');
                            const hasExternalField = !!node.querySelector('[name^="externally_assigned_product_identifier"], [id^="externally_assigned_product_identifier"], kat-input[name*="externally_assigned_product_identifier"], kat-dropdown[name*="externally_assigned_product_identifier"]');
                            if (hasExternalField && (hasToggleText || node.querySelector('kat-toggle, kat-checkbox, input[type="checkbox"], [role="switch"], [role="checkbox"]'))) return node;
                        }
                    }
                    return null;
                }
                const row = findExternalProductIdRow();
                if (!row) return { ok: false, reason: 'external_product_id_row_not_found' };
                const ctrl = row.querySelector('kat-toggle, kat-checkbox, input[type="checkbox"], [role="switch"], [role="checkbox"]');
                if (!ctrl) return { ok: false, reason: 'toggle_not_found_in_row' };

                function checkedOf(c) {
                    if (!c) return null;
                    let inner = null;
                    if (c.shadowRoot) inner = c.shadowRoot.querySelector('input[type="checkbox"], [role="switch"], [role="checkbox"]');
                    if (inner && 'checked' in inner) return !!inner.checked;
                    if (inner) {
                        const innerAria = inner.getAttribute('aria-checked') || inner.getAttribute('aria-pressed');
                        if (innerAria != null) return innerAria === 'true';
                    }
                    const outerAria = c.getAttribute && (c.getAttribute('aria-checked') || c.getAttribute('aria-pressed'));
                    if (outerAria != null) return outerAria === 'true';
                    if (c.tagName === 'INPUT') return !!c.checked;
                    return !!c.checked || c.hasAttribute('checked');
                }

                const before = checkedOf(ctrl);
                let clicked = false;
                // Critical: click host once only. Do not click shadow .action/input too,
                // because that can double-toggle and leave outer/inner state inconsistent.
                if (before !== desired) {
                    try { ctrl.click(); clicked = true; } catch(e) { return { ok: false, reason: String(e), before, clicked }; }
                }

                // Normalize attributes/properties after the single host click.
                if (ctrl.tagName === 'INPUT' && 'checked' in ctrl) ctrl.checked = desired;
                if (desired) ctrl.setAttribute('checked', 'true'); else ctrl.removeAttribute('checked');
                ctrl.setAttribute('aria-checked', desired ? 'true' : 'false');
                if (ctrl.shadowRoot) {
                    const inner = ctrl.shadowRoot.querySelector('input[type="checkbox"], [role="switch"], [role="checkbox"]');
                    if (inner) {
                        if ('checked' in inner) inner.checked = desired;
                        inner.setAttribute('aria-checked', desired ? 'true' : 'false');
                    }
                }
                ctrl.dispatchEvent(new Event('input', { bubbles: true }));
                ctrl.dispatchEvent(new Event('change', { bubbles: true }));
                ctrl.dispatchEvent(new Event('blur', { bubbles: true }));

                return { ok: true, tag: ctrl.tagName, before, clicked, after: checkedOf(ctrl) };
            }""", desired)

            if result.get('ok'):
                self._new_ui_no_product_id_set = bool(desired)
                self._clear_new_ui_external_product_id_errors()
            after_state = self._get_new_ui_no_product_id_state()
            print(f"[FormFiller:new_ui] no Product ID 结果: {result}; after={after_state}")
            return bool(result.get('ok')) and bool(after_state.get('checked')) == bool(desired)
        except Exception as e:
            print(f"[FormFiller:new_ui] no Product ID 设置失败: {e}")
            return False

    def _select_new_ui_browse_node(self, item_type_hint: str = "") -> bool:
        """Select Browse Node on new UI when a browse-node dropdown exists. Absence is OK."""
        print("[FormFiller:new_ui] 检查/选择 Browse Node...")
        try:
            hint_lower = (item_type_hint or "").lower()
            preferred_node = ""
            if "skärmskydd" in hint_lower or "underhåll" in hint_lower:
                preferred_node = "20637844031"  # Sweden mobile screen protectors
            elif "schermbeschermers" in hint_lower or "onderhoud" in hint_lower:
                preferred_node = "16366238031"  # Netherlands mobile screen protectors
            elif "protections d'écran" in hint_lower or "protections d'ecran" in hint_lower:
                preferred_node = "27863027031"  # Belgium mobile screen protectors

            # Real click path first. In BE/EU new UI, JS-setting the host value can
            # update the DOM while leaving React validation incomplete. A real option
            # click correctly advances Product Identity completion.
            try:
                dropdown = self.page.locator('kat-dropdown#recommended_browse_nodes-0-value, kat-dropdown[name="recommended_browse_nodes-0-value"]').first
                if dropdown.count() > 0:
                    current = self.page.evaluate("""() => {
                        const dd = document.querySelector('kat-dropdown#recommended_browse_nodes-0-value, kat-dropdown[name="recommended_browse_nodes-0-value"]');
                        return dd ? (dd.value || dd.getAttribute('value') || '') : '';
                    }""")
                    if current:
                        print(f"[FormFiller:new_ui] Browse Node 已有值: {current}")
                        return True
                    dropdown.scroll_into_view_if_needed(timeout=5000)
                    dropdown.click(force=True, timeout=10000)
                    time.sleep(1)
                    option = self.page.locator(f'kat-option[value="{preferred_node}"]').first if preferred_node else None
                    if option is not None and option.count() > 0:
                        option.click(force=True, timeout=10000)
                        time.sleep(1.5)
                        after = self.page.evaluate("""() => {
                            const dd = document.querySelector('kat-dropdown#recommended_browse_nodes-0-value, kat-dropdown[name="recommended_browse_nodes-0-value"]');
                            return dd ? { value: dd.value || dd.getAttribute('value') || '', text: (dd.shadowRoot && dd.shadowRoot.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 220) } : null;
                        }""")
                        print(f"[FormFiller:new_ui] Browse Node 真实点击后: {after}")
                        if after and after.get('value'):
                            return True
            except Exception as real_click_error:
                print(f"[FormFiller:new_ui] Browse Node 真实点击失败，回退 JS: {real_click_error}")

            result = self.page.evaluate("""(hint) => {
                const dropdown = document.querySelector('kat-dropdown[name="recommended_browse_nodes-0-value"]')
                              || document.getElementById('recommended_browse_nodes#1.value')
                              || document.querySelector('select[name*="browse" i]');
                if (!dropdown) return { ok: true, found: false, reason: 'not_present' };
                if (dropdown.value) return { ok: true, found: true, already_set: true, value: dropdown.value };

                const targetTerms = [
                    'Maintenance, Upkeep & Repairs', 'Screen Protectors', 'Cell Phones', 'Mobile Phones',
                    'Protectores de Pantalla', 'Protections d', 'Displayschutzfolien', 'Schermbeschermers',
                    'Skärmskydd', 'Pellicole', 'Protezioni'
                ];

                if (dropdown.tagName.toLowerCase() === 'select') {
                    const opts = Array.from(dropdown.options || []);
                    let opt = opts.find(o => {
                        const t = (o.textContent || '') + ' ' + (o.value || '');
                        return /screen protect|protectores de pantalla|protections d|displayschutz|schermbescherm|skärmskydd|pellicole|protezioni/i.test(t)
                            && !/psp|legacy|nintendo|switch/i.test(t);
                    }) || opts.find(o => o.value);
                    if (!opt) return { ok: false, found: true, reason: 'no_options' };
                    dropdown.value = opt.value;
                    opt.selected = true;
                    dropdown.dispatchEvent(new Event('input', { bubbles: true }));
                    dropdown.dispatchEvent(new Event('change', { bubbles: true }));
                    dropdown.dispatchEvent(new Event('blur', { bubbles: true }));
                    return { ok: true, found: true, set: true, value: opt.value, text: opt.textContent };
                }

                let options = [];
                try { options = JSON.parse(dropdown.getAttribute('options') || '[]'); } catch(e) {}
                let opt = options.find(o => {
                    const t = ((o.name || '') + ' ' + (o.value || ''));
                    return /screen protect|protectores de pantalla|protections d|displayschutz|schermbescherm|skärmskydd|pellicole|protezioni/i.test(t)
                        && !/psp|legacy|nintendo|switch/i.test(t);
                }) || options.find(o => o.value);
                if (opt) {
                    dropdown.value = opt.value;
                    dropdown.setAttribute('value', opt.value);
                    dropdown.dispatchEvent(new Event('input', { bubbles: true }));
                    dropdown.dispatchEvent(new Event('change', { bubbles: true }));
                    dropdown.dispatchEvent(new Event('blur', { bubbles: true }));
                    return { ok: true, found: true, set: true, value: opt.value, text: opt.name };
                }

                // BE/FR new UI searchable kat-dropdown renders options only inside
                // shadow DOM as <kat-option>; the host has no options attribute.
                // Prefer the exact mobile phone maintenance screen-protector node.
                if (dropdown.shadowRoot) {
                    const shadowOptions = Array.from(dropdown.shadowRoot.querySelectorAll('kat-option[role="option"], kat-option'));
                    const scoreOption = (el) => {
                        const t = (el.textContent || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                        let score = 0;
                        if (/téléphones portables|telephones portables|mobile phones|cell phone|handys|communication mobile/.test(t)) score += 100;
                        if (/maintenance|entretien|repairs|reparaturen|réparations|reparations/.test(t)) score += 50;
                        if (/protections d'écran|protections d'ecran|screen protect|displayschutz|schermbescherm|protectores de pantalla/.test(t)) score += 50;
                        if (/objectif|camera|photo|caméscope|camescope|liseuses|kindle|nintendo|switch|tv|tablettes|tablet|ordinateur|laptop|gps|mp3|smartwatch/.test(t)) score -= 100;
                        return score;
                    };
                    let best = shadowOptions
                        .map(el => ({ el, score: scoreOption(el), text: (el.textContent || '').replace(/\\s+/g, ' ').trim(), value: el.getAttribute('value') || '' }))
                        .sort((a, b) => b.score - a.score)[0];
                    if (best && best.score >= 100) {
                        const value = best.value;
                        try { best.el.scrollIntoView({ block: 'center' }); } catch(e) {}
                        try { best.el.click(); } catch(e) {}
                        if (value) {
                            dropdown.value = value;
                            dropdown.setAttribute('value', value);
                        }
                        dropdown.dispatchEvent(new Event('input', { bubbles: true }));
                        dropdown.dispatchEvent(new Event('change', { bubbles: true }));
                        dropdown.dispatchEvent(new CustomEvent('change', { bubbles: true, detail: { value, selectedOption: { value, name: best.text }, selectedOptions: [{ value, name: best.text }] } }));
                        dropdown.dispatchEvent(new CustomEvent('kat-change', { bubbles: true, detail: { value, selectedOption: { value, name: best.text }, selectedOptions: [{ value, name: best.text }] } }));
                        dropdown.dispatchEvent(new CustomEvent('katChange', { bubbles: true, detail: { value, selectedOption: { value, name: best.text }, selectedOptions: [{ value, name: best.text }] } }));
                        dropdown.dispatchEvent(new Event('blur', { bubbles: true }));
                        return { ok: true, found: true, set: true, source: 'shadow_kat_option', value, text: best.text, score: best.score };
                    }
                    return { ok: false, found: true, reason: 'no_matching_shadow_option', optionCount: shadowOptions.length, best: best ? { text: best.text, value: best.value, score: best.score } : null };
                }
                return { ok: false, found: true, reason: 'no_matching_option' };
            }""", item_type_hint or "")
            print(f"[FormFiller:new_ui] Browse Node 结果: {result}")
            return bool(result.get('ok'))
        except Exception as e:
            print(f"[FormFiller:new_ui] Browse Node 选择失败: {e}")
            return False

    def _fill_new_ui_item_highlight(self, brand_name: str = "") -> bool:
        """Fill Item Highlight on new UI when Amazon exposes it in Product Identity."""
        print("[FormFiller:new_ui] 填写 Item Highlight...")
        value = f"Protection d'écran claire et résistante pour smartphone {brand_name}".strip()
        try:
            result = self.page.evaluate("""({value}) => {
                const el = document.querySelector('kat-input#title_differentiation-0-value, kat-input[name="title_differentiation-0-value"]');
                if (!el) return { ok: true, found: false, reason: 'not_present' };
                el.value = value;
                el.setAttribute('value', value);
                el.classList.add('touched');
                if (el.shadowRoot) {
                    const input = el.shadowRoot.querySelector('input');
                    if (input) {
                        input.value = value;
                        input.dispatchEvent(new Event('input', { bubbles: true }));
                        input.dispatchEvent(new Event('change', { bubbles: true }));
                        input.dispatchEvent(new Event('blur', { bubbles: true }));
                    }
                }
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                el.dispatchEvent(new Event('blur', { bubbles: true }));
                return { ok: true, found: true, value: el.value || el.getAttribute('value') };
            }""", {"value": value})
            print(f"[FormFiller:new_ui] Item Highlight 结果: {result}")
            return bool(result.get('ok'))
        except Exception as e:
            print(f"[FormFiller:new_ui] Item Highlight 填写失败: {e}")
            return False

    def _set_labeled_toggle_new_ui(self, label_text: str, desired: bool) -> bool:
        """Best-effort set a new-UI labeled switch/checkbox without relying on visible text locator."""
        try:
            result = self.page.evaluate("""({labelText, desired}) => {
                const lowerNeedle = labelText.toLowerCase();
                const candidates = Array.from(document.querySelectorAll('label, span, div, p, kat-checkbox, input, button, [role="switch"], [role="checkbox"]'))
                    .filter(el => ((el.textContent || el.getAttribute('aria-label') || el.getAttribute('label') || '').toLowerCase()).includes(lowerNeedle));

                function checkedOf(ctrl) {
                    if (!ctrl) return null;
                    if (ctrl.tagName === 'INPUT') return !!ctrl.checked;
                    if (ctrl.tagName === 'KAT-CHECKBOX') {
                        if (ctrl.shadowRoot) {
                            const inner = ctrl.shadowRoot.querySelector('[role="checkbox"], [role="switch"]');
                            if (inner) return inner.getAttribute('aria-checked') === 'true';
                        }
                        return !!ctrl.checked || ctrl.hasAttribute('checked');
                    }
                    const aria = ctrl.getAttribute && (ctrl.getAttribute('aria-checked') || ctrl.getAttribute('aria-pressed'));
                    if (aria != null) return aria === 'true';
                    return !!ctrl.checked;
                }

                function applyState(ctrl) {
                    if (!ctrl) return false;
                    const current = checkedOf(ctrl);
                    if (current !== desired) {
                        try { ctrl.click(); } catch(e) {}
                    }
                    if (ctrl.tagName === 'INPUT') ctrl.checked = desired;
                    if (desired) {
                        ctrl.setAttribute('checked', 'true');
                        ctrl.setAttribute('aria-checked', 'true');
                    } else {
                        ctrl.removeAttribute('checked');
                        ctrl.setAttribute('aria-checked', 'false');
                    }
                    if (ctrl.tagName === 'KAT-CHECKBOX' && ctrl.shadowRoot) {
                        const inner = ctrl.shadowRoot.querySelector('[role="checkbox"], [role="switch"]');
                        if (inner) inner.setAttribute('aria-checked', desired ? 'true' : 'false');
                    }
                    ctrl.dispatchEvent(new Event('input', { bubbles: true }));
                    ctrl.dispatchEvent(new Event('change', { bubbles: true }));
                    return true;
                }

                for (const textEl of candidates) {
                    let node = textEl;
                    for (let depth = 0; depth < 6 && node; depth++, node = node.parentElement) {
                        const controls = [];
                        if (node.matches && node.matches('kat-toggle,kat-checkbox,input[type="checkbox"],button,[role="switch"],[role="checkbox"]')) controls.push(node);
                        controls.push(...node.querySelectorAll('kat-toggle,kat-checkbox,input[type="checkbox"],button,[role="switch"],[role="checkbox"]'));
                        for (const ctrl of controls) {
                            if (applyState(ctrl)) return { ok: true, current: checkedOf(ctrl), tag: ctrl.tagName, depth };
                        }
                    }
                }
                return { ok: false, reason: 'control_not_found', matches: candidates.length };
            }""", {"labelText": label_text, "desired": desired})
            print(f"[FormFiller:new_ui] toggle '{label_text}' => {result}")
            return bool(result.get('ok'))
        except Exception as e:
            print(f"[FormFiller:new_ui] toggle '{label_text}' 设置失败: {e}")
            return False

    def ensure_add_product_ready_for_continue(self, ui_type: str | None = None, item_type_hint: str = "") -> Dict[str, bool]:
        """Final pre-continue normalization. Safe for both UI versions."""
        ui = ui_type or self.detect_add_product_ui()
        results: Dict[str, bool] = {}
        if ui == "new_ui":
            results["item_type_keyword"] = self._select_new_ui_item_type_keyword(item_type_hint=item_type_hint)
            results["browse_node"] = self._select_new_ui_browse_node(item_type_hint=item_type_hint)
            results["variations_unchecked"] = self._set_labeled_toggle_new_ui("has variations", False)
            # Keep Product ID exemption as the last state-changing action. The BE/EU
            # new UI can leave Continue disabled if this toggle is set before other
            # Product Identity fields have committed.
            results["no_product_id"] = self._set_new_ui_no_product_id(True)
        else:
            results["no_product_id"] = self.check_no_product_id()
        return results

    def fill_product_identity_legacy(self, brand_name: str, item_name: str, item_type_hint: str = "") -> Dict[str, bool]:
        """Legacy Add Product identity form path. Kept separate from new UI."""
        print("\n[FormFiller:legacy] 开始填写旧 UI Product Identity 表单...")
        results: Dict[str, bool] = {}
        results['item_name'] = self.fill_item_name(item_name)
        time.sleep(1)
        results['item_type_keyword'] = self.select_item_type_keyword()
        time.sleep(1)
        results['no_brand_unchecked'] = self.uncheck_no_brand_name()
        time.sleep(1)
        results['brand_name'] = self.fill_brand_name(brand_name)
        time.sleep(1)
        results['no_brand_unchecked_2'] = self.uncheck_no_brand_name()
        time.sleep(0.5)
        results.update(self.ensure_add_product_ready_for_continue("legacy", item_type_hint=item_type_hint))
        print("\n[FormFiller:legacy] 表单填写结果:")
        for k, v in results.items():
            print(f"  [{'OK' if v else 'FAIL'}] {k}")
        return results

    def fill_product_identity_new_ui(self, brand_name: str, item_name: str, item_type_hint: str = "") -> Dict[str, bool]:
        """New Add Product identity form path. Coexists with legacy path without sharing UI-specific selectors."""
        print("\n[FormFiller:new_ui] 开始填写新 UI Product Identity 表单...")
        results: Dict[str, bool] = {}

        # Important for Amazon's newer BE/EU Product Identity UI:
        # fill visible identity fields first, then enable the Product ID exemption last.
        # Enabling "This product does not have a Product ID" too early can cause Amazon's
        # React state/validation to recompute before Browse Node + Brand are committed,
        # leaving "Continue to Description" disabled even when DOM values look correct.
        results['item_name'] = self.fill_item_name(item_name)
        results['item_name_react'] = self._call_react_on_blur_for_field('item_name-0-value', item_name)
        time.sleep(1)
        results['item_type_keyword'] = self._select_new_ui_item_type_keyword(item_type_hint=item_type_hint)
        results['browse_node'] = self._select_new_ui_browse_node(item_type_hint=item_type_hint)
        time.sleep(1)

        results['no_brand_unchecked'] = self.uncheck_no_brand_name()
        results['brand_name'] = self.fill_brand_name(brand_name)
        results['brand_name_react'] = self._call_react_on_blur_for_field('brand-0-value', brand_name)
        time.sleep(1)

        results['no_brand_unchecked_2'] = self.uncheck_no_brand_name()
        time.sleep(0.5)

        # Final pass must not re-open Product ID before Browse Node/Brand. Keep the
        # exemption as the last mutating action in the identity section.
        results['browse_node_final'] = self._select_new_ui_browse_node(item_type_hint=item_type_hint)
        results['variations_unchecked'] = self._set_labeled_toggle_new_ui("has variations", False)
        results['no_product_id'] = self._set_new_ui_no_product_id(True)
        print("\n[FormFiller:new_ui] 表单填写结果:")
        for k, v in results.items():
            print(f"  [{'OK' if v else 'FAIL'}] {k}")
        return results

    def fill_product_identity_form(self, brand_name: str, item_name: str = "Screen Protector for iPhone 15 Pro Max, Tempered Glass, 9H Hardness, Case Friendly", item_type_hint: str = "") -> Dict[str, bool]:
        """
        Dispatcher for Product Identity form filling.
        New UI and legacy UI intentionally coexist as separate branches.
        """
        print("\n[FormFiller] 开始填写 Product Identity 表单...")
        self._hide_navbar_overlay()
        ui = self.detect_add_product_ui()
        print(f"[FormFiller] Add Product UI 类型: {ui}")
        if ui == "new_ui":
            return self.fill_product_identity_new_ui(brand_name, item_name, item_type_hint=item_type_hint)
        return self.fill_product_identity_legacy(brand_name, item_name, item_type_hint=item_type_hint)

    def click_next(self, check_enabled: bool = True, timeout: int = 10) -> bool:
        """
        点击 Next 按钮
        兼容新旧UI结构
        """
        print("[FormFiller] 点击 Next 按钮...")
        
        # 最终检查 variations checkbox - Amazon 可能多次自动勾选
        print("[FormFiller] 最终检查 variations checkbox...")
        try:
            final_check = self.page.evaluate('''() => {
                const cbs = document.querySelectorAll('kat-checkbox');
                for (let cb of cbs) {
                    const label = cb.getAttribute('label') || '';
                    const parent = cb.parentElement;
                    const siblingText = parent ? parent.textContent : '';
                    if ((label + siblingText).includes('has variations')) {
                        let isChecked = false;
                        if (cb.shadowRoot) {
                            const inner = cb.shadowRoot.querySelector('div[role="checkbox"]');
                            if (inner) isChecked = inner.getAttribute('aria-checked') === 'true';
                        }
                        if (cb.hasAttribute('checked') || cb.checked) isChecked = true;
                        return { found: true, isChecked };
                    }
                }
                return { found: false };
            }''')
            if final_check.get('found') and final_check.get('isChecked'):
                print("[FormFiller] !! variations 仍被勾选，强制取消...")
                # 使用 Playwright click（更可靠的取消方式）
                try:
                    for cb in self.page.locator('kat-checkbox').all():
                        label = cb.get_attribute('label') or ''
                        sibling = cb.evaluate('el => el.parentElement ? el.parentElement.textContent : ""') or ''
                        if 'has variations' in (label + sibling):
                            cb.click(force=True)
                            time.sleep(0.5)
                            print("[FormFiller] variations 已通过 Playwright 取消勾选")
                            break
                except Exception as e:
                    print(f"[FormFiller] Playwright 取消勾选失败: {e}")
            else:
                print("[FormFiller] OK variations 未勾选")
        except Exception as e:
            print(f"[FormFiller] 最终检查 variations 失败: {e}")
        
        # 最终检查 no Product ID 是否被勾选
        try:
            pid_check = self.page.evaluate('''() => {
                const cbs = document.querySelectorAll('kat-checkbox');
                for (let cb of cbs) {
                    const parent = cb.parentElement;
                    const siblingText = parent ? parent.textContent : '';
                    if (siblingText.includes('does not have a Product ID')) {
                        let isChecked = false;
                        if (cb.shadowRoot) {
                            const inner = cb.shadowRoot.querySelector('div[role="checkbox"]');
                            if (inner) isChecked = inner.getAttribute('aria-checked') === 'true';
                        }
                        if (cb.hasAttribute('checked') || cb.checked) isChecked = true;
                        return { found: true, isChecked };
                    }
                }
                return { found: false };
            }''')
            if pid_check.get('found') and not pid_check.get('isChecked'):
                print("[FormFiller] !! no Product ID 未被勾选，重新勾选...")
                try:
                    for cb in self.page.locator('kat-checkbox').all():
                        sibling = cb.evaluate('el => el.parentElement ? el.parentElement.textContent : ""') or ''
                        if 'does not have a Product ID' in sibling:
                            cb.click(force=True)
                            time.sleep(0.5)
                            print("[FormFiller] no Product ID 已重新勾选")
                            break
                except Exception as e:
                    print(f"[FormFiller] 重新勾选 no Product ID 失败: {e}")
        except Exception as e:
            print(f"[FormFiller] 检查 no Product ID 失败: {e}")
        
        # 人性化延迟：模拟人类检查表单的时间
        import random
        human_delay = random.randint(5, 12)
        print(f"[FormFiller] 人性化延迟 {human_delay} 秒...")
        time.sleep(human_delay)
        
        try:
            # 等待按钮可用
            if check_enabled:
                print("  等待表单验证...")
                time.sleep(3)
            
            # 优先使用 Playwright 真实交互点击 Next；失败再用 JS fallback。
            result = {"found": False, "clicked": False, "wasDisabled": False}
            next_locators = [
                self.page.locator('kat-button#next-button').first,
                self.page.locator('kat-button[data-testid="continue-button"]').first,
                self.page.locator('kat-button[kat-aria-label="Next"]').first,
                self.page.get_by_role("button", name=re.compile(r"^(Next|Continue)$", re.I)).first,
                self.page.locator('kat-button').filter(has_text=re.compile(r"^(Next|Continue)$", re.I)).first,
                self.page.locator('button').filter(has_text=re.compile(r"^(Next|Continue)$", re.I)).first,
            ]
            for loc in next_locators:
                try:
                    if loc.count() > 0:
                        disabled = loc.get_attribute('disabled') in ('true', '')
                        clicked = self._human_click_locator(loc, "Next/Continue", timeout=10000, force=False)
                        result = {"found": True, "clicked": clicked, "wasDisabled": disabled, "method": "playwright"}
                        if clicked:
                            break
                except Exception:
                    continue
            if not result.get('clicked'):
                print("[FormFiller] Playwright 点击 Next/Continue 未成功，使用 JS fallback")
                result = self.page.evaluate('''
                    () => {
                        const selectors = [
                            'kat-button#next-button',
                            'kat-button[data-testid="continue-button"]',
                            'kat-button[kat-aria-label="Next"]'
                        ];
                        let btn = null;
                        for (const sel of selectors) {
                            try { btn = document.querySelector(sel); if (btn) break; } catch(e) {}
                        }
                        if (!btn) {
                            const allBtns = document.querySelectorAll('kat-button, button');
                            for (const b of allBtns) {
                                const text = (b.getAttribute('label') || b.textContent || '').trim().toLowerCase();
                                if (text === 'next' || text === 'continue') { btn = b; break; }
                            }
                        }
                        if (!btn) return { found: false, error: 'Button not found' };
                        const isDisabled = btn.getAttribute('disabled') === 'true' || btn.disabled;
                        try { btn.scrollIntoView({block:'center', inline:'center'}); } catch(e) {}
                        btn.click();
                        return { found: true, clicked: true, wasDisabled: isDisabled, method: 'js-fallback' };
                    }
                ''')
            
            if result.get('found'):
                if result.get('wasDisabled'):
                    print("[FormFiller] [警告] Next 按钮被禁用，但已尝试点击")
                else:
                    print("[FormFiller] Next 按钮已启用，已点击")
                time.sleep(3)
                
                # Handle category picker modal (BE/EU sites)
                try:
                    confirm_btn = self.page.locator('#category_picker_modal_ok').first
                    if confirm_btn.count() > 0 and confirm_btn.is_visible():
                        print("[FormFiller] 检测到类别更改确认框，点击确认...")
                        confirm_btn.click(force=True, timeout=5000)
                        time.sleep(2)
                except Exception:
                    pass
                
                return True
            else:
                print("[FormFiller] 未找到 Next 按钮")
                return False
            
        except Exception as e:
            print(f"[FormFiller] 点击 Next 失败: {e}")
            return False
    
    def _close_5461_panel(self) -> dict:
        """Close/hide the Amazon 5461 right-side panel without treating hiding as success."""
        try:
            return self.page.evaluate('''
                () => {
                    const selectors = [
                        'kat-button[data-testid*="close"]',
                        '[data-action*="close"]',
                        'kat-button[label*="Close"]',
                        'kat-button[label*="close"]',
                        'button[aria-label="close"]',
                        'button[aria-label="Close"]',
                        'button[part="panel-close-button"]',
                        'button.close, .close-button, [class*="close"]'
                    ];
                    for (const sel of selectors) {
                        for (const el of document.querySelectorAll(sel)) {
                            try { el.click(); return {closed: 'clicked', selector: sel}; } catch(e) {}
                        }
                    }
                    document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', keyCode:27, bubbles:true}));
                    for (const p of document.querySelectorAll('kat-panel-wrapper')) {
                        try {
                            p.setAttribute('panel-visible', 'false');
                            p.removeAttribute('panelVisible');
                            p.style.display = 'none';
                            p.style.visibility = 'hidden';
                        } catch(e) {}
                    }
                    return {closed: 'force'};
                }
            ''')
        except Exception as e:
            return {"closed": "error", "error": str(e)}

    def _probe_5461_panel(self) -> dict:
        """Observe whether the 5461 panel is present and whether real fields/uploads loaded."""
        return self.page.evaluate('''
            () => {
                // ★ 2026-07-15: 新UI panel 没有 data-testid="kat-panel-wrapper-QualificationWidget"
                // 扩展查找策略：先找特定 testid，再按内容判断
                let panel = document.querySelector('kat-panel-wrapper[data-testid="kat-panel-wrapper-QualificationWidget"]');
                if (!panel) {
                    // 新UI: 找含 5461 表单内容的任意 kat-panel-wrapper
                    const allWrappers = document.querySelectorAll('kat-panel-wrapper');
                    for (const w of allWrappers) {
                        const t = w.innerText || w.textContent || '';
                        if (t.includes('Listing approval') || t.includes('listing approval') ||
                            t.includes('cat_auth_mo') || t.includes('product_title') ||
                            t.includes('Brands require approval') || t.includes('Submit required information')) {
                            panel = w;
                            break;
                        }
                    }
                }
                const fieldSelectors = [
                    'kat-input[id*="product_title"]',
                    'kat-input#question-cat_auth_mo_question_string_id_product_title',
                    'kat-input#question-cat_auth_mo_question_string_id_manufacturer',
                    'kat-input#question-cat_auth_mo_question_string_id_product_description',
                    'kat-input#contact_info_email_input',
                    'input[id*="document_upload"][id*="document_input"]'
                ];
                const hasRealFields = fieldSelectors.some(sel => !!document.querySelector(sel));
                if (panel) {
                    const style = window.getComputedStyle(panel);
                    return {
                        found: true,
                        visibleAttr: panel.getAttribute('panel-visible'),
                        inlineDisplay: panel.style.display,
                        computedDisplay: style.display,
                        computedVisibility: style.visibility,
                        width: panel.offsetWidth,
                        height: panel.offsetHeight,
                        hasContent: panel.innerHTML.length > 100,
                        hasRealFields
                    };
                }
                return { found: false, hasRealFields };
            }
        ''')

    def _wait_for_5461_real_fields(self, timeout_sec: int = 12, interval_sec: int = 2) -> bool:
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            try:
                if self._probe_5461_panel().get('hasRealFields'):
                    return True
            except Exception:
                pass
            time.sleep(interval_sec)
        return False

    def select_create_new_asins_application(self) -> dict:
        """Select the create-ASIN application card when that exact safe path exists.

        Brands that go directly to the form return ``none``.  A sell-products-only
        or unrecognised chooser is deliberately not clicked and is left for human
        review/incident handling.
        """
        from .application_type_selection import probe_application_type_options

        probe = probe_application_type_options(self.page)
        result = dict(probe)
        result.update({"clicked": False, "fields_ready": False})
        status = result.get("status")

        if status != "create_new_asins_available":
            self.application_type_result = result
            return result

        if not self.brand_name:
            result.update({
                "status": "target_brand_missing",
                "reason": "create-ASIN card is visible but the target brand is unavailable",
            })
            self.application_type_result = result
            return result

        expected_text = f"Application to create new ASINs for {self.brand_name}"
        exact_pattern = re.compile(
            rf"^\s*Application\s+to\s+create\s+new\s+ASINs\s+for\s+{re.escape(self.brand_name)}\s*$",
            re.I,
        )
        clicked_by = None

        candidates = []
        try:
            candidates.append(("exact-text", self.page.get_by_text(exact_pattern).first))
        except Exception:
            pass
        try:
            candidates.append((
                "application-tile-header",
                self.page.locator('[data-cy="application_tile_header"]').filter(has_text=exact_pattern).first,
            ))
        except Exception:
            pass
        try:
            candidates.append((
                "application-kat-box",
                self.page.locator("kat-box").filter(has_text=exact_pattern).first,
            ))
        except Exception:
            pass

        for label, locator in candidates:
            try:
                if locator.count() > 0 and locator.is_visible():
                    if self._human_click_locator(locator, expected_text, timeout=10000):
                        clicked_by = label
                        break
            except Exception:
                continue

        if not clicked_by:
            try:
                fallback = self.page.evaluate(
                    """(expectedText) => {
                        const normalize = (value) => (value || '').replace(/\s+/g, ' ').trim();
                        const expected = normalize(expectedText).toLowerCase();
                        const all = [];
                        const visit = (root) => {
                            for (const el of root.querySelectorAll('*')) {
                                all.push(el);
                                if (el.shadowRoot) visit(el.shadowRoot);
                            }
                        };
                        visit(document);
                        const matches = all.filter((el) => {
                            const text = normalize(el.innerText || el.textContent).toLowerCase();
                            return text === expected;
                        }).sort((a, b) => a.childElementCount - b.childElementCount);
                        if (!matches.length) return {clicked: false, reason: 'exact_brand_card_not_found'};
                        const label = matches[0];
                        const clickable = label.closest(
                            'a, button, [role="button"], kat-box, ' +
                            '[data-cy*="application"], [data-testid*="application"]'
                        ) || label;
                        clickable.scrollIntoView({block: 'center'});
                        clickable.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
                        clickable.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
                        clickable.click();
                        return {clicked: true, method: 'exact-brand-js'};
                    }""",
                    expected_text,
                )
                if fallback and fallback.get("clicked"):
                    clicked_by = fallback.get("method") or "exact-brand-js"
            except Exception:
                pass

        if not clicked_by:
            result.update({
                "status": "create_card_click_failed",
                "reason": "exact create-new-ASINs card for the target brand was not clickable",
            })
            self.application_type_result = result
            return result

        fields_ready = self._wait_for_5461_real_fields(timeout_sec=24, interval_sec=2)
        result.update({
            "clicked": True,
            "clicked_by": clicked_by,
            "fields_ready": fields_ready,
            "status": "create_card_selected" if fields_ready else "create_card_selected_form_not_ready",
        })
        self.application_type_result = result
        return result

    def click_apply_to_sell(self) -> bool:
        """
        点击 Apply to sell 按钮，支持 429/410001 错误重试
        """
        print("[FormFiller] 点击 Apply to sell 按钮...")

        try:
            if self._probe_5461_panel().get('hasRealFields'):
                print("[FormFiller] 5461 真实字段已经可用，无需再次点击 Apply to sell")
                return True
        except Exception:
            pass

        # The application-type chooser may already be open (for example after
        # Product Identity triggers it).  Handle it before looking for another
        # Apply button so the panel is not mistaken for a half-loaded shell.
        preselection = self.select_create_new_asins_application()
        if preselection.get('clicked') and preselection.get('fields_ready'):
            print("[FormFiller] 已选择 create new ASINs 申请入口，5461 字段可用")
            return True
        if preselection.get('status') in {
            'sell_products_only', 'unknown_application_options',
            'target_brand_missing', 'create_card_click_failed',
        }:
            print(f"[FormFiller] 申请类型面板需人工复核: {preselection.get('status')}")
            return False
        panel_already_open = bool(preselection.get('panel_found'))
        
        max_retries = 6
        wait_min = 18
        wait_max = 45
        for attempt in range(max_retries):
            try:
                # 优先使用 Playwright hover/click，失败再 JS fallback。
                if attempt == 0 and panel_already_open:
                    result = {"clicked": True, "location": "existing-application-panel"}
                else:
                    result = self._click_apply_to_sell_human()
                
                if result.get('clicked'):
                    print(f"[FormFiller] 已点击 Apply to sell ({result.get('location')})")
                    time.sleep(12)  # 增加等待时间到 12 秒，让弹窗充分加载
                    
                    # 检查 5461 弹窗是否已打开。
                    # 注意：这里只做观测，不强制 display=block；强制显示会把半加载 shell 误判为真表单。
                    panel_check = self._probe_5461_panel()
                    print(f"[FormFiller] 5461 弹窗状态: {panel_check}")
                    if not panel_check.get('hasRealFields'):
                        # The application-type chooser is a real, visible panel, but it
                        # does not contain the legacy Listing approval markers used by
                        # _probe_5461_panel.  Probe it even when ``found`` is false so we
                        # can safely select the exact create-new-ASINs card instead of
                        # retrying the obscured page-level View link.
                        selection = self.select_create_new_asins_application()
                        if selection.get('clicked') and selection.get('fields_ready'):
                            print("[FormFiller] 已选择 create new ASINs 申请入口，5461 字段可用")
                            return True
                        if selection.get('status') in {
                            'sell_products_only', 'unknown_application_options',
                            'target_brand_missing', 'create_card_click_failed',
                        }:
                            print(f"[FormFiller] 申请类型面板需人工复核: {selection.get('status')}")
                            return False

                    if panel_check.get('found') and not panel_check.get('hasRealFields'):
                        print("[FormFiller] 5461 panel 仅 shell/半加载，等待字段真实出现，不强制显示...")
                        # 检查 panel 内是否已有 Case ID；必须同时确认状态。
                        # 旧 declined Case 也会显示 Case ID，不能误判为 Under Review。
                        try:
                            panel_case = self.page.evaluate("""() => {
                                const panel = document.querySelector('kat-panel-wrapper[data-testid="kat-panel-wrapper-QualificationWidget"]');
                                if (!panel) return null;
                                const text = panel.innerText || panel.textContent || '';
                                const m = text.match(/(?:Case\\s*ID\\s*[-:#]?\\s*)?(\\d{10,12})/i);
                                if (!m) return null;
                                const start = Math.max(0, m.index - 80);
                                const end = Math.min(text.length, m.index + m[0].length + 140);
                                const context = text.slice(start, end);
                                const ctx = context.toLowerCase();
                                let status = 'unknown';
                                if (/declined|rejected|denied/.test(ctx)) status = 'declined';
                                else if (/under\\s+review|submitted|decision\\s+expected/.test(ctx)) status = 'under_review';
                                // 提取 application tile header 里的品牌名
                                // "Application to create new ASINs for <BRAND>"
                                const headerEl = panel.querySelector('[data-cy="application_tile_header"]');
                                const headerText = headerEl ? (headerEl.innerText || headerEl.textContent || '') : '';
                                const brandMatch = headerText.match(/for\\s+(.+)$/i);
                                const panelBrand = brandMatch ? brandMatch[1].trim() : null;
                                return {case_id: m[1], status, context, panel_brand: panelBrand};
                            }""")
                            if panel_case and panel_case.get('case_id'):
                                panel_case_id = panel_case.get('case_id')
                                panel_status = panel_case.get('status')
                                if panel_status == 'declined':
                                    print(f"[FormFiller] panel 内检测到 Declined 旧 Case ID: {panel_case_id}，不能当作 Under Review；交给 declined recovery 点击右侧 > 新建表单")
                                    self.declined_case_id = panel_case_id
                                    self.declined_case_context = panel_case.get('context')
                                    return False
                                elif panel_status == 'under_review':
                                    panel_brand = panel_case.get('panel_brand')
                                    # 品牌名校验：面板里的品牌必须与当前提交品牌一致
                                    # 防止同 tab 复用时读到上一个品牌残留的 Under Review Case ID
                                    brand_match_ok = True
                                    if panel_brand and hasattr(self, 'brand_name') and self.brand_name:
                                        brand_match_ok = (
                                            panel_brand.lower() == self.brand_name.lower()
                                            or self.brand_name.lower() in panel_brand.lower()
                                            or panel_brand.lower() in self.brand_name.lower()
                                        )
                                    if not brand_match_ok:
                                        print(f"[FormFiller] panel 内 Under Review Case ID: {panel_case_id}，但品牌不匹配（panel={panel_brand!r} vs current={self.brand_name!r}），忽略旧 panel，继续填表")
                                    else:
                                        print(f"[FormFiller] panel 内检测到 Under Review Case ID: {panel_case_id}（品牌={panel_brand!r}），无需重新申请")
                                        self.under_review_case_id = panel_case_id
                                        self.under_review_case_context = panel_case.get('context')
                                        return True
                                else:
                                    print(f"[FormFiller] panel 内检测到 Case ID: {panel_case_id}，但状态不明，不能当作 Under Review")
                        except Exception:
                            pass
                        if self._wait_for_5461_real_fields(timeout_sec=12, interval_sec=2):
                            panel_check = self._probe_5461_panel()
                            print(f"[FormFiller] 5461 真实字段已加载: {panel_check}")
                    
                    # 检查是否有 429 或 410001 错误
                    error_check = self.page.evaluate('''
                        () => {
                            const pageText = document.body.innerText || '';
                            const has410001 = pageText.includes('410001');
                            const has429 = pageText.includes('429') || pageText.includes('Too Many Requests');
                            const hasError = has410001 || has429;
                            
                            return {
                                hasError: hasError,
                                has410001: has410001,
                                has429: has429,
                                errorText: has410001 ? '410001' : (has429 ? '429' : '')
                            };
                        }
                    ''')
                    
                    if error_check.get('hasError'):
                        error_text = error_check.get('errorText')
                        print(f"[FormFiller] [警告] 检测到错误 {error_text}，尝试关闭弹窗后重试...")
                        
                        # 关闭弹窗 - 增强版，支持多种关闭方式
                        close_result = self.page.evaluate('''
                            () => {
                                let closed = false;
                                let method = '';
                                
                                // 方法1: 查找包含 "Close" 文本的按钮（Amazon 常用）
                                const allButtons = document.querySelectorAll('button, kat-button, [role="button"]');
                                for (let btn of allButtons) {
                                    const text = (btn.textContent || btn.getAttribute('label') || '').toLowerCase();
                                    if (text.includes('close') || text.includes('fermer') || text.includes('schließen')) {
                                        btn.click();
                                        closed = true;
                                        method = 'close-text-button';
                                        break;
                                    }
                                }
                                
                                // 方法2: 通过 aria-label 查找关闭按钮
                                if (!closed) {
                                    let closeBtn = document.querySelector('button[aria-label="close"]');
                                    if (closeBtn) {
                                        closeBtn.click();
                                        closed = true;
                                        method = 'aria-label';
                                    }
                                }
                                
                                // 方法3: 通过 part 属性查找
                                if (!closed) {
                                    let closeBtn = document.querySelector('button[part="panel-close-button"]');
                                    if (closeBtn) {
                                        closeBtn.click();
                                        closed = true;
                                        method = 'part-attribute';
                                    }
                                }
                                
                                // 方法4: 通过 class 查找
                                if (!closed) {
                                    let closeBtn = document.querySelector('button.close, .close-button, [class*="close"]');
                                    if (closeBtn) {
                                        closeBtn.click();
                                        closed = true;
                                        method = 'class';
                                    }
                                }
                                
                                // 方法5: 在弹窗内查找关闭按钮
                                if (!closed) {
                                    const panel = document.querySelector('kat-panel-wrapper[panel-visible="true"]');
                                    if (panel) {
                                        const panelButtons = panel.querySelectorAll('button, kat-button');
                                        for (let btn of panelButtons) {
                                            const text = (btn.textContent || btn.getAttribute('label') || '').toLowerCase();
                                            if (text.includes('close') || text.includes('×') || text.includes('x')) {
                                                btn.click();
                                                closed = true;
                                                method = 'panel-button';
                                                break;
                                            }
                                        }
                                    }
                                }
                                
                                // 方法6: 尝试按 ESC 键关闭
                                if (!closed) {
                                    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', keyCode: 27 }));
                                    closed = true;
                                    method = 'escape-key';
                                }
                                
                                return { closed: closed, method: method };
                            }
                        ''')
                        
                        if close_result.get('closed'):
                            wait_sec = random.randint(wait_min, wait_max)
                            print(f"[FormFiller] 已关闭弹窗 ({close_result.get('method')})，随机等待 {wait_sec} 秒后重试 ({attempt + 2}/{max_retries})...")
                            time.sleep(wait_sec)
                            
                            # 额外等待：检查弹窗是否真正关闭
                            check_closed = self.page.evaluate('''
                                () => {
                                    const panel = document.querySelector('kat-panel-wrapper[panel-visible="true"]');
                                    return !panel;
                                }
                            ''')
                            if not check_closed:
                                print("[FormFiller] 弹窗可能未关闭，尝试强制隐藏后再等待...")
                                try:
                                    self.page.evaluate('''
                                        () => {
                                            document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', keyCode: 27 }));
                                            const panels = document.querySelectorAll('kat-panel-wrapper');
                                            panels.forEach(p => {
                                                p.removeAttribute('panel-visible');
                                                p.setAttribute('panel-visible', 'false');
                                                p.style.display = 'none';
                                                p.style.visibility = 'hidden';
                                            });
                                            const dialogs = document.querySelectorAll('[role="dialog"], [class*="modal"], [class*="popup"]');
                                            dialogs.forEach(d => {
                                                if ((d.innerText || '').includes('410001')) {
                                                    d.style.display = 'none';
                                                    d.style.visibility = 'hidden';
                                                }
                                            });
                                        }
                                    ''')
                                except Exception:
                                    pass
                                time.sleep(random.randint(5, 12))
                            
                            continue  # 重试
                        else:
                            print(f"[FormFiller] 无法关闭弹窗，尝试刷新页面...")
                            self.page.reload(wait_until="domcontentloaded", timeout=45000)
                            time.sleep(8)  # 增加等待时间
                            continue
                    
                    # 不再强制显示 5461 panel。只有真实字段/上传控件出现，才把本次 Apply to sell 视为成功。
                    if panel_check.get('hasRealFields'):
                        print("[FormFiller] Apply to sell 已触发，5461 真实字段可用")
                        return True

                    # ★ 2026-07-25: 在 close-panel 重试之前，先用宽泛选择器扫全页面检查是否有 Declined 状态。
                    # 场景：新 UI 面板没有 data-testid="kat-panel-wrapper-QualificationWidget"，
                    # 导致 _probe_5461_panel 返回 found=False，跳过了上面的 Declined 检测块，
                    # 然后盲目进入 close-panel 循环，永远无法触发 handle_declined_case_application。
                    try:
                        declined_scan = self.page.evaluate("""() => {
                            // 扫所有 kat-panel-wrapper，不限 data-testid
                            const panels = document.querySelectorAll('kat-panel-wrapper');
                            for (const panel of panels) {
                                const txt = (panel.innerText || panel.textContent || '');
                                if (!/declined/i.test(txt)) continue;
                                // 找 Case ID
                                const m = txt.match(/(?:Case\\s*ID\\s*[-:#]?\\s*)?(\\d{10,12})/i);
                                const case_id = m ? m[1] : null;
                                // 确认有 Application to create new ASINs 标题，排除误判
                                const hasAppTitle = /Application to create new ASINs/i.test(txt) ||
                                                    /Apply to sell/i.test(txt);
                                if (hasAppTitle || case_id) {
                                    return {found: true, case_id: case_id, snippet: txt.slice(0, 200)};
                                }
                            }
                            // 也扫整个 body（面板可能在 shadow DOM 外层）
                            const body = document.body.innerText || '';
                            if (/declined/i.test(body) && /Application to create new ASINs/i.test(body)) {
                                const m = body.match(/(?:Case\\s*ID\\s*[-:#]?\\s*)?(\\d{10,12})/i);
                                return {found: true, case_id: m ? m[1] : null, snippet: body.slice(0, 200)};
                            }
                            return {found: false};
                        }""")
                        if declined_scan and declined_scan.get('found'):
                            declined_id = declined_scan.get('case_id')
                            print(f"[FormFiller] ⚠️ 宽泛扫描检测到 Declined 状态（Case ID: {declined_id}），"
                                  f"交给 declined recovery 点击右侧 > 新建表单，停止 close-panel 重试")
                            if declined_id:
                                self.declined_case_id = declined_id
                            return False
                    except Exception as _de:
                        print(f"[FormFiller] Declined 宽泛扫描异常（忽略）: {_de}")

                    # 429/410001 常见状态：panel shell 出现但字段没加载，或点击后 panel 不出现。
                    # 在这里补回受控恢复链路：关闭/隐藏 panel -> 随机等待 -> 重新真实点击 Apply to sell。
                    if attempt < max_retries - 1:
                        closed = self._close_5461_panel()
                        wait_sec = random.randint(wait_min, wait_max)
                        print(f"[FormFiller] Apply to sell 后未加载真实字段；关闭 panel {closed}，等待 {wait_sec}s 后重试 ({attempt + 2}/{max_retries})...")
                        time.sleep(wait_sec)
                        continue

                    print("[FormFiller] Apply to sell 多次重试后仍未加载真实 5461 字段/上传控件")
                    return False
                else:
                    print(f"[FormFiller] 未找到 Apply to sell 按钮: {result.get('error')}")
                    return False
                
            except Exception as e:
                print(f"[FormFiller] 点击 Apply to sell 失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(3)
                    continue
                return False
        
        print(f"[FormFiller] 点击 Apply to sell 失败，已达到最大重试次数")
        return False
