#!/usr/bin/env python3
"""
Browser Manager - 统一的浏览器连接管理模块

提供 AdsPower 浏览器的连接、管理和复用功能
"""
import sys
import time
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
from contextlib import contextmanager

from playwright.sync_api import sync_playwright, Page, BrowserContext

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from adspower_client import AdsPowerClient
from config_loader import resolve_accounts_path


class BrowserManager:
    """浏览器管理器 - 统一管理 AdsPower 浏览器连接"""
    
    def __init__(self, api_base_url: str = "http://127.0.0.1:50325"):
        self.api_base_url = api_base_url
        self.client = AdsPowerClient(api_base_url=api_base_url)
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._profile_id = None
    
    def get_profile_id(self, account_id: str, config_path: Optional[Path] = None) -> str:
        """从配置文件中获取 profile_id"""
        if config_path is None:
            config_path = resolve_accounts_path(Path(__file__).parent.parent / "config" / "accounts.json")
        
        import json
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        for account in config.get('accounts', []):
            if account['account_id'] == account_id:
                return account.get('adspower_profile_id')
        
        raise ValueError(f"找不到账号: {account_id}")
    
    def get_account_config(self, account_id: str, config_path: Optional[Path] = None) -> Dict[str, Any]:
        """获取完整的账号配置"""
        if config_path is None:
            config_path = resolve_accounts_path(Path(__file__).parent.parent / "config" / "accounts.json")
        
        import json
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        for account in config.get('accounts', []):
            if account['account_id'] == account_id:
                return account
        
        raise ValueError(f"找不到账号: {account_id}")
    
    def connect(self, profile_id: str) -> Page:
        """
        连接到 AdsPower 浏览器
        
        Args:
            profile_id: AdsPower profile ID
            
        Returns:
            Playwright Page 对象
        """
        print(f"[BrowserManager] 连接到 AdsPower: {profile_id}")
        
        # 启动浏览器
        result = self.client.start_profile(profile_id=profile_id)
        if not result.get('ok'):
            raise RuntimeError(f"启动浏览器失败: {result}")
        
        ws_url = result['ws_endpoint']
        self._profile_id = profile_id
        
        # 连接 Playwright
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.connect_over_cdp(ws_url)
        
        # 获取或创建上下文
        self._context = self._browser.contexts[0] if self._browser.contexts else self._browser.new_context()
        
        # 获取或创建页面
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        
        print(f"[BrowserManager] 已连接到浏览器")
        print(f"[BrowserManager] 当前 URL: {self._page.url[:80]}...")
        
        return self._page
    
    def connect_by_account(self, account_id: str) -> Tuple[Page, Dict[str, Any]]:
        """
        通过账号 ID 连接浏览器
        
        Args:
            account_id: 账号 ID (如 us_store_530)
            
        Returns:
            (Page, account_config) 元组
        """
        config = self.get_account_config(account_id)
        profile_id = config.get('adspower_profile_id')
        page = self.connect(profile_id)
        return page, config
    
    def navigate(self, url: str, wait_time: int = 5) -> None:
        """导航到指定 URL"""
        if not self._page:
            raise RuntimeError("浏览器未连接，请先调用 connect()")
        
        print(f"[BrowserManager] 导航到: {url[:80]}...")
        self._page.goto(url)
        time.sleep(wait_time)
        print(f"[BrowserManager] 页面已加载")
    
    def new_tab(self) -> Page:
        """创建新标签页"""
        if not self._context:
            raise RuntimeError("浏览器未连接")
        
        page = self._context.new_page()
        return page
    
    def close_tab(self, page: Optional[Page] = None) -> None:
        """关闭标签页"""
        target = page or self._page
        if target:
            target.close()
    
    def screenshot(self, path: str, full_page: bool = True) -> None:
        """保存截图"""
        if not self._page:
            raise RuntimeError("浏览器未连接")
        
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._page.screenshot(path=path, full_page=full_page, timeout=10000)
        print(f"[BrowserManager] 截图已保存: {path}")
    
    def get_page_text(self) -> str:
        """获取页面文本"""
        if not self._page:
            raise RuntimeError("浏览器未连接")
        return self._page.inner_text('body')
    
    def close(self) -> None:
        """关闭浏览器连接"""
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()
        print("[BrowserManager] 浏览器连接已关闭")
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
    
    @property
    def page(self) -> Page:
        """获取当前页面"""
        if not self._page:
            raise RuntimeError("浏览器未连接")
        return self._page


@contextmanager
def managed_browser(api_base_url: str = "http://127.0.0.1:50325"):
    """
    上下文管理器，自动管理浏览器连接
    
    用法:
        with managed_browser() as bm:
            page, config = bm.connect_by_account('us_store_530')
            # 使用 page 进行操作
    """
    manager = BrowserManager(api_base_url=api_base_url)
    try:
        yield manager
    finally:
        manager.close()


if __name__ == "__main__":
    # 测试代码
    print("=" * 60)
    print("BrowserManager 测试")
    print("=" * 60)
    
    with managed_browser() as bm:
        # 测试连接
        page, config = bm.connect_by_account('us_store_530')
        print(f"\n账号配置: {config.get('account_id')}")
        print(f"Profile ID: {config.get('adspower_profile_id')}")
        print(f"当前 URL: {page.url}")
        
        # 测试导航
        bm.navigate("https://sellercentral.amazon.com")
        
        # 测试截图
        bm.screenshot("artifacts/debug/browser_manager_test.png")
        
        print("\n[OK] 测试完成")
