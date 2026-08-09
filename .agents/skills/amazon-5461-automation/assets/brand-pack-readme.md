# 品牌资料包结构

每个品牌需要一个独立的资料包目录，结构如下：

```
brand_packs/<BRAND_NAME>/
├── manifest.json              # 品牌元数据（必需）
├── docs/
│   ├── 5461_statement_us.txt      # 5461 声明文本
│   └── gtin_exemption_statement_us.txt  # GTIN 豁免声明
├── images/
│   ├── packaging_front.jpg        # 包装正面图（必需）
│   └── product_main.jpg           # 产品主图（必需）
└── attachments/
    └── invoice.pdf                # 发票或其他附件
```

## 文件要求

### 图片要求
- 格式: PNG 或 JPEG
- 大小: 不超过 10MB
- 内容: 
  - 包装正面图：清晰显示品牌名称
  - 产品主图：产品本身的照片
  - 必须是实拍图，不能是 PS 或数字修改

### 声明文本要求
- 说明品牌与卖家的关系
- 说明产品的真实性
- 针对 5461 或 GTIN 豁免的具体说明

## 创建步骤

1. 复制 `manifest.json` 模板到品牌目录
2. 填写品牌名称和其他信息
3. 准备图片并放入 `images/` 目录
4. 编写声明文本并放入 `docs/` 目录
5. 准备附件（如有）放入 `attachments/` 目录

## 验证

运行以下命令验证品牌资料包完整性：

```bash
python check-project.py
```
