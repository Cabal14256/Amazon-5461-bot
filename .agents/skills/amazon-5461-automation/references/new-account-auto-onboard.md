# 新账号自动 Onboard 流程（无需向用户确认）

收到账号编号（如 `641`、`641EU`）+ 品牌列表 + 站点时，**直接执行以下步骤，不要停下来问用户**：

1. **查 AdsPower Local API** 确认 profile 存在：
   ```bash
   curl -s "http://127.0.0.1:50325/api/v1/user/list?page=1&page_size=100" | python -c "
   import sys, json
   data = json.load(sys.stdin)
   lst = data.get('data', {}).get('list', [])
   matches = [p for p in lst if '<N>' in str(p.get('name',''))]
   for p in matches:
       print(p.get('name'), p.get('user_id'), p.get('username',''))
   "
   ```
   - 账号不在 `accounts.json` ≠ 账号不存在。authoritative source 是 AdsPower。
   - 只有 AdsPower 里也找不到该 profile，才需要告知用户。

2. **生成所有品牌的站点文案**：
   ```bash
   .venv/Scripts/python.exe auto_add_account_data.py --all-brands <账号> --site <SITE>
   ```

3. **验证目标品牌文案已生成**：
   ```bash
   for brand in BRAND1 BRAND2 ...; do
     f="brand_packs/$brand/docs/5461_statement_<site>.account_<N>.txt"
     [ -f "$f" ] && echo "OK: $brand" || echo "MISSING: $brand"
   done
   ```

4. **后台启动批次**（必须 `background=true, notify_on_complete=true`）：
   ```bash
   .venv/Scripts/python.exe scripts/run_full_5461_batch.py \
     --accounts <N> --brands "BRAND1,BRAND2,..." --site <SITE> --no-confirm
   ```

## 注意
- `--brands` 参数用逗号分隔，不是空格分隔。
- AdsPower page_size=100 不够时加 `&page=2`。
