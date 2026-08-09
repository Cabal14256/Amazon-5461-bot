# Same-Tab Reuse: kat-panel 轮换机制 (2026-06-27)

## 问题

626US 批次：WILLONE 提交成功后复用同一标签页给 JZG，JZG 的 `fill_5461_form` 检测到
`Under Review Case ID: <CASE_ID_REDACTED>（品牌='WILLONE'）` 并直接跳过填表，把 WILLONE 的 Case ID 误归属给 JZG。

## CDP 实时监控确认的根本原因

通过 `_tmp_watch_panel.py`（每0.5秒轮询 `kat-panel.shadowRoot`）接入浏览器观察，发现：

**Amazon 新UI 在页面上预渲染了 3 个 `kat-panel` 实例，始终有一个处于 `visible=true`。**

品牌A提交成功后，Amazon 自动把下一品牌的内容填入另一个 panel 并设 `visible=true`——
不是"关闭旧panel再重新打开"，而是三个 panel 实例轮换显示。

时间线（11:41-11:43 监控日志）：
- 11:41:17 — IKABO 进行中，此时 VASG 的 panel 已经 `visible=true`，内容已是 VASG 的授权申请
- 11:42:58 — panel 内容变为 `"Apply to sell"`（过渡状态）
- 11:42:59 — panel 内容变为 `"What is this for? We restrict the sale..."`（VASG 5461 表单打开）

## 为什么 close_5461_panel() 无效

提交成功时 panel 状态：
```json
{
  "visible": null,        ← 没有 visible 属性
  "btn_visible": false,   ← 按钮 offsetParent === null（不可见）
  "btn_display": "block"  ← display 是 block，但父容器隐藏
}
```

此时没有可点的按钮。Amazon 在后台已经把下一品牌内容填入了另一个 panel。

### Shadow DOM 穿透尝试史（全部无效）

| 方法 | 结果 | 原因 |
|------|------|------|
| 原始 JS `querySelectorAll('button[part="panel-close-button"]')` | 找不到按钮 | 穿不透 Shadow DOM |
| Playwright `page.locator('button[aria-label="close"]').click()` | 返回 `clicked`，但无效 | 点到的是已不 visible 的按钮，Amazon React 不响应 |
| `kat-panel.shadowRoot.querySelector('button.close').click()` + dispatch MouseEvent | 技术上执行，但无效 | 时机已晚，面板已收起，React 状态不响应 |

## 已实施的修复

### Fix 1: 品牌名校验（`src/form_filler.py`，2026-06-27）

JS evaluate 同时提取 `application_tile_header` 里的品牌名：
```js
const headerEl = panel.querySelector('[data-cy="application_tile_header"]');
const headerText = headerEl ? (headerEl.innerText || '') : '';
const brandMatch = headerText.match(/for\s+(.+)$/i);
const panelBrand = brandMatch ? brandMatch[1].trim() : null;
```

Python 侧：`panel_brand` 不匹配 `self.brand_name` 时，打印警告并继续填表，不设 `under_review_case_id`。
这是第二道防线，能阻止误判，但不能阻止 Amazon 把错误内容填入 panel。

### Fix 2（待实施）: reset_page_for_next_brand() 做 page.reload()

根本修复：成功拿到 Case ID 后，在 `reset_page_for_next_brand()` 里执行 `page.reload()`，
彻底清掉所有预渲染 panel 的 DOM 状态。代价是每个品牌间多约 5-10 秒页面加载时间，
但彻底解决 panel 内容污染问题。

## CDP 监控接入方法

```python
# _tmp_watch_panel.py 核心模式
WS = "ws://127.0.0.1:<debug_port>/devtools/browser/<id>"
# debug_port 从 AdsPower API 获取:
# curl "http://local.adspower.net:50325/api/v1/browser/start?user_id=<profile_id>"
# → data.ws.puppeteer

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp(WS)
    for ctx in browser.contexts:
        for pg in ctx.pages:
            if 'sellercentral' in pg.url:
                # 轮询 kat-panel shadowRoot 状态
                state = pg.evaluate("""() => {
                    const panels = document.querySelectorAll('kat-panel');
                    return [...panels].map(p => ({
                        visible: p.getAttribute('visible'),
                        has_shadow: !!p.shadowRoot,
                        btn_visible: p.shadowRoot?.querySelector('button.close')?.offsetParent !== null,
                        text: (p.innerText || '').slice(0, 150),
                    }));
                }""")
```

注意：监控连接的是具体标签页对象，标签页关闭后连接失效，需重新 `find_seller_pages()`。

## 626US 批次最终状态（2026-06-27）

| 品牌 | Case ID | 备注 |
|------|---------|------|
| WILLONE | <CASE_ID_REDACTED> | ✅ batch1 成功 |
| JZG | <CASE_ID_REDACTED> | ✅ batch3 成功（品牌名校验通过） |
| MP-MALL | <CASE_ID_REDACTED> | ✅ batch1 成功 |
| XDesign | <CASE_ID_REDACTED> | ✅ batch3 Under Review |
| IKABO | <CASE_ID_REDACTED> | ✅ batch4 成功 |
| uShield | <CASE_ID_REDACTED> | ✅ batch3 成功 |
| OUNNE | <CASE_ID_REDACTED> | ✅ batch3 成功 |
| VASG | <CASE_ID_REDACTED> | ✅ batch4 成功 |
| mocodi | <CASE_ID_REDACTED> | ✅ batch3 成功 |
| JavoYion | <CASE_ID_REDACTED> | ✅ batch3 成功 |
| MoShieldwish | <CASE_ID_REDACTED> | ✅ batch3 成功 |
