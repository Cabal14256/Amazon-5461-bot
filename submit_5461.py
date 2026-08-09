#!/usr/bin/env python3
"""
5461 提交脚本 - 统一的 5461 申请提交入口

用法:
    python submit_5461.py <账号> <品牌名>
    
示例:
    python submit_5461.py us_store_530 WILLONE

流程:
    1. 检查当前页面状态
    2. 如果在 Product Identity: 填写表单 → 点击 Next
    3. 点击 Apply to sell
    4. 填写 5461 表单
    5. 上传图片
    6. 填写联系信息
    7. 提交表单
    8. 提取 Case ID
"""
import sys
import re
import json
import time
import random
import openpyxl
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional

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

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent / "src"))

from browser_manager import BrowserManager
from form_filler import KatalFormFiller
from registration import RegistrationManager


class Submit5461:
    """5461 提交器"""
    
    # 5461 表单选择器
    SELECTORS = {
        'apply_button': 'kat-button[data-cy="seller-qualification-path-forward-button"]',
        'product_title': 'kat-input#question-cat_auth_mo_question_string_id_product_title',
        'manufacturer': 'kat-input#question-cat_auth_mo_question_string_id_manufacturer',
        'product_description': 'kat-input#question-cat_auth_mo_question_string_id_product_description',
        'product_id': 'kat-input#question-cat_auth_mo_question_string_id_product_id',
        'email': 'kat-input#contact_info_email_input',
        'phone': 'kat-input#contact_info_phone_input',
        'file_input': 'input[id*="document_upload"][id*="document_input"]',
        'submit_button': 'kat-button#submit_button',
        'qualification_panel': 'kat-panel[data-testid="kat-panel-QualificationWidget"]',
    }
    
    def __init__(self, account_id: str, brand_name: str, site: str = None):
        self.account_id = account_id
        self.brand_name = brand_name  # 保留原始大小写
        self.site = site  # 指定站点，如 UK, BE, US
        self.bm = BrowserManager()
        self.page = None
        self.config = None
        self.brand_data = None
        self.registry = RegistrationManager()  # 登记表管理器
        
        self.result = {
            'account_id': account_id,
            'brand': self.brand_name,
            'site': site,
            'timestamp': datetime.now().isoformat(),
            'success': False,
            'case_id': None,
            'error': None,
            'screenshots': [],
        }
    
    def connect(self) -> None:
        """连接浏览器"""
        print(f"\n{'='*60}")
        print(f"5461 提交: {self.account_id} / {self.brand_name}")
        if self.site:
            print(f"指定站点: {self.site}")
        print(f"{'='*60}")
        
        self.page, self.config = self.bm.connect_by_account(self.account_id)
        
        # 如果指定了站点，从 marketplace_configs 应用配置
        if self.site and 'marketplace_configs' in self.config:
            mp_configs = self.config.get('marketplace_configs', {})
            if self.site in mp_configs:
                site_config = mp_configs[self.site]
                print(f"[OVERRIDE] 使用指定站点: {self.site} (覆盖账号默认的 {self.config.get('marketplace', 'US')})")
                # 覆盖相关配置
                for key in ['marketplace', 'domain', 'entry_url', 'item_type_keyword', 'mons_sel_mkid']:
                    if key in site_config:
                        old_val = self.config.get(key, '')
                        new_val = site_config[key]
                        if old_val != new_val:
                            print(f"[OVERRIDE] {key}: {str(old_val)[:50]}... -> {str(new_val)[:50]}...")
                        self.config[key] = new_val
            else:
                print(f"[警告] 账号没有 {self.site} 的站点配置，使用默认配置")
    
    def read_brand_data(self) -> Dict[str, Any]:
        """读取品牌数据：优先从 brand_packs 读取，其次从 Excel 读取"""
        print(f"\n[1] 读取品牌数据...")
        
        account_num = self.account_id.split('_')[-1]
        
        # 优先从 brand_packs 读取
        brand_pack_dir = Path(__file__).parent / 'brand_packs' / self.brand_name
        statement_file = brand_pack_dir / 'docs' / f'5461_statement_us.account_{account_num}.txt'
        generic_statement = brand_pack_dir / 'docs' / '5461_statement_us.txt'
        
        brand_pack_data = None
        if statement_file.exists() and statement_file.stat().st_size > 100:
            brand_pack_data = self._parse_statement_file(statement_file)
            print(f"    [OK] 从 brand_packs 读取到账号专属文案")
        if not brand_pack_data and generic_statement.exists() and generic_statement.stat().st_size > 100:
            brand_pack_data = self._parse_statement_file(generic_statement)
            print(f"    [OK] 从 brand_packs 读取到通用文案")
        
        # 从 Excel 补充
        excel_data = self._read_excel_brand_data()
        
        if brand_pack_data:
            self.brand_data = {
                'region': brand_pack_data.get('region', excel_data.get('region', 'US')),
                'account': excel_data.get('account', f'正常号-US-{account_num}号'),
                'country': brand_pack_data.get('country', excel_data.get('country', 'US')),
                'brand': self.brand_name,
                'sku': brand_pack_data.get('sku', excel_data.get('sku', f'{account_num}-US-{self.brand_name}-XX')),
                'title': brand_pack_data.get('title', excel_data.get('title', f'{self.brand_name} Screen Protector')),
                'description': brand_pack_data.get('description', excel_data.get('description', '')),
            }
            if not excel_data.get('has_account_match'):
                print(f"    [信息] Excel 中缺少数据，自动补充...")
                self._append_to_excel(self.brand_data)
        else:
            self.brand_data = excel_data
        
        # 确保字段不为 None
        for key in ['title', 'description', 'sku', 'country', 'account']:
            if self.brand_data.get(key) is None:
                self.brand_data[key] = ''
        
        print(f"    [OK] 品牌: {self.brand_data['brand']}")
        print(f"    [OK] SKU: {self.brand_data['sku']}")
        title_preview = str(self.brand_data['title'])[:50] if self.brand_data.get('title') else ''
        print(f"    [OK] 标题: {title_preview}...")
        return self.brand_data
    
    def _parse_statement_file(self, file_path):
        """解析 5461 声明文案文件"""
        import re
        with open(file_path, 'r', encoding='utf-8') as f:
            text = f.read()
        
        data = {}
        patterns = {
            'brand': r'Brand[:：]\s*(.+)',
            'manufacturer': r'Manufacturer[:：]\s*(.+)',
            'title': r'Item name[:：]\s*(.+)',
            'country': r'Item Category[:：]\s*(.+)',
            'sku': r'SKU[:：]\s*(.+)',
            'description': r'Item desrciption[:：]\s*(.+)',
        }
        
        for key, pattern in patterns.items():
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                data[key] = match.group(1).strip()
        
        data['description'] = text[:500]
        
        if data.get('sku'):
            parts = data['sku'].split('-')
            if len(parts) >= 2:
                data['country'] = parts[1].upper()
        
        return data
    
    def _read_excel_brand_data(self):
        """从 Excel 读取品牌数据"""
        import re
        excel_candidates = list(Path(__file__).parent.glob('5461*.xlsx'))
        if not excel_candidates:
            return {}
        
        excel_path = excel_candidates[0]
        wb = openpyxl.load_workbook(excel_path, data_only=True)
        ws = wb.worksheets[4]
        
        account_num = self.account_id.split('_')[-1]
        
        matching_rows = []
        for row in ws.iter_rows(min_row=7, values_only=True):
            if row[3] and str(row[3]).lower() == self.brand_name.lower():
                matching_rows.append(row)
        
        if not matching_rows:
            return {'has_account_match': False}
        
        selected_row = None
        for row in matching_rows:
            sku = str(row[4]) if row[4] else ""
            account = str(row[1]) if row[1] else ""
            if account_num in sku or account_num in account:
                selected_row = row
                break
        
        has_account_match = selected_row is not None
        if not selected_row:
            selected_row = matching_rows[0]
        
        original_sku = str(selected_row[4]) if selected_row[4] else ""
        adapted_sku = re.sub(r'^(\d+)-', f'{account_num}-', original_sku)
        
        return {
            'region': selected_row[0] or '',
            'account': str(selected_row[1]) if selected_row[1] else f'正常号-US-{account_num}号',
            'country': selected_row[2] or '',
            'sku': adapted_sku or '',
            'title': selected_row[5] or '',
            'description': selected_row[6] or '',
            'has_account_match': has_account_match,
        }
    
    def _append_to_excel(self, brand_data):
        """将品牌数据追加到 Excel"""
        try:
            excel_candidates = list(Path(__file__).parent.glob('5461*.xlsx'))
            if not excel_candidates:
                return
            
            excel_path = excel_candidates[0]
            wb = openpyxl.load_workbook(excel_path)
            ws = wb.worksheets[4]
            
            last_row = ws.max_row + 1
            
            ws.cell(row=last_row, column=1, value=brand_data.get('region'))
            ws.cell(row=last_row, column=2, value=brand_data.get('account'))
            ws.cell(row=last_row, column=3, value=brand_data.get('country'))
            ws.cell(row=last_row, column=4, value=brand_data['brand'])
            ws.cell(row=last_row, column=5, value=brand_data.get('sku'))
            ws.cell(row=last_row, column=6, value=brand_data.get('title'))
            ws.cell(row=last_row, column=7, value=brand_data.get('description'))
            
            wb.save(excel_path)
            print(f"    [OK] 已追加到 Excel 第 {last_row} 行")
        except Exception as e:
            print(f"    [警告] 追加到 Excel 失败: {e}")

    def get_image_files(self) -> List[str]:
        """获取品牌图片文件"""
        print(f"\n[2] 获取品牌图片...")
        
        picture_base = Path(__file__).parent / 'picture'
        brand_folder = picture_base / self.brand_name
        
        if not brand_folder.exists():
            raise FileNotFoundError(f"图片文件夹不存在: {brand_folder}")
        
        # 获取所有图片文件
        image_extensions = {'.jpg', '.jpeg', '.png'}
        excluded_files = {'Thumbs.db', '.DS_Store', 'desktop.ini'}
        
        images = [
            str(f) for f in brand_folder.iterdir()
            if f.suffix.lower() in image_extensions and f.name not in excluded_files
        ]
        
        if not images:
            raise FileNotFoundError(f"品牌文件夹中没有图片: {brand_folder}")
        
        print(f"    [OK] 找到 {len(images)} 张图片")
        return images
    
    def check_page_state(self) -> str:
        """检查当前页面状态"""
        current_url = self.page.url
        page_text = self.page.inner_text('body')
        
        # 检查是否有 5461 表单弹窗已打开
        try:
            qualification_panel = self.page.locator('kat-panel-wrapper[data-testid="kat-panel-wrapper-QualificationWidget"]').first
            if qualification_panel.count() > 0:
                panel_visible = qualification_panel.get_attribute('panel-visible')
                if panel_visible == 'true':
                    # 弹窗已打开，检查内容
                    panel_text = qualification_panel.inner_text()
                    if 'Listing approval' in panel_text or 'Submit required information' in panel_text:
                        return '5461_FORM_OPEN'
                    elif 'Brand Authorization Required' in panel_text:
                        return 'AUTH_REQUIRED'
                    elif 'Apply to sell' in panel_text:
                        # 弹窗刚打开，内容可能还在加载
                        return '5461_FORM_OPEN'
        except:
            pass
        
        # 检查是否是 GTIN 豁免页面（排除仍在 Product Identity 页面的情况）
        if 'product_identity' not in current_url.lower() and ('GTIN Exemption' in page_text or 'UPC Exemption' in page_text or 'gtin-exemption' in current_url.lower()):
            return 'GTIN_EXEMPTION'
        if 'product_identity' not in current_url.lower() and 'document_upload_new' in page_text and 'Branding on the image' in page_text:
            return 'GTIN_EXEMPTION'
        
        if 'product_identity' in current_url.lower():
            # 检查是否显示授权要求（但弹窗未打开）
            if 'Brand Authorization Required' in page_text:
                return 'AUTH_REQUIRED'
            # 检查是否有 Suggest an ASIN / UPC Exemption Required 提示
            if 'UPC Exemption Required' in page_text or 'Suggest an ASIN' in page_text:
                return 'PRODUCT_IDENTITY_UPC_REQUIRED'
            return 'PRODUCT_IDENTITY'
        elif 'Brand Authorization Required' in page_text:
            return 'AUTH_REQUIRED'
        elif 'Listing approval' in page_text or 'document_upload_new' in page_text:
            return '5461_FORM'
        else:
            return 'UNKNOWN'
    
    def handle_product_identity(self) -> bool:
        """处理 Product Identity 页面"""
        print(f"\n[3] 处理 Product Identity 页面...")
        
        # 先关闭可能打开的 5461 表单弹窗
        print("    检查并关闭弹窗...")
        try:
            # 注意：不再关闭 QualificationWidget 面板，因为关闭后可能导致 Apply to sell 无法重新打开
            # 只关闭非 QualificationWidget 的面板
            other_panels = self.page.locator('kat-panel-wrapper[panel-visible="true"]').all()
            for p in other_panels:
                testid = p.get_attribute('data-testid') or ''
                if 'QualificationWidget' not in testid:
                    p.evaluate('el => el.setAttribute("panel-visible", "false")')
                    time.sleep(0.5)
            print("    [OK] 非QualificationWidget弹窗已关闭")
        except Exception as e:
            print(f"    [信息] 关闭弹窗: {e}")
        
        filler = KatalFormFiller(self.page)
        
        # 从 brand_data 的 title 提取关键词作为 item_type_hint
        item_type_hint = ""
        if self.brand_data and self.brand_data.get('title'):
            title = self.brand_data['title'].lower()
            # 提取关键词用于匹配
            if 'cell phone' in title or 'iphone' in title or 'samsung' in title:
                item_type_hint = "cell phone"
            elif 'screen protector' in title or 'tempered glass' in title:
                item_type_hint = "screen protector"
            elif 'case' in title or 'cover' in title:
                item_type_hint = "case"
            elif 'charger' in title or 'cable' in title:
                item_type_hint = "charger"
            print(f"    [信息] 从标题提取 item_type_hint: '{item_type_hint}'")
        
        # 填写表单
        fill_results = filler.fill_product_identity_form(self.brand_name, item_type_hint=item_type_hint)
        
        if not all(fill_results.values()):
            print("[警告] 部分字段填写失败，但继续尝试...")
        
        # 保存填写后的截图
        try:
            self.bm.screenshot(f"artifacts/5461/after_fill_{self.brand_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")
        except:
            pass
        
        # 先检查页面底部是否已出现 Apply to sell 按钮（某些情况下不需要点 Next）
        has_apply_now = self.page.evaluate('''() => {
            const allBtns = document.querySelectorAll('kat-button, button, a');
            for (const b of allBtns) {
                const text = (b.getAttribute('label') || b.textContent || b.innerText || '').toLowerCase();
                if (text.includes('apply to sell')) {
                    return { found: true, text: text.substring(0, 50) };
                }
            }
            // 也检查通知文本
            const pageText = document.body.innerText || '';
            if (pageText.includes('Brand Authorization Required') || pageText.includes('UPC Exemption Required')) {
                return { found: true, text: 'brand_auth_or_upc_notice' };
            }
            return { found: false };
        }''')
        print(f"[3] 页面底部 Apply to sell 检查: {has_apply_now}")
        
        if has_apply_now.get('found'):
            # 页面已出现授权通知，直接点 Apply to sell
            print("[3] 页面已出现授权通知，直接点 Apply to sell（不点 Next）")
            if not self.click_apply_to_sell():
                print("[3] 直接点 Apply to sell 失败，尝试先点 Next")
                next_clicked = filler.click_next(check_enabled=True, timeout=10)
                if not next_clicked:
                    print("[错误] 点击 Next 失败")
                    return False
                # 等待页面状态变化（离开 product_identity 或 panel 出现），最多 5s
                try:
                    self.page.wait_for_function(
                        """() => {
                            const url = window.location.href;
                            const p = document.querySelector('kat-panel-wrapper[panel-visible="true"]');
                            return !url.includes('product_identity') || p !== null;
                        }""",
                        timeout=5000
                    )
                except Exception:
                    pass
            else:
                # Apply to sell 成功，等待 panel 出现，最多 5s
                try:
                    self.page.wait_for_function(
                        """() => document.querySelector('kat-panel-wrapper[panel-visible="true"]') !== null""",
                        timeout=5000
                    )
                except Exception:
                    pass
        else:
            # 没有授权通知，点 Next 继续
            next_clicked = filler.click_next(check_enabled=True, timeout=10)
            if not next_clicked:
                print("[错误] 点击 Next 失败")
                return False
            # 等待页面状态变化，最多 5s
            try:
                self.page.wait_for_function(
                    """() => {
                        const url = window.location.href;
                        return !url.includes('product_identity') ||
                               document.querySelector('kat-panel-wrapper[panel-visible="true"]') !== null;
                    }""",
                    timeout=5000
                )
            except Exception:
                pass
        
        # 检查是否出现品牌选择提示（针对未在 Brand Registry 注册的品牌）
        try:
            page_text = self.page.inner_text('body')
            if 'Select brand' in page_text or 'Clarify which brand' in page_text:
                print("\n[3] 检测到品牌选择提示，尝试点击 'Select brand' 按钮...")
                # 尝试点击 Select brand 按钮
                select_brand_btn = self.page.locator('button').filter(has_text=re.compile("Select brand", re.IGNORECASE)).first
                if select_brand_btn.count() > 0 and select_brand_btn.is_visible():
                    select_brand_btn.click()
                    print("[3] 已点击 'Select brand' 按钮")
                    # 等待品牌选项弹窗出现，最多 3s
                    try:
                        self.page.wait_for_selector(
                            'kat-box[data-testid="brand-info-option"], kat-radio, input[type="radio"]',
                            timeout=3000
                        )
                    except Exception:
                        pass
                    # 截图查看弹窗内容
                    self.bm.screenshot(f"artifacts/5461/select_brand_popup_{self.brand_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")
                    print("[3] 已保存品牌选择弹窗截图")
                    
                    # 根据品牌文案智能选择正确的品牌
                    brand_keywords = {
                        'JZG': ['protective phone cases', 'screen protectors', 'Samsung', 'Google Pixel', 'Apple iPhone'],
                        'MP-MALL': ['phone and tablet accessories', 'screen protectors', 'phone mounts', 'camera lens covers', 'smart watch protectors'],
                        'XDesign': ['protective cases', 'battery packs', 'chargers', 'cables'],
                        'OUNNE': ['Cell phone film', 'lens film', 'mobile phone film'],
                        'VASG': ['phone cases', 'screen protectors', 'smartwatch', '手机保护壳', '手机保护膜', '手表保护膜']
                    }
                    
                    keywords = brand_keywords.get(self.brand_name, [self.brand_name.lower()])
                    print(f"[3] 品牌 '{self.brand_name}' 的关键词: {keywords}")
                    
                    # 在弹窗中查找匹配的品牌选项
                    print("[3] 尝试自动选择匹配的品牌...")
                    
                    # 先等待弹窗内容加载
                    time.sleep(2)
                    
                    selected = self.page.evaluate(f'''
                        (keywords) => {{
                            console.log('开始查找品牌选项，关键词:', keywords);
                            
                            // 方法1: 查找所有单选按钮
                            const radioInputs = document.querySelectorAll('input[type="radio"]');
                            const radioRoles = document.querySelectorAll('[role="radio"]');
                            console.log('找到 input[type="radio"] 数量:', radioInputs.length);
                            console.log('找到 [role="radio"] 数量:', radioRoles.length);
                            
                            // 合并所有可能的单选元素
                            let allRadios = [...Array.from(radioInputs), ...Array.from(radioRoles)];
                            
                            // 方法2: 查找包含品牌描述的父元素，然后找其中的单选按钮
                            const brandContainers = document.querySelectorAll('[class*="brand"], [class*="option"], .kat-selectable-item, .kat-radio-item, [data-testid*="brand"]');
                            console.log('找到品牌容器数量:', brandContainers.length);
                            
                            // 遍历所有品牌容器，查找包含关键词的
                            for (let i = 0; i < brandContainers.length; i++) {{
                                const container = brandContainers[i];
                                const text = (container.textContent || container.innerText || '').trim();
                                console.log('容器 ' + i + ' 文本:', text.substring(0, 150));
                                
                                // 检查是否包含关键词
                                for (let keyword of keywords) {{
                                    if (text.toLowerCase().includes(keyword.toLowerCase())) {{
                                        console.log('匹配到关键词:', keyword);
                                        
                                        // 查找容器内的单选按钮
                                        const radio = container.querySelector('input[type="radio"], [role="radio"]');
                                        if (radio) {{
                                            console.log('找到单选按钮，点击...');
                                            radio.click();
                                            return {{index: i, text: text.substring(0, 100), matched: keyword, type: 'container-radio'}};
                                        }}
                                        
                                        // 如果容器本身是可点击的
                                        if (container.getAttribute('role') === 'radio' || container.tagName.toLowerCase() === 'kat-radio') {{
                                            console.log('容器本身是可点击的，点击...');
                                            container.click();
                                            return {{index: i, text: text.substring(0, 100), matched: keyword, type: 'container-click'}};
                                        }}
                                    }}
                                }}
                            }}
                            
                            // 方法3: 直接查找所有文本元素，找到匹配关键词的，然后找其前面的单选按钮
                            const allElements = document.querySelectorAll('*');
                            for (let el of allElements) {{
                                if (el.children.length === 0) {{  // 只检查叶子节点
                                    const text = (el.textContent || '').trim();
                                    if (text.length > 30 && text.length < 300) {{
                                        for (let keyword of keywords) {{
                                            if (text.toLowerCase().includes(keyword.toLowerCase())) {{
                                                console.log('在叶子节点找到匹配:', text.substring(0, 100));
                                                // 向上查找父元素中的单选按钮
                                                let parent = el.parentElement;
                                                for (let p = 0; p < 5 && parent; p++) {{  // 向上查找5层
                                                    const radio = parent.querySelector('input[type="radio"], [role="radio"]');
                                                    if (radio) {{
                                                        console.log('在父元素中找到单选按钮，点击...');
                                                        radio.click();
                                                        return {{index: 0, text: text.substring(0, 100), matched: keyword, type: 'parent-radio'}};
                                                    }}
                                                    parent = parent.parentElement;
                                                }}
                                            }}
                                        }}
                                    }}
                                }}
                            }}
                            
                            // 如果没找到匹配，点击第一个单选按钮
                            if (allRadios.length > 0) {{
                                console.log('未找到匹配，点击第一个单选按钮');
                                allRadios[0].click();
                                return {{index: 0, text: '第一个单选按钮（默认）', matched: 'default'}};
                            }}
                            
                            return null;
                        }}
                    ''', keywords)
                    
                    if selected:
                        print(f"[3] 已选择品牌选项 #{selected['index']}: {selected['text']}")
                        print(f"[3] 匹配关键词: {selected['matched']}")
                        # 等待 Connect this brand 按钮变为 enabled，最多 3s
                        try:
                            self.page.wait_for_function(
                                """() => {
                                    for (const btn of document.querySelectorAll('button')) {
                                        if (btn.textContent.includes('Connect') && !btn.disabled)
                                            return true;
                                    }
                                    return false;
                                }""",
                                timeout=3000
                            )
                        except Exception:
                            pass
                        
                        # 滚动到按钮位置并等待按钮启用
                        print("[3] 等待 'Connect this brand' 按钮启用...")
                        
                        # 使用 JavaScript 滚动到按钮位置
                        self.page.evaluate('''() => {
                            const buttons = document.querySelectorAll('button');
                            for (let btn of buttons) {
                                if (btn.textContent.includes('Connect')) {
                                    btn.scrollIntoView({ behavior: 'smooth', block: 'center' });
                                    break;
                                }
                            }
                        }''')\
                        # 等待 Connect 按钮可见（scrollIntoView 完成），最多 2s
                        try:
                            self.page.wait_for_function(
                                """() => {
                                    for (const btn of document.querySelectorAll('button')) {
                                        if (btn.textContent.includes('Connect') && !btn.disabled)
                                            return true;
                                    }
                                    return false;
                                }""",
                                timeout=2000
                            )
                        except Exception:
                            pass
                        
                        # 尝试多种方式查找按钮
                        connect_btn = None
                        for selector in [
                            'button:has-text("Connect this brand")',
                            'button[class*="connect"]',
                            'button:has-text("Connect")',
                            'kat-button:has-text("Connect")',
                            '[class*="connect-brand"] button'
                        ]:
                            try:
                                btn = self.page.locator(selector).first
                                if btn.count() > 0 and btn.is_visible():
                                    connect_btn = btn
                                    print(f"[3] 使用选择器找到按钮: {selector}")
                                    break
                            except:
                                continue
                        
                        if connect_btn:
                            # 等待按钮启用（最多等待10秒）
                            for i in range(10):
                                if connect_btn.is_enabled():
                                    print(f"[3] 按钮已启用，点击...")
                                    connect_btn.click()
                                    print("[3] 已点击 'Connect this brand' 按钮")
                                    # 等待弹窗关闭或 URL 跳转，最多 5s 早退
                                    print("[3] 等待页面响应...")
                                    try:
                                        self.page.wait_for_function(
                                            """() => {
                                                const popup = document.querySelector(
                                                    '[class*="modal"], [class*="dialog"], [role="dialog"], kat-modal'
                                                );
                                                const url = window.location.href;
                                                return (!popup || popup.style.display === 'none' || !popup.isConnected) ||
                                                       url.includes('seller-qualification') ||
                                                       url.includes('description');
                                            }""",
                                            timeout=5000
                                        )
                                    except Exception:
                                        pass
                                    
                                    # 检查弹窗是否关闭
                                    popup_closed = self.page.evaluate('''() => {
                                        const popup = document.querySelector('[class*="modal"], [class*="dialog"], [role="dialog"], kat-modal');
                                        return !popup || popup.style.display === 'none' || !popup.isConnected;
                                    }''')
                                    
                                    if popup_closed:
                                        print("[3] 弹窗已关闭，继续流程...")
                                    else:
                                        print("[3] 弹窗仍在，等待更多时间...")
                                        try:
                                            self.page.wait_for_function(
                                                """() => {
                                                    const popup = document.querySelector(
                                                        '[class*="modal"], [class*="dialog"], [role="dialog"], kat-modal'
                                                    );
                                                    return !popup || popup.style.display === 'none' || !popup.isConnected;
                                                }""",
                                                timeout=5000
                                            )
                                        except Exception:
                                            pass
                                    break
                                else:
                                    print(f"[3] 按钮未启用，等待... ({i+1}/10)")
                                    time.sleep(1)
                            else:
                                print("[3] 按钮仍未启用，尝试强制点击...")
                                try:
                                    # 使用 JavaScript 强制点击
                                    self.page.evaluate('''() => {
                                        const btn = document.querySelector('button');
                                        const buttons = document.querySelectorAll('button');
                                        for (let b of buttons) {
                                            if (b.textContent.includes('Connect')) {
                                                b.disabled = false;
                                                b.click();
                                                return true;
                                            }
                                        }
                                        return false;
                                    }''')
                                    time.sleep(3)
                                except Exception as e:
                                    print(f"[3] 强制点击失败: {e}")
                                    print("[3] 保存截图等待人工介入...")
                                    self.bm.screenshot(f"artifacts/5461/brand_select_wait_{self.brand_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")
                                    print("\n[人工介入] 请手动点击 'Connect this brand' 按钮后按 Enter 继续...")
                                    input()
                                    time.sleep(3)
                        else:
                            print("[3] 未找到 'Connect this brand' 按钮，保存截图...")
                            self.bm.screenshot(f"artifacts/5461/brand_select_manual_{self.brand_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")
                            print("\n[人工介入] 请手动选择品牌并点击 'Connect this brand' 按钮后按 Enter 继续...")
                            input()
                            time.sleep(3)
                    else:
                        print("[3] 未找到匹配的品牌选项，保存截图等待人工介入...")
                        self.bm.screenshot(f"artifacts/5461/brand_select_manual_{self.brand_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")
                        print("\n[人工介入] 请手动选择正确的品牌后按 Enter 继续...")
                        input()
                        time.sleep(3)
                else:
                    print("[3] 未找到 'Select brand' 按钮")
        except Exception as e:
            print(f"[3] 检查品牌选择提示时出错: {e}")
            import traceback
            traceback.print_exc()
        
        # 检查是否进入授权页面或 5461 表单
        # 先处理可能出现的类别更改确认对话框
        try:
            confirm_btn = self.page.locator('#category_picker_modal_ok').first
            if confirm_btn.count() > 0 and confirm_btn.is_visible():
                print("[3] 检测到类别更改确认对话框，点击 'Yes, start over'...")
                confirm_btn.click(force=True, timeout=5000)
                time.sleep(3)
        except Exception as e:
            pass  # No dialog, continue
        
        state = self.check_page_state()
        print(f"[3] 点击 Next 后页面状态: {state}")
        
        if state in ['AUTH_REQUIRED', '5461_FORM', '5461_FORM_OPEN']:
            print("[3] 成功进入授权页面/5461表单")
            return True
        elif state == 'PRODUCT_IDENTITY_UPC_REQUIRED':
            # 检测到 UPC Exemption Required / Suggest an ASIN，需要点击 Apply to sell 进入 GTIN 豁免流程
            print("[3] 检测到 UPC Exemption Required，点击 Apply to sell 进入 GTIN 豁免流程...")
            if not self.click_apply_to_sell():
                print("[3] 点击 Apply to sell 失败")
                return False
            # 等待 panel 或 URL 变化，最多 5s 早退
            try:
                self.page.wait_for_function(
                    """() => {
                        const url = window.location.href;
                        const p = document.querySelector('kat-panel-wrapper[panel-visible="true"]');
                        return p !== null || url.includes('seller-qualification') || url.includes('gtin');
                    }""",
                    timeout=5000
                )
            except Exception:
                pass
            state = self.check_page_state()
            print(f"[3] 点击 Apply to sell 后状态: {state}")
            if state == 'GTIN_EXEMPTION':
                return True
            return True
        elif state == 'GTIN_EXEMPTION':
            # Next 后直接进入 GTIN 豁免页面（墨西哥站点常见）
            print("[3] 已进入 GTIN 豁免页面")
            return True
        elif state == 'PRODUCT_IDENTITY':
            # 可能还在 Product Identity 页面，检查是否需要授权
            page_text = self.page.inner_text('body')
            if 'Brand Authorization Required' in page_text:
                print("[3] 检测到 Brand Authorization Required，需要点击 Apply to sell")
                # 点击 Apply to sell 按钮
                if not self.click_apply_to_sell():
                    print("[3] 点击 Apply to sell 失败")
                    return False
                # 等待 5461 panel 出现，最多 5s 早退
                try:
                    self.page.wait_for_function(
                        """() => document.querySelector('kat-panel-wrapper[panel-visible="true"]') !== null""",
                        timeout=5000
                    )
                except Exception:
                    pass
                # 再次检查状态
                state = self.check_page_state()
                print(f"[3] 点击 Apply to sell 后状态: {state}")
                if state in ['AUTH_REQUIRED', '5461_FORM', '5461_FORM_OPEN']:
                    return True
                return True  # 即使状态未知，也继续尝试
            else:
                print("[3] 仍在 Product Identity 页面，等待页面更新...")
                # 等待 URL 变化或 panel 出现，最多 5s 早退
                try:
                    self.page.wait_for_function(
                        """() => {
                            const url = window.location.href;
                            return !url.includes('product_identity') ||
                                   document.querySelector('kat-panel-wrapper[panel-visible="true"]') !== null;
                        }""",
                        timeout=5000
                    )
                except Exception:
                    pass
                state = self.check_page_state()
                print(f"[3] 等待后状态: {state}")
                if state in ['AUTH_REQUIRED', '5461_FORM', '5461_FORM_OPEN', 'GTIN_EXEMPTION']:
                    return True
                # 再等一次
                try:
                    self.page.wait_for_function(
                        """() => {
                            const url = window.location.href;
                            return !url.includes('product_identity') ||
                                   document.querySelector('kat-panel-wrapper[panel-visible="true"]') !== null;
                        }""",
                        timeout=5000
                    )
                except Exception:
                    pass
                state = self.check_page_state()
                print(f"[3] 再次等待后状态: {state}")
                if state in ['AUTH_REQUIRED', '5461_FORM', '5461_FORM_OPEN', 'GTIN_EXEMPTION']:
                    return True
                return False
        elif state == 'UNKNOWN':
            # 检查是否在 Description 页面（可能不需要 5461）
            page_text = self.page.inner_text('body')
            current_url = self.page.url
            
            if 'Description' in page_text or 'description' in current_url.lower():
                print("[3] 已进入 Description 页面，该品牌可能不需要 5461 申请")
                print("[3] 该账号可能已有权限销售此品牌")
                return True  # 返回 True，让主流程处理这种情况
            else:
                print(f"[3] 未知页面状态: {state}")
                print(f"[3] 当前 URL: {current_url}")
                return False
        else:
            print(f"[3] 未知页面状态: {state}")
            return False
    
    def click_apply_to_sell(self) -> bool:
        """点击 Apply to sell 按钮"""
        print(f"\n[4] 点击 Apply to sell...")
        
        try:
            # 注意：不再关闭弹窗，让 Amazon 自然显示面板
            # 之前关闭弹窗会干扰 Apply to sell 后的选择面板加载
            
            # 方法1: 使用 ID 选择器点击（最可靠）
            try:
                apply_btn = self.page.locator('kat-button#path-forward-button').first
                if apply_btn.count() > 0 and apply_btn.is_visible():
                    # 确保按钮可点击（滚动到视图）
                    apply_btn.scroll_into_view_if_needed()
                    time.sleep(0.5)
                    # UK/EU站点: 弹窗可能遮挡按钮，使用 force=True 强制点击
                    apply_btn.click(force=True)
                    print("    [OK] 使用 ID 选择器点击 Apply to sell")
                    # 等待 QualificationWidget panel 出现，最多 3s 早退
                    try:
                        self.page.wait_for_selector(
                            'kat-panel-wrapper[data-testid="kat-panel-wrapper-QualificationWidget"]',
                            timeout=3000
                        )
                    except Exception:
                        pass

                    # 选择 "Application to create new ASINs"（带重试）
                    selected = False
                    for retry in range(3):
                        if retry > 0:
                            print(f"    [重试 {retry}/2] 重新点击 Apply to sell...")
                            apply_btn.click(force=True)
                            try:
                                self.page.wait_for_selector(
                                    'kat-box.clickable, kat-box[data-testid]',
                                    timeout=5000
                                )
                            except Exception:
                                pass
                        result = self._select_create_new_asins()
                        if result:
                            selected = True
                            break
                        print(f"    [信息] 选择失败，等待10秒后重试...")
                        try:
                            self.page.wait_for_selector(
                                'kat-box.clickable, kat-box[data-testid]',
                                timeout=10000
                            )
                        except Exception:
                            pass
                    
                    if not selected:
                        print("    [警告] 3次选择均失败，可能是410001错误，继续尝试...")
                    
                    # 显示弹窗（关键步骤！）
                    print("    显示5461表单弹窗...")
                    self.page.evaluate('''
                        const panel = document.querySelector('kat-panel-wrapper[data-testid="kat-panel-wrapper-QualificationWidget"]');
                        if (panel) {
                            panel.setAttribute('panel-visible', 'true');
                            panel.style.display = 'block';
                            panel.style.visibility = 'visible';
                            panel.style.opacity = '1';
                            panel.style.zIndex = '9999';
                            
                            // 移除遮罩层
                            const masks = document.querySelectorAll('.mask, [class*="mask"], .overlay, [class*="overlay"]');
                            masks.forEach(m => {
                                m.style.display = 'none';
                            });
                        }
                    ''')
                    time.sleep(2)
                    return True
            except Exception as e:
                print(f"    [警告] ID 选择器点击失败: {e}")
            
            # 方法2: 使用 data-cy 选择器点击
            try:
                apply_btn = self.page.locator('kat-button[data-cy="seller-qualification-path-forward-button"]').first
                if apply_btn.count() > 0 and apply_btn.is_visible():
                    apply_btn.click(force=True)
                    print("    [OK] 使用 data-cy 选择器点击 Apply to sell")
                    # 等待 QualificationWidget panel 出现，最多 3s 早退
                    try:
                        self.page.wait_for_selector(
                            'kat-panel-wrapper[data-testid="kat-panel-wrapper-QualificationWidget"]',
                            timeout=3000
                        )
                    except Exception:
                        pass

                    # 选择 "Application to create new ASINs"（带重试）
                    for retry in range(3):
                        if retry > 0:
                            apply_btn.click(force=True)
                            try:
                                self.page.wait_for_selector(
                                    'kat-box.clickable, kat-box[data-testid]',
                                    timeout=5000
                                )
                            except Exception:
                                pass
                        if self._select_create_new_asins():
                            break
                        try:
                            self.page.wait_for_selector(
                                'kat-box.clickable, kat-box[data-testid]',
                                timeout=10000
                            )
                        except Exception:
                            pass

                    # 等待 5461 表单字段出现，最多 2s 早退
                    try:
                        self.page.wait_for_selector(
                            'kat-input#question-cat_auth_mo_question_string_id_product_title',
                            timeout=2000
                        )
                    except Exception:
                        pass
                    return True
            except Exception as e:
                print(f"    [警告] data-cy 选择器点击失败: {e}")
            
            # 方法3: 使用 JavaScript 点击（通过Shadow DOM）
            result = self.page.evaluate('''
                const katBtn = document.querySelector('kat-button#path-forward-button');
                if (!katBtn) return { success: false, reason: 'button not found' };
                
                // 滚动到视图
                katBtn.scrollIntoView({ behavior: 'smooth', block: 'center' });
                
                return new Promise((resolve) => {
                    setTimeout(() => {
                        // 点击shadowRoot内部的button
                        if (katBtn.shadowRoot) {
                            const innerBtn = katBtn.shadowRoot.querySelector('button');
                            if (innerBtn) {
                                innerBtn.click();
                            }
                        }
                        // 也触发kat-button的点击
                        katBtn.click();
                        
                        resolve({ success: true });
                    }, 500);
                });
            ''')
            
            if result.get('success'):
                print("    [OK] 使用 JavaScript 点击 Apply to sell")
                # 等待 QualificationWidget panel 出现，最多 3s 早退
                try:
                    self.page.wait_for_selector(
                        'kat-panel-wrapper[data-testid="kat-panel-wrapper-QualificationWidget"]',
                        timeout=3000
                    )
                except Exception:
                    pass

                # 选择 "Application to create new ASINs"（带重试）
                for retry in range(3):
                    if retry > 0:
                        print(f"    [重试 {retry}/2] 重新点击 Apply to sell...")
                        katBtn = self.page.locator('kat-button#path-forward-button').first
                        if katBtn.count() > 0:
                            katBtn.click()
                        try:
                            self.page.wait_for_selector(
                                'kat-box.clickable, kat-box[data-testid]',
                                timeout=5000
                            )
                        except Exception:
                            pass
                    if self._select_create_new_asins():
                        break
                    try:
                        self.page.wait_for_selector(
                            'kat-box.clickable, kat-box[data-testid]',
                            timeout=10000
                        )
                    except Exception:
                        pass
                
                # 显示弹窗（关键步骤！）
                print("    显示5461表单弹窗...")
                self.page.evaluate('''
                    const panel = document.querySelector('kat-panel-wrapper[data-testid="kat-panel-wrapper-QualificationWidget"]');
                    if (panel) {
                        panel.setAttribute('panel-visible', 'true');
                        panel.style.display = 'block';
                        panel.style.visibility = 'visible';
                        panel.style.opacity = '1';
                        panel.style.zIndex = '9999';
                        
                        // 移除遮罩层
                        const masks = document.querySelectorAll('.mask, [class*="mask"], .overlay, [class*="overlay"]');
                        masks.forEach(m => {
                            m.style.display = 'none';
                        });
                    }
                ''')
                time.sleep(2)
                return True
            else:
                print(f"    [错误] 点击失败: {result.get('reason')}")
                return False
            
        except Exception as e:
            print(f"    [错误] 点击失败: {e}")
            return False
    
    def _ensure_marketplace(self):
        """确保浏览器在正确的 marketplace（EU 账号可能需要切换）"""
        target_mkid = self.config.get('mons_sel_mkid')
        if not target_mkid:
            return
        
        try:
            # Use MarketplaceSwitcher for proper switching
            from marketplace_switcher import MarketplaceSwitcher
            
            # Determine target marketplace code from config
            target_mp = self.config.get('marketplace', '')
            if not target_mp:
                # Try to infer from mkid or domain
                domain = self.config.get('domain', '')
                if 'amazon.com' == domain.split('/')[-1]:
                    target_mp = 'US'
                elif 'amazon.co.uk' in domain:
                    target_mp = 'UK'
                else:
                    return
            
            switcher = MarketplaceSwitcher(self.page)
            
            # 先检查当前是否已经在目标市场
            current = switcher.get_current_marketplace()
            current_url = self.page.url
            is_account_switcher = 'account-switcher' in current_url or 'merchantMarketplace' in current_url
            
            if current == target_mp and not is_account_switcher:
                print(f"[Marketplace] 已经在 {target_mp} 市场，无需切换")
                return
            
            # 执行切换
            success, actual = switcher.switch_to_marketplace(target_mp, max_retries=2)
            
            if success:
                print(f"[Marketplace] 已切换到 {actual}")
                # 切换后导航到 Seller Central 首页，避免留在 account-switcher
                domain = self.config.get('domain', 'amazon.com')
                home_url = f"https://sellercentral.{domain}/home"
                print(f"[Marketplace] 导航到: {home_url}")
                self.page.goto(home_url, wait_until='domcontentloaded')
                time.sleep(5)
            else:
                print(f"[Marketplace] 切换失败: {actual}")
        except Exception as e:
            print(f"[Marketplace] 切换失败: {e}")

    def _select_create_new_asins(self) -> bool:
        """在 Apply to sell 弹窗中选择 'Application to create new ASINs'
        
        DOM结构（从调试发现）:
        - 选择面板在 kat-panel-wrapper[data-testid="kat-panel-wrapper-QualificationWidget"] 内
        - 选项是 kat-box.clickable 元素
        - 'Application to create new ASINs' 在第二个 kat-box.clickable 里
        """
        print("    选择 'Application to create new ASINs'...")
        
        try:
            # 等待 QualificationWidget panel 出现，最多 3s 早退
            try:
                self.page.wait_for_selector(
                    'kat-panel-wrapper[data-testid="kat-panel-wrapper-QualificationWidget"]',
                    timeout=3000
                )
            except Exception:
                pass

            # 确保 QualificationWidget 面板可见
            self.page.evaluate("""() => {
                const panel = document.querySelector('kat-panel-wrapper[data-testid="kat-panel-wrapper-QualificationWidget"]');
                if (panel && panel.getAttribute('panel-visible') !== 'true') {
                    panel.setAttribute('panel-visible', 'true');
                    panel.style.display = '';
                }
            }""")
            # 等待面板内容出现，最多 2s 早退
            try:
                self.page.wait_for_selector(
                    'kat-box.clickable, kat-input#question-cat_auth_mo_question_string_id_product_title',
                    timeout=2000
                )
            except Exception:
                pass

            # 方法1: JS 点击 kat-box.clickable 中包含 'create new ASINs' 的选项
            result = self.page.evaluate("""() => {
                const panel = document.querySelector('kat-panel-wrapper[data-testid="kat-panel-wrapper-QualificationWidget"]');
                if (!panel) return { found: false, reason: 'no_panel' };
                
                const panelText = (panel.textContent || '').toLowerCase();
                
                // BE/EU站点: 面板可能直接显示5461表单内容（不需要选择选项）
                if (panelText.includes('listing approval') || panelText.includes('submit required information') || panelText.includes('seller approval application')) {
                    return { found: true, clicked: true, method: 'already_open', preview: panelText.substring(0, 150) };
                }
                
                if (!panelText.includes('create new asins')) {
                    return { found: false, reason: 'no_text', preview: panelText.substring(0, 300) };
                }
                
                // 查找所有 kat-box.clickable 选项
                const boxes = panel.querySelectorAll('kat-box.clickable');
                for (const box of boxes) {
                    const text = (box.textContent || '').toLowerCase();
                    if (text.includes('create new asins')) {
                        box.click();
                        if (box.shadowRoot) {
                            const inner = box.shadowRoot.querySelector('button, [role="button"]');
                            if (inner) inner.click();
                        }
                        return { found: true, clicked: true, method: 'kat_box', text: text.substring(0, 80) };
                    }
                }
                
                // 备选：点第二个 kat-box.clickable
                if (boxes.length >= 2) {
                    boxes[1].click();
                    return { found: true, clicked: true, method: 'second_box' };
                }
                
                // 更宽泛的搜索
                const allClickable = panel.querySelectorAll('[class*="clickable"]');
                for (const el of allClickable) {
                    const text = (el.textContent || '').toLowerCase();
                    if (text.includes('create new asins')) {
                        el.click();
                        return { found: true, clicked: true, method: 'class_clickable', text: text.substring(0, 80) };
                    }
                }
                
                return { found: false, reason: 'no_matching' };
            }""")
            
            print(f"    选择结果: {result}")
            
            if result.get('found') and result.get('clicked'):
                # 等待 5461 表单字段出现，最多 5s 早退
                try:
                    self.page.wait_for_selector(
                        'kat-input#question-cat_auth_mo_question_string_id_product_title',
                        timeout=5000
                    )
                except Exception:
                    pass
                print("    [OK] 已选择 'Application to create new ASINs'")
                return True
            else:
                # 方法2: Playwright locator
                print(f"    [信息] JS选择失败: {result}, 尝试 Playwright")
                try:
                    panel_loc = self.page.locator('kat-panel-wrapper[data-testid="kat-panel-wrapper-QualificationWidget"]')
                    box_loc = panel_loc.locator('kat-box.clickable')
                    count = box_loc.count()
                    print(f"    [信息] 找到 {count} 个 kat-box.clickable")
                    if count >= 2:
                        box_loc.nth(1).click(force=True)
                        try:
                            self.page.wait_for_selector(
                                'kat-input#question-cat_auth_mo_question_string_id_product_title',
                                timeout=5000
                            )
                        except Exception:
                            pass
                        print("    [OK] 已通过 Playwright 点击第二个 kat-box")
                        return True
                    elif count == 1:
                        box_loc.first.click(force=True)
                        try:
                            self.page.wait_for_selector(
                                'kat-input#question-cat_auth_mo_question_string_id_product_title',
                                timeout=5000
                            )
                        except Exception:
                            pass
                        print("    [OK] 已通过 Playwright 点击唯一 kat-box")
                        return True
                except Exception as e:
                    print(f"    [信息] Playwright 点击失败: {e}")
                
                print("    [信息] 无法选择选项")
                
            return False  # 选择失败
            
        except Exception as e:
            print(f"    [信息] 选择选项失败: {e}")
            return False


    def fill_kat_input(self, selector: str, value: str) -> bool:
        """填写 kat-input 组件 - 使用逐字符输入触发表单验证"""
        import random
        
        try:
            # 使用逐字符输入（推荐，能更好触发表单验证）
            elem = self.page.locator(selector).first
            if elem.count() > 0:
                # 点击获取焦点
                elem.click()
                self.page.wait_for_timeout(random.randint(100, 300))
                
                # 清除现有内容
                elem.fill("")
                self.page.wait_for_timeout(200)
                
                # 逐字符输入
                for char in value:
                    self.page.keyboard.press(char)
                    self.page.wait_for_timeout(random.randint(50, 150))
                
                return True
            
            return False
            
        except Exception as e:
            print(f"    [错误] 填写失败: {e}")
            return False
    
    def fill_kat_input_js(self, selector: str, value: str) -> bool:
        """填写 kat-input 组件 - 使用 JavaScript 直接设置值（用于长文本）"""
        try:
            self.page.evaluate(f'''
                const el = document.querySelector('{selector}');
                if (el) {{
                    el.value = {json.dumps(value)};
                    el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                }}
            ''')
            return True
            
        except Exception as e:
            print(f"    [错误] 填写失败: {e}")
            return False
    
    def fill_5461_form(self) -> bool:
        """填写 5461 表单"""
        print(f"\n[5] 填写 5461 表单...")
        
        try:
            print("    Filling Product title...")
            self.fill_kat_input_js(self.SELECTORS['product_title'], self.brand_data['title'])
            time.sleep(0.5)
            
            # 2. Manufacturer - use JS direct fill
            print("    Filling Manufacturer...")
            self.fill_kat_input_js(self.SELECTORS['manufacturer'], self.brand_data['brand'])
            time.sleep(0.5)
            
            # 3. Product Description - JS direct fill for long text
            print("    Filling Product Description...")
            self.fill_kat_input_js(self.SELECTORS['product_description'], self.brand_data['description'])
            time.sleep(0.5)
            
            # 4. Product ID (leave empty - GTIN exemption)
            print("    Product ID: leave empty (GTIN exemption)")
            
            print("    [OK] Form filled")
            return True
            
        except Exception as e:
            print(f"    [Error] Fill form failed: {e}")
            return False
    
    def upload_images(self, image_files: List[str]) -> bool:
        """上传产品图片"""
        print(f"\n[6] 上传产品图片 ({len(image_files)} 张)...")
        
        try:
            # 找到文件上传 input：先尝试原选择器，再尝试通用扫描
            file_input_selector = None
            
            # 方法 1: 原选择器
            primary = self.SELECTORS['file_input']
            try:
                if self.page.locator(primary).count() > 0:
                    file_input_selector = primary
                    print(f"    [找到] 文件输入: {primary}")
            except Exception:
                pass
            
            if not file_input_selector:
                # 方法 2: 扫描页面上所有 input[type=file]
                candidates = self.page.evaluate("""
                    () => Array.from(document.querySelectorAll('input[type="file"]')).map((el, i) => ({
                        index: i,
                        id: el.id,
                        name: el.name,
                        accept: el.accept
                    }))
                """)
                print(f"    [扫描] 页面上所有 file input: {candidates}")
                
                if candidates:
                    img_input = next((c for c in candidates if 'image' in (c.get('accept') or '')), None)
                    chosen = img_input or candidates[0]
                    if chosen.get('id'):
                        file_input_selector = f'input[id="{chosen["id"]}"]'
                    elif chosen.get('name'):
                        file_input_selector = f'input[name="{chosen["name"]}"]'
                    else:
                        file_input_selector = f'input[type="file"]:nth-of-type({chosen["index"]+1})'
                    print(f"    [选定] 使用输入: {file_input_selector}")
                else:
                    print("    [失败] 未找到任何 file input 元素")
                    return False
            
            # 上传文件
            self.page.locator(file_input_selector).set_input_files(image_files)
            
            print("    [OK] 图片已选择，等待上传...")
            # 等待 file input 有文件（上传完成），最多 5s 早退
            try:
                self.page.wait_for_function(
                    """() => {
                        const inp = document.querySelector('input[type="file"]');
                        return inp && inp.files && inp.files.length > 0;
                    }""",
                    timeout=5000
                )
            except Exception:
                pass
            
            # 勾选复选框（精确控制，避免勾选品牌名复选框）
            print("    勾选复选框...")
            check_result = self.page.evaluate('''() => {
                let result = [];
                
                // 1. 只在 5461 表单面板内勾选复选框（图片确认复选框）
                // 避免勾选 Product Identity 页面的 "This product does not have a brand name"
                const qualificationPanel = document.querySelector('kat-panel[data-testid="kat-panel-QualificationWidget"]');
                if (qualificationPanel) {
                    const katCheckboxes = qualificationPanel.querySelectorAll('kat-checkbox');
                    katCheckboxes.forEach(cb => {
                        cb.checked = true;
                        cb.dispatchEvent(new Event('change', { bubbles: true }));
                    });
                    result.push('QualificationWidget: ' + katCheckboxes.length + ' checkboxes');
                } else {
                    result.push('QualificationWidget not found');
                }
                
                return result.join(', ') || 'No checkboxes found';
            }''')
            print(f"    [OK] {check_result}")
            
            time.sleep(1)
            print("    [OK] 复选框已勾选")
            
            # 保存截图
            try:
                screenshot_path = f"artifacts/5461/before_submit_{self.brand_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                self.bm.screenshot(screenshot_path)
                self.result['screenshots'].append(screenshot_path)
            except Exception:
                pass  # Screenshot timeout shouldn't block submission
            
            return True
            
        except Exception as e:
            print(f"    [错误] 上传图片失败: {e}")
            return False
    
    def get_autofill_email(self) -> Optional[str]:
        """
        获取浏览器自动填充的真实邮箱
        
        尝试多种方法获取邮箱：
        1. 从 5461 表单的 email 输入框读取
        2. 从 Seller Central 页面头部获取
        3. 从浏览器 localStorage/sessionStorage 获取
        4. 从页面 JavaScript 变量获取
        """
        email = None
        
        # 方法1: 等待并读取 5461 表单中的 email 输入框
        try:
            print("    等待浏览器自动填充邮箱...")
            # 轮询等待 email 输入框有值，最多 3s（autofill 无法用事件触发）
            try:
                self.page.wait_for_function(
                    """() => {
                        const el = document.querySelector('kat-input#contact_info_email_input');
                        if (el && el.value && el.value.trim()) return true;
                        if (el && el.shadowRoot) {
                            const inp = el.shadowRoot.querySelector('input');
                            return inp && inp.value && inp.value.trim();
                        }
                        return false;
                    }""",
                    timeout=3000
                )
            except Exception:
                pass
            
            # 读取输入框的 value
            email = self.page.evaluate('''() => {
                const el = document.querySelector('kat-input#contact_info_email_input');
                if (el && el.value && el.value.trim()) {
                    return el.value.trim();
                }
                // 尝试从 Shadow DOM 内部获取
                if (el && el.shadowRoot) {
                    const input = el.shadowRoot.querySelector('input');
                    if (input && input.value) {
                        return input.value.trim();
                    }
                }
                return null;
            }''')
            
            if email and '@' in email and 'example' not in email.lower():
                print(f"    [OK] 从表单获取到自动填充邮箱: {email}")
                return email
        except Exception as e:
            print(f"    [信息] 从表单获取邮箱失败: {e}")
        
        # 方法2: 从 Seller Central 页面头部获取账户邮箱
        try:
            print("    尝试从 Seller Central 页面获取邮箱...")
            email = self.page.evaluate(r'''() => {
                // 尝试从账户下拉菜单获取
                const accountBtn = document.querySelector('button[data-testid="account-menu"], [aria-label*="account" i], [aria-label*="Account" i]');
                if (accountBtn) {
                    // 点击展开菜单
                    accountBtn.click();
                    // 等待菜单展开
                    setTimeout(() => {}, 500);
                    // 查找邮箱
                    const emailEl = document.querySelector('[data-testid="account-menu"] .email, .account-dropdown .email, [class*="email" i]');
                    if (emailEl) {
                        const text = emailEl.textContent.trim();
                        if (text.includes('@')) return text;
                    }
                }
                
                // 尝试从页面任何位置找到邮箱文本
                const bodyText = document.body.innerText;
                const emailMatches = bodyText.match(/[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/g);
                if (emailMatches && emailMatches.length > 0) {
                    // 过滤掉常见的示例邮箱
                    const validEmails = emailMatches.filter(e => 
                        !e.toLowerCase().includes('example') && 
                        !e.toLowerCase().includes('test@') &&
                        !e.toLowerCase().includes('user@')
                    );
                    if (validEmails.length > 0) {
                        return validEmails[0];
                    }
                }
                
                return null;
            }''')
            
            if email and '@' in email:
                print(f"    [OK] 从 Seller Central 页面获取到邮箱: {email}")
                return email
        except Exception as e:
            print(f"    [信息] 从页面获取邮箱失败: {e}")
        
        # 方法3: 从浏览器存储获取
        try:
            print("    尝试从浏览器存储获取邮箱...")
            email = self.page.evaluate(r'''() => {
                // 尝试从 localStorage 获取
                for (let i = 0; i < localStorage.length; i++) {
                    const key = localStorage.key(i);
                    const value = localStorage.getItem(key);
                    if (value && value.includes('@') && !value.toLowerCase().includes('example')) {
                        const match = value.match(/[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/);
                        if (match) return match[0];
                    }
                }
                
                // 尝试从 sessionStorage 获取
                for (let i = 0; i < sessionStorage.length; i++) {
                    const key = sessionStorage.key(i);
                    const value = sessionStorage.getItem(key);
                    if (value && value.includes('@') && !value.toLowerCase().includes('example')) {
                        const match = value.match(/[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/);
                        if (match) return match[0];
                    }
                }
                
                return null;
            }''')
            
            if email and '@' in email:
                print(f"    [OK] 从浏览器存储获取到邮箱: {email}")
                return email
        except Exception as e:
            print(f"    [信息] 从浏览器存储获取邮箱失败: {e}")
        
        # 方法4: 从 Amazon 的 JavaScript 全局变量获取
        try:
            print("    尝试从页面 JavaScript 变量获取邮箱...")
            email = self.page.evaluate('''() => {
                // 尝试常见的 Amazon 全局变量
                if (window.sellerCentral && window.sellerCentral.user && window.sellerCentral.user.email) {
                    return window.sellerCentral.user.email;
                }
                if (window.amzn && window.amzn.user && window.amzn.user.email) {
                    return window.amzn.user.email;
                }
                if (window.UserContext && window.UserContext.email) {
                    return window.UserContext.email;
                }
                
                // 尝试从任何包含 email 的全局变量获取
                const globals = Object.keys(window);
                for (const key of globals) {
                    try {
                        const val = window[key];
                        if (val && typeof val === 'object') {
                            if (val.email && typeof val.email === 'string' && val.email.includes('@')) {
                                return val.email;
                            }
                        }
                    } catch (e) {
                        // 忽略访问错误
                    }
                }
                
                return null;
            }''')
            
            if email and '@' in email:
                print(f"    [OK] 从 JavaScript 变量获取到邮箱: {email}")
                return email
        except Exception as e:
            print(f"    [信息] 从 JS 变量获取邮箱失败: {e}")
        
        return None
    
    def fill_contact_info(self) -> bool:
        """填写联系信息 - 使用浏览器自动填充的真实邮箱"""
        print(f"\n[7] 填写联系信息...")
        
        try:
            filler = KatalFormFiller(self.page)
            
            # 首先尝试获取浏览器自动填充的真实邮箱
            email = self.get_autofill_email()
            
            # 如果没有获取到，尝试从配置获取
            if not email:
                email = self.config.get('email') or self.config.get('username')
                if email:
                    print(f"    [信息] 使用配置中的邮箱: {email}")
            
            # 如果仍然没有邮箱，提示用户输入
            if not email:
                print("    [警告] 无法自动获取邮箱")
                print("    请在浏览器中查看该账号的真实邮箱（通常在页面右上角账户信息中）")
                print("    然后输入邮箱继续...")
                
                # 保存当前截图供用户参考
                try:
                    self.bm.screenshot(f"artifacts/5461/need_email_{self.brand_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")
                except:
                    pass
                
                # 尝试从配置获取（可能是之前保存的）
                email = self.config.get('email') or self.config.get('username')
                
                if not email:
                    # 使用一个标记值，让流程继续但记录需要后续处理
                    print("    [警告] 将使用占位邮箱继续，请在提交前手动修改为真实邮箱")
                    email = "NEED_REAL_EMAIL@example.com"
                else:
                    print(f"    [信息] 使用配置中的邮箱: {email}")
            
            # 检查当前输入框的值
            current_value = self.page.evaluate('''() => {
                const el = document.querySelector('kat-input#contact_info_email_input');
                if (el && el.shadowRoot) {
                    const input = el.shadowRoot.querySelector('input');
                    return input ? input.value : el.value;
                }
                return el ? el.value : null;
            }''')
            
            # 如果已经有有效的自动填充值，保留它
            if current_value and '@' in current_value and 'example' not in current_value.lower():
                if current_value != email:
                    print(f"    [OK] 保留浏览器自动填充的邮箱: {current_value}")
                    email = current_value
                else:
                    print(f"    [OK] 邮箱已正确填充: {email}")
            else:
                # 需要填写邮箱
                print(f"    填写 Email: {email}")
                email_input = self.page.locator(self.SELECTORS['email']).first
                if email_input.count() > 0 and email_input.is_visible():
                    email_input.click()
                    filler.type_like_human(email)
                    self.page.keyboard.press('Tab')
                else:
                    print("    [信息] Email 输入框未找到，BE/EU站点可能不需要手动填写邮箱")
            
            # Phone 不填
            print("    Phone: 跳过")
            
            return True
            
        except Exception as e:
            print(f"    [错误] 填写联系信息失败: {e}")
            return False
    
    def submit_form(self) -> bool:
        """提交表单"""
        print(f"\n[8] 提交表单...")
        
        try:
            # 首先尝试触发所有字段的验证事件
            print("    触发表单验证...")
            self.page.evaluate('''() => {
                // 触发所有 kat-input 的 blur 事件
                const inputs = document.querySelectorAll('kat-input');
                inputs.forEach(input => {
                    input.dispatchEvent(new Event('blur', { bubbles: true }));
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                });
                // 触发所有 kat-checkbox 的 change 事件
                const checkboxes = document.querySelectorAll('kat-checkbox');
                checkboxes.forEach(cb => {
                    cb.dispatchEvent(new Event('change', { bubbles: true }));
                });
            }''')
            time.sleep(2)
            
            # 检查表单验证状态
            print("    检查表单验证状态...")
            validation_result = self.page.evaluate('''() => {
                // 检查是否有错误提示
                const errorMessages = document.querySelectorAll('.error-message, [data-testid*="error"], .validation-error');
                const requiredFields = document.querySelectorAll('[required]');
                let emptyRequired = [];
                requiredFields.forEach(field => {
                    if (!field.value || field.value.trim() === '') {
                        emptyRequired.push(field.id || field.name || 'unknown');
                    }
                });
                return {
                    errorCount: errorMessages.length,
                    emptyRequiredFields: emptyRequired
                };
            }''')
            
            if validation_result.get('emptyRequiredFields'):
                print(f"    [警告] 以下必填字段为空: {validation_result['emptyRequiredFields']}")
            
            submit_button = self.page.locator(self.SELECTORS['submit_button'])
            
            # 检查按钮是否可用
            is_disabled = True
            try:
                is_disabled_attr = submit_button.get_attribute('disabled')
                is_disabled = is_disabled_attr is not None
                if is_disabled:
                    print(f"    [警告] 提交按钮被禁用，尝试强制启用...")
                    # 尝试强制启用按钮
                    self.page.evaluate('''() => {
                        const btn = document.querySelector('kat-button#submit_button');
                        if (btn) {
                            btn.disabled = false;
                            btn.removeAttribute('disabled');
                            return 'Button enabled';
                        }
                        return 'Button not found';
                    }''')
                    time.sleep(1)
                    is_disabled = False
            except Exception as e:
                print(f"    [信息] 检查按钮状态: {e}")
                is_disabled = False
            
            # 尝试点击提交按钮
            print("    点击提交按钮...")
            try:
                submit_button.click()
                print("    [OK] 已点击提交按钮")
            except Exception as click_error:
                print(f"    [警告] 常规点击失败: {click_error}")
                # 使用 JavaScript 点击
                print("    尝试使用 JavaScript 点击...")
                result = self.page.evaluate('''() => {
                    const btn = document.querySelector('kat-button#submit_button');
                    if (btn) {
                        btn.disabled = false;
                        btn.removeAttribute('disabled');
                        btn.click();
                        return 'Clicked via JS';
                    }
                    // 尝试其他选择器
                    const altBtn = document.querySelector('kat-button[label="Submit"]');
                    if (altBtn) {
                        altBtn.click();
                        return 'Clicked via alt selector';
                    }
                    return 'Submit button not found';
                }''')
                print(f"    [结果] {result}")
            
            # 等待提交结果：轮询等待 Case ID 或成功标记出现，最多 30s 早退
            print("    等待提交结果（最多30秒）...")
            try:
                self.page.wait_for_function(
                    """() => {
                        const t = document.body ? document.body.innerText : '';
                        return /\\d{11,}/.test(t) ||
                               t.includes('Under review') || t.includes('Case ID') ||
                               t.includes('submitted') || t.includes('created');
                    }""",
                    timeout=30000
                )
            except Exception:
                pass
            
            # 检查提交后的页面状态
            current_url = self.page.url
            page_text = self.page.inner_text('body')
            
            # 检查是否提交成功
            if 'Case' in page_text and ('ID' in page_text or 'created' in page_text.lower()):
                print("    [OK] 检测到提交成功提示")
                return True
            elif 'error' in page_text.lower() or 'fail' in page_text.lower():
                print("    [警告] 页面显示错误信息")
                return False
            else:
                print(f"    [信息] 提交后 URL: {current_url[:80]}...")
                return True
            
        except Exception as e:
            print(f"    [错误] 提交失败: {e}")
            return False
    
    def extract_case_id(self) -> Optional[str]:
        """提取 Case ID"""
        print(f"\n[9] 提取 Case ID...")
        
        try:
            current_url = self.page.url
            page_text = self.page.inner_text('body')
            
            print(f"    当前 URL: {current_url}")
            
            # 保存截图
            try:
                screenshot_path = f"artifacts/5461/after_submit_{self.brand_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                self.bm.screenshot(screenshot_path)
                self.result['screenshots'].append(screenshot_path)
            except Exception:
                pass
            
            # 查找 Case ID 模式（更全面的模式）
            patterns = [
                r'Case\s*ID\s*[-:]?\s*([0-9]+)',  # 匹配 "Case ID - 19999999999"
                r'Case\s*ID[:\s]+([A-Z0-9\-]+)',
                r'case[_\-]?id[:\s]+([A-Z0-9\-]+)',
                r'case\s*#?\s*[:\s]+([A-Z0-9\-]+)',
                r'support\s*case\s*[:\s]+([A-Z0-9\-]+)',
                r'([0-9]{9,12})',  # 9-12位数字
                r'([A-Z]{2,4}\d{6,10})',  # 如 US12345678
            ]
            
            for pattern in patterns:
                matches = re.findall(pattern, page_text, re.IGNORECASE)
                if matches:
                    case_id = matches[0]
                    print(f"    [OK] 找到 Case ID: {case_id}")
                    return case_id
            
            # 尝试从特定的 kat-icon 结构中提取
            try:
                case_id_from_html = self.page.evaluate('''() => {
                    // 查找包含 "Case ID" 的 span
                    const spans = document.querySelectorAll('span');
                    for (const span of spans) {
                        if (span.textContent.includes('Case ID')) {
                            const match = span.textContent.match(/Case\s*ID\s*[-:]?\s*(\d+)/);
                            if (match) return match[1];
                        }
                    }
                    return null;
                }''')
                if case_id_from_html:
                    print(f"    [OK] 从 HTML 结构找到 Case ID: {case_id_from_html}")
                    return case_id_from_html
            except Exception as e:
                pass
            
            # 检查是否有提交成功的提示
            if 'submitted' in page_text.lower() or 'success' in page_text.lower():
                print("    [OK] 检测到提交成功，但未找到 Case ID")
            elif 'error' in page_text.lower():
                print("    [警告] 页面显示错误")
            else:
                print("    [警告] 未找到 Case ID，请手动检查截图")
            
            return None
            
        except Exception as e:
            print(f"    [错误] 提取 Case ID 失败: {e}")
            return None
    
    def run(self) -> Dict[str, Any]:
        """执行完整流程"""
        try:
            # 1. 读取品牌数据
            self.read_brand_data()
            
            # 2. 获取图片文件
            image_files = self.get_image_files()
            
            # 3. 连接浏览器
            self.connect()
            
            # 3.5 确保在正确的 marketplace
            self._ensure_marketplace()
            
            # 4. 清除旧弹窗（不刷新页面，减少 API 触发）
            print("\n[准备] 清除旧弹窗...")
            try:
                # 只关闭可能存在的 5461 弹窗
                self.page.evaluate("""() => {
                    const panel = document.querySelector('kat-panel-wrapper[data-testid="kat-panel-wrapper-QualificationWidget"]');
                    if (panel) {
                        panel.setAttribute('panel-visible', 'false');
                        panel.style.display = 'none';
                    }
                }""")
                print("    [OK] 旧弹窗已关闭")
            except Exception as e:
                print(f"    [信息] 清除弹窗: {e}")
                print(f"    [信息] 刷新页面: {e}")
            
            # 5. 检查当前页面状态
            state = self.check_page_state()
            print(f"\n[初始状态] {state}")
            
            # 6. 根据状态处理
            # 只有 PRODUCT_IDENTITY (干净表单) 和 5461_FORM (草稿页面) 可以直接使用
            # 其他状态（AUTH_REQUIRED, 5461_FORM_OPEN, PRODUCT_IDENTITY_UPC_REQUIRED 等）
            # 都是上一个品牌提交后的残留状态，需要导航到干净页面
            needs_fresh_nav = state not in ('PRODUCT_IDENTITY', '5461_FORM')
            
            if needs_fresh_nav:
                entry_url = self.config.get('entry_url')
                if entry_url:
                    reason = '未知页面' if state == 'UNKNOWN' else f'上一个品牌残留状态({state})'
                    print(f"\n[导航] {reason}，重新导航到 Product Identity 页面...")
                    
                    # 确保 entry_url 包含正确的 mkid
                    target_mkid = self.config.get('mons_sel_mkid')
                    if target_mkid and 'mons_sel_mkid' not in entry_url:
                        separator = '&' if '?' in entry_url else '?'
                        entry_url = f"{entry_url}{separator}mons_sel_mkid={target_mkid}"
                        print(f"[导航] 已添加 mkid 参数: {target_mkid}")
                    
                    self.bm.navigate(entry_url, wait_time=10)
                    state = self.check_page_state()
                    print(f"[导航后状态] {state}")
                    
                    # 如果导航后仍然不是干净状态，报错
                    if state not in ('PRODUCT_IDENTITY', 'UNKNOWN'):
                        print(f"[警告] 导航后状态异常: {state}，尝试刷新...")
                        self.page.reload(wait_until='domcontentloaded')
                        time.sleep(3)
                        state = self.check_page_state()
                        print(f"[刷新后状态] {state}")
                    
                    if state == 'UNKNOWN':
                        raise RuntimeError("导航后仍无法识别页面状态")
                else:
                    raise RuntimeError("未配置 entry_url，无法导航到 Product Identity 页面")
            
            if state == '5461_FORM_OPEN':
                # 5461 表单弹窗已打开，等待内容加载
                print("\n[状态] 5461 表单弹窗已打开，等待内容完全加载...")
                for wait in range(12):
                    time.sleep(5)
                    panel = self.page.locator('kat-panel-wrapper[data-testid="kat-panel-wrapper-QualificationWidget"]').first
                    if panel.count() > 0:
                        panel_text = panel.inner_text()
                        if 'Listing approval' in panel_text or 'Submit required information' in panel_text or 'Product title' in panel_text:
                            print(f"[等待] 弹窗内容已加载 (第 {wait+1} 次检查)")
                            break
                        # 也检查是否有 file upload
                        if self.page.locator('input[type="file"]').count() > 0 or self.page.locator('kat-upload').count() > 0:
                            print(f"[等待] 上传区域已出现 (第 {wait+1} 次检查)")
                            break
                    print(f"[等待] 弹窗内容仍在加载 (第 {wait+1}/12 次检查)")
                print("\n[状态] 开始填写 5461 表单...")
            elif state == 'PRODUCT_IDENTITY':
                if not self.handle_product_identity():
                    raise RuntimeError("处理 Product Identity 失败")
                
                # 再次检查状态
                state = self.check_page_state()
                
                # 如果直接进入 GTIN 豁免页面
                if state == 'GTIN_EXEMPTION':
                    print("\n[状态] Product Identity 后直接进入 GTIN 豁免页面")
                    # GTIN 豁免立即处理
                    import sys
                    from pathlib import Path
                    src_dir = Path(__file__).parent / 'src'
                    if str(src_dir) not in sys.path:
                        sys.path.insert(0, str(src_dir))
                    from gtin_exemption_handler import GTINExemptionHandler
                    handler = GTINExemptionHandler(self.page, {'title': self.brand_name}, self.get_image_files())
                    gtin_result = handler.handle()
                    
                    if gtin_result['success']:
                        print("[GTIN 豁免] 成功")
                    else:
                        print("[GTIN 豁免] 失败")
                        raise RuntimeError("GTIN 豁免处理失败")
                    
                    # GTIN 处理后检查状态
                    state = self.check_page_state()
                    print(f"[状态] GTIN 豁免后: {state}")
                    
                    # 如果 GTIN 处理后仍在 GTIN_EXEMPTION，跳过不再处理
                    if state == 'GTIN_EXEMPTION':
                        print("[状态] GTIN 处理后仍为 GTIN_EXEMPTION，不再重复处理")
                        # 尝试判断是否实际在 Product Identity 页面
                        if 'product_identity' in self.page.url.lower():
                            state = 'PRODUCT_IDENTITY'
            
            if state == 'AUTH_REQUIRED':
                # 已经在 Product Identity 页面且需要授权
                # 注意：此时表单应该已经填写过了（前面 handle_product_identity 已经执行过）
                # 只需要点击 Apply to sell 按钮即可
                print("\n[状态] 在 Product Identity 页面且需要授权，点击 Apply to sell...")
                # 人性化延迟：模拟人类阅读授权提示的时间
                human_delay = random.randint(10, 20)
                print(f"[等待] 人性化延迟 {human_delay} 秒...")
                time.sleep(human_delay)
                if not self.click_apply_to_sell():
                    raise RuntimeError("点击 Apply to sell 失败")
                
                # 等待 5461 表单加载
                print("\n[等待] 等待 5461 表单弹窗内容加载...")
                time.sleep(5)
                
                # 检查弹窗内容是否完全加载（不只是标题）
                for wait in range(12):
                    panel = self.page.locator('kat-panel-wrapper[data-testid="kat-panel-wrapper-QualificationWidget"]').first
                    if panel.count() > 0:
                        panel_text = panel.inner_text()
                        if len(panel_text) > 50 and ('Listing approval' in panel_text or 'Product title' in panel_text or 'Submit required information' in panel_text or 'Upload' in panel_text or 'image' in panel_text.lower() or 'Branding' in panel_text):
                            print(f"[等待] 弹窗内容已加载 (第 {wait+1} 次检查, 文本长度: {len(panel_text)})")
                            break
                    print(f"[等待] 弹窗内容仍在加载 (第 {wait+1}/12 次检查)")
                    time.sleep(5)
                
                # 检查是否进入 5461 表单
                state = self.check_page_state()
                print(f"[状态] 点击 Apply to sell 后: {state}")
                
                if state not in ['5461_FORM', '5461_FORM_OPEN']:
                    # 保存当前页面截图
                    try:
                        self.bm.screenshot(f"artifacts/5461/after_apply_{self.brand_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")
                    except:
                        pass
                    
                    # 如果还在 Product Identity 页面但显示了授权要求，自动重试
                    if state == 'AUTH_REQUIRED' or state == 'PRODUCT_IDENTITY':
                        print("\n[自动重试] Apply to sell 后状态未变，等待弹窗加载...")
                        for retry in range(6):
                            time.sleep(10)
                            state = self.check_page_state()
                            print(f"[重试 {retry+1}/6] 状态: {state}")
                            if state in ['5461_FORM', '5461_FORM_OPEN', 'GTIN_EXEMPTION']:
                                break
                        if state not in ['5461_FORM', '5461_FORM_OPEN', 'GTIN_EXEMPTION']:
                            raise RuntimeError("自动重试后仍未打开 5461 表单，可能账号被rate limit(410001)或页面异常")
            
            elif state == '5461_FORM_OPEN':
                # 5461 表单弹窗已经打开（通过品牌选择流程）
                print("\n[状态] 5461 表单弹窗已通过品牌选择打开，继续填写...")
                # 不需要额外操作，直接进入填写流程
            
            elif state == 'UNKNOWN':
                # 检查是否在 Description 或其他页面（可能不需要 5461）
                page_text = self.page.inner_text('body')
                current_url = self.page.url
                
                if 'Description' in page_text or 'description' in current_url.lower():
                    print("\n[状态] 已进入 Description 页面，该品牌可能不需要 5461 申请")
                    print("[信息] 该账号可能已有权限销售此品牌，或品牌已在 Brand Registry 中注册")
                    # 记录成功但不提取 Case ID
                    self.result['success'] = True
                    self.result['case_id'] = None
                    self.result['error'] = None
                    print("\n============================================================")
                    print("[PASS] 品牌验证通过")
                    print(f"  品牌: {self.brand_name}")
                    print("  状态: 不需要 5461 申请，可直接创建 listing")
                    print("============================================================")
                    return True
                else:
                    print(f"\n[警告] 未知页面状态: {state}")
                    print(f"[信息] 当前 URL: {current_url}")
                    # 保存截图供人工检查
                    self.bm.screenshot(f"artifacts/5461/unknown_state_{self.brand_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")
                    raise RuntimeError(f"未知页面状态: {state}")
            
            # 6. 处理 GTIN 豁免流程
            if state == 'GTIN_EXEMPTION':
                print("\n[状态] 进入 GTIN 豁免申请流程...")
                from gtin_exemption_handler import GTINExemptionHandler
                handler = GTINExemptionHandler(self.page, self.brand_data, image_files)
                gtin_result = handler.handle()
                
                if gtin_result['success']:
                    self.result['success'] = True
                    self.result['case_id'] = gtin_result.get('case_id')
                    print(f"\n{'='*60}")
                    print("[PASS] GTIN 豁免申请填写完成")
                    print(f"  品牌: {self.brand_name}")
                    print("  状态: 表单已填写，等待人工确认提交")
                    print(f"{'='*60}")
                    return self.result
                else:
                    raise RuntimeError(f"GTIN 豁免处理失败: {gtin_result.get('error')}")
            
            # 7. 填写 5461 表单（只有在需要 5461 时才执行）
            if state in ['5461_FORM', '5461_FORM_OPEN']:
                if not self.fill_5461_form():
                    raise RuntimeError("填写 5461 表单失败")
            
            # 8. 上传图片
            if not self.upload_images(image_files):
                raise RuntimeError("上传图片失败")
            
            # 9. 填写联系信息
            if not self.fill_contact_info():
                raise RuntimeError("填写联系信息失败")
            
            # 10. 提交表单
            if not self.submit_form():
                raise RuntimeError("提交表单失败")
            
            # 11. 提取 Case ID
            case_id = self.extract_case_id()
            
            # 设置成功结果
            self.result['success'] = True
            self.result['case_id'] = case_id
            
            # 12. 登记到申请表
            self._register_application(case_id)
            
            print(f"\n{'='*60}")
            print("[PASS] 5461 申请提交完成")
            print(f"  品牌: {self.brand_name}")
            print(f"  Case ID: {case_id or '未找到'}")
            print(f"{'='*60}")
            
        except Exception as e:
            self.result['success'] = False
            self.result['error'] = str(e)
            
            print(f"\n{'='*60}")
            print(f"[FAIL] 提交失败: {e}")
            print(f"{'='*60}")
            
            # 保存错误截图
            try:
                screenshot_path = f"artifacts/5461/error_{self.brand_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                self.bm.screenshot(screenshot_path)
                self.result['screenshots'].append(screenshot_path)
            except:
                pass
        
        finally:
            # 保存结果
            self._save_result()
            self.bm.close()
        
        return self.result
    
    def _register_application(self, case_id: str) -> None:
        """登记申请到登记表"""
        try:
            # 提取站点信息
            site = 'US'  # 默认 US
            if self.brand_data and self.brand_data.get('country'):
                country = self.brand_data['country'].upper()
                if country in ['US', 'USA']:
                    site = 'US'
                elif country in ['UK', 'GB']:
                    site = 'UK'
                elif country in ['DE', 'GERMANY']:
                    site = 'DE'
                else:
                    site = country
            
            # 构建记录
            record = {
                'site': site,
                'account': self.account_id,
                'brand': self.brand_name,
                'sku': self.brand_data.get('sku', '') if self.brand_data else '',
                'case_id': case_id or '',
                'status': '申请中',
                'notes': f"自动提交成功" if case_id else "Case ID 未提取到",
            }
            
            # 添加到登记表
            self.registry.add_record(record)
            
        except Exception as e:
            print(f"\n[Registry] [警告] 登记申请失败: {e}")
            print(f"[Registry] 请手动记录: {self.account_id} / {self.brand_name} / {case_id or 'N/A'}")
    
    def _save_result(self) -> None:
        """保存结果到文件"""
        result_file = Path(f"artifacts/results/submit_5461_{self.brand_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        result_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(result_file, 'w', encoding='utf-8') as f:
            json.dump(self.result, f, indent=2, ensure_ascii=False)
        
        print(f"\n[保存] 结果已保存: {result_file}")


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='5461 提交脚本')
    parser.add_argument('account_id', help='账号 ID (如 us_store_580)')
    parser.add_argument('brand_name', help='品牌名 (如 HOMEMO)')
    parser.add_argument('--site', help='指定站点 (如 UK, BE, US, DE, MX)', default=None)
    
    args = parser.parse_args()
    
    submitter = Submit5461(args.account_id, args.brand_name, site=args.site)
    result = submitter.run()
    
    # 输出结果
    print("\n" + "="*60)
    print("最终结果 (JSON):")
    print("="*60)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
