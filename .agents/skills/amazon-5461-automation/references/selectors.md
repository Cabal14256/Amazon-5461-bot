# Selectors Reference

> 位置: `projects/amazon-5461-bot/config/selectors/`
> 最后更新: 2026-04-12

## 文件结构

```
config/selectors/
├── flow_add_product_verify.yaml    # ✅ 已配置真实选择器
├── flow_submit_5461.yaml           # ⚠️ 部分配置
└── flow_submit_gtin_exemption.yaml # ⚠️ 占位符
```

## Katal 组件处理

Amazon 使用 Katal 框架的自定义元素，需要通过 Shadow DOM 访问:

```python
# 获取 Shadow Root
element = page.locator('kat-input[name="brand-0-value"]')
shadow_root = element.evaluate('el => el.shadowRoot')

# 访问内部元素
inner_input = element.evaluate('''el => {
    const shadow = el.shadowRoot;
    return shadow.querySelector('input');
}''')
```

### 通用 Shadow DOM 操作

```python
# 填写 Katal Input
def fill_katal_input(page, selector, value):
    page.evaluate(f'''() => {{
        const el = document.querySelector('{selector}');
        if (el && el.shadowRoot) {{
            const input = el.shadowRoot.querySelector('input');
            if (input) {{
                input.value = '{value}';
                input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                input.dispatchEvent(new Event('change', {{ bubbles: true }}));
            }}
        }}
    }}''')

# 点击 Katal Button
def click_katal_button(page, selector):
    page.evaluate(f'''() => {{
        const btn = document.querySelector('{selector}');
        if (btn && btn.shadowRoot) {{
            const innerBtn = btn.shadowRoot.querySelector('button');
            if (innerBtn) innerBtn.click();
        }}
    }}''')
```

## 逐字符输入

直接设置 `input.value` 不会触发 Amazon 的表单验证，需要逐字符输入:

```python
def fill_char_by_char(page, selector, text, delay_ms=50):
    """逐字符输入以触发表单验证"""
    for char in text:
        page.locator(selector).press(char)
        page.wait_for_timeout(delay_ms)
```

## 主要选择器

### Add Product 页面

```yaml
item_name:
  selector: 'kat-textarea[name="item_name-0-value"]'
  type: Shadow DOM

brand_name:
  selector: 'kat-input[name="brand-0-value"]'
  type: Shadow DOM

no_product_id_checkbox:
  selector: 'kat-checkbox:has-text("This product does not have a Product ID")'
  type: Shadow DOM

next_button:
  selector: 'kat-button#next-button'
  type: Shadow DOM
```

### 5461 表单页面

```yaml
apply_to_sell_button:
  primary: 'kat-button[data-cy="seller-qualification-path-forward-button"]'
  fallback: 'kat-button:has-text("Apply to sell")'

product_title:
  selector: 'kat-input#question-cat_auth_mo_question_string_id_product_title'

manufacturer:
  selector: 'kat-input#question-cat_auth_mo_question_string_id_manufacturer'

product_description:
  selector: 'kat-input#question-cat_auth_mo_question_string_id_product_description'

contact_email:
  selector: 'kat-input#contact_info_email_input'

submit_button:
  selector: 'kat-button#submit_button'
```

## 选择器收集纪律

1. **优先使用 data-cy 属性**: Amazon 的测试标识符相对稳定
2. **准备备选选择器**: 主选择器失效时使用 fallback
3. **避免依赖样式类**: 容易变化
4. **记录发现时间**: 便于追踪选择器老化
5. **保存截图**: 作为选择器验证依据
