# GTIN Exemption Reference

> 位置: `projects/amazon-5461-bot/`
> 最后更新: 2026-04-12

## 流程概述

GTIN 豁免申请流程:

```
GTIN 豁免申请页面
    ↓
选择品牌
    ↓
选择分类路径
    ↓
上传产品图片
    ↓
填写声明文本
    ↓
点击 Submit
    ↓
等待 Case ID
```

## 关键选择器

### 品牌选择

```yaml
selector: 'kat-dropdown[name="brand"]'  # 或类似
```

### 分类路径

```yaml
selector: 'kat-dropdown[name="category"]'  # 或类似
```

### 文件上传

与 5461 流程类似，使用 Katal 上传组件。

### 声明文本

```yaml
selector: 'kat-textarea[name="statement"]'  # 或类似
```

### 提交按钮

```yaml
selector: 'kat-button:has-text("Submit")'
```

## 品牌资料包要求

```
brand_packs/<BRAND_NAME>/
├── manifest.json
├── docs/
│   └── gtin_exemption_statement_us.txt  # GTIN 豁免声明
├── images/
│   ├── packaging_front.jpg
│   └── product_main.jpg
└── attachments/
    └── (可选)
```

## 执行脚本

### 统一入口

```bash
python submit_5461.py <account_id> <brand_name> --flow gtin
# 或
python scripts/run_full_5461_workflow.py <account_id> <brand_name> --flow gtin
```

### MCP 工具调用

```bash
python -m src.mcp_server.server '{"tool":"submit_gtin_exemption","args":{"account_id":"us_store_517","brand_name":"HOMEMO"}}'
```

## 注意事项

1. GTIN 豁免与 5461 申请有关联关系
2. 部分市场要求先通过 5461 才能申请 GTIN 豁免
3. 图片要求与 5461 相同
4. 声明文本需针对 GTIN 豁免场景编写

## 当前限制

- `config/selectors/` 中 GTIN 流程的选择器仍为占位符
- 需要补充真实的选择器配置
