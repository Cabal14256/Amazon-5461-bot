import random
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.sync_api import Page, sync_playwright

from .amazon_error_handler import ensure_page_ready
from .application_type_selection import classify_application_type_text
from .capture.dom_contract import capture_dom_shadow_contract
from .email_resolver import clean_email, get_autofill_email_from_page
from .evidence import EVIDENCE_NODES, build_evidence_dir, capture_evidence_safe, write_text
from .human_interaction import human_click, human_type


_APPLICATION_REQUIRED_RE = re.compile(r"applications?\s*required", re.IGNORECASE)
_RESTRICTION_COUNT_RE = re.compile(r"\b\d+\s*restrictions?\b", re.IGNORECASE)


def has_application_required_entry_text(text: str) -> bool:
    """Recognize the new Add Product approval entry for any item count.

    Amazon renders both singular and plural variants (for example
    ``1 application required`` and ``2 applications required``).  Some Katal
    snapshots concatenate adjacent nodes, so whitespace around the count is
    intentionally optional.
    """
    normalized = " ".join(str(text or "").split())
    if _APPLICATION_REQUIRED_RE.search(normalized):
        return True
    return bool(_RESTRICTION_COUNT_RE.search(normalized) and re.search(r"\bview\b", normalized, re.I))


def _async_screenshot(page, path: str, full_page: bool = False, timeout: int = 10000) -> None:
    """
    非关键截图异步化辅助函数。
    在后台线程拍照，不阻塞主流程。page 对象由调用方保证在截图前不被关闭。
    失败时静默忽略（与原有 try/except pass 保持一致）。
    """
    def _do():
        try:
            page.screenshot(path=path, full_page=full_page, timeout=timeout)
        except Exception:
            pass
    threading.Thread(target=_do, daemon=True).start()


def close_5461_panel(page: Page) -> dict:
    """Best-effort close/hide the Amazon 5461 right-side panel."""
    # 关闭按钮在 kat-panel 的 Shadow DOM 里：
    #   kat-panel > #shadow-root > div.header > button[part="panel-close-button"]
    # 必须通过 shadowRoot 访问，普通 querySelectorAll 和 Playwright locator 全局搜索均无法可靠触发

    # 方法1: 通过 shadowRoot 直接 click（最可靠）
    try:
        result = page.evaluate("""() => {
            // kat-panel 可能在 kat-panel-wrapper 里，也可能直接在 DOM 里
            const panels = document.querySelectorAll('kat-panel');
            for (const panel of panels) {
                const sr = panel.shadowRoot;
                if (!sr) continue;
                const btn = sr.querySelector('button[part="panel-close-button"]')
                           || sr.querySelector('button[aria-label="close"]')
                           || sr.querySelector('button[aria-label="Close"]')
                           || sr.querySelector('button.close');
                if (btn) {
                    btn.click();
                    btn.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
                    btn.dispatchEvent(new MouseEvent('mouseup',   {bubbles: true}));
                    btn.dispatchEvent(new MouseEvent('click',     {bubbles: true}));
                    return {closed: 'shadow_click', tag: panel.tagName};
                }
            }
            return null;
        }""")
        if result:
            return result
    except Exception:
        pass

    # 方法2: kat-panel-wrapper 的 shadowRoot（有些版本 wrapper 包着 panel）
    try:
        result = page.evaluate("""() => {
            const wrappers = document.querySelectorAll('kat-panel-wrapper');
            for (const w of wrappers) {
                const sr = w.shadowRoot;
                if (!sr) continue;
                const btn = sr.querySelector('button[part="panel-close-button"]')
                           || sr.querySelector('button[aria-label="close"]')
                           || sr.querySelector('button.close');
                if (btn) {
                    btn.click();
                    btn.dispatchEvent(new MouseEvent('click', {bubbles: true}));
                    return {closed: 'wrapper_shadow_click'};
                }
                // wrapper shadow 里可能嵌了 kat-panel，再深一层
                const innerPanel = sr.querySelector('kat-panel');
                if (innerPanel && innerPanel.shadowRoot) {
                    const btn2 = innerPanel.shadowRoot.querySelector('button[part="panel-close-button"]')
                                || innerPanel.shadowRoot.querySelector('button.close');
                    if (btn2) {
                        btn2.click();
                        btn2.dispatchEvent(new MouseEvent('click', {bubbles: true}));
                        return {closed: 'nested_shadow_click'};
                    }
                }
            }
            return null;
        }""")
        if result:
            return result
    except Exception:
        pass

    # 方法3: Playwright locator — kat-panel 内部的 button（chain locator 穿透单层 shadow）
    try:
        btn = page.locator('kat-panel').locator('button[aria-label="close"]').first
        if btn.count() > 0:
            btn.click(timeout=3000)
            return {"closed": "clicked", "selector": "kat-panel >> button[aria-label=close]"}
    except Exception:
        pass

    # 方法4: Escape 键 + JS 强制隐藏（最后兜底）
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    try:
        return page.evaluate("""() => {
            for (const p of document.querySelectorAll('kat-panel-wrapper, kat-panel')) {
                try {
                    p.setAttribute('panel-visible', 'false');
                    p.removeAttribute('panelVisible');
                    p.style.display = 'none';
                    p.style.visibility = 'hidden';
                } catch(e) {}
            }
            return {closed: 'force'};
        }""")
    except Exception as e:
        return {"closed": "error", "error": str(e)}

def has_5461_form_fields(page: Page) -> bool:
    """Return True only when the 5461 panel has real fillable fields/upload input, not just an empty shell."""
    try:
        return bool(page.evaluate("""() => {
            const selectors = [
                // Listing Approval plain HTML inputs (name=question-cat_auth_mo_*)
                'input[name="question-cat_auth_mo_question_string_id_product_title"]',
                'input[name="question-cat_auth_mo_question_string_id_manufacturer"]',
                'input[name="question-cat_auth_mo_question_string_id_product_description"]',
                // Standard cat_auth_mo kat-input fields (Shadow DOM)
                'kat-input[id*="product_title"]',
                'kat-input#question-cat_auth_mo_question_string_id_product_title',
                'kat-input#question-cat_auth_mo_question_string_id_manufacturer',
                'kat-input#question-cat_auth_mo_question_string_id_product_description',
                'kat-input#question-cat_auth_mo_question_string_id_contact_info_email',
                'kat-input#contact_info_email_input',
                'input[id*="document_upload"][id*="document_input"]',
                // Listing Approval form (plain HTML inputs/textareas, e.g. MX market)
                'input[data-cy*="product-title"], textarea[data-cy*="product-title"]',
                'input[data-cy*="manufacturer"], textarea[data-cy*="manufacturer"]',
                'input[data-cy*="product-description"], textarea[data-cy*="product-description"]',
                'input[placeholder*="Product title"], textarea[placeholder*="Product title"]',
                'input[placeholder*="Manufacturer"], textarea[placeholder*="Manufacturer"]',
                'input[name*="product_title"], textarea[name*="product_title"]',
                'input[name*="manufacturer"], textarea[name*="manufacturer"]',
            ];
            for (const sel of selectors) {
                if (document.querySelector(sel)) return true;
            }
            // Also check by panel inner text: if panel contains "Product title" or "Manufacturer"
            // label text, the form is loaded even if selectors don't match exactly.
            const panel = document.querySelector('kat-panel-wrapper[panel-visible="true"]');
            if (panel) {
                const txt = (panel.innerText || panel.textContent || '').toLowerCase();
                if ((txt.includes('product title') || txt.includes('manufacturer')) &&
                    (txt.includes('submit') || txt.includes('upload') || txt.includes('select files'))) {
                    // confirm at least one real input/textarea exists inside the panel subtree
                    // (including shadow roots via querySelectorAll on the panel's children)
                    const inputs = panel.querySelectorAll('input, textarea, kat-input');
                    if (inputs.length > 0) return true;
                }
            }
            return false;
        }"""))
    except Exception:
        return False


def wait_for_5461_form_fields(page: Page, timeout_sec: int = 24, interval_sec: int = 2) -> bool:
    deadline = time.time() + timeout_sec
    waited = 0
    while time.time() < deadline:
        time.sleep(interval_sec)
        waited += interval_sec
        if has_5461_form_fields(page):
            print(f"[5461Panel] ✅ 表单字段已出现（等待 {waited}s）")
            return True
    return False


def recover_half_loaded_5461_panel(page: Page, filler, brand_name: str, max_attempts: int = 3, reason: str = "") -> bool:
    """
    Recover from the common Amazon state where kat-panel-wrapper exists but its JS/Shadow DOM fields/upload input did not load.
    Close panel -> randomized wait -> Apply to sell -> wait for real fields. Returns True when real fields are ready.
    """
    if has_5461_form_fields(page):
        return True
    print(f"[5461Panel] ⚠️ 表单半加载/字段缺失，开始 close-panel 重试: {reason or brand_name}")
    for attempt in range(1, max_attempts + 1):
        closed = close_5461_panel(page)
        # 指数退避：首次 10–20s，每次翻倍，上限 55s
        base_lo = min(10 + (attempt - 1) * 10, 35)
        base_hi = min(20 + (attempt - 1) * 15, 55)
        wait_sec = random.randint(base_lo, base_hi)
        print(f"[5461Panel] 尝试 {attempt}/{max_attempts}: 已关闭/隐藏 panel {closed}，等待 {wait_sec}s 后重新 Apply to sell")
        time.sleep(wait_sec)
        try:
            opened = filler.click_apply_to_sell() if filler else False
        except Exception as e:
            print(f"[5461Panel] Apply to sell 异常: {e}")
            opened = False
        # ★ 优先检查：click_apply_to_sell 内部检测到 Under Review Case ID 时直接返回成功
        if filler and getattr(filler, 'under_review_case_id', None):
            under_review_id = filler.under_review_case_id
            print(f"[5461Panel] ✅ panel 内已检测到 Under Review Case ID: {under_review_id}，立即退出重试循环")
            return True
        if not opened:
            print(f"[5461Panel] 尝试 {attempt}/{max_attempts}: Apply to sell 未成功触发")
            continue
        # 等待 panel 出现或页面变化，最多 6s 早退
        try:
            page.wait_for_function(
                """() => {
                    const p = document.querySelector('kat-panel-wrapper[panel-visible="true"]');
                    return p !== null;
                }""",
                timeout=6000
            )
        except Exception:
            pass
        # ★ 再次检查：wait_for_function 后 filler 可能已在等待期间更新了 under_review_case_id
        if filler and getattr(filler, 'under_review_case_id', None):
            under_review_id = filler.under_review_case_id
            print(f"[5461Panel] ✅ panel 等待期间检测到 Under Review Case ID: {under_review_id}，立即退出重试循环")
            return True
        state = check_page_state(page)
        print(f"[5461Panel] 尝试 {attempt}/{max_attempts}: 重开后状态 {state.get('page_type')}")
        if wait_for_5461_form_fields(page, timeout_sec=24, interval_sec=2):
            print(f"[5461Panel] ✅ close-panel 重试恢复成功 ({attempt}/{max_attempts})")
            return True
        print(f"[5461Panel] 尝试 {attempt}/{max_attempts}: 字段仍未加载")
    print(f"[5461Panel] ❌ close-panel 重试 {max_attempts} 次后仍半加载")
    return False


def resolve_connect_brand_loading_state(page, filler, brand_name: str, state: dict) -> tuple[dict, bool]:
    """Wait/recover when stale brand text masks a newly opened 5461 panel."""
    fields_ready = state.get('page_type') == '5461_FORM_OPEN'
    if state.get('page_type') == 'BRAND_SELECTION' and not fields_ready:
        fields_ready = wait_for_5461_form_fields(page, timeout_sec=24, interval_sec=2)
        if not fields_ready:
            fields_ready = recover_half_loaded_5461_panel(
                page, filler, brand_name, max_attempts=3,
                reason="Connect brand 后停留在加载骨架"
            )
        if fields_ready:
            state['page_type'] = '5461_FORM_OPEN'
            state['has_5461_form'] = True
    return state, fields_ready


def extract_item_name_from_statement(statement_text: str) -> str:
    """从品牌声明文案中提取 Item name"""
    for line in statement_text.split('\n'):
        if line.startswith('Item name：') or line.startswith('Item name:'):
            return line.split('：', 1)[1].strip() if '：' in line else line.split(':', 1)[1].strip()
    return ""


def fill_katal_textarea(page: Page, selector: str, text: str) -> bool:
    """填写 Katal textarea 组件"""
    try:
        result = page.evaluate(f'''
            () => {{
                const el = document.querySelector('{selector}');
                if (!el) return 'not_found';
                
                const shadow = el.shadowRoot;
                if (shadow) {{
                    const textarea = shadow.querySelector('textarea');
                    if (textarea) {{
                        textarea.focus();
                        textarea.value = '';
                        textarea.value = `{text}`;
                        textarea.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        textarea.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        textarea.dispatchEvent(new Event('blur', {{ bubbles: true }}));
                        textarea.dispatchEvent(new Event('focusout', {{ bubbles: true }}));
                    }}
                }}
                
                el.value = `{text}`;
                el.setAttribute('value', `{text}`);
                el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                el.dispatchEvent(new Event('blur', {{ bubbles: true }}));
                el.dispatchEvent(new Event('focusout', {{ bubbles: true }}));
                return 'shadow_dom';
            }}
        ''')
        time.sleep(0.8)
        return result != 'not_found'
    except Exception as e:
        print(f"[fill_katal_textarea] 失败: {e}")
        return False


def fill_katal_input(page: Page, selector: str, text: str) -> bool:
    """填写 Katal input/textarea 组件。

    Katal/Shadow DOM 长文本字段（尤其 Product Description）用 keyboard/human_type
    逐字输入时偶发丢尾。这里必须回读完整值；只要不完全一致，就改用 JS
    直接写入 inner input/textarea + host value，并触发 input/change/blur/focusout。
    """

    def _read_value() -> str:
        try:
            return page.evaluate("""(sel) => {
                const el = document.querySelector(sel);
                if (!el) return '';
                const tag = (el.tagName || '').toLowerCase();
                const inner = el.shadowRoot && el.shadowRoot.querySelector(tag === 'kat-textarea' ? 'textarea' : 'input,textarea');
                const iv = inner ? (inner.value || inner.getAttribute('value') || '') : '';
                return iv || el.value || el.getAttribute('value') || '';
            }""", selector)
        except Exception:
            return ""

    def _js_set_value() -> bool:
        try:
            result = page.evaluate("""({sel, value}) => {
                const el = document.querySelector(sel);
                if (!el) return {ok: false, reason: 'not_found'};
                const tag = (el.tagName || '').toLowerCase();
                const fire = (node) => {
                    node.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
                    node.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
                    node.dispatchEvent(new Event('blur', { bubbles: true, composed: true }));
                    node.dispatchEvent(new Event('focusout', { bubbles: true, composed: true }));
                };
                const inner = el.shadowRoot && el.shadowRoot.querySelector(tag === 'kat-textarea' ? 'textarea' : 'input,textarea');
                if (inner) {
                    inner.focus();
                    inner.value = '';
                    inner.value = value;
                    inner.setAttribute('value', value);
                    fire(inner);
                }
                el.value = value;
                el.setAttribute('value', value);
                fire(el);
                return {ok: true, tag, inner: !!inner, actual: inner ? inner.value : (el.value || el.getAttribute('value') || '')};
            }""", {"sel": selector, "value": text})
            time.sleep(0.8)
            actual = _read_value()
            ok = bool(result and result.get("ok") and actual == text)
            if not ok:
                print(f"    [fill_katal_input] JS 写入校验失败: expected={len(text)} actual={len(actual)} result={result}")
            return ok
        except Exception as e:
            print(f"    [fill_katal_input] JS 写入异常: {e}")
            return False

    try:
        locator = page.locator(selector).first
        if locator.count() > 0:
            try:
                if human_type(page, locator, text, label=selector, timeout=8000, clear=True):
                    actual = _read_value()
                    if actual == text:
                        print(f"    [fill_katal_input] Playwright真实输入: {text[:50]}...")
                        return True
                    print(f"    [fill_katal_input] 真实输入不完整，改用 JS: expected={len(text)} actual={len(actual)}")
            except Exception as e:
                print(f"    [fill_katal_input] 真实输入失败，改用 JS 兜底: {e}")

        # 获取元素类型（检测是否为自定义元素）
        tag_name = page.evaluate(f'''
            () => {{
                const el = document.querySelector('{selector}');
                return el ? el.tagName.toLowerCase() : 'not_found';
            }}
        ''')
        
        if tag_name == 'not_found':
            print(f"    [fill_katal_input] 元素未找到: {selector}")
            return False
        
        # kat-input / kat-textarea 是自定义元素，必须使用 Shadow DOM
        is_custom_element = tag_name in ('kat-input', 'kat-textarea')
        
        if is_custom_element:
            if _js_set_value():
                print(f"    [fill_katal_input] Shadow DOM 完整写入: {text[:50]}...")
                return True
            return False
        
        else:
            # 标准 input/textarea - 使用真实输入
            locator = page.locator(selector).first
            if locator.count() > 0:
                if human_type(page, locator, text, label=selector, timeout=8000, clear=True):
                    actual = _read_value()
                    if actual == text:
                        print(f"    [fill_katal_input] Playwright真实输入: {text[:50]}...")
                        return True
                    print(f"    [fill_katal_input] 标准输入不完整，改用 JS: expected={len(text)} actual={len(actual)}")
                    return _js_set_value()
        
        return False
    except Exception as e:
        print(f"[fill_katal_input] 失败: {e}")
        return False


def click_katal_button(page: Page, selector: str) -> bool:
    """点击 Katal button 组件 - Playwright真实点击优先，Shadow DOM JS 兜底"""
    try:
        loc = page.locator(selector).first
        if loc.count() > 0:
            try:
                if human_click(loc, label=selector, timeout=8000, force=False):
                    return True
            except Exception as e:
                print(f"[click_katal_button] 真实点击失败，改用 JS 兜底: {e}")

        result = page.evaluate(f'''
            () => {{
                const btn = document.querySelector('{selector}');
                if (!btn) return 'not_found';
                
                const shadow = btn.shadowRoot;
                if (shadow) {{
                    const innerBtn = shadow.querySelector('button');
                    if (innerBtn) {{
                        innerBtn.click();
                        return 'shadow_click';
                    }}
                }}
                
                btn.click();
                return 'direct_click';
            }}
        ''')
        time.sleep(1)
        return result != 'not_found'
    except Exception as e:
        print(f"[click_katal_button] 失败: {e}")
        return False


def upload_files_to_katal(page: Page, selector: str, files: list) -> bool:
    """上传文件到 Katal 文件上传组件"""
    try:
        # 查找文件输入元素（可能在 Shadow DOM 中）
        for file_path in files:
            if not Path(file_path).exists():
                print(f"[upload_files_to_katal] 文件不存在: {file_path}")
                return False
        
        # 尝试通过标准 input[type="file"] 上传
        file_inputs = page.locator('input[type="file"]').all()
        if file_inputs:
            file_inputs[0].set_input_files(files)
            time.sleep(2)
            return True
        
        return False
    except Exception as e:
        print(f"[upload_files_to_katal] 失败: {e}")
        return False


def submit_5461(
    cdp_url: str,
    account_id: str,
    marketplace: str,
    brand_name: str,
    flow_url: str,
    selectors_cfg: dict,
    statement_text: str,
    upload_files: list,
    evidence_root: str,
    require_human_confirm: bool = True,
    keep_browser_open: bool = False,
):
    """
    提交 5461 表单
    
    Args:
        cdp_url: Chrome DevTools Protocol URL
        account_id: 账户 ID
        marketplace: 市场（US/EU 等）
        brand_name: 品牌名称
        flow_url: 流程入口 URL
        selectors_cfg: 选择器配置
        statement_text: 声明文本
        upload_files: 要上传的文件列表
        evidence_root: 证据保存根目录
        require_human_confirm: 是否需要人工确认后再提交
        keep_browser_open: 完成后是否保留已连接的浏览器
    
    Returns:
        结果字典
    """
    sel = selectors_cfg.get("selectors", {})
    evidence_dir = build_evidence_dir(evidence_root, account_id, brand_name, "submit")

    result = {
        "submit_result": "failed",
        "case_id": None,
        "submission_started_at": datetime.now().isoformat(),
        "note": "",
        "evidence_files": [],
        "steps": []
    }

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        
        # 复用已有页面，避免创建新标签页
        existing_pages = context.pages
        if existing_pages:
            page = existing_pages[-1]
            # 关闭其他多余的页面
            for old_page in existing_pages[:-1]:
                try:
                    old_page.evaluate('() => { window.close(); }')
                except:
                    pass
        else:
            page = context.new_page()
        
        page.set_default_timeout(30000)  # 增加超时到 30 秒
        
        try:
            # 步骤 1: 导航到页面
            print("[submit_5461] 导航到 5461 表单页面...")
            page.goto(flow_url, wait_until="domcontentloaded", timeout=45000)
            # 等待品牌输入框出现，最多 5s 早退
            try:
                page.wait_for_selector(
                    'kat-input[data-cy="listing-approval-brand-name-input"]',
                    timeout=5000
                )
            except Exception:
                pass
            result["steps"].append({"step": 1, "action": "navigate", "status": "ok"})
            
            # 步骤 2: 填写品牌名称
            print("[submit_5461] 填写品牌名称...")
            brand_selector = sel.get("brand_input", 'kat-input[data-cy="listing-approval-brand-name-input"]')
            if fill_katal_input(page, brand_selector, brand_name):
                result["steps"].append({"step": 2, "action": "fill_brand", "status": "ok"})
            else:
                result["steps"].append({"step": 2, "action": "fill_brand", "status": "failed"})
            
            # 步骤 3: 填写声明文本
            print("[submit_5461] 填写声明文本...")
            statement_selector = sel.get("statement_textarea", 'kat-textarea[data-cy="listing-approval-brand-statement-textarea"]')
            if fill_katal_textarea(page, statement_selector, statement_text):
                result["steps"].append({"step": 3, "action": "fill_statement", "status": "ok"})
            else:
                result["steps"].append({"step": 3, "action": "fill_statement", "status": "failed"})
            
            # 步骤 4: 上传文件
            if upload_files:
                print(f"[submit_5461] 上传文件: {upload_files}")
                upload_selector = sel.get("upload_input", 'input[type="file"]')
                if upload_files_to_katal(page, upload_selector, upload_files):
                    result["steps"].append({"step": 4, "action": "upload_files", "status": "ok", "files": upload_files})
                else:
                    result["steps"].append({"step": 4, "action": "upload_files", "status": "failed", "files": upload_files})
            else:
                result["steps"].append({"step": 4, "action": "upload_files", "status": "skipped"})
            
            # 保存提交前截图
            before_submit = evidence_dir / "before_submit.png"
            page.screenshot(path=str(before_submit), full_page=True, timeout=10000)
            result["evidence_files"].append(str(before_submit))
            
            # 步骤 5: 检测验证码/2FA
            print("[submit_5461] 检测验证码/2FA...")
            captcha_detected = False
            for marker in sel.get("captcha_markers", []):
                if page.locator(marker).count() > 0:
                    captcha_detected = True
                    result["note"] = "检测到验证码/2FA，已暂停"
                    txt = evidence_dir / "captcha_detected.txt"
                    write_text(txt, result["note"])
                    result["evidence_files"].append(str(txt))
                    result["steps"].append({"step": 5, "action": "check_captcha", "status": "detected"})
                    return result
            
            if not captcha_detected:
                result["steps"].append({"step": 5, "action": "check_captcha", "status": "ok"})
            
            # 步骤 6: 人工确认
            if require_human_confirm:
                import sys
                if sys.stdin.isatty():
                    print("[submit_5461] 等待人工确认...")
                    input("请检查页面后按 Enter 提交（取消请 Ctrl+C）...")
                else:
                    print("[submit_5461] 非交互环境，跳过人工确认...")
            
            # 步骤 7: 点击提交按钮
            print("[submit_5461] 点击提交按钮...")
            submit_selector = sel.get("submit_button", 'kat-button[data-cy="listing-approval-submit-button"]')
            if click_katal_button(page, submit_selector):
                result["steps"].append({"step": 7, "action": "click_submit", "status": "ok"})
            else:
                result["steps"].append({"step": 7, "action": "click_submit", "status": "failed"})
            
            # 等待提交结果（Case ID 或成功标记出现），最多 5s 早退
            try:
                page.wait_for_function(
                    """() => {
                        const t = document.body ? document.body.innerText : '';
                        return /Case\\s*ID|case[-_]?id|\\d{11,}/i.test(t) ||
                               t.includes('Under review') || t.includes('submitted');
                    }""",
                    timeout=5000
                )
            except Exception:
                pass
            
            # 保存提交后截图
            after_submit = evidence_dir / "after_submit.png"
            page.screenshot(path=str(after_submit), full_page=True, timeout=10000)
            result["evidence_files"].append(str(after_submit))
            
            # 步骤 8: 检测成功标记
            print("[submit_5461] 检测提交结果...")
            success_hit = any(page.locator(marker).count() > 0 for marker in sel.get("success_markers", []))
            
            # 也检测常见的成功文本
            page_text = page.inner_text('body')
            if "success" in page_text.lower() or "submitted" in page_text.lower() or "received" in page_text.lower():
                success_hit = True
            
            if success_hit:
                result["submit_result"] = "success"
                result["note"] = "检测到提交成功标记"
                result["steps"].append({"step": 8, "action": "check_result", "status": "success"})
            else:
                result["submit_result"] = "partial"
                result["note"] = "已点击提交，但未识别到明确成功标记（请人工核对截图）"
                result["steps"].append({"step": 8, "action": "check_result", "status": "uncertain"})
            
            txt = evidence_dir / "submit_result.txt"
            write_text(txt, result["note"])
            result["evidence_files"].append(str(txt))
            
        except Exception as e:
            result["submit_result"] = "error"
            result["note"] = f"执行出错: {str(e)}"
            result["steps"].append({"step": "error", "error": str(e)})
            
            # 保存错误截图
            try:
                error_shot = evidence_dir / "error.png"
                page.screenshot(path=str(error_shot), full_page=True, timeout=10000)
                result["evidence_files"].append(str(error_shot))
            except:
                pass
        
        finally:
            if not keep_browser_open:
                try:
                    page.close()
                except:
                    pass
                try:
                    browser.close()
                except:
                    pass
            else:
                print("[清理] 批量模式：保持浏览器开启")
    
    return result


def check_page_state(page) -> dict:
    """
    检查当前页面状态
    根据 _scratch/submit_5461_v2.py 和 _scratch/run_541_mocodi_fixed.py 的经验
    """
    current_url = page.url
    
    # 使用 JS 获取页面文本，避免 inner_text 超时
    # 注意：Amazon 使用 Shadow DOM，需要用递归方式获取文本
    try:
        page_text = page.evaluate('''() => {
            function getAllText(element) {
                let text = '';
                if (element.shadowRoot) {
                    for (const child of element.shadowRoot.children) {
                        text += getAllText(child);
                    }
                }
                for (const child of element.children) {
                    text += getAllText(child);
                }
                if (element.textContent) {
                    text += element.textContent + ' ';
                }
                return text;
            }
            return getAllText(document.body);
        }''') or ''
    except Exception as e:
        print(f"[DEBUG] 获取页面文本失败: {e}")
        page_text = ''
    
    state = {
        'url': current_url,
        'page_type': 'UNKNOWN',
        'has_permission': False,
        'needs_auth': False,
        'has_5461_form': False,
        'brand_blocked': False,
    }

    def classify_visible_panel_without_fields(panel_text=None):
        application_type = classify_application_type_text(panel_text if panel_text is not None else page_text)
        state['application_type'] = application_type
        application_status = application_type.get('status')
        if application_status == 'create_new_asins_available':
            state['page_type'] = 'APPLICATION_TYPE_SELECTION'
            state['needs_auth'] = True
            return state
        if application_status == 'sell_products_only':
            state['page_type'] = 'APPLICATION_SELL_ONLY'
            state['needs_auth'] = True
            state['requires_human_review'] = True
            return state
        if application_status == 'unknown_application_options':
            state['page_type'] = 'APPLICATION_TYPE_UNKNOWN'
            state['needs_auth'] = True
            state['requires_human_review'] = True
            return state
        state['page_type'] = '5461_PANEL_SHELL'
        state['has_5461_form'] = False
        state['panel_shell'] = True
        return state
    
    # 检查自动批准弹窗（英国站点特有）
    auto_approval_modal = page.evaluate('''() => {
        const modal = document.querySelector('kat-modal[data-cy="seller-qualification-auto-approval-modal"]');
        if (modal) {
            const style = window.getComputedStyle(modal);
            return {
                found: true,
                visible: style.display !== 'none' && style.visibility !== 'hidden',
                text: modal.textContent.slice(0, 200)
            };
        }
        return { found: false };
    }''')
    if auto_approval_modal.get('found') and auto_approval_modal.get('visible'):
        print("[check_page_state] 检测到自动批准弹窗，点击 Acknowledge...")
        page.evaluate('''() => {
            const btn = document.querySelector('kat-button[data-cy="seller-qualification-auto-approval-modal-button"]');
            if (btn) btn.click();
        }''')
        # 等待弹窗消失，最多 3s 早退
        try:
            page.wait_for_function(
                """() => {
                    const btn = document.querySelector(
                        'kat-button[data-cy="seller-qualification-auto-approval-modal-button"]'
                    );
                    return !btn || btn.offsetParent === null;
                }""",
                timeout=3000
            )
        except Exception:
            pass
        # 重新获取页面文本
        try:
            page_text = page.evaluate('''() => {
                function getAllText(element) {
                    let text = '';
                    if (element.shadowRoot) {
                        for (const child of element.shadowRoot.children) {
                            text += getAllText(child);
                        }
                    }
                    for (const child of element.children) {
                        text += getAllText(child);
                    }
                    if (element.textContent) {
                        text += element.textContent + ' ';
                    }
                    return text;
                }
                return getAllText(document.body);
            }''') or ''
        except:
            pass
    
    # 检查品牌是否被封锁（Amazon 不接受申请）
    if 'Unable to list' in page_text or 'not accepting applications for approval' in page_text:
        state['page_type'] = 'BRAND_BLOCKED'
        state['brand_blocked'] = True
        return state
    
    # 预计算小写文本（供多处使用）
    page_text_lower = page_text.lower()
    
    # 检查是否已进入 Description 页面
    # 注意：Description 页面**不代表**已有权限！Amazon 可能允许进入 Description，
    # 但在后续提交 listing 时才拦截。因此这里只做保守检测，不假设权限状态。
    if '/description' in current_url.lower():
        # 检查 Description 页面上是否有明确的授权提示
        has_apply_to_sell = 'apply to sell' in page_text_lower
        has_brand_auth = 'brand authorization' in page_text_lower or 'authorization required' in page_text_lower
        has_needs_approval = 'you need approval' in page_text_lower or 'listing approval' in page_text_lower
        
        if has_apply_to_sell or has_brand_auth or has_needs_approval:
            state['page_type'] = 'DESCRIPTION_NEEDS_AUTH'
            state['needs_auth'] = True
            print("[DEBUG] Description 页面检测到需要授权")
            return state
        
        # 没有明确授权提示时，**不假设已有权限**。
        # 返回中性状态，让外层流程继续尝试触发授权检查。
        state['page_type'] = 'DESCRIPTION'
        # has_permission 保持 False，needs_auth 也保持 False
        # 外层代码会继续检查是否需要授权，或尝试其他方式
        print("[DEBUG] Description 页面，无明确授权提示，继续检查...")
        return state
    
    # 检查 5461 表单弹窗（增强版）- 优先检查（即使在新UI页面也可能有弹窗）
    try:
        # 方法1: 通过 JavaScript 检查（更可靠，优先使用）
        panel_check = page.evaluate('''
            () => {
                let panel = document.querySelector('kat-panel-wrapper[data-testid="kat-panel-wrapper-QualificationWidget"]');
                if (!panel) {
                    for (const wrapper of document.querySelectorAll('kat-panel-wrapper')) {
                        const text = (wrapper.innerText || wrapper.textContent || '').toLowerCase();
                        const style = window.getComputedStyle(wrapper);
                        const visible = wrapper.getAttribute('panel-visible') === 'true' ||
                            (style.display !== 'none' && style.visibility !== 'hidden' &&
                             wrapper.offsetWidth > 0 && wrapper.offsetHeight > 0);
                        if (visible && (text.includes('apply to sell') || text.includes('application to'))) {
                            panel = wrapper;
                            break;
                        }
                    }
                }
                if (panel) {
                    return {
                        found: true,
                        visible: panel.getAttribute('panel-visible') === 'true',
                        display: panel.style.display !== 'none',
                        width: panel.offsetWidth,
                        height: panel.offsetHeight,
                        text: panel.innerText || panel.textContent || ''
                    };
                }
                return { found: false };
            }
        ''')
        print(f"[DEBUG] 5461 弹窗 JS 检查: {panel_check}")
        
        if panel_check.get('found') and panel_check.get('visible'):
            if has_5461_form_fields(page):
                state['page_type'] = '5461_FORM_OPEN'
                state['has_5461_form'] = True
                return state
            classified = classify_visible_panel_without_fields(panel_check.get('text'))
            print(f"[DEBUG] 5461 panel 无真实字段，分类为 {classified['page_type']}")
            return classified
        
        # 方法2: 通过 Playwright locator 检查
        panel = page.locator('kat-panel-wrapper[data-testid="kat-panel-wrapper-QualificationWidget"]').first
        if panel.count() > 0:
            panel_visible = panel.get_attribute('panel-visible')
            if panel_visible == 'true':
                if has_5461_form_fields(page):
                    state['page_type'] = '5461_FORM_OPEN'
                    state['has_5461_form'] = True
                    return state
                try:
                    locator_panel_text = panel.text_content(timeout=2000) or ''
                except Exception:
                    locator_panel_text = page_text
                classified = classify_visible_panel_without_fields(locator_panel_text)
                print(f"[DEBUG] locator 检测到无字段 panel，分类为 {classified['page_type']}")
                return classified
    except Exception as e:
        print(f"[DEBUG] 检查 5461 弹窗失败: {e}")
        pass

    # Connect Brand can open a plain kat-panel while the underlying Product
    # Identity page keeps its "Select Brand" warning visible. Real form fields
    # are authoritative and must win over stale brand-selection page text.
    if has_5461_form_fields(page):
        state['page_type'] = '5461_FORM_OPEN'
        state['has_5461_form'] = True
        print("[DEBUG] 检测到真实 5461 字段，优先于残留的品牌选择提示")
        return state
    
    # 检查是否是品牌选择页面（多种方式）
    has_select_brand_text = 'select brand' in page_text_lower or 'clarify which brand' in page_text_lower or 'choose a brand' in page_text_lower
    has_brand_warning = 'could not associate' in page_text_lower or 'brand registry' in page_text_lower
    
    # 调试输出
    print(f"[DEBUG] 页面文本长度: {len(page_text)}")
    print(f"[DEBUG] has_select_brand_text: {has_select_brand_text}, has_brand_warning: {has_brand_warning}")
    if len(page_text) > 0:
        print(f"[DEBUG] 页面文本前200字符: {page_text[:200]}")
    
    # 关键修复：只有当页面上真的有品牌选项时才判定为品牌选择页面
    # 避免把普通的 "Apply to sell" 提示误判为品牌选择
    # 品牌选择弹窗的特征：有 "select brand" 文本 + 页面上有多个品牌描述
    if has_select_brand_text or has_brand_warning:
        # 检查页面文本中是否有多个品牌选项的特征（品牌名 + 描述）
        # 真正的品牌选择弹窗会有类似 "DEMO_JADEA brand offering..." 或 "DEMO_JADEManufacturer of..." 的文本
        has_brand_descriptions = False
        # 检查是否有品牌描述模式（品牌名后跟描述性文本）
        brand_desc_patterns = ['brand offering', 'manufacturer of', 'seller of', 'specializes in']
        for pattern in brand_desc_patterns:
            if pattern in page_text_lower:
                has_brand_descriptions = True
                break
        
        # 也检查是否有 "Connect this brand" 按钮（品牌选择弹窗特有）
        has_connect_button = 'connect this brand' in page_text_lower
        # "couldn't associate" 也是品牌选择弹窗的特征（Select brand 按钮场景）
        has_couldnt_associate = "couldn't associate" in page_text_lower or 'could not associate' in page_text_lower
        
        if has_brand_descriptions or has_connect_button or has_couldnt_associate:
            state['page_type'] = 'BRAND_SELECTION'
            state['needs_brand_selection'] = True
            print("[DEBUG] 检测到品牌选择弹窗（有品牌选项描述）")
            return state
        # UK/EU 新 UI 有时只暴露 select brand / brand registry 文案，
        # 品牌选项和 Connect 按钮在 Katal/Shadow DOM 或后续 overlay 中，body inner_text 不一定能读到。
        # 如果同时出现品牌选择文案 + brand registry/associate 警告，应优先判定为品牌选择，
        # 否则会误走 Apply to sell fallback，把卡在品牌选择页误判成 429/panel 半加载。
        if has_select_brand_text and has_brand_warning:
            state['page_type'] = 'BRAND_SELECTION'
            state['needs_brand_selection'] = True
            print("[DEBUG] 检测到品牌选择页面（select brand + brand warning，无需等待描述文本）")
            return state
        else:
            print("[DEBUG] 有品牌相关文本但无品牌选项描述，继续检测其他状态...")
    
    # 检查是否是 GTIN 豁免页面
    if 'GTIN Exemption' in page_text or 'UPC Exemption' in page_text or 'gtin-exemption' in current_url.lower():
        state['page_type'] = 'GTIN_EXEMPTION'
        state['needs_auth'] = True
        return state
    if 'document_upload_new' in page_text and 'Branding on the image' in page_text:
        state['page_type'] = 'GTIN_EXEMPTION'
        state['needs_auth'] = True
        return state
    
    # 检查新UI的 "You need approval" 弹窗
    if 'You need approval to list this product' in page_text or 'Brand authorisation required' in page_text:
        state['page_type'] = 'NEEDS_APPROVAL_NEW_UI'
        state['needs_auth'] = True
        print("[DEBUG] 检测到新UI的授权弹窗")
        return state
    
    # 检查新UI的 5461 表单（直接嵌入页面）
    if 'Listing approval' in page_text or 'Apply to sell' in page_text:
        # 进一步确认是否有表单字段
        has_form_fields = 'Product ID Number' in page_text or 'Product title' in page_text or 'Manufacturer' in page_text
        if has_form_fields:
            state['page_type'] = '5461_FORM_OPEN'
            state['has_5461_form'] = True
            print("[DEBUG] 检测到新UI的 5461 表单")
            return state
    
    # 检查是否在新UI的 Product Identity 页面（只有在没有弹窗的情况下）
    if 'interactive/listing/workflow/create' in current_url.lower():
        # ★ 新UI把 "Apply to sell" 替换为
        # "N restriction(s) / N application(s) required" + kat-link "View"
        # 这种情况必须识别为 NEEDS_APPROVAL_NEW_UI，否则阶段2跳过授权步骤
        has_application_required = has_application_required_entry_text(page_text)
        if has_application_required:
            state['page_type'] = 'NEEDS_APPROVAL_NEW_UI'
            state['needs_auth'] = True
            print("[DEBUG] 新UI检测到 'application required' + View链接，识别为 NEEDS_APPROVAL_NEW_UI")
            return state
        state['page_type'] = 'PRODUCT_IDENTITY_NEW_UI'
        # 新UI可能需要点击 Continue 或 Submit 才能触发授权检查
        return state
    
    # 检查 Product Identity 页面中的 UPC Exemption 提示
    if 'product_identity' in current_url.lower():
        # 调试输出：检查页面文本中是否包含关键文本
        has_brand_auth = 'Brand Authorization Required' in page_text or 'Authorization Required' in page_text or 'brand authorization' in page_text.lower()
        has_upc_exemption = 'UPC Exemption Required' in page_text or 'Suggest an ASIN' in page_text or 'Apply to sell' in page_text or 'upc exemption' in page_text.lower()
        print(f"[DEBUG] Product Identity 页面检测: has_brand_auth={has_brand_auth}, has_upc_exemption={has_upc_exemption}")
        
        if has_brand_auth or has_upc_exemption:
            state['page_type'] = 'AUTH_REQUIRED'
            state['needs_auth'] = True
        else:
            # 如果页面文本加载不完全（429），但URL是product_identity，也标记为可能需要授权
            state['page_type'] = 'PRODUCT_IDENTITY'
            state['needs_auth'] = True  # 保守策略：Product Identity 页面默认可能需要授权
    elif 'Brand Authorization Required' in page_text or 'Authorization Required' in page_text:
        state['page_type'] = 'AUTH_REQUIRED'
        state['needs_auth'] = True
    elif 'Listing approval' in page_text or 'document_upload_new' in page_text:
        state['page_type'] = '5461_FORM'
        state['has_5461_form'] = True
    
    return state


def capture_failure_page_evidence(page) -> dict:
    """Capture a bounded, read-only summary before Dashboard navigation.

    Screenshots remain private. This summary gives incident triage enough
    state, visible text, and selector-probe context to distinguish a state
    recognizer defect from a changed workflow or a genuinely missing control.
    """
    evidence = {
        "url": str(getattr(page, "url", "") or ""),
        "visible_text": "",
        "recognized_state": {},
        "selector_probes": {},
    }
    errors = []
    try:
        evidence["visible_text"] = str(page.inner_text("body") or "")[:8192]
    except Exception as exc:
        errors.append(f"visible_text: {exc}")
    try:
        state = check_page_state(page)
        evidence["recognized_state"] = {
            key: state.get(key)
            for key in (
                "page_type",
                "needs_auth",
                "has_5461_form",
                "needs_brand_selection",
            )
        }
    except Exception as exc:
        errors.append(f"recognized_state: {exc}")
    try:
        evidence["selector_probes"] = page.evaluate(
            r"""() => {
                const deepRoots = [document];
                for (let i = 0; i < deepRoots.length; i++) {
                    for (const node of deepRoots[i].querySelectorAll('*')) {
                        if (node.shadowRoot) deepRoots.push(node.shadowRoot);
                    }
                }
                const deepQuery = (selector) => deepRoots.flatMap(
                    root => Array.from(root.querySelectorAll(selector))
                );
                const normalizedText = node => (
                    node.innerText || node.textContent || node.getAttribute?.('label') || ''
                ).replace(/\s+/g, ' ').trim();
                const controls = deepQuery('button, kat-button, a, kat-link, [role="button"]');
                const approvalControls = controls.filter(node => {
                    const text = normalizedText(node).toLowerCase();
                    return text === 'view' || text.includes('apply to sell') ||
                        /applications?\s*required/.test(text);
                });
                return {
                    approval_control_count: approvalControls.length,
                    approval_control_texts: approvalControls.slice(0, 10).map(
                        node => normalizedText(node).slice(0, 160)
                    ),
                    visible_panel_count: deepQuery(
                        'kat-panel-wrapper[panel-visible="true"]'
                    ).length,
                    form_field_count: deepQuery(
                        'kat-input[id*="product_title"], ' +
                        'kat-input#question-cat_auth_mo_question_string_id_product_title, ' +
                        'input[id*="document_upload"][id*="document_input"]'
                    ).length,
                    restriction_entry_count: deepQuery('body, kat-panel-wrapper').filter(node =>
                        /\b\d+\s*restrictions?\b/i.test(normalizedText(node)) &&
                        (/applications?\s*required/i.test(normalizedText(node)) ||
                         /\bview\b/i.test(normalizedText(node)))
                    ).length,
                };
            }"""
        ) or {}
    except Exception as exc:
        errors.append(f"selector_probes: {exc}")
    evidence["dom_contract"] = capture_dom_shadow_contract(page)
    if errors:
        evidence["capture_errors"] = errors
    return evidence


def handle_brand_selection(page, brand_name: str, brand_keywords: list = None) -> bool:
    """
    处理品牌选择页面 - 重写版
    通过弹窗容器限定范围，精准匹配品牌选项
    """
    import time
    
    print(f"[品牌选择] 处理品牌选择页面，目标品牌: {brand_name}")
    
    try:
        # 步骤1: 点击 "Select brand" 按钮（如果在 Product Identity 页面）
        print("[品牌选择] 尝试点击 'Select brand' 按钮...")
        select_brand_clicked = page.evaluate('''() => {
            // 方法1: kat-button label
            const katButtons = document.querySelectorAll('kat-button');
            for (let btn of katButtons) {
                const label = (btn.getAttribute('label') || '').toLowerCase();
                if (label.includes('select brand') || label.includes('choose brand')) {
                    btn.click();
                    return { clicked: true, method: 'kat-button' };
                }
            }
            // 方法2: 普通按钮/链接文本。当前 Product Identity 页面会把
            // "Select Brand" 渲染为 kat-link，而不是 kat-button。
            const buttons = document.querySelectorAll('kat-link, button, a[role="button"], a');
            for (let btn of buttons) {
                const text = (btn.textContent || '').toLowerCase();
                const label = (btn.getAttribute('label') || btn.getAttribute('aria-label') || '').toLowerCase();
                if (text.includes('select brand') || text.includes('choose brand') ||
                    label.includes('select brand') || label.includes('choose brand')) {
                    btn.click();
                    return { clicked: true, method: btn.tagName.toLowerCase() + '-text' };
                }
            }
            return { clicked: false };
        }''')
        
        if select_brand_clicked.get('clicked'):
            print("[品牌选择] 已点击 'Select brand' 按钮")
            # 轮询等待 radio 选项出现（最多 15 秒），避免弹窗加载慢时抢读
            for _wait_i in range(15):
                time.sleep(1)
                _radio_count = page.evaluate('''() => {
                    return document.querySelectorAll('kat-radio, [type="radio"], kat-box-radio, .kat-box-brand').length;
                }''')
                if _radio_count > 0:
                    print(f"[品牌选择] 弹窗 radio 已出现 ({_radio_count} 个)，等待 {_wait_i+1}s")
                    time.sleep(1)  # 再稳定 1 秒
                    break
            else:
                print("[品牌选择] 等待 15s 后仍未检测到 radio，继续尝试读取...")
            # 额外等待 kat-box[data-testid="brand-info-option"] 渲染（最多 10 秒）
            # 这些 kat-box 比 radio 渲染慢，是品牌选项的真实容器
            for _box_wait_i in range(10):
                _box_count = page.evaluate('''() => {
                    return document.querySelectorAll('kat-box[data-testid="brand-info-option"]').length;
                }''')
                if _box_count > 0:
                    print(f"[品牌选择] brand-info-option kat-box 已出现 ({_box_count} 个)，等待 {_box_wait_i}s")
                    break
                time.sleep(1)
            else:
                print("[品牌选择] 等待 10s 后 kat-box brand-info-option 仍为 0，继续尝试...")
        else:
            print("[品牌选择] 未找到 'Select brand' 按钮，可能已在弹窗中")
        
        # 步骤2: 在弹窗内查找品牌选项
        print("[品牌选择] 查找弹窗内的品牌选项...")
        
        options_result = page.evaluate('''() => {
            // 首先尝试找到品牌选择弹窗容器
            // 策略：查找包含品牌选项描述的 kat-modal 或 dialog
            let dialogContainer = null;
            
            // 方法A: 通过 kat-modal 的文本内容查找
            const modals = document.querySelectorAll('kat-modal');
            for (let modal of modals) {
                const text = modal.textContent?.toLowerCase() || '';
                if (text.includes('clarify which brand') || text.includes('connect this brand') || 
                    text.includes('select the brand') || text.includes('brand matches')) {
                    dialogContainer = modal;
                    break;
                }
            }
            
            // 方法B: 通过 role="dialog" 查找
            if (!dialogContainer) {
                const dialogs = document.querySelectorAll('[role="dialog"]');
                for (let d of dialogs) {
                    const text = d.textContent?.toLowerCase() || '';
                    if (text.includes('clarify which brand') || text.includes('connect this brand')) {
                        dialogContainer = d;
                        break;
                    }
                }
            }
            
            // 方法C: 查找包含 "Connect this brand" 按钮的父容器
            if (!dialogContainer) {
                const allEls = document.querySelectorAll('*');
                for (let el of allEls) {
                    const text = el.textContent?.toLowerCase() || '';
                    if (text.includes('connect this brand')) {
                        // 向上查找最近的 modal/dialog 容器
                        let parent = el.parentElement;
                        while (parent && parent.tagName !== 'BODY') {
                            if (parent.tagName === 'KAT-MODAL' || parent.getAttribute('role') === 'dialog') {
                                dialogContainer = parent;
                                break;
                            }
                            parent = parent.parentElement;
                        }
                        if (!dialogContainer) {
                            dialogContainer = el.closest('div[class*="modal"], div[class*="dialog"], kat-modal') || el.parentElement;
                        }
                        break;
                    }
                }
            }
            
            let options = [];
            let containerType = 'none';
            
            if (dialogContainer) {
                containerType = dialogContainer.tagName;
                
                // 策略0: kat-box[data-testid="brand-info-option"]（品牌选择弹窗特有）
                const brandBoxes = dialogContainer.querySelectorAll('kat-box[data-testid="brand-info-option"]');
                if (brandBoxes.length > 0) {
                    brandBoxes.forEach((box, i) => {
                        const descEl = box.querySelector('div[data-testid="brand-description-content"]');
                        const text = (descEl ? descEl.textContent : box.textContent || '').trim();
                        options.push({ index: i, type: 'kat-box-brand', text: text, label: '', combined: text });
                    });
                }
                
                // 在弹窗容器内查找选项
                // 策略1: 查找 kat-radiobutton（在弹窗内）
                const radiobuttons = options.length === 0 ? dialogContainer.querySelectorAll('kat-radiobutton') : [];
                if (radiobuttons.length > 0) {
                    radiobuttons.forEach((rb, i) => {
                        // textContent 包含 Shadow DOM 的文本
                        const text = rb.textContent?.trim() || '';
                        const labelText = rb.getAttribute('label') || '';
                        options.push({
                            index: i,
                            type: 'kat-radiobutton',
                            text: text,
                            label: labelText,
                            combined: (labelText + ' ' + text).trim()
                        });
                    });
                }
                
                // 策略2: 查找 input[type="radio"]（在弹窗内）
                if (options.length === 0) {
                    const inputs = dialogContainer.querySelectorAll('input[type="radio"]');
                    inputs.forEach((inp, i) => {
                        // 查找关联 label
                        let labelText = '';
                        const id = inp.id;
                        if (id) {
                            const label = dialogContainer.querySelector('label[for="' + id + '"]');
                            if (label) {
                                labelText = label.textContent?.trim() || '';
                            }
                        }
                        // 回退：查找父元素文本
                        if (!labelText) {
                            const parent = inp.closest('label, div, li, span');
                            if (parent) {
                                labelText = parent.textContent?.trim() || '';
                            }
                        }
                        options.push({
                            index: i,
                            type: 'input-radio',
                            text: labelText,
                            label: '',
                            combined: labelText
                        });
                    });
                }
                
                // 策略3: 查找 kat-radio（在弹窗内）
                if (options.length === 0) {
                    const radios = dialogContainer.querySelectorAll('kat-radio');
                    radios.forEach((r, i) => {
                        const text = r.textContent?.trim() || '';
                        const label = r.getAttribute('label') || '';
                        options.push({
                            index: i,
                            type: 'kat-radio',
                            text: text,
                            label: label,
                            combined: (label + ' ' + text).trim()
                        });
                    });
                }
            }
            
            // 如果弹窗内没找到，先尝试全局 kat-box[data-testid="brand-info-option"]
            if (options.length === 0) {
                const globalBrandBoxes = document.querySelectorAll('kat-box[data-testid="brand-info-option"]');
                if (globalBrandBoxes.length > 0) {
                    globalBrandBoxes.forEach((box, i) => {
                        const descEl = box.querySelector('div[data-testid="brand-description-content"]');
                        const text = (descEl ? descEl.textContent : box.textContent || '').trim();
                        options.push({ index: i, type: 'kat-box-brand', text: text, label: '', combined: text });
                    });
                }
            }

            // 如果弹窗内没找到，回退到全局查找（但过滤非品牌选项）
            if (options.length === 0) {
                // 全局查找 input[type="radio"]，过滤属性过滤器
                const allInputs = document.querySelectorAll('input[type="radio"]');
                let validIndex = 0;
                allInputs.forEach(function(inp) {
                    const name = inp.name || '';
                    // 过滤属性过滤器 radio（name 匹配）
                    if (name.indexOf('attribute_filter') >= 0 || name.indexOf('REQUIRED') >= 0 || name.indexOf('RELEVANT') >= 0) {
                        return;
                    }
                    // 过滤通用 UI radio（aria-label 是 Amazon 页面固定词汇，不是品牌名）
                    const ariaLabel = (inp.getAttribute('aria-label') || '').trim().toLowerCase();
                    const uiLabels = ['required', 'recommended', 'all attributes', 'irrelevant attribute', 'value missing', 'relevant attribute'];
                    if (uiLabels.indexOf(ariaLabel) >= 0) {
                        return;
                    }
                    
                    let labelText = '';
                    
                    // 方法1: aria-label
                    labelText = (inp.getAttribute('aria-label') || '').trim();
                    
                    // 方法2: aria-labelledby
                    if (!labelText) {
                        const labelledBy = inp.getAttribute('aria-labelledby');
                        if (labelledBy) {
                            const labelEl = document.getElementById(labelledBy);
                            if (labelEl) {
                                labelText = (labelEl.textContent || '').trim();
                            }
                        }
                    }
                    
                    // 方法3: 关联 label[for]
                    if (!labelText) {
                        const id = inp.id;
                        if (id) {
                            const label = document.querySelector('label[for="' + id + '"]');
                            if (label) {
                                labelText = (label.textContent || '').trim();
                            }
                        }
                    }
                    
                    // 方法4: closest kat-radiobutton Shadow DOM
                    if (!labelText) {
                        const rb = inp.closest('kat-radiobutton');
                        if (rb && rb.shadowRoot) {
                            const label = rb.shadowRoot.querySelector('label');
                            if (label) {
                                labelText = (label.textContent || '').trim();
                            }
                        }
                    }
                    
                    // 方法5: closest kat-radio Shadow DOM
                    if (!labelText) {
                        const kr = inp.closest('kat-radio');
                        if (kr && kr.shadowRoot) {
                            const label = kr.shadowRoot.querySelector('label');
                            if (label) {
                                labelText = (label.textContent || '').trim();
                            }
                        }
                    }
                    
                    // 方法6: 向上遍历祖先元素获取文本
                    if (!labelText) {
                        let container = inp.parentElement;
                        while (container && container.tagName !== 'BODY') {
                            let text = container.textContent || '';
                            if (container.shadowRoot) {
                                text += ' ' + (container.shadowRoot.textContent || '');
                            }
                            text = text.trim();
                            if (text.length > 3) {
                                labelText = text;
                                break;
                            }
                            container = container.parentElement;
                        }
                    }
                    
                    // 过滤 Report an issue 等非品牌选项
                    const lowerText = labelText.toLowerCase();
                    if (lowerText.indexOf('report an issue') >= 0 || 
                        lowerText.indexOf('attributes required') >= 0 ||
                        lowerText.indexOf('view minimum') >= 0 ||
                        lowerText.indexOf('irrelevant attribute') >= 0 ||
                        lowerText.indexOf('value missing') >= 0) {
                        return;
                    }
                    
                    options.push({
                        index: validIndex,
                        type: 'input-radio-global',
                        text: labelText,
                        label: '',
                        combined: labelText,
                        originalIndex: Array.from(allInputs).indexOf(inp)
                    });
                    validIndex++;
                });
            }
            
            return {
                containerType: containerType,
                options: options,
                totalRadiosOnPage: document.querySelectorAll('input[type="radio"]').length
            };
        }''')
        
        container_type = options_result.get('containerType', 'none')
        options = options_result.get('options', [])
        total_radios = options_result.get('totalRadiosOnPage', 0)
        
        print(f"[品牌选择] 弹窗容器: {container_type}, 找到 {len(options)} 个品牌选项 (页面共 {total_radios} 个 radio)")
        
        for opt in options:
            print(f"  [{opt['index']}] ({opt['type']}) text='{opt['text'][:120]}'")
        
        if not options:
            print("[品牌选择] 未找到品牌选项")
            return False
        
        # 步骤3: 匹配品牌关键词
        matched_index = None
        matched_type = None
        matched_reason = ''
        
        if brand_keywords:
            print(f"[品牌选择] 使用关键词匹配: {brand_keywords}")
            for opt in options:
                combined = opt.get('combined', '').lower()
                for keyword in brand_keywords:
                    if keyword.lower() in combined:
                        matched_index = opt['index']
                        matched_type = opt['type']
                        matched_reason = f"关键词 '{keyword}'"
                        break
                if matched_index is not None:
                    break
        
        # 配置了品牌专属识别文本时，它是权威匹配条件；未命中就拒绝猜测。
        if brand_keywords and matched_index is None:
            print("[品牌选择] 品牌专属识别文本未命中，拒绝按品牌名或首个选项回退")
            return False

        # 未配置专属识别文本时，尝试匹配品牌名
        if matched_index is None:
            for opt in options:
                combined = opt.get('combined', '').lower()
                if brand_name.lower() in combined:
                    matched_index = opt['index']
                    matched_type = opt['type']
                    matched_reason = f"品牌名 '{brand_name}'"
                    break
        
        if matched_index is None:
            # 回退：选择第一个有文本描述的选项
            for opt in options:
                if opt.get('text', '').strip():
                    matched_index = opt['index']
                    matched_type = opt['type']
                    matched_reason = "第一个有效选项（回退）"
                    break
        
        if matched_index is None:
            print("[品牌选择] 无法匹配任何选项")
            return False
        
        print(f"[品牌选择] 匹配成功 ({matched_reason}): [{matched_index}] {matched_type}")
        
        # 步骤4: 点击匹配的选项
        click_result = page.evaluate('''({index, type}) => {
            if (type === 'kat-box-brand') {
                const boxes = document.querySelectorAll('kat-box[data-testid="brand-info-option"]');
                if (boxes[index]) {
                    const radio = boxes[index].querySelector('input[type="radio"][name="brand-record-selection-radio-button"]');
                    if (radio) {
                        const nativeSet = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'checked').set;
                        nativeSet.call(radio, true);
                        radio.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
                        radio.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
                        radio.click();
                        radio.dispatchEvent(new Event('change', {bubbles: true}));
                        radio.dispatchEvent(new Event('input', {bubbles: true}));
                        // 也点击父 kat-box 触发 Katal 组件状态更新
                        boxes[index].click();
                        return { clicked: true, method: 'kat-box-radio-react' };
                    }
                    boxes[index].click();
                    return { clicked: true, method: 'kat-box' };
                }
            } else if (type === 'kat-radiobutton') {
                const buttons = document.querySelectorAll('kat-radiobutton');
                if (buttons[index]) {
                    buttons[index].click();
                    return { clicked: true, method: 'kat-radiobutton' };
                }
            } else if (type === 'kat-radio') {
                const radios = document.querySelectorAll('kat-radio');
                if (radios[index]) {
                    radios[index].click();
                    return { clicked: true, method: 'kat-radio' };
                }
            } else if (type === 'input-radio' || type === 'input-radio-global') {
                const inputs = document.querySelectorAll('input[type="radio"]');
                const validInputs = Array.from(inputs).filter(inp => {
                    const name = inp.name || '';
                    return !name.includes('attribute_filter') && !name.includes('REQUIRED') && !name.includes('RELEVANT');
                });
                if (validInputs[index]) {
                    const inp = validInputs[index];
                    const nativeSet = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'checked').set;
                    nativeSet.call(inp, true);
                    inp.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
                    inp.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
                    inp.click();
                    inp.dispatchEvent(new Event('change', {bubbles: true}));
                    inp.dispatchEvent(new Event('input', {bubbles: true}));
                    return { clicked: true, method: 'input-radio-react' };
                }
            }
            return { clicked: false };
        }''', {'index': matched_index, 'type': matched_type})
        
        print(f"[品牌选择] 点击选项: {click_result}")
        # 等待 Katal 更新按钮状态（Connect this brand 变为 enabled），最多 3s 早退
        try:
            page.wait_for_function(
                """() => {
                    const btn = document.querySelector('kat-button[data-testid="connect-brand-button"]');
                    if (!btn) return false;
                    return !btn.disabled && btn.getAttribute('disabled') === null &&
                           btn.getAttribute('state') !== 'disabled';
                }""",
                timeout=3000
            )
        except Exception:
            pass
        
        # 步骤5: 点击 "Connect this brand" 按钮（精确匹配，避免误点关闭按钮）
        print("[品牌选择] 点击 'Connect this brand' 按钮...")
        connect_result = page.evaluate('''() => {
            const buttons = document.querySelectorAll('kat-button, button, [role="button"]');
            // 优先精确匹配 'connect this brand'，跳过 disabled 按钮
            // Katal kat-button disabled: attribute disabled="true" 或 state="disabled"
            function isKatalDisabled(btn) {
                const d = btn.getAttribute('disabled');
                if (d === 'true' || d === '') return true;
                if (btn.getAttribute('state') === 'disabled') return true;
                if (btn.disabled === true) return true;
                return false;
            }
            for (let btn of buttons) {
                const text = (btn.textContent || btn.getAttribute('label') || '').toLowerCase().trim();
                if (text.includes('connect this brand') && !isKatalDisabled(btn)) {
                    btn.click();
                    return { clicked: true, text: text, method: 'exact', disabled: isKatalDisabled(btn) };
                }
            }
            // 如果按钮存在但 disabled，强制点击（radio 已选中，按钮可能只是 Katal 状态未更新）
            for (let btn of buttons) {
                const text = (btn.textContent || btn.getAttribute('label') || '').toLowerCase().trim();
                if (text.includes('connect this brand')) {
                    btn.click();
                    return { clicked: true, text: text, method: 'force-disabled' };
                }
            }
            // 最后回退：列出所有可见按钮供调试
            const allTexts = Array.from(buttons).map(b => (b.textContent || b.getAttribute('label') || '').trim().slice(0, 40));
            return { clicked: false, allButtons: allTexts.filter(t => t) };
        }''')
        
        print(f"[品牌选择] 确认按钮: {connect_result}")
        if not connect_result.get('clicked'):
            print(f"[品牌选择] ⚠️ 未找到 Connect this brand 按钮，可能 radio 未选中导致按钮仍为 disabled")
            print(f"[品牌选择] 页面上的按钮: {connect_result.get('allButtons', [])}")
            return False
        # 等待 URL 变化（跳转到 seller-qualification/description）或 panel 出现，最多 8s
        try:
            page.wait_for_function(
                """() => {
                    const url = window.location.href;
                    return url.includes('seller-qualification') ||
                           url.includes('description') ||
                           !url.includes('product_identity') ||
                           document.querySelector('kat-panel-wrapper[panel-visible="true"]') !== null;
                }""",
                timeout=8000
            )
        except Exception:
            pass
        
        # 检查页面状态
        current_url = page.url
        print(f"[品牌选择] 当前 URL: {current_url[:100]}")
        
        if 'seller-qualification' in current_url or 'description' in current_url:
            print("[品牌选择] ✅ 成功跳转到 GTIN 豁免/Description 页面")
            return True
        
        # 如果还在 product_identity，检查是否成功
        if 'product_identity' in current_url:
            # 检查是否还有品牌选择提示
            has_brand_selection = page.evaluate('''() => {
                return document.body.textContent.toLowerCase().includes('clarify which brand') ||
                       document.body.textContent.toLowerCase().includes('select brand');
            }''')
            if not has_brand_selection:
                print("[品牌选择] ✅ 品牌选择提示已消失")
                return True
        
        print("[品牌选择] 页面未跳转，但操作已执行")
        return True
        
    except Exception as e:
        print(f"[品牌选择] 处理失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def connect_brand_in_5461_panel(page, brand_name: str, keywords: list = None) -> dict:
    """
    处理 5461 面板内 / 独立弹出的 Connect brand 品牌选择 UI。

    Amazon DOM 结构（来自实测）：
      - 品牌列表容器: div[data-cy="brex-widget"]
      - 每个品牌选项:  kat-box[data-testid="brand-info-option"]
      - 描述文字:      div[data-testid="brand-description-content"]
      - 单选按钮:      input[type="radio"][name="brand-record-selection-radio-button"]
                       (在 kat-radiobutton 的 light DOM 中，可直接 querySelector)
      - 确认按钮:      kat-button[data-testid="connect-brand-button"]
                       (初始 disabled，选中 radio 后自动启用)

    流程：
      1. 检查 brex-widget 是否已显示（直接选 radio）
      2. 若无 brex-widget，检查初始覆盖层（"Clarify which brand" + "Connect this brand" 按钮）
         → 点击匹配的 "Connect this brand"
         → 等待 brex-widget 出现
      3. 在 brex-widget 中按关键词选正确 radio
      4. 点击 kat-button[data-testid="connect-brand-button"]
    """
    default_kws = [
        'screen protector', 'phone case', 'protective', '手机屏幕保护膜',
        'screen', 'mobile', 'cellphone', 'smartphone',
    ]
    kws = [str(k).strip().lower() for k in (keywords or []) if str(k).strip()] or default_kws
    print(f"[ConnectBrand] 关键词: {kws}")

    def _select_from_brex_widget():
        """从 brex-widget 选正确品牌 radio 并确认。返回 dict。"""
        opts = page.evaluate("""
            () => {
                const boxes = document.querySelectorAll('kat-box[data-testid="brand-info-option"]');
                return Array.from(boxes).map((box, i) => {
                    const nameEl  = box.querySelector('p');
                    const descEl  = box.querySelector('div[data-testid="brand-description-content"]');
                    const name    = (nameEl  ? nameEl.textContent  : '').trim();
                    const desc    = (descEl  ? descEl.textContent  : '').trim();
                    return { index: i, name, description: desc, text: (name + ' ' + desc).trim() };
                });
            }
        """)

        print(f"[ConnectBrand] brex-widget 品牌选项 ({len(opts)}个):")
        for o in opts:
            print(f"  [{o['index']}] {o['name']}: {o['description'][:80]}")

        if not opts:
            return {'found': True, 'selected': False, 'brand_text': '', 'note': 'brex-widget 无品牌选项'}

        best_idx = None
        for o in opts:
            if any(kw in o['text'].lower() for kw in kws):
                best_idx = o['index']
                print(f"[ConnectBrand] 关键词匹配 [{best_idx}]: {o['text'][:80]}")
                break

        if best_idx is None and keywords:
            print("[ConnectBrand] 品牌专属识别文本未命中，拒绝选择首个选项")
            return {
                'found': True,
                'selected': False,
                'brand_text': '',
                'note': '品牌专属识别文本未命中，拒绝猜测',
            }
        if best_idx is None:
            best_idx = 0
            print(f"[ConnectBrand] 未匹配默认关键词，选择第一个: {opts[0]['text'][:80]}")

        best_text = opts[best_idx]['text']

        # 点击 radio 按钮（light DOM，直接可查）
        radio_ok = page.evaluate("""
            (idx) => {
                const boxes = document.querySelectorAll('kat-box[data-testid="brand-info-option"]');
                if (!boxes[idx]) return false;
                const radio = boxes[idx].querySelector(
                    'input[type="radio"][name="brand-record-selection-radio-button"]'
                );
                if (radio) { radio.click(); return true; }
                return false;
            }
        """, best_idx)

        if not radio_ok:
            return {'found': True, 'selected': False, 'brand_text': best_text, 'note': '单选按钮点击失败'}

        print(f"[ConnectBrand] ✅ 已点击单选按钮 [{best_idx}]")
        # 等待确认按钮从 disabled 变为 enabled，最多 2s 早退
        try:
            page.wait_for_function(
                """() => {
                    const btn = document.querySelector('kat-button[data-testid="connect-brand-button"]');
                    if (!btn) return false;
                    return !btn.disabled && btn.getAttribute('disabled') === null &&
                           btn.getAttribute('state') !== 'disabled';
                }""",
                timeout=2000
            )
        except Exception:
            pass

        # 点击 "Connect this brand" 确认按钮
        confirmed = page.evaluate("""
            () => {
                const btn = document.querySelector('kat-button[data-testid="connect-brand-button"]');
                if (!btn) return 'not_found';
                // 移除 disabled（以防万一）
                btn.removeAttribute('disabled');
                // 先尝试点击外层 kat-button
                btn.click();
                // 再尝试点击 shadow DOM 内的 <button>
                if (btn.shadowRoot) {
                    const inner = btn.shadowRoot.querySelector('button');
                    if (inner) {
                        inner.removeAttribute('disabled');
                        inner.click();
                    }
                }
                return 'clicked';
            }
        """)

        if confirmed == 'clicked':
            print("[ConnectBrand] ✅ 已点击 'Connect this brand' 确认按钮")
            # 等待 brex-widget 或 brand-info-option 出现（最快返回，超时降为5s兜底）
            try:
                page.wait_for_function(
                    """() => document.querySelector('div[data-cy="brex-widget"]') !== null
                         || document.querySelector('kat-box[data-testid="brand-info-option"]') !== null
                         || document.querySelector('kat-panel-wrapper[panel-visible="true"]') !== null""",
                    timeout=5000
                )
            except Exception:
                pass
            return {'found': True, 'selected': True, 'brand_text': best_text, 'note': f'选择了选项 [{best_idx}]'}
        else:
            return {'found': True, 'selected': False, 'brand_text': best_text, 'note': 'connect-brand-button 未找到'}

    try:
        # ── 步骤 1：检查 brex-widget 是否已显示 ────────────────────────────
        brex_visible = page.evaluate("""
            () => document.querySelector('div[data-cy="brex-widget"]') !== null
               || document.querySelector('kat-box[data-testid="brand-info-option"]') !== null
        """)

        if brex_visible:
            print("[ConnectBrand] brex-widget 已显示，直接选择品牌...")
            return _select_from_brex_widget()

        # ── 步骤 2：检查初始覆盖层（"Clarify which brand" + "Connect this brand" 按钮）─
        def _allText(js_var='el'):
            return f"""
                function allText({js_var}) {{
                    let t = {js_var}.textContent || '';
                    if ({js_var}.shadowRoot) for (const c of {js_var}.shadowRoot.children) t += allText(c);
                    return t;
                }}
            """

        has_overlay = page.evaluate("""
            () => {
                function allText(el) {
                    let t = el.textContent || '';
                    if (el.shadowRoot) for (const c of el.shadowRoot.children) t += allText(c);
                    return t;
                }
                const panel = document.querySelector(
                    'kat-panel-wrapper[data-testid="kat-panel-wrapper-QualificationWidget"]'
                );
                if (!panel) return false;
                const t = allText(panel).toLowerCase();
                return t.includes("clarify which brand") || t.includes("couldn't associate") ||
                       t.includes("connect this brand") || t.includes("could not associate");
            }
        """)

        if not has_overlay:
            return {'found': False, 'selected': False, 'brand_text': '', 'note': '未检测到品牌选择 UI'}

        print("[ConnectBrand] 检测到初始覆盖层，收集品牌选项...")

        # 收集覆盖层中的 "Connect this brand" 按钮及旁边文本
        overlay_opts = page.evaluate("""
            () => {
                function allText(el) {
                    let t = el.textContent || '';
                    if (el.shadowRoot) for (const c of el.shadowRoot.children) t += allText(c);
                    return t;
                }
                const results = [];
                function walk(el) {
                    if (!el) return;
                    const tag = (el.tagName || '').toUpperCase();
                    const label = (el.getAttribute('label') || '').toLowerCase();
                    const txt   = (el.textContent || '').toLowerCase().trim();
                    if ((tag === 'KAT-BUTTON' || tag === 'BUTTON') &&
                        (label.includes('connect this brand') || txt === 'connect this brand')) {
                        let container = el.parentElement;
                        let depth = 0, containerText = '';
                        while (container && depth < 6) {
                            containerText = allText(container);
                            if (containerText.length > 30 && containerText.length < 2000) break;
                            container = container.parentElement;
                            depth++;
                        }
                        results.push({ text: containerText.trim().slice(0, 300) });
                    }
                    if (el.shadowRoot) for (const c of el.shadowRoot.children) walk(c);
                    for (const c of el.children) walk(c);
                }
                const panel = document.querySelector(
                    'kat-panel-wrapper[data-testid="kat-panel-wrapper-QualificationWidget"]'
                );
                walk(panel || document.body);
                return results.map((o, i) => ({ index: i, text: o.text }));
            }
        """)

        print(f"[ConnectBrand] 覆盖层选项 ({len(overlay_opts)}个)")
        for o in overlay_opts:
            print(f"  [{o['index']}] {o['text'][:100]}")

        if not overlay_opts:
            return {'found': True, 'selected': False, 'brand_text': '', 'note': '覆盖层无 Connect this brand 按钮'}

        # 选匹配的覆盖层选项
        best_overlay_idx = 0
        for o in overlay_opts:
            if any(kw in o['text'].lower() for kw in kws):
                best_overlay_idx = o['index']
                print(f"[ConnectBrand] 覆盖层匹配 [{best_overlay_idx}]")
                break

        # 点击覆盖层中对应的 "Connect this brand" 按钮 → 触发 brex-widget
        page.evaluate("""
            (targetIdx) => {
                let count = 0;
                function walk(el) {
                    if (!el) return false;
                    const tag   = (el.tagName || '').toUpperCase();
                    const label = (el.getAttribute('label') || '').toLowerCase();
                    const txt   = (el.textContent || '').toLowerCase().trim();
                    if ((tag === 'KAT-BUTTON' || tag === 'BUTTON') &&
                        (label.includes('connect this brand') || txt === 'connect this brand')) {
                        if (count === targetIdx) { el.click(); return true; }
                        count++;
                    }
                    if (el.shadowRoot) for (const c of el.shadowRoot.children) { if (walk(c)) return true; }
                    for (const c of el.children) { if (walk(c)) return true; }
                    return false;
                }
                const panel = document.querySelector(
                    'kat-panel-wrapper[data-testid="kat-panel-wrapper-QualificationWidget"]'
                );
                return walk(panel || document.body);
            }
        """, best_overlay_idx)

        print(f"[ConnectBrand] ✅ 已点击覆盖层选项 [{best_overlay_idx}]，等待 brex-widget...")
        # 等待 brex-widget 或 brand-info-option 实际出现，最多 5s
        try:
            page.wait_for_function(
                """() => document.querySelector('div[data-cy="brex-widget"]') !== null
                     || document.querySelector('kat-box[data-testid="brand-info-option"]') !== null""",
                timeout=5000
            )
        except Exception:
            pass

        # 确认 brex-widget 出现
        brex_visible2 = page.evaluate("""
            () => document.querySelector('div[data-cy="brex-widget"]') !== null
               || document.querySelector('kat-box[data-testid="brand-info-option"]') !== null
        """)

        if not brex_visible2:
            return {'found': True, 'selected': False, 'brand_text': '', 'note': '点击覆盖层后 brex-widget 未出现'}

        print("[ConnectBrand] brex-widget 已出现，进行 radio 选择...")
        return _select_from_brex_widget()

    except Exception as e:
        print(f"[ConnectBrand] 出错: {e}")
        import traceback; traceback.print_exc()
        return {'found': False, 'selected': False, 'brand_text': '', 'note': str(e)}


def fill_optional_contact_email(page, account_email: str) -> bool:
    """Fill a contact email only when the current approval form exposes one."""
    selectors = [
        'kat-input#contact_info_email_input',
        'kat-input[id*="contact_info_email"]',
        'input#contact_info_email_input',
        'input[name*="contact_info_email"]',
        'input[type="email"]',
    ]
    present_selector = None
    for selector in selectors:
        try:
            if page.locator(selector).count() > 0:
                present_selector = selector
                break
        except Exception:
            continue
    if not present_selector:
        return False

    resolved_email = clean_email(account_email)
    if not resolved_email:
        resolved_email = get_autofill_email_from_page(page, wait_sec=2.0)
    if not resolved_email:
        print("  [fill_5461_form] 检测到联系邮箱字段，但未找到可用邮箱")
        return False

    filled = fill_katal_input(page, present_selector, resolved_email)
    if filled:
        print("  [fill_5461_form] 联系邮箱已填写")
    return filled


def fill_5461_form(page, brand_name: str, statement_text: str, upload_files: list, account_email: str = "") -> dict:
    """
    填写 5461 表单
    根据 _archive/scripts/auto_submit_5461.py 的经验
    支持两种弹窗类型：
    - Catalog Authorization (cat_auth_mo)：含 product_title / manufacturer / product_description / email
    - GTIN Exemption Request：只含 Product Name 一个字段
    """
    result = {"status": "ok", "steps": []}

    # --- GTIN Exemption 弹窗检测 & 处理 ---
    # 如果弹窗标题含 "GTIN Exemption"，走单字段填写路径
    try:
        panel_text = page.evaluate(r"""() => {
            const panel = document.querySelector('kat-panel-wrapper[panel-visible="true"]')
                       || document.querySelector('kat-panel-wrapper[data-testid*="QualificationWidget"]');
            return panel ? (panel.innerText || panel.textContent || "") : "";
        }""")
        if "GTIN Exemption" in (panel_text or "") or "GTIN Exemption" in (page.evaluate("() => document.body ? document.body.innerText : ''") or ""):
            print("  [fill_5461_form] 检测到 GTIN Exemption 弹窗，填写 Product Name 字段...")
            # Product Name 字段 selector（尝试多种）
            GTIN_PRODUCT_NAME_SELECTORS = [
                'kat-input[label="Product Name"]',
                'kat-input[id*="product_name"]',
                'kat-input[data-cy*="product_name"]',
                'kat-input[placeholder*="Product Name"]',
                'kat-input[placeholder*="product name"]',
                # 通用回退：弹窗内第一个空 kat-input
            ]
            # 从 statement_text 提取 item name 作为 Product Name
            import re as _re
            _m = _re.search(r"Item name[：:]\s*(.+)", statement_text or "")
            product_name_value = _m.group(1).strip() if _m else f"{brand_name} Screen Protector"

            filled_gtin = False
            for _sel in GTIN_PRODUCT_NAME_SELECTORS:
                try:
                    if page.locator(_sel).count() > 0:
                        fill_katal_input(page, _sel, product_name_value)
                        print(f"  [fill_5461_form] GTIN Product Name 已填写: {product_name_value[:60]} (sel={_sel})")
                        result["steps"].append("gtin_product_name")
                        result["gtin_product_name"] = product_name_value
                        filled_gtin = True
                        break
                except Exception:
                    pass

            if not filled_gtin:
                # 通用回退：找弹窗内第一个 kat-input 并填写
                try:
                    fallback_filled = page.evaluate(r"""(val) => {
                        const panel = document.querySelector('kat-panel-wrapper[panel-visible="true"]')
                                   || document.querySelector('kat-panel-wrapper[data-testid*="QualificationWidget"]')
                                   || document;
                        const inp = panel.querySelector('kat-input');
                        if (!inp) return false;
                        inp.value = val;
                        inp.setAttribute('value', val);
                        inp.dispatchEvent(new Event('input', {bubbles: true}));
                        inp.dispatchEvent(new Event('change', {bubbles: true}));
                        const inner = inp.shadowRoot && inp.shadowRoot.querySelector('input');
                        if (inner) {
                            const nativeSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
                            if (nativeSetter && nativeSetter.set) nativeSetter.set.call(inner, val);
                            inner.dispatchEvent(new Event('input', {bubbles: true}));
                            inner.dispatchEvent(new Event('change', {bubbles: true}));
                            inner.dispatchEvent(new Event('blur', {bubbles: true}));
                        }
                        return true;
                    }""", product_name_value)
                    if fallback_filled:
                        print(f"  [fill_5461_form] GTIN Product Name 回退填写成功: {product_name_value[:60]}")
                        result["steps"].append("gtin_product_name_fallback")
                        result["gtin_product_name"] = product_name_value
                    else:
                        print("  [fill_5461_form] GTIN Product Name 填写失败，无可用字段")
                except Exception as _e:
                    print(f"  [fill_5461_form] GTIN Product Name 回退填写异常: {_e}")

            time.sleep(random.uniform(0.5, 1.0))

            # 上传图片（与 cat_auth_mo 路径相同）
            if upload_files:
                print(f"  - 上传图片 ({len(upload_files)} 张)...")
                try:
                    page.locator('input[id*="document_upload"][id*="document_input"]').set_input_files(upload_files)
                    try:
                        page.wait_for_function(
                            """() => {
                                const inp = document.querySelector('input[id*="document_upload"][id*="document_input"]');
                                return inp && inp.files && inp.files.length > 0;
                            }""",
                            timeout=5000
                        )
                    except Exception:
                        pass
                    result["steps"].append("upload_images")
                except Exception as _e:
                    print(f"    [警告] 图片上传失败: {_e}")

            # 勾选复选框
            try:
                checked_count = page.evaluate(r"""() => {
                    const panel = document.querySelector('kat-panel-wrapper[panel-visible="true"]')
                               || document.querySelector('kat-panel-wrapper[data-testid*="QualificationWidget"]')
                               || document;
                    let count = 0;
                    panel.querySelectorAll('kat-checkbox').forEach(cb => {
                        if (cb.disabled || cb.hasAttribute('disabled')) return;
                        cb.checked = true;
                        cb.dispatchEvent(new Event('change', {bubbles: true}));
                        count++;
                    });
                    panel.querySelectorAll('input[type="checkbox"]').forEach(inp => {
                        if (inp.disabled || inp.hasAttribute('disabled')) return;
                        inp.checked = true;
                        inp.dispatchEvent(new Event('change', {bubbles: true}));
                        count++;
                    });
                    return count;
                }""")
                time.sleep(1)
                print(f"    复选框已勾选（{checked_count} 个）")
            except Exception as _e:
                print(f"    [警告] 勾选复选框失败: {_e}")

            # Newer MX GTIN variants may expose a required contact email even
            # though the original form had only Product Name + documents.
            if fill_optional_contact_email(page, account_email):
                result["steps"].append("gtin_contact_email")

            return result
    except Exception as _gtin_e:
        print(f"  [fill_5461_form] GTIN Exemption 检测异常（继续走标准路径）: {_gtin_e}")

    # --- Listing Approval 表单检测 & 处理 ---
    # 这种表单用普通 HTML input/textarea，不是 kat-input Shadow DOM。
    # 特征：panel 内含 "Listing approval for" 或 "Submit required information" 文字。
    try:
        panel_text_la = page.evaluate(r"""() => {
            const panel = document.querySelector('kat-panel-wrapper[panel-visible="true"]')
                       || document.querySelector('kat-panel-wrapper[data-testid*="QualificationWidget"]');
            return panel ? (panel.innerText || panel.textContent || "") : "";
        }""")
        is_listing_approval = (
            "Listing approval for" in (panel_text_la or "")
            or "Submit required information" in (panel_text_la or "")
            or "Submit documents" in (panel_text_la or "")
        )
        if not is_listing_approval:
            # Also check full page body as fallback
            body_text = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
            is_listing_approval = (
                "Listing approval for" in body_text
                or ("Submit required information" in body_text and "Submit documents" in body_text)
            )
        if is_listing_approval:
            print("  [fill_5461_form] 检测到 Listing Approval 表单，走普通 HTML 字段填写路径...")
            import re as _re2

            # 从 statement_text 提取字段值
            def _extract(pattern, fallback):
                m = _re2.search(pattern, statement_text or "", _re2.IGNORECASE)
                return m.group(1).strip() if m else fallback

            product_title_val = _extract(r"Item name[：:][^\S\r\n]*([^\r\n]+)", f"{brand_name} Screen Protector")
            manufacturer_val  = _extract(r"Manufacturer[：:][^\S\r\n]*([^\r\n]+)", brand_name)
            # Product Description 使用完整声明文本（Brand/Manufacturer/Item name/Category/Color/
            # Specification/description/SKU/Model/品牌授权声明句），与旧 kat-input 路径（见下方
            # fill_5461_form 尾部 SELECTORS['product_description'] 填法）保持一致，不能只截取
            # "Item description" 单行，否则会丢失品牌、类别、SKU、型号和授权声明等关键信息。
            description_val = (statement_text or "").strip() or product_title_val

            print(f"    product_title: {product_title_val[:60]}")
            print(f"    manufacturer:  {manufacturer_val[:60]}")
            print(f"    description:   {description_val[:60]}")

            # Helper: set kat-input value by name using host-element attribute + shadow inner input setter.
            # This is the correct approach for these fields — keyboard.type() misses the panel overlay.
            def _fill_kat_input_by_name(field_name, value):
                """Set kat-input[name=field_name].value and trigger React/Katal change events."""
                result = page.evaluate("""([name, val]) => {
                    let ki = document.querySelector("kat-input[name='" + name + "']");
                    if (!ki) ki = document.querySelector("kat-input#" + name);
                    if (!ki) return {ok: false, reason: "kat-input not found for name=" + name};
                    // Set value on host element
                    ki.setAttribute("value", val);
                    ki.value = val;
                    // Set value on shadow inner input[part="input"]
                    const shadowInput = ki.shadowRoot && ki.shadowRoot.querySelector("input[part='input']");
                    if (shadowInput) {
                        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value");
                        if (setter && setter.set) setter.set.call(shadowInput, val);
                        shadowInput.dispatchEvent(new Event("input", {bubbles: true, composed: true}));
                        shadowInput.dispatchEvent(new Event("change", {bubbles: true, composed: true}));
                        shadowInput.dispatchEvent(new Event("blur", {bubbles: true, composed: true}));
                    }
                    ki.dispatchEvent(new Event("change", {bubbles: true, composed: true}));
                    ki.dispatchEvent(new CustomEvent("change", {bubbles: true, composed: true, detail: {value: val}}));
                    // Read back value to confirm
                    const readBack = ki.value || (shadowInput ? shadowInput.value : "");
                    return {ok: true, value: readBack};
                }""", [field_name, value])
                if result.get("ok"):
                    print(f"    ✓ {field_name[-25:]}: {result.get('value', '')[:50]}")
                else:
                    print(f"    ✗ {field_name}: {result.get('reason', 'unknown error')}")
                return result.get("ok", False)

            # Fill Product title
            _fill_kat_input_by_name('question-cat_auth_mo_question_string_id_product_title', product_title_val)
            result["steps"].append("la_product_title")

            # Fill Manufacturer
            _fill_kat_input_by_name('question-cat_auth_mo_question_string_id_manufacturer', manufacturer_val)
            result["steps"].append("la_manufacturer")

            # Fill Product Description
            _fill_kat_input_by_name('question-cat_auth_mo_question_string_id_product_description', description_val)
            result["steps"].append("la_product_description")

            # Upload images
            if upload_files:
                print(f"  - 上传图片 ({len(upload_files)} 张)...")
                try:
                    # DOM: input[id*="document_upload_new"][type="file"] or data-cy contains "document_upload_new"
                    file_loc = page.locator('input[id*="document_upload_new"][type="file"]').first
                    if file_loc.count() == 0:
                        file_loc = page.locator('input[type="file"]').first
                    if file_loc.count() > 0:
                        file_loc.set_input_files(upload_files)
                        result["steps"].append("la_upload_images")
                        print(f"    图片已上传: {len(upload_files)} 张")
                    else:
                        print("    [警告] 未找到 input[type=file]，跳过上传")
                except Exception as _ue:
                    print(f"    [警告] 图片上传失败: {_ue}")
                time.sleep(random.uniform(1.0, 2.0))

            # Tick all checkboxes in the panel
            try:
                checked_count = page.evaluate(r"""() => {
                    const panel = document.querySelector('kat-panel-wrapper[panel-visible="true"]')
                               || document.querySelector('kat-panel-wrapper[data-testid*="QualificationWidget"]')
                               || document;
                    let count = 0;
                    panel.querySelectorAll('input[type="checkbox"]').forEach(inp => {
                        if (inp.disabled || inp.hasAttribute('disabled')) return;
                        if (!inp.checked) {
                            inp.checked = true;
                            inp.dispatchEvent(new Event('change', {bubbles: true}));
                        }
                        count++;
                    });
                    panel.querySelectorAll('kat-checkbox').forEach(cb => {
                        if (cb.disabled || cb.hasAttribute('disabled')) return;
                        cb.checked = true;
                        cb.dispatchEvent(new Event('change', {bubbles: true}));
                        count++;
                    });
                    return count;
                }""")
                print(f"    复选框已勾选（{checked_count} 个）")
                result["steps"].append("la_checkboxes")
                time.sleep(random.uniform(0.5, 1.0))
            except Exception as _ce:
                print(f"    [警告] 勾选复选框失败: {_ce}")

            # Fill contact email/phone if present
            if account_email:
                try:
                    _fill_kat_input_by_name('email', account_email)
                    result["steps"].append("la_email")
                except Exception as _ee:
                    print(f"    [警告] email 填写失败: {_ee}")
            # phone is optional — leave blank

            return result
    except Exception as _la_e:
        print(f"  [fill_5461_form] Listing Approval 检测异常（继续走标准路径）: {_la_e}")

    # 5461 表单选择器（来自 auto_submit_5461.py）
    SELECTORS = {
        'product_title': 'kat-input#question-cat_auth_mo_question_string_id_product_title',
        'manufacturer': 'kat-input#question-cat_auth_mo_question_string_id_manufacturer',
        'product_description': 'kat-input#question-cat_auth_mo_question_string_id_product_description',
        'email': 'kat-input#contact_info_email_input',
        'phone': 'kat-input#contact_info_phone_input',
        'file_input': 'input[id*="document_upload"][id*="document_input"]',
        'submit_button': 'kat-button#submit_button',
    }
    
    # 0. Defensive guard for direct callers.  The normal entry flow resolves
    # this chooser before calling fill_5461_form, but never keep the historical
    # broad "Application to..." click here: it could select sell-products.
    print("  - 检查是否需要选择申请类型...")
    from .application_type_selection import probe_application_type_options
    application_probe = probe_application_type_options(page)
    application_status = application_probe.get('status')
    if application_status == 'create_new_asins_available':
        from .form_filler import KatalFormFiller
        application_filler = KatalFormFiller(page)
        application_filler.brand_name = brand_name
        application_result = application_filler.select_create_new_asins_application()
        if not application_result.get('fields_ready'):
            raise RuntimeError(
                f"application_type_selection_failed:{application_result.get('status')}"
            )
    elif application_status in {
        'sell_products_only', 'unknown_application_options', 'existing_application',
    }:
        raise RuntimeError(f"application_type_requires_human_review:{application_status}")
    
    # 1. 填写 Product title（从声明文案中提取 Item name，回退到品牌名+Screen Protector）
    print("  - 填写 Product title...")
    item_name_match = re.search(r'Item name[：:][^\S\r\n]*([^\r\n]+)', statement_text)
    if item_name_match:
        product_title = item_name_match.group(1).strip()
        print(f"    从文案提取: {product_title}")
    else:
        product_title = f"{brand_name} Screen Protector"
        print(f"    回退到默认值: {product_title}")
    fill_katal_input(page, SELECTORS['product_title'], product_title)
    time.sleep(0.5)
    result["steps"].append("product_title")
    
    # 2. 填写 Manufacturer
    print("  - 填写 Manufacturer...")
    fill_katal_input(page, SELECTORS['manufacturer'], brand_name)
    time.sleep(0.5)
    result["steps"].append("manufacturer")
    
    # 3. 填写 Product Description（使用声明文本）
    print("  - 填写 Product Description...")
    fill_katal_input(page, SELECTORS['product_description'], statement_text)
    time.sleep(0.5)
    result["steps"].append("product_description")
    
    # 4. 填写 Email
    resolved_email = clean_email(account_email)
    if not resolved_email:
        resolved_email = get_autofill_email_from_page(page, wait_sec=2.0)
        if resolved_email:
            print(f"  - 从页面/浏览器自动获取 Email: {resolved_email}")
    if resolved_email:
        print(f"  - 填写 Email: {resolved_email}")
        fill_katal_input(page, SELECTORS['email'], resolved_email)
        time.sleep(random.uniform(0.6, 1.4))
        result["steps"].append("email")
        result["resolved_email"] = resolved_email
    else:
        print("  - [警告] 未提供/未获取到 Email，跳过填写")
    
    # 5. 上传图片
    if upload_files:
        print(f"  - 上传图片 ({len(upload_files)} 张)...")
        try:
            page.locator(SELECTORS['file_input']).set_input_files(upload_files)
            # 等待上传完成：轮询 file input 的 files 属性或进度消失，最多 5s 早退
            try:
                page.wait_for_function(
                    """() => {
                        const inp = document.querySelector('input[id*="document_upload"][id*="document_input"]');
                        return inp && inp.files && inp.files.length > 0;
                    }""",
                    timeout=5000
                )
            except Exception:
                pass
            result["steps"].append("upload_images")
        except Exception as e:
            print(f"    [警告] 图片上传失败: {e}")
        
        # 无论上传是否成功，都尝试勾选复选框
        print("  - 勾选复选框...")
        try:
            checked_count = page.evaluate('''
                () => {
                    // 仅在 5461 面板内操作，避免误劾左侧 Add Product 表单的复选框
                    const panel = document.querySelector(
                        'kat-panel-wrapper[data-testid*="QualificationWidget"], ' +
                        'kat-panel-wrapper[panel-visible="true"]'
                    );
                    const scope = panel || document;

                    let count = 0;

                    // kat-checkbox：跳过 disabled 和 pointer-events:none 的
                    scope.querySelectorAll('kat-checkbox').forEach(cb => {
                        if (cb.disabled || cb.hasAttribute('disabled')) return;
                        // 检查父元素是否被禁用/遗罩
                        let el = cb.parentElement;
                        while (el && el !== scope) {
                            if (el.disabled || el.hasAttribute('disabled')) return;
                            if (window.getComputedStyle(el).pointerEvents === 'none') return;
                            el = el.parentElement;
                        }
                        cb.checked = true;
                        cb.dispatchEvent(new Event('change', { bubbles: true }));
                        count++;
                    });

                    // 普通 input[type=checkbox]：同样跳过 disabled
                    scope.querySelectorAll('input[type="checkbox"]').forEach(input => {
                        if (input.disabled || input.hasAttribute('disabled')) return;
                        input.checked = true;
                        input.dispatchEvent(new Event('change', { bubbles: true }));
                        count++;
                    });

                    return count;
                }
            ''')
            time.sleep(1)
            print(f"    复选框已勾选（{checked_count} 个）")
        except Exception as e:
            print(f"    [警告] 勾选复选框失败: {e}")
    
    return result


def extract_case_id(page) -> str:
    """
    提取当前有效 Case ID。

    增强点：
    - 支持 success/banner/body 文本中的多种格式；
    - 排除明显的年份、电话、request id；
    - 如果 Case ID 附近出现 declined/rejected/denied，则忽略旧拒绝 Case；
    - 优先选择附近包含 submitted/under review/application 的候选。
    """
    try:
        page_text = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
    except Exception:
        try:
            page_text = page.inner_text('body')
        except Exception:
            return None

    if not page_text:
        return None

    patterns = [
        r'(?:Case\s*(?:ID|Number|#)\s*[-:#]?\s*)([0-9]{9,14})',
        r'(?:Case\s*(?:ID|Number|#)\s*[-:#]?\s*)([A-Za-z0-9]{6,14})',
        r'\b(20(?:2[0-9])\d{7,10})\b',
        r'\b([0-9]{10,14})\b',
    ]

    candidates = []
    seen = set()
    lower = page_text.lower()
    for pattern in patterns:
        for m in re.finditer(pattern, page_text, re.IGNORECASE):
            cid = (m.group(1) if m.groups() else m.group(0)).strip()
            if cid in seen:
                continue
            seen.add(cid)
            if cid.isdigit() and len(cid) == 4:
                continue
            start, end = m.span()
            nearby = lower[max(0, start - 60): min(len(lower), end + 100)]
            if cid.isdigit() and len(cid) >= 10 and cid.startswith('1') and 'case' not in nearby:
                # Amazon EU/UK Case IDs can start with 1 (e.g. "Case ID - 19999999999").
                # Only filter 1-prefixed long numbers when they are not near an explicit Case label.
                continue
            decline_window = lower[max(0, start - 25): min(len(lower), end + 90)]
            if any(w in decline_window for w in ['declined', 'rejected', 'denied']):
                continue
            score = 0
            if 'case' in nearby:
                score += 3
            if any(w in nearby for w in ['submitted', 'under review', 'application', 'request', 'created']):
                score += 2
            if cid.isdigit() and cid.startswith(('2025', '2026')):
                score += 2
            if len(cid) >= 10:
                score += 1
            candidates.append((score, start, cid))

    if not candidates:
        return None
    candidates.sort(key=lambda x: (-x[0], x[1]))
    return candidates[0][2]


def extract_case_ids_with_context(text: str) -> list[dict]:
    """Utility for tests/debugging: return all non-declined Case ID candidates with context."""
    out = []
    if not text:
        return out
    for m in re.finditer(r'(?:Case\s*(?:ID|Number|#)\s*[-:#]?\s*)?\b(20(?:2[0-9])\d{7,10}|[0-9]{10,14})\b', text, re.IGNORECASE):
        cid = m.group(1)
        ctx = text[max(0, m.start() - 60): min(len(text), m.end() + 100)]
        decline_ctx = text[max(0, m.start() - 25): min(len(text), m.end() + 90)]
        if any(w in decline_ctx.lower() for w in ['declined', 'rejected', 'denied']):
            continue
        out.append({'case_id': cid, 'context': ctx})
    return out


def handle_declined_case_application(page, brand_name: str, evidence_dir, filler=None) -> dict:
    """
    处理 Apply to sell 弹窗中显示 Declined 状态的情况。
    点击弹窗内的 ">" 箭头（应用详情链接），尝试发起新申请。

    Returns:
        {
            'success': bool,       # True = 新表单已打开，可继续填写
            'action': str,         # 'new_form_opened' | 'no_link' | 'no_new_form' | 'error'
            'note': str,
        }
    """
    print(f"[DeclinedHandler] 检测到 Declined 申请，尝试点击 '>' 发起新申请...")
    try:
        # 尝试在 QualificationWidget 面板内找到 Declined 卡片右侧的 “>” 链接/按钮并点击。
        # 经验规则（2026-06-06）：Declined 卡片右侧 “>” 会直接新建一个新的 5461 表单；
        # 填完提交后，只有出现新的 Case ID 才算提交成功。旧 declined Case ID 不能算成功。
        nav_url = page.evaluate("""
            () => {
                const panel = document.querySelector(
                    'kat-panel-wrapper[data-testid="kat-panel-wrapper-QualificationWidget"]'
                ) || document.querySelector('kat-panel-wrapper[panel-visible="true"]') || document;

                function visible(el) {
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    const s = window.getComputedStyle(el);
                    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
                }
                function textOf(el) { return (el.textContent || el.getAttribute('label') || el.getAttribute('aria-label') || '').trim(); }
                function clickEl(el, method) {
                    try {
                        el.scrollIntoView({block:'center', inline:'center'});
                        el.dispatchEvent(new MouseEvent('mousedown', {bubbles:true, composed:true}));
                        el.dispatchEvent(new MouseEvent('mouseup', {bubbles:true, composed:true}));
                        el.click();
                        return method;
                    } catch(e) { return null; }
                }

                // 1) 优先找 Declined / Case ID 所在卡片中的右侧 chevron/箭头按钮。
                const all = Array.from(panel.querySelectorAll('*'));
                const declinedNodes = all.filter(el => /declined/i.test(textOf(el)) || /Case\\s*ID/i.test(textOf(el)));
                for (const node of declinedNodes) {
                    let card = node;
                    for (let depth = 0; card && depth < 8; depth++, card = card.parentElement) {
                        const cardText = textOf(card);
                        if (!/declined/i.test(cardText) && !/Case\\s*ID/i.test(cardText)) continue;
                        // 精确入口：Amazon Declined application tile 的右侧 >
                        // DOM: [data-cy$=":application_tile"] [data-cy="application_navigation"]
                        const exactNavs = Array.from(card.querySelectorAll('[data-cy="application_navigation"], kat-icon[data-cy="navigation_icon"][name="keyboard_arrow_right"]')).filter(visible)
                            .sort((a,b) => b.getBoundingClientRect().right - a.getBoundingClientRect().right);
                        for (const nav of exactNavs) {
                            const nr = nav.getBoundingClientRect();
                            const x = nr.left + nr.width / 2;
                            const y = nr.top + nr.height / 2;
                            const target = document.elementFromPoint(x, y) || nav;
                            const r = clickEl(target, 'clicked_declined_data_cy_application_navigation');
                            if (r) return r;
                            const rp = clickEl(nav, 'clicked_declined_data_cy_navigation_self');
                            if (rp) return rp;
                            const rpp = clickEl(nav.parentElement || nav, 'clicked_declined_data_cy_navigation_parent');
                            if (rpp) return rpp;
                        }

                        // Declined 卡片右侧 “>” 是一个空文本 kat-icon，位于卡片顶部最右侧；
                        // 下方 Case ID 行也有 kat-icon，但那不是新建表单入口，必须排除。
                        const rct = card.getBoundingClientRect();
                        const topRightIcons = Array.from(card.querySelectorAll('kat-icon')).filter(visible).filter(icon => {
                            const ir = icon.getBoundingClientRect();
                            const cls = String(icon.className || '');
                            return !cls.includes('alert-status') && ir.top < (rct.top + Math.min(34, rct.height * 0.55));
                        }).sort((a,b) => b.getBoundingClientRect().right - a.getBoundingClientRect().right);
                        if (topRightIcons.length > 0) {
                            const icon = topRightIcons[0];
                            const ir = icon.getBoundingClientRect();
                            const x = ir.left + ir.width / 2;
                            const y = ir.top + ir.height / 2;
                            const target = document.elementFromPoint(x, y) || icon;
                            const r = clickEl(target, 'clicked_declined_top_right_chevron_icon');
                            if (r) return r;
                            const rp = clickEl(icon.parentElement || icon, 'clicked_declined_top_right_chevron_parent');
                            if (rp) return rp;
                        }

                        const controls = Array.from(card.querySelectorAll('a,button,kat-button,kat-icon-button,kat-link,[role="button"],[tabindex]')).filter(visible);
                        // 右侧 > 通常是卡片最右侧的可点击控件，文本可能为空/›/>/chevron。
                        controls.sort((a,b) => b.getBoundingClientRect().right - a.getBoundingClientRect().right);
                        for (const c of controls) {
                            const t = textOf(c).toLowerCase();
                            const href = c.href || c.getAttribute('href') || '';
                            const aria = (c.getAttribute('aria-label') || '').toLowerCase();
                            if (t.includes('close') || aria.includes('close')) continue;
                            if (href) {
                                const r = clickEl(c, href);
                                if (r) return r;
                            }
                            if (t === '>' || t === '›' || t === '»' || aria.includes('details') || aria.includes('open') || aria.includes('view') || controls.length === 1) {
                                const r = clickEl(c, 'clicked_declined_card_chevron');
                                if (r) return r;
                            }
                        }
                        // 如果 > 没触发，按用户经验尝试点卡片文字区域：Declined 或 Application to create new ASINs for...
                        const textTargets = Array.from(card.querySelectorAll('*')).filter(visible).filter(el => {
                            const t = textOf(el);
                            return /declined/i.test(t) || /Application to create new ASINs for/i.test(t);
                        }).sort((a,b) => {
                            // 优先较小且靠上的文字节点，避免点到下方 Case ID 行
                            const ar = a.getBoundingClientRect(), br = b.getBoundingClientRect();
                            return (ar.top - br.top) || (ar.width * ar.height - br.width * br.height);
                        });
                        for (const tEl of textTargets) {
                            const tr = tEl.getBoundingClientRect();
                            if (tr.top >= rct.top && tr.bottom <= rct.bottom) {
                                const r = clickEl(tEl, 'clicked_declined_text_or_application_title');
                                if (r) return r;
                            }
                        }

                        // 如果没找到明确控件，点击卡片最右侧区域；很多 Amazon 卡片整行可点。
                        if (rct.width > 80 && rct.height > 20) {
                            const x = rct.right - 24;
                            const y = rct.top + rct.height / 2;
                            const target = document.elementFromPoint(x, y);
                            if (target && card.contains(target)) {
                                const r = clickEl(target, 'clicked_declined_card_right_edge');
                                if (r) return r;
                            }
                            const r = clickEl(card, 'clicked_declined_card_row');
                            if (r) return r;
                        }
                    }
                }

                function searchClick(el) {
                    if (!el) return null;
                    const tag = (el.tagName || '').toUpperCase();
                    const href = el.href || el.getAttribute('href') || '';
                    if ((tag === 'A' || tag === 'KAT-LINK') &&
                        (href.includes('applicationId') || href.includes('approvalrequest') ||
                         href.includes('case-id') || href.includes('sellerCentral'))) {
                        return clickEl(el, href);
                    }
                    if (el.shadowRoot) {
                        for (const c of el.shadowRoot.children) {
                            const r = searchClick(c);
                            if (r) return r;
                        }
                    }
                    for (const c of el.children || []) {
                        const r = searchClick(c);
                        if (r) return r;
                    }
                    return null;
                }

                function findAndClickRow(root) {
                    function walk(el) {
                        if (!el) return null;
                        const text = (el.textContent || '').trim();
                        const tag = (el.tagName || '').toUpperCase();
                        if (text.includes('Application to create') &&
                            (tag.startsWith('KAT-') || tag === 'LI' || tag === 'DIV' || tag === 'A') &&
                            text.length < 500) {
                            const r = clickEl(el, el.href || el.getAttribute('href') || 'clicked_row');
                            if (r) return r;
                        }
                        if (el.shadowRoot) {
                            for (const c of el.shadowRoot.children) {
                                const r = walk(c); if (r) return r;
                            }
                        }
                        for (const c of el.children || []) {
                            const r = walk(c); if (r) return r;
                        }
                        return null;
                    }
                    return walk(root);
                }

                const r = searchClick(panel);
                if (r) return r;
                const r2 = findAndClickRow(panel);
                if (r2) return r2;

                const links = document.querySelectorAll(
                    'a[href*="applicationId"], a[href*="approvalrequest"], kat-link[href*="applicationId"]'
                );
                if (links.length > 0) return clickEl(links[0], links[0].href || links[0].getAttribute('href'));

                return null;
            }
        """)

        if not nav_url:
            print("[DeclinedHandler] 未找到/未点中 Declined 卡片右侧 '>'，停止自动恢复并保留页面")
            return {'success': False, 'action': 'no_chevron', 'note': "未找到/未点中 Declined 卡片右侧 '>'，无法新建表单"}

        print(f"[DeclinedHandler] 已点击链接: {nav_url}")
        # 等待页面导航完成（URL 变化或加载 domcontentloaded），最多 8s
        try:
            page.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception:
            pass

        # 截图记录（异步，不阻塞后续状态检查）
        try:
            shot = evidence_dir / "declined_nav.png"
            _async_screenshot(page, str(shot), full_page=False, timeout=10000)
        except Exception:
            pass

        # 检查页面状态
        state = check_page_state(page)
        print(f"[DeclinedHandler] 跳转后页面状态: {state['page_type']} | URL: {page.url}")

        if state['page_type'] in ('5461_FORM', '5461_FORM_OPEN'):
            print("[DeclinedHandler] ✅ 已进入 5461 表单，检查字段是否真实加载...")
            if filler and not wait_for_5461_form_fields(page, timeout_sec=24, interval_sec=2):
                recovered = recover_half_loaded_5461_panel(
                    page, filler, brand_name, max_attempts=3,
                    reason="declined > clicked but 5461 fields missing"
                )
                if not recovered:
                    return {
                        'success': False,
                        'action': 'half_loaded_panel',
                        'note': 'Declined 点击 > 后进入半加载 5461 panel，close-panel 重试后字段仍未加载'
                    }
            return {'success': True, 'action': 'new_form_opened', 'note': '跳转后进入 5461 表单且字段可用'}

        # 尝试在新页面上找 "Submit new application" 或类似按钮
        new_btn = page.evaluate("""
            () => {
                const keywords = [
                    'submit new', 'new application', 'apply again', 'reapply',
                    'start new', 'create new', 'new request', '重新申请', '发起新申请'
                ];
                function tryClick(el) {
                    const text = (
                        el.textContent || el.getAttribute('label') || el.getAttribute('value') || ''
                    ).toLowerCase().trim();
                    for (const kw of keywords) {
                        if (text.includes(kw)) { el.click(); return text; }
                    }
                    if (el.shadowRoot) {
                        for (const c of el.shadowRoot.children) {
                            const r = tryClick(c); if (r) return r;
                        }
                    }
                    for (const c of el.children) {
                        const r = tryClick(c); if (r) return r;
                    }
                    return null;
                }
                return tryClick(document.body);
            }
        """)

        if new_btn:
            print(f"[DeclinedHandler] 找到并点击新申请按钮: {new_btn}")
            # 等待页面状态变化（5461表单出现 or URL变化），最多 3s 早退
            try:
                page.wait_for_function(
                    """() => {
                        const url = window.location.href;
                        const p = document.querySelector('kat-panel-wrapper[panel-visible="true"]');
                        return url.includes('sq/approvalrequest') || p !== null;
                    }""",
                    timeout=3000
                )
            except Exception:
                pass
            state2 = check_page_state(page)
            print(f"[DeclinedHandler] 点击后状态: {state2['page_type']}")
            opened = state2['page_type'] in ('5461_FORM', '5461_FORM_OPEN')
            if opened and filler and not wait_for_5461_form_fields(page, timeout_sec=24, interval_sec=2):
                opened = recover_half_loaded_5461_panel(
                    page, filler, brand_name, max_attempts=3,
                    reason="declined new application button opened half-loaded panel"
                )
            return {
                'success': opened,
                'action': 'new_form_opened' if opened else 'half_loaded_panel',
                'note': f'点击新申请按钮后状态: {state2["page_type"]}' + ('' if opened else '，字段未加载'),
            }

        # ★ 2026-07-25: 点击 ► 后页面回到 Apply to sell 入口状态（NEEDS_APPROVAL_NEW_UI）。
        # 这说明 Amazon 把 Declined 卡片的 ► 箭头处理为"重置状态回到初始申请页面"，
        # 而不是直接进入新 5461 表单。此时只需要再次点击 Apply to sell 即可进入新表单。
        if state['page_type'] == 'NEEDS_APPROVAL_NEW_UI' and filler is not None:
            print("[DeclinedHandler] 跳转后状态为 NEEDS_APPROVAL_NEW_UI，再次触发 Apply to sell 进入新表单...")
            import time as _time
            _time.sleep(3)
            try:
                apply_ok = filler.click_apply_to_sell()
            except Exception as _ae:
                print(f"[DeclinedHandler] 二次 Apply to sell 异常: {_ae}")
                apply_ok = False
            if apply_ok:
                print("[DeclinedHandler] ✅ 二次 Apply to sell 成功，5461 表单字段可用")
                return {'success': True, 'action': 'new_form_opened',
                        'note': 'Declined ► 后回到 NEEDS_APPROVAL_NEW_UI，二次 Apply to sell 进入新表单'}
            # 检查是否 Declined again（新申请又回到 Declined，可能是旧申请 still pending）
            if getattr(filler, 'declined_case_id', None):
                new_declined = filler.declined_case_id
                print(f"[DeclinedHandler] 二次 Apply to sell 后又检测到 Declined (Case ID: {new_declined})，停止递归")
            print("[DeclinedHandler] 二次 Apply to sell 后仍未进入新表单，停止自动恢复")
            return {
                'success': False,
                'action': 'no_new_form',
                'note': f'Declined ► 后 NEEDS_APPROVAL_NEW_UI 状态，二次 Apply to sell 失败（未获得真实字段）',
            }

        return {
            'success': False,
            'action': 'no_new_form',
            'note': f'跳转到 {page.url} 后未找到新申请入口（状态: {state["page_type"]}）',
        }

    except Exception as e:
        print(f"[DeclinedHandler] 出错: {e}")
        return {'success': False, 'action': 'error', 'note': str(e)}


def reset_page_for_next_brand(page: Page, cooldown_sec: int = 0) -> dict:
    """
    提交成功拿到 Case ID 后的清理函数。

    Amazon 新 UI 在同一标签页上预渲染多个 kat-panel 实例，提交成功后
    会把下一个品牌的面板内容预填入另一个 panel 并设为 visible。
    关闭按钮无法可靠清理这些预渲染状态，同 tab 复用会导致下一品牌
    误读上一品牌的 panel 内容。

    策略：始终返回 ready=False，强制 batch 侧为每个品牌新建独立标签页。
    """
    return {"ready": False, "reason": "disabled: new tab per brand for reliability", "url": ""}


def submit_5461_from_add_product(
    cdp_url: str,
    account_id: str,
    marketplace: str,
    brand_name: str,
    add_product_url: str,
    statement_text: str,
    upload_files: list,
    evidence_root: str,
    require_human_confirm: bool = False,  # 默认自动提交
    item_type_keyword: str = "cell-phone-screen-protectors",
    account_email: str = "",
    keep_browser_open: bool = False,  # 批量模式下保持浏览器开启
    page: Optional[Page] = None,  # 复用已有页面实例
    skip_market_switch: bool = False,  # 跳过市场切换（已在正确市场时使用）
    no_submit: bool = False,  # dry-run：真实走流程到提交前一步，绝不点击提交
    skip_add_product_goto: bool = False,  # 跳过 goto(add_product_url)，页面已就位时使用
):
    """
    从 Add Product 页面开始，自动流转到 5461 表单并提交
    
    修复：添加页面状态检测，处理品牌已有权限的情况

    no_submit=True（dry-run 语义）：真实走完市场切换、Add Product、授权面板、
    5461 表单填写、文件上传与截图留证，但在任何提交按钮点击前短路返回，
    跳过 Case ID 轮询，返回 submit_result="dry_run"。
    """
    from .form_filler import KatalFormFiller
    
    evidence_dir = build_evidence_dir(evidence_root, account_id, brand_name, "submit_full")
    
    result = {
        "submit_result": "failed",
        "case_id": None,
        "note": "",
        "evidence_files": [],
        "steps": [],
        "retrigger_results": [],
        "marketplace_switched": False,
        "actual_marketplace": None,
    }
    
    # 加载账号配置（用于获取 mons_sel_mkid 等）
    from .config_loader import load_account
    acc = load_account("config/accounts.json", account_id) or {}
    
    owns_page = page is None

    def _run_core(page):
        nonlocal marketplace
        from pathlib import Path
        import json
        
        # 设置 console 监听，捕获 error/warning
        console_logs = []
        def handle_console(msg):
            if msg.type in ('error', 'warning'):
                console_logs.append(f"[{msg.type}] {msg.text}")
        page.on("console", handle_console)
        
        # 添加唯一 dialog 处理器，避免 JS alert/confirm 卡住页面。
        # 注意：不要在外层/批处理器重复绑定；双 handler 会导致
        # ProtocolError: Page.handleJavaScriptDialog: No dialog is showing
        def _safe_dismiss_dialog(d):
            try:
                d.dismiss()
            except Exception as e:
                print(f"[dialog] 忽略已处理/已关闭的 dialog: {e}")
        page.on("dialog", _safe_dismiss_dialog)
        
        def _dry_run_no_submit_return(phase, path_desc):
            """no_submit(dry-run) 统一短路：留证截图、记录 step、返回 dry_run，绝不点击提交。"""
            result["steps"].append({"phase": phase, "action": "click_submit", "status": "skipped_no_submit"})
            try:
                shot_ns = evidence_dir / "no_submit_before_submit.png"
                _async_screenshot(page, str(shot_ns), full_page=True, timeout=10000)
                result["evidence_files"].append(str(shot_ns))
            except Exception as _shot_err:
                print(f"[警告] no_submit 截图失败: {_shot_err}")
            result["submit_result"] = "dry_run"
            result["note"] = f"dry-run：已走到提交前一步，未点击提交（{path_desc}）"
            print(f"[DRY-RUN] no_submit=True：{path_desc}，跳过点击提交与 Case ID 轮询")
            return result

        try:
            # 阶段 0: 切换目标国家市场
            if skip_market_switch:
                print(f"\n[阶段 0] 跳过市场切换（skip_market_switch=True），当前市场: {marketplace}")
                actual_marketplace = marketplace
                result["marketplace_switched"] = True
                result["actual_marketplace"] = actual_marketplace
            else:
                from .marketplace_switcher import MarketplaceSwitcher
                
                switcher = MarketplaceSwitcher(page, evidence_dir=str(evidence_dir))
                # 直接切换到指定市场，不再智能选择
                success, actual_marketplace = switcher.switch_to_marketplace(
                    target=marketplace,
                    max_retries=3
                )
                
                if success:
                    print(f"\n[阶段 0] ✅ 成功切换到 {actual_marketplace}")
                    result["marketplace_switched"] = True
                    result["actual_marketplace"] = actual_marketplace
                    if actual_marketplace != marketplace:
                        print(f"[阶段 0] 注意: 请求市场 {marketplace}，实际切换到 {actual_marketplace}")
                        marketplace = actual_marketplace
                else:
                    print(f"\n[阶段 0] ⚠️ 市场切换失败: {actual_marketplace}")
                    raise RuntimeError(f"无法切换到目标市场 {marketplace}: {actual_marketplace}")
            
            # 保存 mkid（如果 URL 中有）
            current_url = page.url
            mkid_match = re.search(r'mons_sel_mkid=([^&]+)', current_url)
            if mkid_match:
                mkid = mkid_match.group(1)
                print(f"[阶段 0] 提取到 mkid: {mkid}")
                try:
                    from .config_loader import resolve_accounts_path
                    accounts_path = resolve_accounts_path("config/accounts.json")
                    accounts_data = json.loads(accounts_path.read_text(encoding="utf-8"))
                    for a in accounts_data.get("accounts", []):
                        if a.get("account_id") == account_id:
                            a["mons_sel_mkid"] = mkid
                            break
                    accounts_path.write_text(json.dumps(accounts_data, ensure_ascii=False, indent=2), encoding="utf-8")
                    print(f"[阶段 0] [AUTO-SAVE] 已保存 mkid 到 accounts.json")
                except Exception as e:
                    print(f"[阶段 0] [警告] 保存 mkid 失败: {e}")
            
            # 阶段 1: 填写 Add Product 表单
            print("\n[阶段 1/3] 填写 Add Product 表单...")
            if skip_add_product_goto:
                # 页面复用模式：面板已由调用方关闭，页面停在 Product Identity，直接重填字段
                print("[阶段 1/3] 页面复用模式：跳过 goto，直接在当前页面重填 Brand Name 等字段")
                try:
                    page.wait_for_function(
                        """() => document.readyState === 'complete' && document.body.innerText.length > 200""",
                        timeout=5000
                    )
                except Exception:
                    pass
            else:
                # 若市场切换失败后页面仍停在 account-switcher，先重置到 about:blank 避免导航被截断
                try:
                    if 'account-switcher' in page.url or 'merchantMarketplace' in page.url:
                        print("[阶段 1/3] 检测到页面停留在 account-switcher，先重置页面...")
                        page.goto('about:blank', wait_until='commit', timeout=8000)
                        time.sleep(2)
                except Exception:
                    pass
                # ERR_ABORTED / 超时 重试（wait_until=commit 更快，避免 JS 重定向引起 timeout）
                for nav_attempt in range(3):
                    try:
                        page.goto(add_product_url, wait_until="commit", timeout=60000)
                        page.wait_for_load_state("domcontentloaded", timeout=60000)
                        break
                    except Exception as nav_err:
                        err_str = str(nav_err)
                        if ("ERR_ABORTED" in err_str or "Timeout" in err_str) and nav_attempt < 2:
                            print(f"[阶段 1/3] 页面导航被中断，等待 15 秒后重试 ({nav_attempt+1}/3)...")
                            time.sleep(15)
                        else:
                            raise
                # 等待页面关键内容出现（避免固定 8s），最多等 8s 早退
                try:
                    page.wait_for_function(
                        """() => {
                            const url = window.location.href;
                            // 已到目标页面且有内容
                            return document.readyState === 'complete' &&
                                   !url.includes('account-switcher') &&
                                   document.body.innerText.length > 200;
                        }""",
                        timeout=8000
                    )
                except Exception:
                    pass

                # [2026-05-18] 检测 Amazon 服务端错误页面并自动恢复
                # 经验: DEMO_HOME 成功后，DEMO_WILL/DEMO_JADE/MP-MALL 全部遇到服务端错误，导致连锁失败
                print("[阶段 1/3] 检查页面状态...")
                recovery_result = ensure_page_ready(
                    page=page,
                    target_url=add_product_url,
                    max_retries=3,
                    wait_seconds=90,
                )
                if not recovery_result['success']:
                    print(f"[阶段 1/3] ❌ Amazon 服务端错误页面持续存在，无法恢复: {recovery_result['error']}")
                    result["status"] = "failed"
                    result["error"] = f"Amazon SERVER_ERROR: {recovery_result['error']}"
                    result["steps"].append({"phase": 1, "action": "server_error_recovery", "result": recovery_result})
                    # 保存证据（异步，下方立即 return）
                    try:
                        error_shot = evidence_dir / "server_error.png"
                        _async_screenshot(page, str(error_shot), full_page=False, timeout=10000)
                        result["evidence_files"].append(str(error_shot))
                    except:
                        pass
                    return result
                elif recovery_result['retries'] > 0:
                    print(f"[阶段 1/3] ✅ 从服务端错误页面恢复成功（重试 {recovery_result['retries']} 次）")
            
            filler = KatalFormFiller(page)
            filler.brand_name = brand_name
            
            # 从声明文案中提取 Item Name
            item_name = extract_item_name_from_statement(statement_text)
            if not item_name:
                item_name = f"{brand_name} Screen Protector"
            print(f"[阶段 1/3] 使用 Item Name: {item_name}")
            
            fill_results = filler.fill_product_identity_form(
                brand_name,
                item_name=item_name,
                item_type_hint=item_type_keyword,
            )
            result["steps"].append({"phase": 1, "action": "fill_add_product", "results": fill_results})
            
            # [阶段 1] 证据采集：Add Product 页面加载完成
            capture_evidence_safe(
                page, EVIDENCE_NODES["add_product_start"],
                root=evidence_root, account=account_id, brand=brand_name
            )

            # 保存截图（异步，不阻塞后续 Brand Name 验证）
            try:
                shot1 = evidence_dir / "01_add_product_filled.png"
                _async_screenshot(page, str(shot1), full_page=False, timeout=10000)
                result["evidence_files"].append(str(shot1))
            except Exception as e:
                print(f"[警告] 截图失败: {e}")
            
            # 关键修复：在点击 Next 之前，确保 Brand Name 正确且复选框状态正确
            print("[阶段 1/3] 最终验证 Brand Name 和复选框...")
            
            # 1. 确保 "no brand name" 复选框是未勾选的
            page.evaluate('''() => {
                const checkboxes = document.querySelectorAll('kat-checkbox');
                for (let cb of checkboxes) {
                    const label = cb.getAttribute('label') || cb.textContent || '';
                    if (label.includes('does not have a brand name')) {
                        let isChecked = false;
                        if (cb.shadowRoot) {
                            const inner = cb.shadowRoot.querySelector('div[role="checkbox"]');
                            if (inner) isChecked = inner.getAttribute('aria-checked') === 'true';
                        }
                        if (isChecked) {
                            cb.checked = false;
                            cb.dispatchEvent(new Event('change', { bubbles: true }));
                            if (cb.shadowRoot) {
                                const inner = cb.shadowRoot.querySelector('div[role="checkbox"]');
                                if (inner) {
                                    inner.click();
                                    inner.setAttribute('aria-checked', 'false');
                                }
                            }
                        }
                        return { action: 'unchecked', label: label, wasChecked: isChecked };
                    }
                }
                return { action: 'not_found' };
            }''')
            time.sleep(1)
            
            # 2. 强制设置 Brand Name（使用更强制的方法）
            # 兼容新旧UI
            page.evaluate('''(brand) => {
                // 尝试新UI选择器，然后旧UI
                let el = document.querySelector('kat-input[name="brand-0-value"]');
                if (!el) el = document.querySelector('kat-input[name="brand"]');
                if (!el) return 'not_found';
                
                // 方法1: 直接设置值
                el.value = brand;
                el.setAttribute('value', brand);
                
                // 方法2: 设置 Shadow DOM 内部值
                if (el.shadowRoot) {
                    const input = el.shadowRoot.querySelector('input');
                    if (input) {
                        input.value = brand;
                        input.setAttribute('value', brand);
                        input.dispatchEvent(new Event('input', { bubbles: true }));
                        input.dispatchEvent(new Event('change', { bubbles: true }));
                        input.dispatchEvent(new Event('blur', { bubbles: true }));
                    }
                }
                
                // 方法3: 触发外层事件
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                el.dispatchEvent(new Event('blur', { bubbles: true }));
                
                // 方法4: 聚焦并输入
                el.focus();
                
                return { value: el.value, method: 'forced' };
            }''', brand_name)
            time.sleep(2)
            
            # 3. 再次验证
            current_brand = page.evaluate('''() => {
                let el = document.querySelector('kat-input[name="brand-0-value"]');
                if (!el) el = document.querySelector('kat-input[name="brand"]');
                return el ? { value: el.value, state: el.state } : null;
            }''')
            print(f"[阶段 1/3] 验证结果: {current_brand}")
            
            # 4. 如果还是 Genérico，尝试使用 keyboard 输入
            brand_value = current_brand.get('value', '') if isinstance(current_brand, dict) else current_brand
            if brand_value and 'genérico' in str(brand_value).lower():
                print("[阶段 1/3] Brand Name 仍是 Genérico，尝试 keyboard 输入...")
                # 兼容新旧UI
                brand_input = page.locator('kat-input[name="brand-0-value"]').first
                if brand_input.count() == 0:
                    brand_input = page.locator('kat-input[name="brand"]').first
                if brand_input.count() > 0:
                    brand_input.click(force=True)
                    time.sleep(0.5)
                    page.keyboard.press('Control+a')
                    time.sleep(0.2)
                    page.keyboard.press('Delete')
                    time.sleep(0.2)
                    filler.type_like_human(brand_name)
                    time.sleep(1)
                    page.keyboard.press('Tab')
                    time.sleep(1)
            
            # 5. 429 检测：如果页面有 429 错误，等待后重试
            has_429 = page.evaluate('''() => {
                return document.body.innerText.includes('429') || 
                       document.body.innerText.includes('Too Many Requests') ||
                       document.title.includes('429');
            }''')
            if has_429:
                print("[警告] 检测到 429 限流，等待 60 秒...")
                time.sleep(60)
            
            # 6. 立即点击 Next，不给 Amazon 重置 Brand Name 的机会
            print("[阶段 1/3] 立即点击 Next（防止 Amazon 重置 Brand Name）...")
            
            # 先关闭可能的弹窗（如 "Track your progress" 引导教程）
            print("[阶段 1/3] 关闭可能的引导弹窗...")
            try:
                # 方法1: 按 Escape 键关闭
                page.keyboard.press('Escape')
                time.sleep(1)
                
                # 方法2: 使用 Playwright locator 点击 Dismiss 按钮
                dismiss_btn = page.locator('kat-button[data-testid="newCreate-listing-experience-guide-tour-guide-dismiss-button"]').first
                if dismiss_btn.count() > 0:
                    # 通过 evaluate 点击 Shadow DOM 内部按钮
                    page.evaluate('''() => {
                        const katBtn = document.querySelector('kat-button[data-testid="newCreate-listing-experience-guide-tour-guide-dismiss-button"]');
                        if (katBtn && katBtn.shadowRoot) {
                            const btn = katBtn.shadowRoot.querySelector('button');
                            if (btn) {
                                btn.click();
                                return { clicked: true };
                            }
                        }
                        return { clicked: false };
                    }''')
                    print("[阶段 1/3] 已点击 Dismiss 按钮")
                    time.sleep(2)
                
                # 方法3: 直接移除整个弹窗 DOM
                page.evaluate('''() => {
                    const selectors = [
                        '.tour-content-container',
                        '.tour-overlay',
                        '[class*="tour-content"]',
                        'kat-modal'
                    ];
                    let removed = false;
                    for (const sel of selectors) {
                        const el = document.querySelector(sel);
                        if (el) {
                            el.remove();
                            removed = true;
                        }
                    }
                    // 同时移除可能的遮罩层
                    const overlays = document.querySelectorAll('[class*="overlay"], [class*="backdrop"]');
                    for (const o of overlays) {
                        o.remove();
                        removed = true;
                    }
                    return { removed };
                }''')
                print("[阶段 1/3] 已清理弹窗元素")
                time.sleep(1)
            except Exception as e:
                print(f"[阶段 1/3] 关闭弹窗时出错: {e}")
            
            # Final UI-specific normalization before Continue/Next. The filler owns
            # Add Product new-vs-legacy field handling; this keeps both UI paths
            # coexisting without duplicating selector blocks in the submit flow.
            add_product_ui = filler.detect_add_product_ui()
            ready_results = filler.ensure_add_product_ready_for_continue(
                add_product_ui,
                item_type_hint=item_type_keyword,
            )
            result["steps"].append({"phase": 1, "action": "pre_continue_normalize", "ui": add_product_ui, "results": ready_results})
            print(f"[阶段 1/3] Add Product UI 类型: {add_product_ui}；预提交检查: {ready_results}")
            
            # 使用 JavaScript 直接点击 Continue 或 Next
            # 兼容新旧UI
            print("[阶段 1/3] 点击 Continue/Next...")
            click_result = page.evaluate("""() => {
                // 尝试多种选择器（新旧UI兼容）
                let btn = document.querySelector('kat-button[data-testid="continue-button"]');
                if (!btn) btn = document.querySelector('kat-button#continue-to-description-button');
                if (!btn) btn = document.querySelector('kat-button#next-button');
                if (!btn) btn = document.querySelector('kat-button[kat-aria-label="Next"]');
                if (!btn) btn = document.querySelector('kat-button[kat-aria-label="Continue to Description"]');
                if (!btn) {
                    const allBtns = document.querySelectorAll('kat-button');
                    for (const b of allBtns) {
                        const text = (b.getAttribute('label') || b.textContent || '').toLowerCase();
                        if (text === 'next' || text.includes('continue')) {
                            btn = b;
                            break;
                        }
                    }
                }
                // New listing UI: left sidebar Submit is the real continue/validate CTA for this step.
                if (!btn) btn = document.querySelector('kat-button#form-submit-button[data-testid="submit-button"]');
                if (btn) {
                    btn.click();
                    if (btn.shadowRoot) {
                        const inner = btn.shadowRoot.querySelector('button');
                        try { if (inner) inner.click(); } catch(e) {}
                    }
                    return { clicked: true, selector: btn.id || btn.getAttribute('data-testid') || '', text: btn.getAttribute('label') || btn.textContent };
                }
                return { clicked: false };
            }""")
            print(f"[阶段 1/3] Continue/Next 点击结果: {click_result}")
            # 等待页面状态变化（URL 变化 或 product_identity 页面 DOM 更新），最多 10s
            try:
                page.wait_for_function(
                    """() => {
                        const url = window.location.href;
                        return url.includes('seller-qualification') ||
                               url.includes('description') ||
                               url.includes('listing/workflow') ||
                               document.querySelector('kat-panel-wrapper[panel-visible="true"]') !== null;
                    }""",
                    timeout=10000
                )
            except Exception:
                pass
            
            # 检查页面状态（关键修复！）
            state = check_page_state(page)
            print(f"[阶段 1/3] 页面状态: {state['page_type']}")
            print(f"[阶段 1/3] 当前 URL: {state['url']}")
            
            # [阶段 1] 证据采集：表单填写后/点击 Next 后
            capture_evidence_safe(
                page, EVIDENCE_NODES["after_fill"],
                root=evidence_root, account=account_id, brand=brand_name
            )

            # 保存点击后的截图用于调试（异步，不阻塞品牌选择流程）
            try:
                shot_after_next = evidence_dir / "02_after_next.png"
                _async_screenshot(page, str(shot_after_next), full_page=False, timeout=10000)
                result["evidence_files"].append(str(shot_after_next))
            except Exception as e:
                print(f"[警告] 截图失败: {e}")
            
            # 如果进入品牌选择页面，处理品牌选择
            if state['page_type'] == 'BRAND_SELECTION':
                print("\n[阶段 1.5] 进入品牌选择页面，处理品牌选择...")
                
                # 获取品牌关键词（从 manifest 或传入）
                brand_keywords = None
                # 尝试从 manifest 加载
                manifest_path = Path(f"brand_packs/{brand_name}/manifest.json")
                if manifest_path.exists():
                    try:
                        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
                        brand_keywords = manifest.get('brand_selection_keywords', [])
                        print(f"[阶段 1.5] 品牌关键词: {brand_keywords}")
                    except Exception as e:
                        print(f"[阶段 1.5] 加载 manifest 失败: {e}")
                
                selection_success = handle_brand_selection(page, brand_name, brand_keywords)
                result["steps"].append({"phase": 1.5, "action": "brand_selection", "success": selection_success})
                
                if selection_success:
                    # 重新检查页面状态
                    state = check_page_state(page)
                    # The brand chooser can disappear before the new approval panel
                    # finishes replacing its loading skeleton. The underlying page
                    # still contains the old Brand Clarification warning, so a quick
                    # state read may incorrectly remain BRAND_SELECTION. Wait for
                    # authoritative form fields and recover the half-loaded panel
                    # instead of selecting the brand a second time.
                    state, connect_fields_ready = resolve_connect_brand_loading_state(
                        page, filler, brand_name, state
                    )
                    print(f"[阶段 1.5] 品牌选择后页面状态: {state['page_type']}")
                    # Connect brand 后 panel 已打开，跳过 Apply to sell，直接填写表单
                    if state['page_type'] in ('5461_PANEL_SHELL', '5461_FORM_OPEN'):
                        print("[阶段 1.5] Connect brand 后 5461 panel/form 已打开，跳过 Apply to sell，直接进入表单填写...")
                        if not connect_fields_ready and not wait_for_5461_form_fields(page, timeout_sec=24, interval_sec=2):
                            recovered = recover_half_loaded_5461_panel(
                                page, filler, brand_name, max_attempts=3,
                                reason="Connect brand 后字段未加载"
                            )
                            if not recovered:
                                result["submit_result"] = "failed"
                                result["note"] = "Connect brand 后 5461 panel 字段未加载"
                                return result
                        form_result = fill_5461_form(page, brand_name, statement_text, upload_files, account_email)
                        result["steps"].append({"phase": 1.5, "action": "fill_5461_after_connect_brand", "details": form_result})
                        if no_submit:
                            return _dry_run_no_submit_return(1.5, "Connect brand 早路径 阶段1.5")
                        print("[阶段 1.5] 点击提交...")
                        click_katal_button(page, 'kat-button#submit_button')
                        # 与阶段3保持一致：轮询等待 Case ID，最多90秒
                        print("[阶段 1.5] 等待 Case ID 出现（最多90秒）...")
                        case_id = None
                        max_wait = 90
                        poll_interval = 3
                        elapsed = 0
                        while elapsed < max_wait:
                            case_id = extract_case_id(page)
                            if case_id:
                                print(f"[✓] 提取到 Case ID: {case_id}")
                                break
                            page_text_poll = ""
                            try:
                                page_text_poll = page.inner_text('body')
                            except Exception:
                                pass
                            if page_text_poll and "error" in page_text_poll.lower() and "fail" in page_text_poll.lower():
                                print("[✗] 页面显示错误信息")
                                break
                            time.sleep(poll_interval)
                            elapsed += poll_interval
                        if case_id:
                            result["submit_result"] = "success"
                            result["case_id"] = case_id
                            result["note"] = "Connect brand 后直接提交成功"
                        else:
                            result["submit_result"] = "failed"
                            result["note"] = "Connect brand 后提交未获得 Case ID"
                        return result
                else:
                    result["submit_result"] = "failed"
                    result["note"] = "品牌选择失败"
                    return result
            
            # 如果品牌被封锁，无法申请
            if state['brand_blocked']:
                print("\n[!] 品牌被 Amazon 封锁，无法申请销售权限！")
                result["submit_result"] = "blocked"
                result["note"] = "品牌被Amazon封锁，此账号无法申请该品牌"
                result["steps"].append({"phase": 2, "action": "check_permission", "status": "brand_blocked"})
                
                shot2 = evidence_dir / "02_brand_blocked.png"
                _async_screenshot(page, str(shot2), full_page=True, timeout=10000)
                result["evidence_files"].append(str(shot2))
                return result
            
            # 如果已进入 Description 页面，说明已有权限
            if state['has_permission']:
                print("\n[OK] 品牌已有销售权限，无需提交 5461！")
                result["submit_result"] = "success"
                result["note"] = "品牌已有销售权限，无需5461"
                result["steps"].append({"phase": 2, "action": "check_permission", "status": "already_has_permission"})
                
                shot2 = evidence_dir / "02_description_page.png"
                _async_screenshot(page, str(shot2), full_page=True, timeout=10000)
                result["evidence_files"].append(str(shot2))
                return result
            
            # 新UI处理：如果还在 Product Identity 页面，再做一次新UI专属预提交归一化后点击 Continue/Submit。
            # 字段选择/勾选逻辑仍由 FormFiller 的 new_ui 分支负责，避免在 submit flow 里复制新UI selector。
            if state['page_type'] == 'PRODUCT_IDENTITY_NEW_UI':
                print("\n[阶段 1.8] 检测到新UI，执行新UI预提交检查后重试 Continue...")
                ready_again = filler.ensure_add_product_ready_for_continue("new_ui", item_type_hint=item_type_keyword)
                result["steps"].append({"phase": 1.8, "action": "new_ui_pre_continue_normalize", "results": ready_again})
                
                # 尝试点击 Continue to Description
                continue_result = page.evaluate("""() => {
                    const btn = document.querySelector('kat-button[data-testid="continue-button"]');
                    if (btn) {
                        btn.click();
                        if (btn.shadowRoot) {
                            const inner = btn.shadowRoot.querySelector('button');
                            try { if (inner) inner.click(); } catch(e) {}
                        }
                        return { clicked: true, text: 'data-testid' };
                    }
                    // 回退
                    let btn2 = document.querySelector('kat-button#continue-to-description-button');
                    if (!btn2) {
                        const allBtns = document.querySelectorAll('kat-button');
                        for (const b of allBtns) {
                            const text = (b.getAttribute('label') || b.textContent || '').toLowerCase();
                            if (text.includes('continue to description')) {
                                btn2 = b;
                                break;
                            }
                        }
                    }
                    if (btn2) {
                        btn2.click();
                        if (btn2.shadowRoot) {
                            const inner = btn2.shadowRoot.querySelector('button');
                            try { if (inner) inner.click(); } catch(e) {}
                        }
                        return { clicked: true, text: 'fallback' };
                    }
                    const submit = document.querySelector('kat-button#form-submit-button[data-testid="submit-button"]');
                    if (submit) {
                        submit.click();
                        if (submit.shadowRoot) {
                            const inner = submit.shadowRoot.querySelector('button');
                            try { if (inner) inner.click(); } catch(e) {}
                        }
                        return { clicked: true, text: 'submit-button' };
                    }
                    return { clicked: false };
                }""")
                print(f"[阶段 1.8] Continue 按钮点击结果: {continue_result}")
                
                if continue_result.get('clicked'):
                    # 等待页面状态变化，最多 8s
                    try:
                        page.wait_for_function(
                            """() => {
                                const url = window.location.href;
                                return !url.includes('product_identity') ||
                                       document.querySelector('kat-panel-wrapper[panel-visible="true"]') !== null;
                            }""",
                            timeout=8000
                        )
                    except Exception:
                        pass
                    state = check_page_state(page)
                    print(f"[阶段 1.8] 点击 Continue 后页面状态: {state['page_type']}")
                    
                    if state['page_type'] == 'PRODUCT_IDENTITY_NEW_UI':
                        print("\n[阶段 1.9] 尝试点击 Submit 按钮...")
                        submit_result = page.evaluate("""() => {
                            const btn = document.querySelector('kat-button#form-submit-button');
                            if (btn) {
                                btn.click();
                                if (btn.shadowRoot) {
                                    const inner = btn.shadowRoot.querySelector('button');
                                    try { if (inner) inner.click(); } catch(e) {}
                                }
                                return { clicked: true };
                            }
                            return { clicked: false };
                        }""")
                        print(f"[阶段 1.9] Submit 按钮点击结果: {submit_result}")
                        # 等待页面状态变化（URL 离开 product_identity 或 panel 出现），最多 8s
                        try:
                            page.wait_for_function(
                                """() => {
                                    const url = window.location.href;
                                    return !url.includes('product_identity') ||
                                           document.querySelector('kat-panel-wrapper[panel-visible="true"]') !== null;
                                }""",
                                timeout=8000
                            )
                        except Exception:
                            pass
                        
                        state = check_page_state(page)
                        print(f"[阶段 1.9] 点击 Submit 后页面状态: {state['page_type']}")
            
            # 阶段 2: 点击 Apply to sell
            # 检查是否出现新UI的授权弹窗（直接检测，不依赖 state）
            print("\n[阶段 2/3] 检查是否需要授权...")
            # 等待 Apply to sell 按钮或授权弹窗出现（避免429导致内容未加载），最多 5s
            try:
                page.wait_for_function(
                    """() => {
                        const body = (document.body.innerText || '').toLowerCase();
                        return body.includes('apply to sell') ||
                               body.includes('application required') ||
                               body.includes('applications required') ||
                               body.includes('you need approval') ||
                               body.includes('brand authorisation required') ||
                               document.querySelector('kat-panel-wrapper[panel-visible="true"]') !== null;
                    }""",
                    timeout=5000
                )
            except Exception:
                pass
            has_apply_button = page.evaluate("""() => {
                const allBtns = document.querySelectorAll('kat-button, button, a');
                for (const b of allBtns) {
                    const text = (b.getAttribute('label') || b.textContent || b.innerText || '').toLowerCase();
                    if (text.includes('apply to sell')) {
                        return { found: true, text: text };
                    }
                }
                // ★ 2026-07-15: 新UI的申请入口是 kat-link "View" + "application required" 文案
                const katLinks = document.querySelectorAll('kat-link');
                for (const lnk of katLinks) {
                    const text = (lnk.textContent || lnk.getAttribute('label') || '').trim().toLowerCase();
                    if (text === 'view') {
                        const bodyText = (document.body.innerText || '').toLowerCase();
                        if (bodyText.includes('application required') ||
                            bodyText.includes('applications required') ||
                            /[0-9]+\\s*restrictions?/.test(bodyText)) {
                            return { found: true, text: 'kat-link-view-application-required' };
                        }
                    }
                }
                // 也检查页面文本
                const bodyText = document.body.innerText.toLowerCase();
                if (bodyText.includes('apply to sell')) {
                    return { found: true, text: 'found_in_body' };
                }
                if (bodyText.includes('application required') || bodyText.includes('applications required')) {
                    return { found: true, text: 'application_required_in_body' };
                }
                return { found: false };
            }""")
            print(f"[阶段 2/3] Apply to sell 按钮检测: {has_apply_button}")

            # ★ 阶段1.8 直接落到 BRAND_SELECTION（跳过了 Description 页）
            # 例：DEMO_JADE 在 UK 触发 ConnectBrand 弹窗，Continue 后直接出现 "Select brand" 按钮
            # 此时不需要经过 Product Identity retrigger，直接走品牌选择流程
            if not state.get('has_5461_form') and state.get('page_type') == 'BRAND_SELECTION':
                print("\n[阶段 2/3] 阶段1.8后直接检测到 BRAND_SELECTION，跳过 Apply to sell，直接走品牌选择流程...")
                brand_keywords_early = []
                try:
                    manifest_path_early = Path(f"brand_packs/{brand_name}/manifest.json")
                    if manifest_path_early.exists():
                        manifest_early = json.load(open(manifest_path_early, encoding='utf-8'))
                        brand_keywords_early = manifest_early.get('brand_selection_keywords', [])
                        print(f"[阶段 2/3] 品牌关键词: {brand_keywords_early}")
                except Exception as e_early:
                    print(f"[阶段 2/3] 加载 manifest 失败: {e_early}")
                selection_success_early = handle_brand_selection(page, brand_name, brand_keywords_early)
                if selection_success_early:
                    state = check_page_state(page)
                    print(f"[阶段 2/3] 品牌选择后页面状态: {state['page_type']}")
                    if state['page_type'] in ('5461_FORM_OPEN', '5461_PANEL_SHELL', 'GTIN_EXEMPTION'):
                        print("[阶段 2/3] ✅ 品牌选择后 5461 panel 已打开，继续填写表单...")
                        state['has_5461_form'] = True
                    else:
                        print(f"[阶段 2/3] 品牌选择后状态非 5461 panel: {state['page_type']}，标记失败")
                        result["note"] = f"阶段1.8→BRAND_SELECTION: 品牌选择后状态为 {state['page_type']}，未进入 5461 表单"
                        return result
                else:
                    print("[阶段 2/3] 品牌选择失败，标记失败")
                    result["note"] = "阶段1.8→BRAND_SELECTION: handle_brand_selection 返回 False"
                    return result

            if state['needs_auth'] or state['page_type'] == 'NEEDS_APPROVAL_NEW_UI' or has_apply_button.get('found'):
                print("\n[阶段 2/3] 点击 Apply to sell...")
                
                # 统一使用 filler.click_apply_to_sell() 处理新旧UI
                apply_success = False
                max_apply_retries = 3
                
                for apply_attempt in range(max_apply_retries):
                    apply_success = filler.click_apply_to_sell()
                    result["steps"].append({"phase": 2, "action": f"click_apply_to_sell_attempt_{apply_attempt + 1}", "success": apply_success})
                    
                    if apply_success:
                        break
                    
                    # 检查是否是 410001 错误或其他错误
                    error_check = page.evaluate("""
                        () => {
                            const pageText = document.body.innerText || '';
                            return {
                                has410001: pageText.includes('410001'),
                                hasError: pageText.includes('An unexpected error has occurred'),
                                has429: pageText.includes('429') || pageText.includes('Too Many Requests')
                            };
                        }
                    """)
                    
                    has_error = error_check.get('has410001') or error_check.get('hasError') or error_check.get('has429')
                    
                    if has_error and apply_attempt < max_apply_retries - 1:
                        print(f"[阶段 2/3] 检测到错误 (410001/429/Unexpected)，等待 30 秒后重试 ({apply_attempt + 2}/{max_apply_retries})...")
                        
                        # 等待 30 秒后再重试
                        time.sleep(30)
                        
                        # 策略：完全刷新页面并重新填写整个表单
                        for nav_attempt2 in range(3):
                            try:
                                page.goto(add_product_url, wait_until="domcontentloaded", timeout=45000)
                                break
                            except Exception as nav_err2:
                                if "ERR_ABORTED" in str(nav_err2) and nav_attempt2 < 2:
                                    print(f"[阶段 2/3] 页面导航被中断，等待 5 秒后重试 ({nav_attempt2+1}/3)...")
                                    time.sleep(5)
                                else:
                                    raise
                        # 等待页面内容加载完成（form fields 出现），最多 8s
                        try:
                            page.wait_for_function(
                                """() => document.readyState === 'complete' &&
                                         document.body.innerText.length > 200""",
                                timeout=8000
                            )
                        except Exception:
                            pass
                        
                        # 重新填写完整表单
                        print("[阶段 2/3] 重新填写完整表单...")
                        filler = KatalFormFiller(page)
                        filler.brand_name = brand_name
                        fill_success = filler.fill_product_identity_form(
                            brand_name=brand_name,
                            item_name=item_name,
                            item_type_hint=item_type_keyword,
                        )
                        
                        if fill_success:
                            print("[阶段 2/3] 表单重新填写成功")
                            # 点击 Next/Continue
                            page.evaluate("""() => {
                                let btn = document.querySelector('kat-button[data-testid="continue-button"]');
                                if (!btn) btn = document.querySelector('kat-button#next-button');
                                if (btn) btn.click();
                            }""")
                            # 等待页面状态变化（离开 product_identity 或 panel 出现），最多 8s
                            try:
                                page.wait_for_function(
                                    """() => {
                                        const url = window.location.href;
                                        return !url.includes('product_identity') ||
                                               document.querySelector('kat-panel-wrapper[panel-visible="true"]') !== null;
                                    }""",
                                    timeout=8000
                                )
                            except Exception:
                                pass
                            
                            # 检查页面状态
                            state = check_page_state(page)
                            print(f"[阶段 2/3] 重试后页面状态: {state['page_type']}")
                            
                            if state['has_permission']:
                                print("[阶段 2/3] 重试后已有权限，无需继续...")
                                break
                        else:
                            print("[阶段 2/3] 表单重新填写失败")
                            break
                    else:
                        print("[阶段 2/3] 未检测到可重试错误或已达最大重试次数")
                        break
                
                if not apply_success:
                    declined_id = getattr(filler, 'declined_case_id', None)
                    if declined_id:
                        print(f"[阶段 2/3] 检测到旧 Declined Case ID: {declined_id}，尝试发起新申请...")
                        declined_result = handle_declined_case_application(page, brand_name, evidence_dir, filler=filler)
                        result["declined_recovery"] = declined_result
                        if declined_result.get('success'):
                            print("[阶段 2/3] Declined recovery 已打开新 5461 表单，继续填写")
                            # 继续进入后续 5461_FORM_OPEN / 填表流程
                        else:
                            print(f"[阶段 2/3] Declined recovery 失败: {declined_result.get('note')}")
                            result["submit_result"] = "declined"
                            result["status"] = "declined"
                            result["case_id"] = declined_id
                            result["note"] = f"旧申请已被拒绝 (Case ID: {declined_id})，未能自动发起新申请: {declined_result.get('note')}"
                            return result
                    else:
                        print("[阶段 2/3] click_apply_to_sell 未拿到真实 5461 字段；停止本品牌，避免把半加载 panel 当成弹框")
                        result["submit_result"] = "failed"
                        application_type = getattr(filler, 'application_type_result', None) or {}
                        application_status = application_type.get('status')
                        if application_status == 'sell_products_only':
                            result["note"] = (
                                "semantic_control_missing: Apply to sell 面板仅提供 sell products 入口；"
                                "未自动点击，需人工确认创建新 ASIN 的申请路径"
                            )
                        elif application_status in {
                            'unknown_application_options', 'target_brand_missing',
                            'create_card_click_failed',
                        }:
                            result["note"] = (
                                f"state_unknown: Apply to sell 申请类型面板无法安全处理 "
                                f"({application_status})"
                            )
                        elif application_status == 'create_card_selected_form_not_ready':
                            result["note"] = (
                                "flow_loop_exhausted: 已选择 create new ASINs 入口，"
                                "但真实 5461 字段未加载"
                            )
                        else:
                            result["note"] = "Apply to sell 后未加载真实 5461 字段/上传控件（panel shell 半加载）"
                        return result
                
                # Under Review: panel 内已有 Case ID，无需填表
                under_review_id = getattr(filler, 'under_review_case_id', None)
                if under_review_id:
                    print(f"[阶段 2/3] Under Review Case ID: {under_review_id}，跳过表单填写")
                    result["case_id"] = under_review_id
                    result["submit_result"] = "under_review"
                    result["status"] = "under_review"
                    return result

                # 新UI：点击 Apply to sell 后，等待异步加载（5461 panel 或 case_id 相关文本出现），最多 10s
                print("\n[阶段 2/3] 等待页面响应...")
                try:
                    page.wait_for_function(
                        """() => {
                            const body = (document.body.innerText || '').toLowerCase();
                            return document.querySelector('kat-panel-wrapper[panel-visible="true"]') !== null ||
                                   body.includes('listing approval') ||
                                   body.includes('product title') ||
                                   body.includes('case id') ||
                                   body.includes('under review');
                        }""",
                        timeout=10000
                    )
                except Exception:
                    pass
                
                # [阶段 2] 证据采集：Apply to sell 点击后
                capture_evidence_safe(
                    page, EVIDENCE_NODES["apply_clicked"],
                    root=evidence_root, account=account_id, brand=brand_name
                )

                # 截图检查状态（异步，不阻塞 Case 检查）
                try:
                    shot_after_apply = evidence_dir / "02_after_apply.png"
                    _async_screenshot(page, str(shot_after_apply), full_page=True, timeout=10000)
                    result["evidence_files"].append(str(shot_after_apply))
                except:
                    pass
                
                # 优先检查是否自动创建了 Case（新UI）
                print("\n[阶段 2.5] 检查是否自动创建 Case...")
                
                # 尝试多种方式提取 Case ID
                case_id = None
                
                # 方法1: 正则匹配 "Case ID" 文本
                try:
                    case_id = page.evaluate("""() => {
                        const pageText = document.body.innerText || document.body.textContent || '';
                        const match = pageText.match(/Case ID[:\\s\\-]*(\\d{10,12})/i);
                        return match ? match[1] : null;
                    }""")
                    print(f"[阶段 2.5] 方法1 提取 Case ID: {case_id}")
                except Exception as e:
                    print(f"[阶段 2.5] 方法1 失败: {e}")
                
                # 方法2: 查找所有 10-12 位数字
                if not case_id:
                    try:
                        case_id = page.evaluate("""() => {
                            const pageText = document.body.innerText || document.body.textContent || '';
                            const matches = pageText.match(/\\b\\d{10,12}\\b/g);
                            return matches ? matches[0] : null;
                        }""")
                        print(f"[阶段 2.5] 方法2 提取 Case ID: {case_id}")
                    except Exception as e:
                        print(f"[阶段 2.5] 方法2 失败: {e}")
                
                # 方法3: 使用 Playwright 的 inner_text
                if not case_id:
                    try:
                        page_text = page.inner_text('body')
                        match = re.search(r'Case ID[:\s\-]*(\d{10,12})', page_text, re.IGNORECASE)
                        if match:
                            case_id = match.group(1)
                        print(f"[阶段 2.5] 方法3 提取 Case ID: {case_id}")
                    except Exception as e:
                        print(f"[阶段 2.5] 方法3 失败: {e}")
                
                if case_id:
                    # 检查是否是 Declined/Rejected 状态的旧申请
                    is_declined = False
                    try:
                        page_text_check = page.evaluate("""
                            () => { return document.body.innerText || document.body.textContent || ''; }
                        """) or ''
                        pos = page_text_check.lower().find(case_id.lower())
                        nearby = (page_text_check[max(0, pos - 300): pos + 300]
                                  if pos >= 0 else page_text_check[:600]).lower()
                        is_declined = any(kw in nearby for kw in ['declined', 'rejected', 'denied'])
                    except Exception:
                        pass

                    if not is_declined:
                        # 正常新提交成功
                        print(f"[OK] 自动创建 Case ID: {case_id}")
                        result["submit_result"] = "success"
                        result["note"] = f"新UI自动提交成功，Case ID: {case_id}"
                        result["case_id"] = case_id
                        try:
                            shot2 = evidence_dir / "02_case_created.png"
                            _async_screenshot(page, str(shot2), full_page=True, timeout=10000)
                            result["evidence_files"].append(str(shot2))
                        except Exception:
                            pass
                        return result
                    else:
                        # 发现已被拒绝的旧申请，尝试点击 '>' 发起新申请
                        print(f"[阶段 2.5] ⚠️ 发现 Declined 申请 (Case ID: {case_id})，尝试点击 '>' 发起新申请...")
                        declined_result = handle_declined_case_application(page, brand_name, evidence_dir, filler=filler)
                        if declined_result['success']:
                            print(f"[阶段 2.5] ✅ 已进入新申请表单，继续填写 5461...")
                            result["declined_recovery"] = declined_result
                            case_id = None  # 清空，让后续流程重新判断页面状态
                        else:
                            print(f"[阶段 2.5] ❌ 无法自动发起新申请: {declined_result['note']}")
                            result["declined_recovery"] = declined_result
                            # 半加载/字段缺失属于可重试的流程失败，不应把旧 declined Case ID 当作最终结果。
                            if declined_result.get('action') in ('half_loaded_panel', 'retry_failed', 'no_new_form'):
                                result["submit_result"] = "failed"
                                result["note"] = (f"旧申请已被拒绝 (Case ID: {case_id})，"
                                                  f"新申请恢复失败: {declined_result['note']}")
                                result["case_id"] = None
                            else:
                                result["submit_result"] = "declined"
                                result["note"] = (f"旧申请已被拒绝 (Case ID: {case_id})，"
                                                  f"自动发起新申请失败: {declined_result['note']}")
                                result["case_id"] = case_id
                            try:
                                shot_d = evidence_dir / "02_declined_case.png"
                                _async_screenshot(page, str(shot_d), full_page=False, timeout=10000)
                                result["evidence_files"].append(str(shot_d))
                            except Exception:
                                pass
                            return result

                if not case_id:
                    print("[!] 未自动创建 Case，继续检查...")
                
                # 再次检查状态
                state = check_page_state(page)
                print(f"[阶段 2/3] 页面状态: {state['page_type']}")
                
                # 处理 GTIN 豁免流程（包括从 PRODUCT_IDENTITY_UPC 进入的情况）
                if state['page_type'] in ['GTIN_EXEMPTION', 'PRODUCT_IDENTITY_UPC']:
                    # 先检查 GTIN Exemption 弹窗是否已经打开（面板可见 + 包含 GTIN Exemption 文字）
                    _panel_already_open = False
                    try:
                        _panel_text = page.evaluate(r"""() => {
                            const p = document.querySelector('kat-panel-wrapper[panel-visible="true"]');
                            return p ? (p.innerText || p.textContent || "") : "";
                        }""")
                        if "GTIN Exemption" in (_panel_text or ""):
                            _panel_already_open = True
                    except Exception:
                        pass

                    if _panel_already_open:
                        # 弹窗已打开，直接进入填写流程（fill_5461_form 会处理 GTIN Exemption）
                        print("\n[阶段 2.5] GTIN Exemption 弹窗已打开，直接进入填写流程...")
                        state['has_5461_form'] = True
                        state['page_type'] = '5461_FORM_OPEN'
                    else:
                        # 弹窗未打开（通常是 410001 导致），等待后重试
                        print("\n[阶段 2.5] GTIN_EXEMPTION 状态，等待 30 秒后重新点击 Apply to sell...")
                        time.sleep(30)
                        retry_result = filler.click_apply_to_sell()
                        if retry_result:
                            # 等待 5461 panel 或表单字段出现，最多 8s
                            try:
                                page.wait_for_function(
                                    """() => document.querySelector('kat-panel-wrapper[panel-visible="true"]') !== null
                                         || !!document.querySelector('kat-input#question-cat_auth_mo_question_string_id_product_title')""",
                                    timeout=8000
                                )
                            except Exception:
                                pass
                            state_retry = check_page_state(page)
                            print(f"[阶段 2.5] 重试后状态: {state_retry['page_type']}")
                            if state_retry["page_type"] in ["5461_FORM_OPEN", "GTIN_EXEMPTION"]:
                                print("[阶段 2.5] ✅ 重试后进入表单，继续填写...")
                                state = state_retry
                                state['has_5461_form'] = True
                                state['page_type'] = '5461_FORM_OPEN'
                            else:
                                result["submit_result"] = "failed"
                                result["note"] = f"GTIN_EXEMPTION 重试后状态: {state_retry['page_type']}"
                                return result
                        else:
                            result["submit_result"] = "failed"
                            result["note"] = "GTIN_EXEMPTION 状态下重试 Apply to sell 失败"
                            return result
                
                # 处理品牌选择弹窗（点击 Apply to sell 后可能触发）
                if state['page_type'] == 'BRAND_SELECTION':
                    print("\n[阶段 2.5] 点击 Apply to sell 后进入品牌选择页面，处理品牌选择...")
                    
                    # 获取品牌关键词
                    brand_keywords = None
                    manifest_path = Path(f"brand_packs/{brand_name}/manifest.json")
                    if manifest_path.exists():
                        try:
                            import json
                            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
                            brand_keywords = manifest.get('brand_selection_keywords', [])
                            print(f"[阶段 2.5] 品牌关键词: {brand_keywords}")
                        except Exception as e:
                            print(f"[阶段 2.5] 加载 manifest 失败: {e}")
                    
                    selection_success = handle_brand_selection(page, brand_name, brand_keywords)
                    result["steps"].append({"phase": 2.5, "action": "brand_selection", "success": selection_success})
                    
                    if selection_success:
                        # 等待 panel/brand-info-option 出现，最多 5s
                        try:
                            page.wait_for_function(
                                """() => document.querySelector('kat-panel-wrapper[panel-visible="true"]') !== null
                                     || document.querySelector('kat-box[data-testid="brand-info-option"]') !== null""",
                                timeout=5000
                            )
                        except Exception:
                            pass
                        # 重新检查页面状态
                        state = check_page_state(page)
                        print(f"[阶段 2.5] 品牌选择后页面状态: {state['page_type']}")
                        
                        # 品牌选择后继续检查是否需要 GTIN 豁免
                        if state['page_type'] in ['GTIN_EXEMPTION', 'PRODUCT_IDENTITY_UPC']:
                            # 品牌选择后 5461 弹窗未打开，等待后重试 Apply to sell
                            print("\n[阶段 2.5] 品牌选择后 GTIN_EXEMPTION，等待 30 秒后重试 Apply to sell...")
                            time.sleep(30)
                            retry_r = filler.click_apply_to_sell()
                            if retry_r:
                                # 等待表单字段出现，最多 5s
                                try:
                                    page.wait_for_function(
                                        """() => document.querySelector('kat-panel-wrapper[panel-visible="true"]') !== null
                                             || !!document.querySelector('kat-input#question-cat_auth_mo_question_string_id_product_title')""",
                                        timeout=5000
                                    )
                                except Exception:
                                    pass
                                state_r = check_page_state(page)
                                print(f"[阶段 2.5] 品牌选择后重试状态: {state_r['page_type']}")
                                if state_r['page_type'] == '5461_FORM_OPEN':
                                    print("[阶段 2.5] ✅ 重试成功")
                                    state = state_r
                                else:
                                    result["submit_result"] = "failed"
                                    result["note"] = f"品牌选择后 GTIN 重试状态: {state_r['page_type']}"
                                    return result
                            else:
                                result["submit_result"] = "failed"
                                result["note"] = "品牌选择后 GTIN_EXEMPTION 重试 Apply to sell 失败"
                                return result
                    else:
                        result["submit_result"] = "failed"
                        result["note"] = "品牌选择失败"
                        return result
                
                # 新UI：点击 Apply to sell 后，检查是否自动创建了 Case 或进入 5461 表单
                if state['page_type'] == 'NEEDS_APPROVAL_NEW_UI' or has_apply_button.get('found'):
                    print("\n[阶段 2.5] 新UI：检查是否自动创建 Case 或进入 5461 表单...")
                    # 等待 Case ID 文本、panel 或 5461 表单出现，最多 5s
                    try:
                        page.wait_for_function(
                            """() => {
                                const body = (document.body.innerText || '').toLowerCase();
                                return body.includes('case id') ||
                                       body.includes('under review') ||
                                       body.includes('listing approval') ||
                                       document.querySelector('kat-panel-wrapper[panel-visible="true"]') !== null;
                            }""",
                            timeout=5000
                        )
                    except Exception:
                        pass
                    
                    # 重新检查页面状态
                    state = check_page_state(page)
                    print(f"[阶段 2.5] 新UI页面状态: {state['page_type']}")
                    
                    # 提取 Case ID
                    case_id = page.evaluate(r"""() => {
                        const pageText = document.body.innerText || '';
                        const match = pageText.match(/Case ID[:\s\-]*(\d+)/i);
                        return match ? match[1] : null;
                    }""")
                    
                    if case_id:
                        # 检查是否是 Declined 旧申请
                        is_declined_newui = False
                        try:
                            pt = page.evaluate("() => document.body.innerText || ''") or ''
                            pos = pt.lower().find(case_id.lower())
                            nearby = (pt[max(0, pos-300):pos+300] if pos >= 0 else pt[:600]).lower()
                            is_declined_newui = any(kw in nearby for kw in ['declined', 'rejected', 'denied'])
                        except Exception:
                            pass

                        if not is_declined_newui:
                            print(f"[OK] 新UI自动创建 Case ID: {case_id}")
                            result["submit_result"] = "success"
                            result["note"] = f"新UI自动提交成功，Case ID: {case_id}"
                            result["case_id"] = case_id
                            try:
                                shot2 = evidence_dir / "02_case_created.png"
                                _async_screenshot(page, str(shot2), full_page=True, timeout=10000)
                                result["evidence_files"].append(str(shot2))
                            except Exception:
                                pass
                            return result
                        else:
                            print(f"[阶段 2.5] ⚠️ 新UI发现 Declined 申请 (Case ID: {case_id})，关闭弹窗后随机等待重试...")
                            # 关闭弹窗
                            try:
                                page.evaluate("""() => {
                                    const btns = document.querySelectorAll(
                                        'kat-button[data-testid*="close"], [data-action*="close"], ' +
                                        'kat-button[label*="Close"], kat-button[label*="close"]'
                                    );
                                    for (const b of btns) { b.click(); break; }
                                }""")
                            except Exception:
                                pass
                            wait_sec = random.randint(15, 35)
                            print(f"[阶段 2.5] 等待 {wait_sec} 秒后重试 Apply to sell...")
                            time.sleep(wait_sec)
                            retry2 = filler.click_apply_to_sell()
                            if retry2:
                                # 等待 panel 或表单状态更新，最多 6s
                                try:
                                    page.wait_for_function(
                                        """() => document.querySelector('kat-panel-wrapper[panel-visible="true"]') !== null
                                             || !!document.querySelector('kat-input#question-cat_auth_mo_question_string_id_product_title')
                                             || (document.body.innerText||'').toLowerCase().includes('listing approval')""",
                                        timeout=6000
                                    )
                                except Exception:
                                    pass
                                state_r2 = check_page_state(page)
                                print(f"[阶段 2.5] Declined 重试后状态: {state_r2['page_type']}")
                                if state_r2['page_type'] == '5461_FORM_OPEN':
                                    # 再次检查 Case ID - 如果还是 Declined，先继续 close-panel 受控重试，避免卡在旧申请/半加载 panel
                                    cid2 = page.evaluate(r"() => { const m = (document.body.innerText||'').match(/Case ID[:\s\-]*(\d+)/i); return m?m[1]:null; }")
                                    if cid2:
                                        pt2 = page.evaluate("() => document.body.innerText || ''") or ''
                                        p2 = pt2.lower().find(cid2.lower())
                                        nb2 = (pt2[max(0,p2-300):p2+300] if p2>=0 else pt2[:600]).lower()
                                        if any(k in nb2 for k in ['declined','rejected','denied']):
                                            print(f"[阶段 2.5] ⚠️ 重试后仍显示 Declined (Case ID: {cid2})，继续 close-panel 受控重试而不是立即失败")
                                            recovered = recover_half_loaded_5461_panel(
                                                page, filler, brand_name, max_attempts=3,
                                                reason=f"declined new-ui still shows old case {cid2}"
                                            )
                                            if not recovered:
                                                result["submit_result"] = "declined"
                                                result["note"] = f"旧申请已被拒绝 (Case ID: {cid2})，close-panel 多次重试后仍未进入可填新表单"
                                                result["case_id"] = cid2
                                                return result
                                            case_id = None
                                        else:
                                            print(f"[OK] 重试后新 Case ID: {cid2}")
                                            result["submit_result"] = "success"
                                            result["note"] = f"Declined 重试后成功，Case ID: {cid2}"
                                            result["case_id"] = cid2
                                            return result
                                    # 无 Case ID，确认不是半加载；半加载则 close-panel 多轮恢复
                                    if not wait_for_5461_form_fields(page, timeout_sec=24, interval_sec=2):
                                        recovered = recover_half_loaded_5461_panel(
                                            page, filler, brand_name, max_attempts=3,
                                            reason="declined retry entered 5461_FORM_OPEN but fields missing"
                                        )
                                        if not recovered:
                                            result["submit_result"] = "failed"
                                            result["note"] = "Declined 重试进入半加载 5461 panel，close-panel 多次重试后字段仍未加载"
                                            return result
                                    print("[阶段 2.5] ✅ 重试后进入 5461 表单，继续填写...")
                                    case_id = None
                                else:
                                    # GTIN_EXEMPTION / other states after declined retry are often transient 410001/429; retry panel opening a few times.
                                    print(f"[阶段 2.5] Declined 重试后未进入表单 ({state_r2['page_type']})，执行 close-panel 受控重试...")
                                    recovered = recover_half_loaded_5461_panel(
                                        page, filler, brand_name, max_attempts=3,
                                        reason=f"declined retry state {state_r2['page_type']}"
                                    )
                                    if recovered:
                                        case_id = None
                                    else:
                                        result["submit_result"] = "declined"
                                        result["note"] = f"旧申请已被拒绝 (Case ID: {case_id})，多轮 close-panel 重试后状态仍不可用: {state_r2['page_type']}"
                                        result["case_id"] = case_id
                                        return result
                            else:
                                result["submit_result"] = "declined"
                                result["note"] = f"旧申请已被拒绝 (Case ID: {case_id})，重试 Apply to sell 失败"
                                result["case_id"] = case_id
                                return result
                    
                    # 检查是否进入 5461 表单
                    if state['has_5461_form']:
                        print("[OK] 新UI进入 5461 表单，继续填写...")
                    else:
                        print("[!] 新UI未自动创建 Case，也未检测到 5461 表单，继续检查...")
                
                if not state['has_5461_form'] and not state['has_permission']:
                    result["submit_result"] = "failed"
                    result["note"] = "点击 Apply to sell 后未进入5461表单"
                    
                    shot2 = evidence_dir / "02_after_apply.png"
                    _async_screenshot(page, str(shot2), full_page=True, timeout=10000)
                    result["evidence_files"].append(str(shot2))
                    return result
            
            # 如果仍然没有表单，可能是已有权限，也可能是 Amazon 未在当前页面显示授权提示
            # 保守策略：不假设已有权限，避免误判。由外层或人工复核确认。
            if not state['has_5461_form']:
                if '/description' in page.url:
                    print("\n[阶段 2/3] 到达 Description 且未检测到 5461，尝试左侧 Attributes → Product Identity 触发授权面板...")
                    result["steps"].append({"phase": 2, "action": "description_product_identity_retrigger", "status": "attempt"})
                    retrigger_event = {
                        "captured_at": datetime.now().isoformat(timespec="seconds"),
                        "trigger": "description_product_identity",
                        "navigation": {},
                        "post_trigger_state": {},
                        "apply_control_probe": {},
                        "apply_click": {"attempted": False, "success": False},
                        "outcome": "started",
                        "errors": [],
                    }
                    try:
                        retrigger_result = page.evaluate("""() => {
                        const candidates = [
                            document.querySelector('kat-box#hub-item-box-product_identity'),
                            document.querySelector('kat-box[data-testid="hub-item-box-product_identity"]'),
                            document.querySelector('[data-testid="hub-item-box-product_identity"]'),
                        ].filter(Boolean);
                        let target = candidates[0] || null;
                        if (!target) {
                            const boxes = Array.from(document.querySelectorAll('kat-box, [data-testid^="hub-item-box-"]'));
                            target = boxes.find(el => ((el.textContent || '').toLowerCase().includes('product identity'))) || null;
                        }
                        if (!target) return { clicked: false, reason: 'product_identity_nav_not_found' };
                        target.scrollIntoView({block: 'center', inline: 'nearest'});
                        target.click();
                        const chev = target.querySelector('[data-testid="hub-nav-item-state-right-chevron"], kat-icon[name="chevron-right-bold"]');
                        try { if (chev) chev.click(); } catch (e) {}
                        return { clicked: true, id: target.id || '', testid: target.getAttribute('data-testid') || '', text: (target.textContent || '').slice(0, 120) };
                        }""")
                    except Exception as retrigger_exc:
                        retrigger_event["errors"].append(f"navigation:{type(retrigger_exc).__name__}")
                        retrigger_event["outcome"] = "navigation_error"
                        result["retrigger_results"].append(retrigger_event)
                        raise
                    retrigger_event["navigation"] = retrigger_result or {}
                    print(f"[阶段 2/3] Product Identity 触发结果: {retrigger_result}")
                    # 等待 Apply to sell 按钮或 panel 出现，最多 6s
                    try:
                        page.wait_for_function(
                            """() => {
                                const body = (document.body.innerText || '').toLowerCase();
                                return body.includes('apply to sell') ||
                                       body.includes('application required') ||
                                       body.includes('applications required') ||
                                       body.includes('you need approval') ||
                                       body.includes('brand authorisation required') ||
                                       document.querySelector('kat-panel-wrapper[panel-visible="true"]') !== null;
                            }""",
                            timeout=6000
                        )
                    except Exception:
                        pass
                    # panel visible 了但内容可能还在 loading（转圈），再等内容实际出现（最多15s）
                    try:
                        page.wait_for_function(
                            """() => {
                                const panel = document.querySelector('kat-panel-wrapper[panel-visible="true"]');
                                if (!panel) return false;
                                const body = (panel.innerText || panel.textContent || '').toLowerCase();
                                // 等到 panel 内有实质内容：表单字段、Declined 状态、Apply to sell 按钮或 under review 字样
                                return body.includes('declined') ||
                                       body.includes('under review') ||
                                       body.includes('application submitted') ||
                                       body.includes('listing approval') ||
                                       panel.querySelector('kat-input, input[type="file"], kat-button#submit_button') !== null;
                            }""",
                            timeout=15000
                        )
                    except Exception:
                        pass
                    state = check_page_state(page)
                    retrigger_event["post_trigger_state"] = {
                        key: state.get(key)
                        for key in ("page_type", "needs_auth", "has_5461_form", "needs_brand_selection")
                    }
                    print(f"[阶段 2/3] Product Identity 触发后页面状态: {state['page_type']}")
                    try:
                        retrigger_apply = page.evaluate("""() => {
                        const bodyText = (document.body.innerText || document.body.textContent || '').toLowerCase();
                        const all = Array.from(document.querySelectorAll('kat-button, button, a'));
                        const btn = all.find(b => ((b.getAttribute('label') || b.textContent || b.innerText || '').toLowerCase()).includes('apply to sell'));
                        const hasApplicationRequired = bodyText.includes('application required') ||
                            bodyText.includes('applications required');
                        const hasRestrictionEntry = /[0-9]+\\s*restrictions?/.test(bodyText) && bodyText.includes('view');
                        return {
                            found: !!btn || bodyText.includes('apply to sell') || hasApplicationRequired || hasRestrictionEntry,
                            hasApprovalText: bodyText.includes('you need approval to list this product') || bodyText.includes('brand authorisation required') || bodyText.includes('brand authorization required'),
                            buttonText: btn ? ((btn.getAttribute('label') || btn.textContent || btn.innerText || '').trim()) : ''
                        };
                        }""")
                    except Exception as retrigger_exc:
                        retrigger_event["errors"].append(f"apply_probe:{type(retrigger_exc).__name__}")
                        retrigger_event["outcome"] = "apply_probe_error"
                        result["retrigger_results"].append(retrigger_event)
                        raise
                    retrigger_event["apply_control_probe"] = retrigger_apply or {}
                    print(f"[阶段 2/3] Product Identity 触发后 Apply 检测: {retrigger_apply}")
                    result["steps"].append({"phase": 2, "action": "description_product_identity_retrigger", "status": "checked", "result": retrigger_apply})
                    if state.get('has_5461_form'):
                        print("[阶段 2/3] Product Identity 触发后已出现 5461 表单，继续填写...")
                    elif state.get('needs_auth') or state.get('page_type') == 'NEEDS_APPROVAL_NEW_UI' or retrigger_apply.get('found'):
                        print("[阶段 2/3] Product Identity 触发出授权面板，点击 Apply to sell...")
                        try:
                            apply_success = filler.click_apply_to_sell()
                        except Exception as retrigger_exc:
                            retrigger_event["apply_click"] = {"attempted": True, "success": False}
                            retrigger_event["errors"].append(f"apply_click:{type(retrigger_exc).__name__}")
                            retrigger_event["outcome"] = "apply_click_error"
                            result["retrigger_results"].append(retrigger_event)
                            raise
                        retrigger_event["apply_click"] = {
                            "attempted": True,
                            "success": bool(apply_success),
                        }
                        result["steps"].append({"phase": 2, "action": "click_apply_to_sell_after_product_identity_retrigger", "success": apply_success})
                        if apply_success:
                            # 等待 5461 panel 或表单字段出现，最多 10s
                            try:
                                page.wait_for_function(
                                    """() => document.querySelector('kat-panel-wrapper[panel-visible="true"]') !== null
                                         || !!document.querySelector('kat-input#question-cat_auth_mo_question_string_id_product_title')
                                         || (document.body.innerText||'').toLowerCase().includes('listing approval')""",
                                    timeout=10000
                                )
                            except Exception:
                                pass
                            state = check_page_state(page)
                            retrigger_event["post_apply_state"] = {
                                key: state.get(key)
                                for key in ("page_type", "needs_auth", "has_5461_form", "needs_brand_selection")
                            }
                            print(f"[阶段 2/3] 触发后点击 Apply 的页面状态: {state['page_type']}")
                        else:
                            print("[阶段 2/3] Product Identity 触发后 Apply to sell 未拿到真实 5461 字段")
                    retrigger_event["outcome"] = (
                        "form_visible" if state.get("has_5461_form")
                        else "apply_clicked_no_form" if retrigger_event["apply_click"]["attempted"]
                        else "control_not_actionable"
                    )
                    result["retrigger_results"].append(retrigger_event)
                    # ★ 新增：BRAND_SELECTION 状态 — 页面有 "You need approval" + "Select brand" 按钮
                    # 此时 Apply to sell 按钮不存在，但需要点 Select brand → 选择品牌 → Connect this brand → 5461 panel
                    if not state.get('has_5461_form') and state.get('page_type') == 'BRAND_SELECTION':
                        print("\n[阶段 2/3] 检测到 BRAND_SELECTION（Select brand 弹窗），尝试品牌选择流程...")
                        brand_keywords_bs = None
                        manifest_path_bs = Path(f"brand_packs/{brand_name}/manifest.json")
                        if manifest_path_bs.exists():
                            try:
                                manifest_bs = json.loads(manifest_path_bs.read_text(encoding='utf-8'))
                                brand_keywords_bs = manifest_bs.get('brand_selection_keywords', [])
                                print(f"[阶段 2/3] 品牌关键词: {brand_keywords_bs}")
                            except Exception as e_bs:
                                print(f"[阶段 2/3] 加载 manifest 失败: {e_bs}")
                        selection_success_bs = handle_brand_selection(page, brand_name, brand_keywords_bs)
                        result["steps"].append({"phase": 2, "action": "brand_selection_from_description", "success": selection_success_bs})
                        if selection_success_bs:
                            # 等待 panel 或 brand-info-option 出现，最多 8s
                            try:
                                page.wait_for_function(
                                    """() => document.querySelector('kat-panel-wrapper[panel-visible="true"]') !== null
                                         || document.querySelector('kat-box[data-testid="brand-info-option"]') !== null""",
                                    timeout=8000
                                )
                            except Exception:
                                pass
                            state = check_page_state(page)
                            print(f"[阶段 2/3] 品牌选择后页面状态: {state['page_type']}")
                            if state['page_type'] in ('5461_PANEL_SHELL', '5461_FORM_OPEN'):
                                print("[阶段 2/3] ✅ 品牌选择后 5461 panel 已打开，继续填写表单...")
                                if not wait_for_5461_form_fields(page, timeout_sec=24, interval_sec=2):
                                    recovered_bs = recover_half_loaded_5461_panel(
                                        page, filler, brand_name, max_attempts=3,
                                        reason="Description→BRAND_SELECTION→Connect brand 后字段未加载"
                                    )
                                    if not recovered_bs:
                                        result["submit_result"] = "failed"
                                        result["note"] = "Description→BRAND_SELECTION: Connect brand 后 5461 panel 字段未加载"
                                        return result
                                form_result_bs = fill_5461_form(page, brand_name, statement_text, upload_files, account_email)
                                result["steps"].append({"phase": 2, "action": "fill_5461_after_brand_selection", "details": form_result_bs})
                                if no_submit:
                                    return _dry_run_no_submit_return(2, "Description→BRAND_SELECTION 路径 阶段2/3")
                                print("[阶段 2/3] 点击提交...")
                                click_katal_button(page, 'kat-button#submit_button')
                                print("[阶段 2/3] 等待 Case ID 出现（最多90秒）...")
                                case_id_bs = None
                                for _ in range(30):
                                    case_id_bs = extract_case_id(page)
                                    if case_id_bs:
                                        print(f"[✓] 提取到 Case ID: {case_id_bs}")
                                        break
                                    time.sleep(3)
                                if case_id_bs:
                                    result["case_id"] = case_id_bs
                                    result["submit_result"] = "success"
                                    result["note"] = f"Description→BRAND_SELECTION 路径提交成功，Case ID: {case_id_bs}"
                                    return result
                                else:
                                    result["submit_result"] = "uncertain"
                                    result["note"] = "Description→BRAND_SELECTION: 表单已提交但未提取到 Case ID，请检查 Dashboard"
                                    return result
                        print(f"[阶段 2/3] 品牌选择失败或选择后状态非 5461 panel: {state['page_type']}")
                    # ★ 新增：检测 Apply to sell 弹窗中的 Declined 状态，尝试点击 > 新建表单
                    if not state.get('has_5461_form'):
                        declined_check = page.evaluate("""() => {
                            const body = document.body.innerText || document.body.textContent || '';
                            const hasDeclined = /declined/i.test(body);
                            const hasApplyToSell = /apply to sell/i.test(body);
                            const hasCaseId = /case\\s*id/i.test(body);
                            return { hasDeclined, hasApplyToSell, hasCaseId };
                        }""")
                        if declined_check.get('hasDeclined') and declined_check.get('hasApplyToSell'):
                            print(f"[阶段 2/3] 检测到 Apply to sell 弹窗中有 Declined 状态，尝试点击 '>' 发起新申请...")
                            # 先从弹窗里提取旧的 declined case id 并设到 filler 上，供 handle_declined_case_application 使用
                            if not getattr(filler, 'declined_case_id', None):
                                filler.declined_case_id = None  # 确保属性存在
                            declined_result = handle_declined_case_application(page, brand_name, evidence_dir, filler=filler)
                            result["declined_recovery"] = declined_result
                            result["steps"].append({"phase": 2, "action": "description_declined_recovery", "result": declined_result})
                            if declined_result.get('success'):
                                print("[阶段 2/3] Declined recovery 成功，已打开新 5461 表单，继续填写...")
                                state['has_5461_form'] = True
                                if not wait_for_5461_form_fields(page, timeout_sec=24, interval_sec=2):
                                    recovered = recover_half_loaded_5461_panel(
                                        page, filler, brand_name, max_attempts=3,
                                        reason="Description→Declined recovery 后字段未加载"
                                    )
                                    if not recovered:
                                        result["submit_result"] = "failed"
                                        result["note"] = "Description→Declined recovery: 点击 > 后 5461 panel 字段未加载"
                                        return result
                                form_result_dr = fill_5461_form(page, brand_name, statement_text, upload_files, account_email)
                                result["steps"].append({"phase": 2, "action": "fill_5461_after_declined_recovery", "details": form_result_dr})
                                if no_submit:
                                    return _dry_run_no_submit_return(2, "Description→Declined recovery 路径 阶段2/3")
                                print("[阶段 2/3] 点击提交...")
                                click_katal_button(page, 'kat-button#submit_button')
                                print("[阶段 2/3] 等待 Case ID 出现（最多90秒）...")
                                case_id_dr = None
                                for _ in range(30):
                                    case_id_dr = extract_case_id(page)
                                    if case_id_dr:
                                        print(f"[✓] 提取到 Case ID: {case_id_dr}")
                                        break
                                    time.sleep(3)
                                if case_id_dr:
                                    result["case_id"] = case_id_dr
                                    result["submit_result"] = "success"
                                    result["note"] = f"Description→Declined recovery 提交成功，Case ID: {case_id_dr}"
                                    return result
                                else:
                                    result["submit_result"] = "uncertain"
                                    result["note"] = "Description→Declined recovery: 表单已提交但未提取到 Case ID，请检查 Dashboard"
                                    return result
                            else:
                                print(f"[阶段 2/3] Declined recovery 失败: {declined_result.get('note')}")
                    if not state.get('has_5461_form'):
                        print("\\n[警告] 到达 Description 页面但未检测到 5461 表单或 Apply to sell 按钮。")
                        print("[警告] 该品牌可能：(1) 已有权限 或 (2) 需要人工检查其他申请路径。")
                        result["submit_result"] = "uncertain"
                        result["note"] = "到达 Description 页面，Product Identity 触发后仍未检测到 5461 入口，请人工复核该品牌是否仍需授权"
                        result["steps"].append({"phase": 2, "action": "check_permission", "status": "description_no_auth_prompt"})
                        
                        shot2 = evidence_dir / "02_description_no_auth.png"
                        _async_screenshot(page, str(shot2), full_page=True, timeout=10000)
                        result["evidence_files"].append(str(shot2))
                        return result
                elif state['page_type'] == 'PRODUCT_IDENTITY_UPC':
                    # 在 Product Identity 页面但有 UPC Exemption 提示，需要点击 Apply to sell
                    print("\n[阶段 2/3] 检测到 UPC Exemption 提示，点击 Apply to sell...")
                    apply_success = filler.click_apply_to_sell()
                    if apply_success:
                        # 等待 panel 或状态变化，最多 5s
                        try:
                            page.wait_for_function(
                                """() => document.querySelector('kat-panel-wrapper[panel-visible="true"]') !== null
                                     || (document.body.innerText||'').toLowerCase().includes('gtin exemption')
                                     || (document.body.innerText||'').toLowerCase().includes('listing approval')""",
                                timeout=5000
                            )
                        except Exception:
                            pass
                        state = check_page_state(page)
                        if state['page_type'] == 'GTIN_EXEMPTION':
                            # 先检查弹窗是否已打开
                            _panel_already_open2 = False
                            try:
                                _panel_text2 = page.evaluate(r"""() => {
                                    const p = document.querySelector('kat-panel-wrapper[panel-visible="true"]');
                                    return p ? (p.innerText || p.textContent || "") : "";
                                }""")
                                if "GTIN Exemption" in (_panel_text2 or ""):
                                    _panel_already_open2 = True
                            except Exception:
                                pass

                            if _panel_already_open2:
                                print("\n[阶段 2.5] GTIN Exemption 弹窗已打开，直接进入填写流程...")
                                state['has_5461_form'] = True
                                state['page_type'] = '5461_FORM_OPEN'
                            else:
                                # GTIN_EXEMPTION: 5461 弹窗未打开（通常是 410001 导致），等待后重试
                                print("\n[阶段 2.5] GTIN_EXEMPTION 状态，等待 30 秒后重新点击 Apply to sell...")
                                time.sleep(30)
                                retry_result = filler.click_apply_to_sell()
                                if retry_result:
                                    # 等待 panel/字段出现，最多 8s
                                    try:
                                        page.wait_for_function(
                                            """() => document.querySelector('kat-panel-wrapper[panel-visible="true"]') !== null
                                                 || !!document.querySelector('kat-input#question-cat_auth_mo_question_string_id_product_title')""",
                                            timeout=8000
                                        )
                                    except Exception:
                                        pass
                                    state_retry = check_page_state(page)
                                    print(f"[阶段 2.5] 重试后状态: {state_retry['page_type']}")
                                    if state_retry["page_type"] in ["5461_FORM_OPEN", "GTIN_EXEMPTION"]:
                                        print("[阶段 2.5] ✅ 重试后进入表单，继续填写...")
                                        state = state_retry
                                        state['has_5461_form'] = True
                                        state['page_type'] = '5461_FORM_OPEN'
                                    else:
                                        result["submit_result"] = "failed"
                                        result["note"] = f"GTIN_EXEMPTION 重试后状态: {state_retry['page_type']}"
                                        return result
                                else:
                                    result["submit_result"] = "failed"
                                    result["note"] = "GTIN_EXEMPTION 状态下重试 Apply to sell 失败"
                                    return result
                else:
                    result["submit_result"] = "failed"
                    if state.get('page_type') in ('PRODUCT_IDENTITY', 'PRODUCT_IDENTITY_NEW_UI'):
                        result["note"] = (
                            "semantic_control_missing: Product Identity 页面未识别到授权申请入口，"
                            "无法进入5461表单"
                        )
                    else:
                        result["note"] = "无法进入5461表单"
                    return result
            
            # [阶段 2] 证据采集：Connect brand / 5461 触发
            capture_evidence_safe(
                page, EVIDENCE_NODES["5461_triggered"],
                root=evidence_root, account=account_id, brand=brand_name
            )

            # 阶段 3: 填写 5461 表单
            print("\n[阶段 3/3] 填写 5461 表单...")

            # 先检查 5461 面板内是否有 "Connect brand" 覆盖层（DEMO_JADE/MP-MALL 等多同名品牌）
            connect_keywords = None
            try:
                import json as _json
                from pathlib import Path as _Path
                _mf = _Path(f"brand_packs/{brand_name}/manifest.json")
                if _mf.exists():
                    connect_keywords = _json.loads(_mf.read_text(encoding='utf-8')).get('brand_selection_keywords')
            except Exception:
                pass
            cb_result = connect_brand_in_5461_panel(page, brand_name, connect_keywords)
            if cb_result['found']:
                if cb_result['selected']:
                    print(f"[阶段 3/3] ✅ Connect brand 处理成功: {cb_result['brand_text'][:60]}")
                    # 等待 5461 表单字段出现（Connect brand 后面板有加载延迟）
                    print("[阶段 3/3] 等待 5461 表单字段加载...")
                    form_fields_ready = False
                    for _w in range(12):  # 最多等待 24 秒
                        time.sleep(2)
                        form_fields_ready = page.evaluate("""
                            () => !!document.querySelector(
                                'kat-input[id*="product_title"], ' +
                                'kat-input#question-cat_auth_mo_question_string_id_product_title, ' +
                                'input[id*="document_upload"][id*="document_input"]'
                            )
                        """)
                        if form_fields_ready:
                            print(f"[阶段 3/3] ✅ 表单字段已出现（等待 {(_w+1)*2}s）")
                            break
                    if not form_fields_ready:
                        print("[阶段 3/3] ⚠️ 等待 24s 后表单字段仍未出现，执行 close-panel 受控重试...")
                        form_fields_ready = recover_half_loaded_5461_panel(
                            page, filler, brand_name, max_attempts=3,
                            reason="connect brand 后字段/上传控件缺失"
                        )
                        if not form_fields_ready:
                            result["submit_result"] = "failed"
                            result["note"] = "Connect brand 后 5461 panel 半加载，close-panel 多次重试后字段仍未加载"
                            return result
                else:
                    print(f"[阶段 3/3] ⚠️ Connect brand 检测到但选择失败: {cb_result['note']}")

            if not wait_for_5461_form_fields(page, timeout_sec=8, interval_sec=2):
                recovered = recover_half_loaded_5461_panel(
                    page, filler, brand_name, max_attempts=3,
                    reason="填写前字段/上传控件缺失"
                )
                # ★ recover 可能因检测到 Under Review Case ID 而返回 True（而非真正加载了表单字段）
                _ur_id = getattr(filler, 'under_review_case_id', None) if filler else None
                if _ur_id:
                    print(f"[阶段 3/3] ✅ recover 检测到 Under Review Case ID: {_ur_id}，跳过表单填写，直接记录成功")
                    result["case_id"] = _ur_id
                    result["submit_result"] = "success"
                    result["note"] = f"Under Review Case ID detected in panel during recover: {_ur_id}"
                    return result
                if not recovered:
                    result["submit_result"] = "failed"
                    result["note"] = "5461 panel 半加载，close-panel 多次重试后字段仍未加载"
                    return result

            form_result = fill_5461_form(page, brand_name, statement_text, upload_files, account_email)
            result["steps"].append({"phase": 3, "action": "fill_5461_form", "details": form_result})
            
            # [阶段 3] 证据采集：表单填写完成
            capture_evidence_safe(
                page, EVIDENCE_NODES["form_filled"],
                root=evidence_root, account=account_id, brand=brand_name
            )

            # 保存提交前截图
            try:
                shot3 = evidence_dir / "03_before_submit.png"
                page.screenshot(path=str(shot3), full_page=False, timeout=10000)
                result["evidence_files"].append(str(shot3))
            except Exception as e:
                print(f"[警告] 截图失败: {e}")
            
            # dry-run(no_submit)：表单已填写、03_before_submit.png 已留证，在点击提交前短路返回。
            # 注意：下方 429 重试块（含 JS 重新提交 document.querySelector('kat-button#submit_button')）
            # 在 no_submit 下不可达——此处已 return，永远不会执行到任何提交点击，无需额外守卫。
            if no_submit:
                return _dry_run_no_submit_return(3, "主路径 阶段3/3，提交按钮点击前")

            # 人工确认
            if require_human_confirm:
                import sys
                if sys.stdin.isatty():
                    print("[阶段 3/3] 等待人工确认...")
                    input("请检查页面后按 Enter 提交（取消请 Ctrl+C）...")
                else:
                    print("[阶段 3/3] 非交互环境，跳过人工确认...")
            
            # 点击提交
            _submit_429_retried = False  # 429提交重试：每个品牌仅允许重试一次
            print("[阶段 3/3] 点击提交...")
            click_katal_button(page, 'kat-button#submit_button')
            
            # 等待提交处理完成 - 等待 Case ID 出现或页面离开表单状态
            print("[阶段 3/3] 等待提交处理完成...")
            # 先等 5s 让提交请求发出，再由后续 poll 循环负责等 Case ID
            try:
                page.wait_for_function(
                    """() => {
                        const body = (document.body.innerText || '').toLowerCase();
                        return body.includes('case id') ||
                               body.includes('under review') ||
                               body.includes('application submitted') ||
                               !document.querySelector('kat-button#submit_button');
                    }""",
                    timeout=12000
                )
            except Exception:
                pass
            
            # [阶段 3] 证据采集：提交后
            capture_evidence_safe(
                page, EVIDENCE_NODES["after_submit"],
                root=evidence_root, account=account_id, brand=brand_name
            )

            # 保存提交后截图（初始状态）
            try:
                shot4 = evidence_dir / "04_after_submit.png"
                page.screenshot(path=str(shot4), full_page=False, timeout=10000)
                result["evidence_files"].append(str(shot4))
            except Exception as e:
                print(f"[警告] 截图失败: {e}")
            
            # 轮询等待 Case ID 出现（最多等待90秒）
            print("[阶段 3/3] 等待 Case ID 出现...")
            case_id = None
            max_wait = 90  # 最大等待90秒
            poll_interval = 3  # 每3秒检查一次
            elapsed = 0
            
            while elapsed < max_wait:
                case_id = extract_case_id(page)
                if case_id:
                    print(f"[✓] 提取到 Case ID: {case_id}")
                    break
                
                # 检查是否有错误提示
                page_text = page.inner_text('body')
                if "error" in page_text.lower() and "fail" in page_text.lower():
                    print("[✗] 页面显示错误信息")
                    break
                
                # 检查是否仍在加载中
                if "loading" in page_text.lower() or "processing" in page_text.lower():
                    print(f"[阶段 3/3] 仍在处理中... ({elapsed}s)")
                
                time.sleep(poll_interval)
                elapsed += poll_interval
                
                # 刷新页面文本（不刷新页面，只是重新获取）
                try:
                    page_text = page.inner_text('body')
                except:
                    pass
            
            if not case_id:
                print("[警告] 未能在规定时间内提取到 Case ID，保存最终截图...")
                # 保存最终状态截图
                try:
                    shot_final = evidence_dir / "04_after_submit_final.png"
                    page.screenshot(path=str(shot_final), full_page=False, timeout=10000)
                    result["evidence_files"].append(str(shot_final))
                except Exception as e:
                    print(f"[警告] 截图失败: {e}")

                # --- 429 提交重试机制 ---
                # 如果 console_logs 含 429 且尚未重试，则关闭表单、等待、重新提交
                if not _submit_429_retried:
                    _has_429 = any('429' in _l or 'Too Many Requests' in _l for _l in console_logs)
                    if _has_429:
                        _submit_429_retried = True
                        _retry_wait = random.randint(30, 90)
                        print(f"[阶段 3/3] 检测到 429 限流，关闭表单、等待 {_retry_wait}s 后重新提交...")
                        # 关闭 5461 表单 panel
                        try:
                            page.evaluate("""
                                () => {
                                    // 方式1: 查找 Close 文字按钟
                                    const allBtns = document.querySelectorAll("button, kat-button, [role='button']");
                                    for (const btn of allBtns) {
                                        const t = (btn.textContent || btn.getAttribute('label') || '').trim().toLowerCase();
                                        if (t === 'close' || t === '\u00d7' || t === 'x') { btn.click(); return {closed:'btn'}; }
                                    }
                                    // 方式2: aria-label / part 属性
                                    const cb = document.querySelector('button[aria-label="close"], button[part="panel-close-button"]');
                                    if (cb) { cb.click(); return {closed:'aria'}; }
                                    // 方式3: ESC
                                    document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', keyCode:27}));
                                    // 方式4: 强制隐藏 panel
                                    const p = document.querySelector("kat-panel-wrapper[panel-visible='true']");
                                    if (p) p.setAttribute('panel-visible', 'false');
                                    return {closed:'force'};
                                }
                            """)
                            print("[阶段 3/3] 已关闭 5461 表单 panel")
                        except Exception as _e:
                            print(f"[警告] 关闭表单失败: {_e}")
                        time.sleep(_retry_wait)
                        # 重新点击 Apply to sell
                        print("[阶段 3/3] 重新点击 Apply to sell...")
                        _retry_open = filler.click_apply_to_sell()
                        if _retry_open:
                            if not wait_for_5461_form_fields(page, timeout_sec=24, interval_sec=2):
                                print("[阶段 3/3] 429重试后 panel 仍半加载，执行 close-panel 多轮恢复...")
                                _recovered_after_submit_429 = recover_half_loaded_5461_panel(
                                    page, filler, brand_name, max_attempts=3,
                                    reason="submit 后 429 重试重开 panel 半加载"
                                )
                                # ★ recover 可能因检测到 Under Review Case ID 而返回 True
                                _ur_id_429 = getattr(filler, 'under_review_case_id', None) if filler else None
                                if _ur_id_429:
                                    print(f"[阶段 3/3] ✅ 429重试 recover 检测到 Under Review Case ID: {_ur_id_429}，跳过重填，直接记录成功")
                                    result["case_id"] = _ur_id_429
                                    result["submit_result"] = "success"
                                    result["note"] = f"Under Review Case ID detected in panel during 429-retry recover: {_ur_id_429}"
                                    return result
                                if not _recovered_after_submit_429:
                                    print("[阶段 3/3] 429重试恢复失败，停止本品牌，避免提交空表单")
                                    result["submit_result"] = "failed"
                                    result["note"] = "提交后 429；重开 panel 半加载，close-panel 多轮重试后字段仍未加载"
                                    result["steps"].append({"phase": 3, "action": "submit_429_recovery", "status": "half_loaded_failed"})
                                    return result
                            # 重新填写 5461 表单
                            print("[阶段 3/3] 重新填写 5461 表单...")
                            fill_5461_form(page, brand_name, statement_text, upload_files, account_email)
                            # 重新提交
                            print("[阶段 3/3] 重新提交...")
                            try:
                                page.evaluate("""
                                    () => {
                                        const btn = document.querySelector('kat-button#submit_button');
                                        if (btn) { btn.click(); return true; }
                                        const btns = document.querySelectorAll('kat-button');
                                        for (const b of btns) {
                                            if ((b.getAttribute('label') || '').toLowerCase().includes('submit')) {
                                                b.click(); return true;
                                            }
                                        }
                                        return false;
                                    }
                                """)
                                time.sleep(12)
                            except Exception as _e:
                                print(f"[警告] 重新提交失败: {_e}")
                            # 重新轮询 Case ID
                            print("[阶段 3/3] 重新等待 Case ID（最多90秒）...")
                            _elapsed_r = 0
                            while _elapsed_r < 90:
                                case_id = extract_case_id(page)
                                if case_id:
                                    print(f"[OK] 429重试成功，Case ID: {case_id}")
                                    break
                                time.sleep(3)
                                _elapsed_r += 3
                            if not case_id:
                                print("[阶段 3/3] 429重试后仍未获取到 Case ID")
                        else:
                            print("[警告] Apply to sell 重新点击失败，放弃重试")
                # --- 429 提交重试结束 ---
            
            # 设置结果状态 - 只有在提取到 Case ID 或明确成功时才标记为成功
            page_text = page.inner_text('body')
            has_success_indicator = "success" in page_text.lower() or "submitted" in page_text.lower() or "thank" in page_text.lower() or "case" in page_text.lower()
            
            if case_id:
                result["submit_result"] = "success"
                result["case_id"] = case_id
                result["note"] = f"5461 提交成功，Case ID: {case_id}"
            elif has_success_indicator:
                result["submit_result"] = "partial"
                result["note"] = f"已提交，请人工核对结果（未提取到 Case ID）"
            else:
                result["submit_result"] = "failed"
                result["note"] = f"提交后未检测到成功提示，请人工检查"
            
            result["steps"].append({"phase": 3, "action": "submit", "status": result["submit_result"], "case_id": case_id})
            
            # 保存最终确认截图
            try:
                shot_confirm = evidence_dir / "05_final_confirm.png"
                page.screenshot(path=str(shot_confirm), full_page=False, timeout=10000)
                result["evidence_files"].append(str(shot_confirm))
            except Exception as e:
                print(f"[警告] 截图失败: {e}")
            
        except Exception as e:
            import traceback
            err_detail = traceback.format_exc()
            print(f"[致命错误] {e}\n{err_detail}")
            result["submit_result"] = "error"
            result["note"] = f"执行出错: {str(e)}"
            result["steps"].append({"phase": "error", "error": str(e), "traceback": err_detail})
            try:
                err_txt = evidence_dir / "error_traceback.txt"
                err_txt.write_text(err_detail, encoding="utf-8")
                result["evidence_files"].append(str(err_txt))
            except Exception:
                pass
            
            try:
                error_shot = evidence_dir / "error.png"
                page.screenshot(path=str(error_shot), full_page=False, timeout=10000)
                result["evidence_files"].append(str(error_shot))
            except:
                pass
        
        finally:
            # Save final screenshot first, then run the dashboard check while the
            # Seller Central tab is still alive. Dashboard check is read-only and
            # only triggers for uncertain/failed submissions.
            pre_dashboard_status = result.get("submit_result", "failed")
            if pre_dashboard_status in ("failed", "partial", "error", "uncertain"):
                result["failure_page_evidence"] = capture_failure_page_evidence(page)
            elif pre_dashboard_status in ("success", "under_review", "dry_run"):
                result["success_page_evidence"] = capture_failure_page_evidence(page)
            try:
                final_shot = evidence_dir / "final_state.png"
                page.screenshot(path=str(final_shot), full_page=False, timeout=10000)
                result["evidence_files"].append(str(final_shot))
                print(f"[清理] 已保存最终状态截图: {final_shot}")
            except:
                pass

            submit_status = result.get("submit_result", "failed")
            case_id = result.get("case_id")
            needs_dashboard_check = (
                submit_status in ("failed", "partial", "error")
                or (submit_status == "success" and not case_id)
            )
            if not needs_dashboard_check:
                note_l = str(result.get("note", "")).lower()
                needs_dashboard_check = any(k in note_l for k in ["410001", "429", "case id", "未提取", "未检测到成功", "半加载"])

            if needs_dashboard_check:
                dashboard_page = page
                close_dashboard_page = False
                try:
                    from .case_dashboard_checker import check_case_dashboard_for_brand

                    # The post-submit Add Product tab can abort navigation while
                    # requests are still settling. Use a fresh tab in the same
                    # signed-in context so View Selling Applications is not
                    # analysed from stale Add Product content.
                    try:
                        dashboard_page = page.context.new_page()
                        close_dashboard_page = True
                    except Exception:
                        dashboard_page = page
                    dash = check_case_dashboard_for_brand(
                        page=dashboard_page,
                        account_id=account_id,
                        marketplace=marketplace,
                        brand_name=brand_name,
                        evidence_dir=evidence_dir,
                        submitted_at=result.get("submission_started_at"),
                    )
                    result["dashboard_check"] = dash
                    for f in dash.get("evidence_files", []):
                        if f not in result["evidence_files"]:
                            result["evidence_files"].append(f)

                    dash_status = dash.get("status")
                    dash_case_id = dash.get("case_id")
                    if dash_status in ("under_review", "approved") and dash_case_id:
                        result["submit_result"] = "success"
                        result["case_id"] = dash_case_id
                        result["note"] = (result.get("note", "") + f" | Dashboard confirmed {dash_status}: {dash_case_id}").strip(" |")
                    elif dash_status == "draft":
                        result["submit_result"] = "draft"
                        result["note"] = (result.get("note", "") + " | Dashboard found Draft").strip(" |")
                        if dash.get("application_id"):
                            result["draft_application_id"] = dash.get("application_id")
                        result.setdefault("draft_resume", {"available": bool(dash.get("application_id")), "auto_submitted": False})
                    elif dash_status == "declined":
                        result["submit_result"] = "declined"
                        if dash_case_id and not result.get("case_id"):
                            result["case_id"] = dash_case_id
                        result["note"] = (result.get("note", "") + " | Dashboard found Declined application").strip(" |")
                    elif dash_status == "not_found":
                        result["note"] = (result.get("note", "") + " | Dashboard no case/draft found").strip(" |")
                except Exception as dash_err:
                    print(f"[DashboardCheck] 集成检查失败: {dash_err}")
                    result["dashboard_check"] = {"checked": False, "status": "error", "error": str(dash_err)}
                finally:
                    if close_dashboard_page:
                        try:
                            dashboard_page.close()
                        except Exception:
                            pass

            # 提交成功且提取到 Case ID 时关闭标签页节省内存
            # 没有 Case ID 时保留标签页供人工核对
            submit_status = result.get("submit_result", "failed")
            case_id = result.get("case_id")

            if submit_status == "success" and case_id:
                if not keep_browser_open:
                    print(f"[清理] 执行成功（Case ID: {case_id}），关闭标签页...")
                    try:
                        page.close()
                    except:
                        pass
                    try:
                        browser.close()
                    except:
                        pass
                else:
                    print(f"[清理] 批量模式：执行成功（Case ID: {case_id}），保持浏览器开启")
                    # 关闭面板，页面准备好供下一个品牌复用（冷却由 batch 侧控制，此处不等待）
                    reset_info = reset_page_for_next_brand(page, cooldown_sec=0)
                    result["page_ready_for_reuse"] = reset_info.get("ready", False)
                    result["page_reuse_reason"] = reset_info.get("reason", "")
                    if reset_info.get("ready"):
                        print(f"[清理] 面板已关闭，页面就位可复用（url={reset_info.get('url', '')[:80]}）")
                    else:
                        print(f"[清理] 页面复用不可用：{reset_info.get('reason')}")
            elif submit_status == "success" and not case_id:
                print("[清理] 标记为成功但未提取到 Case ID，保留标签页供人工核对...")
            elif submit_status == "under_review":
                print(f"[清理] Under Review（Case ID: {result.get('case_id', '?')}），无需重新申请")
            elif submit_status == "dry_run":
                print("[清理] dry-run：未点击提交，标签页交由调用方（批处理器）关闭")
            else:
                print(f"[清理] 执行失败（{submit_status}），保留标签页用于调试...")
            
            # 保存 console logs
            if console_logs:
                try:
                    log_path = evidence_dir / "console_logs.txt"
                    log_path.write_text("\n".join(console_logs[-100:]), encoding="utf-8")  # 只保存最后100条
                    print(f"[清理] Console logs 已保存: {log_path} ({len(console_logs)} 条)")
                    result["console_logs"] = str(log_path)
                except Exception as e:
                    print(f"[警告] 保存 console logs 失败: {e}")
    
        return result

    if owns_page:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(cdp_url)
            context = browser.contexts[0] if browser.contexts else browser.new_context()
        
            # 使用已有页面，避免创建新页面导致标签页膨胀
            # 注意：跳过 chrome:// 和 start.adspower.net 起始页，不关闭
            existing_pages = context.pages
            closable_pages = []
            for pg in existing_pages:
                try:
                    pg_url = pg.url
                except Exception:
                    pg_url = ''
                if not pg_url.startswith('chrome://') and not pg_url.startswith('https://start.adspower.net'):
                    closable_pages.append(pg)
            if closable_pages:
                page = closable_pages[-1]
                # 真正关闭其他多余标签页（window.close() 对主标签页无效，必须用 Playwright 的 close）
                for old_page in closable_pages[:-1]:
                    try:
                        old_page.close()
                    except:
                        pass
            else:
                page = context.new_page()
        
            page.set_default_timeout(30000)  # 增加超时到 30 秒
        
            # dialog handler 由 _run_core 独占绑定，避免重复处理导致 Playwright ProtocolError
        
            result = _run_core(page)

            if not keep_browser_open:
                try:
                    page.close()
                except:
                    pass
                try:
                    browser.close()
                except:
                    pass
    else:
        print("[复用] 使用已有页面实例，跳过浏览器连接和账号切换")
        result = _run_core(page)

    return result
