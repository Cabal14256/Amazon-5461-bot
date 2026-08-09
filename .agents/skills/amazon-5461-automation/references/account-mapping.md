# Account Mapping Reference

> 2026-06-01 status note: Standalone AdsPower scan scripts referenced below are not present in the active tree. Profile discovery/backfill is currently integrated into `auto_add_account_data.py` and batch onboarding paths.

> 位置: `projects/amazon-5461-bot/config/accounts.json`
> 最后更新: 2026-04-12

## 配置结构

```json
{
  "accounts": [
    {
      "account_id": "us_store_000",
      "adspower_profile_id": "k1xxxxxx",
      "marketplace": "US",
      "entry_url": "https://sellercentral.amazon.com/abis/listing/create/product_identity?productType=SCREEN_PROTECTOR&itemType=cell-phone-screen-protectors#product_identity",
      "item_type_keyword": "Electronics > Accessories & Supplies > Cell Phone Accessories > Maintenance, Upkeep & Repairs > Screen Protectors",
      "username": "seller@example.com"
    }
  ]
}
```

## 字段说明

| 字段 | 必填 | 说明 |
|------|------|------|
| `account_id` | 是 | 账号标识符 |
| `adspower_profile_id` | 是 | AdsPower Profile ID |
| `marketplace` | 是 | US / EU |
| `entry_url` | 是 | Add Product 页面入口 URL |
| `item_type_keyword` | 是 | 完整分类路径 |
| `username` | 否 | 登录邮箱 |

## 账号管理脚本（当前）

```bash
# 同步品牌文案，并触发账号/profile/email 补全
.\.venv\Scripts\python.exe auto_add_account_data.py <brand> <account> [--site SITE]

# 运行前预检查账号/profile/brand pack
.\.venv\Scripts\python.exe scripts/audit_account_config.py <account> --brand <brand> --site <SITE>

# 批量 dry-run 触发账号配置创建/补全
.\.venv\Scripts\python.exe scripts/run_full_5461_batch.py --accounts <account> --brands all --site <SITE> --dry-run
```

> 历史说明：旧 `scripts/auto_discover_accounts.py`、`scripts/scan_adspower_profiles.py`、`scripts/test_adspower_api.py` 当前不在项目树；不要按旧命令执行。

## 收到新账号编号（如"632EU"）时的核实步骤

用户给出的账号编号不一定已经在 `accounts.json` 里存在。在生成品牌文案 / 启动批次之前，先用只读方式核实：

```bash
# 只读发现：账号是否存在 + 是否有匹配的 AdsPower profile（不写入任何文件）
./.venv/Scripts/python scripts/adspower_profile_sync.py --discover <账号数字> --json

# 只读 dry-run：账号缺失时会同时给出 matched_profile 信息
./.venv/Scripts/python scripts/adspower_profile_sync.py --sync <账号数字> --json
```

`account_exists: false` + `action: "account_missing"` 是正常情况，不代表出错——`auto_add_account_data.py <brand> <账号> --site <SITE>` 和
`scripts/run_full_5461_batch.py --accounts <账号> ...`（内部调用 `ensure_account_exists(..., auto_create=True)`）会在真正执行时自动补全账号配置并匹配 AdsPower profile，不需要手工预先建号。

不要仅凭文本搜索（grep "632"）判断账号是否存在——账号编号可能只以 `us_store_630`、`632-UK-...`（SKU 前缀）等派生形式出现在文件里，而账号本身可能压根还没写入 `accounts.json`。用上面的只读命令做权威核实，而不是猜测文本匹配结果。

## 配置步骤

1. 在 AdsPower 中打开该账号的 Seller Central
2. 进入 Add Product 页面 (Inventory → Add a Product)
3. 选择分类路径
4. 复制浏览器地址栏的完整 URL → 填入 `entry_url`
5. 记录选择的完整分类路径 → 填入 `item_type_keyword`

## 自动账号补全

批量脚本支持账号不存在时自动补全:

```bash
# 传入简写账号，系统自动创建配置并匹配 AdsPower profile
python scripts/run_full_5461_batch.py --accounts 550 --brands all --dry-run
```

输出示例:
```
[AUTO] 已补账号配置: us_store_550 | matched_profile=True
[SYNC] 账号: us_store_550 | 品牌: HOMEMO | SKU: 550-US-HOMEMO-BG42
```

自动补全的字段:
- `adspower_profile_id` (通过扫描匹配)
- `username` / `email`
- `note`
- `status` (设为 `active`)
- `adspower_name` / `adspower_remark`

## 当前配置状态

已配置多个 US 账号，支持自动发现和补全。详见 `config/accounts.json`。

