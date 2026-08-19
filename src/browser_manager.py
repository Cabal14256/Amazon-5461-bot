#!/usr/bin/env python3
"""
Browser Manager - 统一的浏览器连接管理模块

提供 AdsPower 浏览器的连接、管理和复用功能
"""
import json
import sys
import time
from collections.abc import Iterable
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from playwright.sync_api import Page, sync_playwright

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from adspower_client import AdsPowerClient
from config_loader import resolve_accounts_path


class BrowserConnectionError(RuntimeError):
    """Sanitized, retryable AdsPower/CDP connection failure."""

    def __init__(self, error_code: str, error_class: str):
        self.error_code = error_code
        self.error_class = error_class
        super().__init__(f"{error_code} ({error_class})")


class BrowserManager:
    """浏览器管理器 - 统一管理 AdsPower 浏览器连接"""

    def __init__(
        self,
        api_base_url: str = "http://127.0.0.1:50325",
        *,
        cdp_connect_timeout_ms: int = 30_000,
        cdp_health_timeout_sec: float = 5.0,
        cdp_ready_timeout_sec: float = 30.0,
        cdp_restart_attempts: int = 1,
        profile_restart_wait_sec: float = 5.0,
        client: AdsPowerClient | None = None,
    ):
        self.api_base_url = api_base_url
        self.client = client or AdsPowerClient(api_base_url=api_base_url)
        self.cdp_connect_timeout_ms = max(1_000, int(cdp_connect_timeout_ms))
        self.cdp_health_timeout_sec = max(0.1, float(cdp_health_timeout_sec))
        self.cdp_ready_timeout_sec = max(1.0, float(cdp_ready_timeout_sec))
        self.cdp_restart_attempts = max(0, int(cdp_restart_attempts))
        self.profile_restart_wait_sec = max(0.0, float(profile_restart_wait_sec))
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._profile_id = None

    def get_profile_id(self, account_id: str, config_path: Path | None = None) -> str:
        """从配置文件中获取 profile_id"""
        if config_path is None:
            config_path = resolve_accounts_path(Path(__file__).parent.parent / "config" / "accounts.json")
        
        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)
        
        for account in config.get('accounts', []):
            if account['account_id'] == account_id:
                return account.get('adspower_profile_id')
        
        raise ValueError(f"找不到账号: {account_id}")
    
    def get_account_config(self, account_id: str, config_path: Path | None = None) -> dict[str, Any]:
        """获取完整的账号配置"""
        if config_path is None:
            config_path = resolve_accounts_path(Path(__file__).parent.parent / "config" / "accounts.json")
        
        with open(config_path, encoding="utf-8") as f:
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
        if not profile_id:
            raise ValueError("AdsPower profile_id 不能为空")
        print("[BrowserManager] 连接到 AdsPower profile")
        self._profile_id = profile_id

        restart_count = 0
        last_error: Exception | None = None
        while True:
            try:
                result = self.client.ensure_profile_started(
                    profile_id=profile_id,
                    health_timeout_sec=self.cdp_health_timeout_sec,
                    ready_timeout_sec=self.cdp_ready_timeout_sec,
                    restart_wait_sec=self.profile_restart_wait_sec,
                    restart_if_unhealthy=False,
                )
                ws_url = str(result.get("ws_endpoint") or "")
                if not ws_url:
                    raise RuntimeError("AdsPower returned no CDP endpoint")
                self._connect_playwright(ws_url)
                self._context = (
                    self._browser.contexts[0]
                    if self._browser.contexts
                    else self._browser.new_context()
                )
                self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
                print("[BrowserManager] 已连接到浏览器")
                print(f"[BrowserManager] 当前页面: {self._safe_page_location(self._page.url)}")
                return self._page
            except Exception as exc:
                last_error = exc
                self.disconnect(quiet=True)
                if restart_count >= self.cdp_restart_attempts:
                    raise BrowserConnectionError(
                        "cdp_connect_failed",
                        last_error.__class__.__name__,
                    ) from last_error
                restart_count += 1
                print(
                    f"[BrowserManager] CDP 连接失败，自动重启 profile "
                    f"({restart_count}/{self.cdp_restart_attempts})"
                )
                try:
                    self.client.restart_profile(
                        profile_id=profile_id,
                        health_timeout_sec=self.cdp_health_timeout_sec,
                        ready_timeout_sec=self.cdp_ready_timeout_sec,
                        restart_wait_sec=self.profile_restart_wait_sec,
                    )
                except Exception as restart_exc:
                    raise BrowserConnectionError(
                        "adspower_profile_unavailable",
                        restart_exc.__class__.__name__,
                    ) from restart_exc

    def _connect_playwright(self, ws_url: str) -> None:
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.connect_over_cdp(
            ws_url,
            timeout=self.cdp_connect_timeout_ms,
        )

    @staticmethod
    def _safe_page_location(url: str) -> str:
        try:
            parsed = urlsplit(str(url or ""))
            if not parsed.netloc:
                return parsed.scheme or "unknown"
            return f"{parsed.scheme}://{parsed.netloc}{parsed.path[:80]}"
        except Exception:
            return "unknown"
    
    def connect_by_account(self, account_id: str) -> tuple[Page, dict[str, Any]]:
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
        print("[BrowserManager] 页面已加载")
    
    def new_tab(self) -> Page:
        """创建新标签页"""
        if not self._context:
            raise RuntimeError("浏览器未连接")
        
        page = self._context.new_page()
        return page

    def close_tabs_except_hosts(
        self,
        allowed_hosts: Iterable[str],
        kept_pages: Iterable[Any] | None = None,
    ) -> dict[str, int]:
        """Close tabs except exact allowed hosts and explicitly retained pages."""
        if not self._context:
            raise RuntimeError("浏览器未连接")

        allowed = {str(host or "").strip().casefold() for host in allowed_hosts if host}
        kept_page_ids = {id(page) for page in (kept_pages or ())}
        closed = 0
        kept = 0
        failed = 0
        for page in list(self._context.pages):
            try:
                host = (urlsplit(str(getattr(page, "url", "") or "")).hostname or "").casefold()
                if id(page) in kept_page_ids or host in allowed:
                    kept += 1
                    continue
                page.close()
                closed += 1
            except Exception:
                failed += 1
        print(
            f"[BrowserManager] 标签页清理: closed={closed}, kept={kept}, failed={failed}"
        )
        return {"closed": closed, "kept": kept, "failed": failed}
    
    def close_tab(self, page: Page | None = None) -> None:
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
    
    def disconnect(self, *, quiet: bool = False) -> None:
        """Detach Playwright without closing the AdsPower browser profile."""
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        if not quiet:
            print("[BrowserManager] Playwright 已断开，AdsPower 浏览器保持运行")

    def close(self, *, stop_profile: bool = False) -> None:
        """Disconnect by default; stop the AdsPower profile only when requested."""
        profile_id = self._profile_id
        self.disconnect(quiet=True)
        if stop_profile and profile_id:
            try:
                self.client.stop_profile(profile_id=profile_id)
            finally:
                self._profile_id = None
            print("[BrowserManager] AdsPower 浏览器已停止")
        else:
            print("[BrowserManager] Playwright 已断开，AdsPower 浏览器保持运行")
    
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

    @property
    def pages(self) -> list[Page]:
        """Return a snapshot of attached pages without exposing the context."""
        return list(self._context.pages) if self._context is not None else []


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
