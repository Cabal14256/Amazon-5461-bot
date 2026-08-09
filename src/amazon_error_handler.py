"""
Amazon 服务端错误页面检测与自动恢复

经验来源: 2026-05-18 578US 批量提交 — HOMEMO 成功后，WILLONE/JZG/MP-MALL
全部遇到 Amazon 服务端错误页面，导致连锁失败。

错误页面特征:
- 文本: "An error occurred when we tried to process your request"
- 文本: "Please try again at a later time"
- 页面几乎空白，只有顶部错误横幅
- 通常发生在连续提交后，Amazon 服务端临时过载

解决方案:
1. 在每个品牌开始前检测错误页面
2. 检测到后等待 60-120 秒，刷新页面重试
3. 最多重试 3 次，仍失败则标记为 SERVER_ERROR
4. 批量模式下，每个品牌独立检测，避免连锁失败
"""

import time
from typing import Optional, Callable
from playwright.sync_api import Page


# Amazon 服务端错误页面的特征文本
SERVER_ERROR_PATTERNS = [
    "an error occurred when we tried to process your request",
    "we apologize for the inconvenience",
    "please try again at a later time",
    "rest assured that we are working to resolve the problem",
]

# 页面内容过少也是错误页面的特征（正常 Product Identity 页面内容很丰富）
MIN_PAGE_TEXT_LENGTH = 5000  # 正常页面通常 > 10000 字符


def _safe_page_text(page: Page) -> str:
    """Return visible-ish text without double-counting every ancestor's textContent.

    The previous recursive implementation added `textContent` for every node, which can
    inflate normal Seller Central pages and sometimes time out/throw while the page is
    still loading. This helper is intentionally conservative for server-error checks.
    """
    return page.evaluate('''() => {
        function visibleText(root) {
            const walker = document.createTreeWalker(
                root,
                NodeFilter.SHOW_TEXT,
                {
                    acceptNode(node) {
                        const parent = node.parentElement;
                        if (!parent) return NodeFilter.FILTER_REJECT;
                        const tag = parent.tagName;
                        if (tag === 'SCRIPT' || tag === 'STYLE' || tag === 'NOSCRIPT') return NodeFilter.FILTER_REJECT;
                        const text = (node.nodeValue || '').trim();
                        if (!text) return NodeFilter.FILTER_REJECT;
                        return NodeFilter.FILTER_ACCEPT;
                    }
                }
            );
            const parts = [];
            let n;
            while ((n = walker.nextNode())) parts.push(n.nodeValue.trim());
            return parts.join(' ');
        }
        let text = visibleText(document.body || document.documentElement);
        for (const el of document.querySelectorAll('*')) {
            if (el.shadowRoot) text += ' ' + visibleText(el.shadowRoot);
        }
        return text;
    }''') or ''


def is_amazon_server_error_page(page: Page) -> dict:
    """
    检测当前页面是否是 Amazon 服务端错误页面
    
    Returns:
        {
            'is_error': bool,
            'confidence': str,  # 'high' | 'medium' | 'low'
            'matched_patterns': list,
            'page_text_length': int,
        }
    """
    try:
        page_text = _safe_page_text(page)
    except Exception:
        # 如果连文本都获取不了，大概率页面有问题
        return {
            'is_error': True,
            'confidence': 'high',
            'matched_patterns': ['page_text_unavailable'],
            'page_text_length': 0,
        }
    
    page_text_lower = page_text.lower()
    page_text_length = len(page_text)
    
    matched_patterns = []
    for pattern in SERVER_ERROR_PATTERNS:
        if pattern in page_text_lower:
            matched_patterns.append(pattern)
    
    # 判断逻辑
    is_error = False
    confidence = 'low'
    
    # 高置信度：匹配到多个错误特征文本
    if len(matched_patterns) >= 2:
        is_error = True
        confidence = 'high'
    # 中置信度：匹配到一个错误特征文本 + 页面内容极少
    elif len(matched_patterns) >= 1 and page_text_length < MIN_PAGE_TEXT_LENGTH:
        is_error = True
        confidence = 'high'
    # 中置信度：只匹配到一个错误特征文本
    elif len(matched_patterns) >= 1:
        is_error = True
        confidence = 'medium'
    # 低置信度：没有匹配到错误文本，但页面内容极少（可能是其他错误）
    elif page_text_length < MIN_PAGE_TEXT_LENGTH and page_text_length > 0:
        is_error = True
        confidence = 'low'
    
    return {
        'is_error': is_error,
        'confidence': confidence,
        'matched_patterns': matched_patterns,
        'page_text_length': page_text_length,
    }


def recover_from_server_error(
    page: Page,
    target_url: str,
    max_retries: int = 3,
    wait_seconds: int = 90,
    on_retry: Optional[Callable[[int, dict], None]] = None,
) -> dict:
    """
    从 Amazon 服务端错误页面恢复
    
    策略:
    1. 等待 wait_seconds（让 Amazon 服务端恢复）
    2. 刷新页面或重新导航到目标 URL
    3. 再次检测，如果仍是错误页面则重复
    4. 超过 max_retries 次仍失败，返回失败结果
    
    Args:
        page: Playwright Page 对象
        target_url: 恢复后应导航到的 URL
        max_retries: 最大重试次数
        wait_seconds: 每次重试前的等待时间
        on_retry: 可选回调函数，参数为 (retry_count, error_info)
    
    Returns:
        {
            'success': bool,
            'retries': int,
            'final_state': dict,  # is_amazon_server_error_page 的结果
            'error': str | None,
        }
    """
    for retry in range(max_retries):
        error_info = is_amazon_server_error_page(page)
        
        if not error_info['is_error']:
            return {
                'success': True,
                'retries': retry,
                'final_state': error_info,
                'error': None,
            }
        
        print(f"[ServerError] 检测到 Amazon 服务端错误页面 "
              f"(置信度: {error_info['confidence']}, "
              f"匹配: {error_info['matched_patterns']}, "
              f"文本长度: {error_info['page_text_length']})")
        
        if on_retry:
            on_retry(retry, error_info)
        
        if retry < max_retries - 1:
            # 指数退避: attempt 1=30s, 2=60s, 3=90s (上限 wait_seconds)
            backoff = min(30 * (2 ** retry), wait_seconds)
            print(f"[ServerError] 等待 {backoff} 秒后重试 ({retry + 1}/{max_retries})...")
            time.sleep(backoff)
            
            try:
                # 尝试刷新页面
                page.reload(wait_until="domcontentloaded", timeout=30000)
                try:
                    page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass
                
                # 刷新后如果还是错误页面，尝试重新导航
                error_info_after_reload = is_amazon_server_error_page(page)
                if error_info_after_reload['is_error']:
                    print(f"[ServerError] 刷新后仍是错误页面，尝试重新导航...")
                    page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
                    try:
                        page.wait_for_load_state("networkidle", timeout=8000)
                    except Exception:
                        pass
            except Exception as e:
                print(f"[ServerError] 刷新/导航失败: {e}")
                # 继续下一次重试
    
    # 所有重试都失败了
    final_error = is_amazon_server_error_page(page)
    return {
        'success': False,
        'retries': max_retries,
        'final_state': final_error,
        'error': f'Amazon 服务端错误页面持续 {max_retries} 次重试后仍未恢复',
    }


def ensure_page_ready(
    page: Page,
    target_url: str,
    check_callback: Optional[Callable[[Page], bool]] = None,
    max_retries: int = 3,
    wait_seconds: int = 90,
) -> dict:
    """
    确保页面处于可用状态（无服务端错误、正常加载）
    
    在批量模式下，每个品牌开始前调用此函数，避免错误页面连锁传播。
    
    Args:
        page: Playwright Page 对象
        target_url: 目标 URL
        check_callback: 可选的额外检查函数，接收 page 返回 bool
        max_retries: 最大重试次数
        wait_seconds: 每次重试等待时间
    
    Returns:
        同 recover_from_server_error
    """
    # 首先检查服务端错误
    result = recover_from_server_error(
        page=page,
        target_url=target_url,
        max_retries=max_retries,
        wait_seconds=wait_seconds,
    )
    
    if not result['success']:
        return result
    
    # 额外检查（如需要）
    if check_callback and not check_callback(page):
        return {
            'success': False,
            'retries': result['retries'],
            'final_state': result['final_state'],
            'error': '额外页面检查未通过',
        }
    
    return result


# 便捷函数：在 fill_product_identity_form 前调用
def check_and_recover_before_form_fill(page: Page, add_product_url: str) -> dict:
    """
    在填写 Product Identity 表单前检测并恢复错误页面
    
    使用场景: 阶段 1/3 开始填写表单前
    """
    return ensure_page_ready(
        page=page,
        target_url=add_product_url,
        max_retries=3,
        wait_seconds=90,
    )
