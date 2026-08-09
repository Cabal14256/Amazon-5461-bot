"""
Amazon Seller Central 市场切换模块

功能：
1. 自动检测所有可用市场
2. 智能选择最佳市场（根据账号配置）
3. 切换失败时自动重试
4. 切换后验证

作者: AI Assistant
日期: 2026-05-11
"""

import re
import time
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

from playwright.sync_api import Page


class MarketplaceRegion(Enum):
    """市场区域"""
    EU = "eu"          # 欧洲: UK, BE, NL, SE, DE, FR, ES, IT, PL
    NA = "na"          # 北美: US, CA, MX


@dataclass
class MarketplaceInfo:
    """市场信息"""
    code: str          # 市场代码: UK, BE, NL, SE, DE, FR, ES, IT, PL, US, CA, MX
    name: str          # 市场名称: United Kingdom, Belgium, Netherlands, etc.
    region: MarketplaceRegion
    domain: str        # 域名: amazon.co.uk, amazon.com, etc.
    is_available: bool = False  # 是否可用
    is_selected: bool = False   # 是否当前选中


# 市场配置
MARKETPLACE_CONFIG: Dict[str, MarketplaceInfo] = {
    # 欧洲
    "UK": MarketplaceInfo("UK", "United Kingdom", MarketplaceRegion.EU, "amazon.co.uk"),
    "BE": MarketplaceInfo("BE", "Belgium", MarketplaceRegion.EU, "amazon.co.uk"),
    "NL": MarketplaceInfo("NL", "Netherlands", MarketplaceRegion.EU, "amazon.co.uk"),
    "SE": MarketplaceInfo("SE", "Sweden", MarketplaceRegion.EU, "amazon.co.uk"),
    "DE": MarketplaceInfo("DE", "Germany", MarketplaceRegion.EU, "amazon.co.uk"),
    "FR": MarketplaceInfo("FR", "France", MarketplaceRegion.EU, "amazon.co.uk"),
    "ES": MarketplaceInfo("ES", "Spain", MarketplaceRegion.EU, "amazon.co.uk"),
    "IT": MarketplaceInfo("IT", "Italy", MarketplaceRegion.EU, "amazon.co.uk"),
    "PL": MarketplaceInfo("PL", "Poland", MarketplaceRegion.EU, "amazon.co.uk"),
    # 北美
    "US": MarketplaceInfo("US", "United States", MarketplaceRegion.NA, "amazon.com"),
    "CA": MarketplaceInfo("CA", "Canada", MarketplaceRegion.NA, "amazon.ca"),
    "MX": MarketplaceInfo("MX", "Mexico", MarketplaceRegion.NA, "amazon.com"),
}

# 区域到市场的映射
REGION_MARKETPLACES = {
    MarketplaceRegion.EU: ["UK", "BE", "NL", "SE", "DE", "FR", "ES", "IT", "PL"],
    MarketplaceRegion.NA: ["US", "CA", "MX"],
}


class MarketplaceSwitchError(Exception):
    """市场切换错误"""
    pass


class MarketplaceVerificationError(Exception):
    """市场验证错误"""
    pass


class MarketplaceSwitcher:
    """
    Amazon Seller Central 市场切换器
    
    用法:
        switcher = MarketplaceSwitcher(page)
        
        # 自动检测并切换到最佳市场
        result = switcher.switch_to_best_marketplace("BE")
        
        # 或切换到指定市场
        result = switcher.switch_to_marketplace("NL", max_retries=3)
    """
    
    def __init__(self, page: Page, evidence_dir: Optional[str] = None):
        self.page = page
        self.evidence_dir = evidence_dir
        self.detected_marketplaces: List[MarketplaceInfo] = []
        self.current_marketplace: Optional[str] = None
        
    def _take_screenshot(self, name: str):
        """截图保存"""
        if self.evidence_dir:
            from pathlib import Path
            try:
                shot_path = Path(self.evidence_dir) / f"{name}.png"
                self.page.screenshot(path=str(shot_path), full_page=True, timeout=10000)
                print(f"[截图] 已保存: {shot_path}")
            except Exception as e:
                print(f"[警告] 截图失败: {e}")
    
    def detect_available_marketplaces(self, preferred: Optional[str] = None) -> List[MarketplaceInfo]:
        """
        自动检测所有可用市场
        
        参数:
            preferred: 目标市场代码，用于选择正确的切换页面域名
        
        返回:
            List[MarketplaceInfo]: 可用市场列表
        """
        print("\n[市场检测] 开始检测可用市场...")
        
        # 按目标市场区域选择切换页面域名，避免跨区重定向
        # 从 MARKETPLACE_CONFIG 动态获取 region，不硬编码国家列表
        preferred_info = MARKETPLACE_CONFIG.get(preferred) if preferred else None
        if preferred_info and preferred_info.region == MarketplaceRegion.NA:
            switcher_domain = "amazon.com"
        else:
            switcher_domain = "amazon.co.uk"
        switcher_url = f"https://sellercentral.{switcher_domain}/account-switcher/default/merchantMarketplace"
        
        try:
            self.page.goto(switcher_url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)
        except Exception as e:
            print(f"[警告] 访问切换页面失败: {e}")
            # 尝试使用当前页面检测
            return self._detect_from_current_page()
        
        self._take_screenshot("00_account_switcher_detect")
        
        # 方法1: 通过按钮检测可用市场
        detected = []
        
        try:
            # 查找所有市场按钮
            buttons = self.page.locator('button.full-page-account-switcher-account-details')
            count = buttons.count()
            print(f"[市场检测] 找到 {count} 个市场按钮")
            
            for i in range(count):
                btn = buttons.nth(i)
                text = btn.inner_text()
                
                # 检查是否已选中
                is_selected = False
                try:
                    radio = btn.locator('input[type="radio"]')
                    if radio.count() > 0:
                        is_selected = radio.evaluate('el => el.checked')
                except:
                    pass
                
                # 匹配市场名称
                for code, info in MARKETPLACE_CONFIG.items():
                    if info.name in text or code in text:
                        mp = MarketplaceInfo(
                            code=code,
                            name=info.name,
                            region=info.region,
                            domain=info.domain,
                            is_available=True,
                            is_selected=is_selected
                        )
                        detected.append(mp)
                        if is_selected:
                            self.current_marketplace = code
                        print(f"  [检测] {code} ({info.name}): {'已选中' if is_selected else '可用'}")
                        break
                        
        except Exception as e:
            print(f"[警告] 通过按钮检测市场失败: {e}")
        
        # 方法2: 如果方法1失败，通过页面文本检测
        if not detected:
            detected = self._detect_from_page_text()
        
        self.detected_marketplaces = detected
        print(f"[市场检测] 共检测到 {len(detected)} 个可用市场")
        
        return detected
    
    def _detect_from_current_page(self) -> List[MarketplaceInfo]:
        """从当前页面检测市场"""
        return self._detect_from_page_text()
    
    def _detect_from_page_text(self) -> List[MarketplaceInfo]:
        """通过页面文本检测市场"""
        detected = []
        
        try:
            page_text = self.page.evaluate('() => document.body.innerText || ""')
            
            for code, info in MARKETPLACE_CONFIG.items():
                if info.name in page_text:
                    detected.append(MarketplaceInfo(
                        code=code,
                        name=info.name,
                        region=info.region,
                        domain=info.domain,
                        is_available=True
                    ))
                    print(f"  [文本检测] {code} ({info.name}): 可用")
        except Exception as e:
            print(f"[警告] 通过文本检测市场失败: {e}")
        
        return detected
    
    # MKID 到市场代码映射（逐步收集）
    MKID_MAP = {
        "amzn1.mp.o.A1AM78C64UM0Y8": "MX",
        "amzn1.mp.o.AMEN7PMS3EDWL": "BE",
        "amzn1.mp.o.A1F83G8C2ARO7P": "UK",
    }
    
    def get_current_marketplace(self) -> Optional[str]:
        """
        获取当前市场
        
        返回:
            Optional[str]: 当前市场代码，如果无法检测则返回 None
        """
        current_url = self.page.url
        
        # 方法1: 从 URL 中的 mkid 检测（最准确）
        mkid_match = __import__('re').search(r'mons_sel_mkid=([^&]+)', current_url)
        if mkid_match:
            mkid = mkid_match.group(1)
            code = self.MKID_MAP.get(mkid)
            if code:
                self.current_marketplace = code
                return code
        
        # 方法2: 从 URL 域名检测
        if "sellercentral.amazon.co.uk" in current_url:
            try:
                page_text = self.page.evaluate('() => document.body.innerText || ""')
                for code in ["UK", "BE", "NL", "SE", "DE", "FR", "ES", "IT", "PL"]:
                    info = MARKETPLACE_CONFIG[code]
                    if info.name in page_text:
                        self.current_marketplace = code
                        return code
            except:
                pass
            return "UK"
        elif "sellercentral.amazon.com.mx" in current_url:
            return "MX"
        elif "sellercentral.amazon.ca" in current_url:
            return "CA"
        elif "sellercentral.amazon.com" in current_url:
            # amazon.com 可能是 US 或 MX，优先检查 mkid，如果无 mkid 默认 US
            return "US"
        
        return self.current_marketplace
    
    def select_best_marketplace(self, target: str, brand_name: Optional[str] = None) -> str:
        """
        智能选择最佳市场
        
        策略:
        1. 如果目标市场可用，直接使用
        2. 如果目标市场不可用，选择同区域的其他可用市场
        3. 如果同区域都不可用，选择已批准的市场（对于已有5461的品牌）
        4. 最后选择默认市场
        
        参数:
            target: 目标市场代码
            brand_name: 品牌名称（用于检查是否已有批准）
            
        返回:
            str: 选中的市场代码
        """
        print(f"\n[智能选择] 目标市场: {target}")
        
        if not self.detected_marketplaces:
            self.detect_available_marketplaces()
        
        # 策略1: 目标市场可用
        target_info = MARKETPLACE_CONFIG.get(target)
        if target_info:
            available_codes = [mp.code for mp in self.detected_marketplaces if mp.is_available]
            if target in available_codes:
                print(f"[智能选择] 目标市场 {target} 可用，直接使用")
                return target
        
        # 策略2: 同区域其他市场
        if target_info:
            target_region = target_info.region
            region_codes = REGION_MARKETPLACES.get(target_region, [])
            
            for code in region_codes:
                if code != target:
                    mp = next((m for m in self.detected_marketplaces 
                              if m.code == code and m.is_available), None)
                    if mp:
                        print(f"[智能选择] 目标市场不可用，选择同区域市场: {code}")
                        return code
        
        # 策略3: 选择第一个可用的市场
        available = [mp for mp in self.detected_marketplaces if mp.is_available]
        if available:
            selected = available[0].code
            print(f"[智能选择] 选择第一个可用市场: {selected}")
            return selected
        
        # 策略4: 默认返回目标市场（即使检测失败）
        print(f"[智能选择] 无法检测可用市场，默认使用目标: {target}")
        return target
    
    def switch_to_marketplace(self, target: str, max_retries: int = 3, 
                             verify: bool = True) -> Tuple[bool, str]:
        """
        切换到指定市场
        
        参数:
            target: 目标市场代码
            max_retries: 最大重试次数
            verify: 是否验证切换结果
            
        返回:
            Tuple[bool, str]: (是否成功, 实际市场代码或错误信息)
        """
        print(f"\n[市场切换] 目标: {target} (最大重试: {max_retries})")
        
        # 获取目标市场信息
        target_info = MARKETPLACE_CONFIG.get(target)
        if not target_info:
            return False, f"未知市场: {target}"
        
        # 检查当前页面是否是 account-switcher（如果是，不能认为已经在目标市场）
        current_url = self.page.url
        is_account_switcher = 'account-switcher' in current_url or 'merchantMarketplace' in current_url
        
        # 如果已经在目标市场，且不在 account-switcher 页面，直接返回
        current = self.get_current_marketplace()
        if current == target and not is_account_switcher:
            print(f"[市场切换] 已经在 {target} 市场，无需切换")
            return True, target
        
        # 如果当前市场无法检测，但 URL 包含目标 mkid，也认为成功（但不在 account-switcher 时）
        mkid_match = __import__('re').search(r'mons_sel_mkid=([^&]+)', current_url)
        if mkid_match and not is_account_switcher:
            mkid = mkid_match.group(1)
            if self.MKID_MAP.get(mkid) == target:
                print(f"[市场切换] URL 中的 mkid 匹配 {target}，确认已在目标市场")
                return True, target
        
        if is_account_switcher:
            print(f"[市场切换] 当前在 account-switcher 页面，需要执行切换")
        
        # 尝试切换
        for attempt in range(1, max_retries + 1):
            print(f"\n[市场切换] 尝试 {attempt}/{max_retries}")
            
            try:
                success = self._do_switch(target, target_info)
                
                if success and verify:
                    # 验证切换结果
                    verified, actual = self._verify_switch(target)
                    if verified:
                        print(f"[市场切换] [OK] 成功切换到 {actual}")
                        self.current_marketplace = actual
                        return True, actual
                    else:
                        print(f"[市场切换] ⚠️ 切换后验证失败，实际市场: {actual}")
                        if attempt < max_retries:
                            print(f"[市场切换] 等待后重试...")
                            time.sleep(5)
                            continue
                        else:
                            return False, f"验证失败: 期望 {target}, 实际 {actual}"
                
                elif success:
                    print(f"[市场切换] [OK] 切换完成（未验证）")
                    return True, target
                else:
                    print(f"[市场切换] [FAIL] 切换操作失败")
                    if attempt < max_retries:
                        time.sleep(5)
                        continue
                        
            except Exception as e:
                print(f"[市场切换] 错误: {e}")
                if attempt < max_retries:
                    time.sleep(5)
                    continue
                else:
                    return False, str(e)
        
        return False, f"重试 {max_retries} 次后仍失败"
    
    def _do_switch(self, target: str, target_info: MarketplaceInfo) -> bool:
        """
        执行实际的切换操作
        
        返回:
            bool: 是否成功
        """
        domain = target_info.domain
        target_country = target_info.name
        
        # 访问切换页面
        switcher_url = f"https://sellercentral.{domain}/account-switcher/default/merchantMarketplace"
        
        try:
            self.page.goto(switcher_url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(4)
        except Exception as e:
            print(f"[切换操作] 访问切换页面失败: {e}")
            return False
        
        self._take_screenshot(f"01_switch_attempt_{target}")
        
        # 方法1: 使用 Playwright locator 点击目标国家按钮
        try:
            country_btn = self.page.locator(
                'button.full-page-account-switcher-account-details',
                has_text=target_country
            ).first
            
            if country_btn.count() > 0:
                country_btn.click()
                print(f"[切换操作] 已点击 {target_country}")
                time.sleep(2)
            else:
                print(f"[切换操作] 未找到 {target_country} 按钮，尝试方法2...")
                
                # 方法2: JavaScript 查找并点击
                js_result = self.page.evaluate(f'''() => {{
                    const buttons = document.querySelectorAll('button.full-page-account-switcher-account-details');
                    for (const btn of buttons) {{
                        const text = btn.textContent || '';
                        if (text.includes('{target_country}')) {{
                            const radio = btn.querySelector('input[type="radio"]');
                            if (radio) {{
                                radio.checked = true;
                                radio.dispatchEvent(new Event('change', {{ bubbles: true }}));
                            }}
                            btn.click();
                            return {{ clicked: true, method: 'js', text: text.substring(0, 50) }};
                        }}
                    }}
                    return {{ clicked: false, available: Array.from(buttons).map(b => b.textContent.substring(0, 50)) }};
                }}''')
                print(f"[切换操作] JS 点击结果: {js_result}")
                time.sleep(2)
                
                if not js_result.get('clicked'):
                    return False
        except Exception as e:
            print(f"[切换操作] 点击国家按钮失败: {e}")
            return False
        
        # 点击 "Select account" 按钮
        try:
            # 方法1: Playwright locator
            select_btn = self.page.locator('button:has-text("Select account")').first
            if select_btn.count() == 0:
                # 方法2: 查找 kat-button
                select_btn = self.page.locator('kat-button:has-text("Select account")').first
            if select_btn.count() == 0:
                # 方法3: JS 查找
                js_result = self.page.evaluate('''() => {
                    const buttons = document.querySelectorAll('button, kat-button, a[role="button"]');
                    for (let btn of buttons) {
                        const text = (btn.textContent || btn.label || '').toLowerCase();
                        if (text.includes('select account')) {
                            btn.click();
                            return { clicked: true, method: 'js', text: btn.textContent?.slice(0, 50) };
                        }
                    }
                    return { clicked: false };
                }''')
                if js_result.get('clicked'):
                    print("[切换操作] JS 点击 Select account")
                    time.sleep(6)
                else:
                    print("[警告] 未找到 Select account 按钮")
            else:
                select_btn.click()
                print("[切换操作] 已点击 Select account")
                time.sleep(6)
        except Exception as e:
            print(f"[警告] 点击 Select account 失败: {e}")
        
        self._take_screenshot(f"02_switch_after_{target}")
        return True
    
    def _verify_switch(self, expected: str) -> Tuple[bool, str]:
        """
        验证切换结果
        
        返回:
            Tuple[bool, str]: (是否正确, 实际市场代码)
        """
        print(f"[验证] 验证是否切换到 {expected}...")
        
        # 方法1: 检查 URL
        current_url = self.page.url
        expected_info = MARKETPLACE_CONFIG.get(expected)
        
        if expected_info and expected_info.domain in current_url:
            # URL 匹配，进一步确认具体国家
            actual = self._detect_country_from_page()
            if actual == expected:
                return True, actual
            elif actual:
                print(f"[验证] URL 匹配但国家不匹配: 期望 {expected}, 实际 {actual}")
                return False, actual
        
        # 方法2: 检查页面文本
        actual = self._detect_country_from_page()
        if actual == expected:
            return True, actual
        
        # 方法3: 检查页面上的市场标识
        try:
            page_text = self.page.evaluate('() => document.body.innerText || ""')
            expected_name = MARKETPLACE_CONFIG.get(expected, {}).name
            
            if expected_name and expected_name in page_text:
                return True, expected
            elif actual:
                return False, actual
        except:
            pass
        
        return False, "unknown"
    
    def _detect_country_from_page(self) -> Optional[str]:
        """从页面检测当前国家"""
        try:
            # 检查 URL 参数
            current_url = self.page.url
            mkid_match = re.search(r'mons_sel_mkid=([^&]+)', current_url)
            if mkid_match:
                mkid = mkid_match.group(1)
                # 根据 mkid 前缀判断国家（这需要映射表）
                # 暂时跳过
            
            # 检查页面上的国家文本
            page_text = self.page.evaluate('() => document.body.innerText || ""')
            
            for code, info in MARKETPLACE_CONFIG.items():
                if info.name in page_text:
                    return code
                    
        except Exception as e:
            print(f"[检测] 从页面检测国家失败: {e}")
        
        return None
    
    def switch_to_best_marketplace(self, preferred: str, brand_name: Optional[str] = None,
                                   max_retries: int = 3) -> Tuple[bool, str]:
        """
        智能切换到最佳市场
        
        流程:
        1. 检测所有可用市场
        2. 智能选择最佳市场
        3. 执行切换
        4. 验证结果
        
        参数:
            preferred: 首选市场
            brand_name: 品牌名称
            max_retries: 最大重试次数
            
        返回:
            Tuple[bool, str]: (是否成功, 实际市场代码)
        """
        print(f"\n{'='*70}")
        print(f"[智能切换] 首选市场: {preferred}")
        print(f"{'='*70}")
        
        # 快速检查：如果已经在目标市场，且不在 account-switcher 页面，直接返回
        current_url = self.page.url
        is_account_switcher = 'account-switcher' in current_url or 'merchantMarketplace' in current_url
        current = self.get_current_marketplace()
        if current == preferred and not is_account_switcher:
            print(f"[智能切换] 已经在 {preferred} 市场，无需切换")
            return True, preferred
        if is_account_switcher:
            print(f"[智能切换] 当前在 account-switcher 页面，需要执行切换")
        
        # 步骤1: 检测可用市场（传入目标市场以选择正确切换页面域名）
        self.detect_available_marketplaces(preferred)
        
        # 步骤2: 智能选择
        best = self.select_best_marketplace(preferred, brand_name)
        
        # 步骤3: 执行切换
        return self.switch_to_marketplace(best, max_retries=max_retries)


# 便捷函数
def switch_marketplace(page: Page, target: str, evidence_dir: Optional[str] = None,
                      max_retries: int = 3) -> Tuple[bool, str]:
    """
    便捷函数：切换到指定市场
    
    用法:
        success, actual = switch_marketplace(page, "BE")
        if success:
            print(f"已切换到 {actual}")
    """
    switcher = MarketplaceSwitcher(page, evidence_dir)
    return switcher.switch_to_marketplace(target, max_retries=max_retries)


def switch_to_best_marketplace(page: Page, preferred: str, 
                               evidence_dir: Optional[str] = None,
                               max_retries: int = 3) -> Tuple[bool, str]:
    """
    便捷函数：智能切换到最佳市场
    
    用法:
        success, actual = switch_to_best_marketplace(page, "BE")
    """
    switcher = MarketplaceSwitcher(page, evidence_dir)
    return switcher.switch_to_best_marketplace(preferred, max_retries=max_retries)
