# Amazon 5461 自动化项目待修复 / 改进清单

更新时间：2026-05-27 15:25 GMT+8

> 目标：记录当前项目还需要修复、验证、产品化和整理的事项。  
> 当前主流程已经能跑，短板主要不再是“能不能填表”，而是“异常状态能不能可靠判断、复核、续接、避免重复提交”。

---

## 一、最高优先级

### 1. 刚改完代码后的安全验证

刚刚已修改：

- 邮箱自动获取 / 回填
- Dashboard check 稳定性
- Item Type Keyword 误报
- 真实交互输入层

当前只做过：

- `py_compile`
- `email_resolver` 小型 smoke 测试

还没做真实页面验证。

建议验证顺序：

1. **dry-run / no-submit 诊断**
   - 确认不会误填；
   - 确认不会误点提交；
   - 确认 email resolver 不会拿到奇怪邮箱；
   - 确认 Item Type Keyword 不再误报。

2. **单账号单品牌真实验证**
   - 只跑 1 个品牌；
   - 拿到 Case ID 或明确 Dashboard 状态后停止；
   - 不连续撞 589/590 这种 429 重账号。

---

### 2. Draft 续接流程产品化

这是当前最大功能缺口。

现状：

- 系统能识别 Draft；
- 但 `/sq/approvalrequest` 的续接流程还没稳定产品化。

需要实现：

- Dashboard / Selling Applications 找到 Draft；
- 提取 `applicationId`；
- 打开 `/hz/reEvaluateApplicationEndpoint?applicationId=...` 或跳转后的 `/sq/approvalrequest`；
- 识别 `/sq/approvalrequest` 字段：
  - `level-0:question-0:text`
  - `level-0:question-1:text`
  - `level-0:question-2:text`
  - `level-0:question-3:text`
  - `contact_info_email_input`
- 补字段、补图片、重新 submit；
- 成功后提 Case ID；
- 失败则保存 evidence，不重复创建新申请。

完成后，很多“半提交 / Draft / 无 Case ID”的情况可以自动收口。

---

### 3. declined-old-application 流程继续收口

现状：

- declined 旧 Case 检测已有；
- 点击旧申请行 / `>` 进入重新申请也部分有效；
- 但 declined-old-application → 新表单加载这段仍容易半加载或卡壳。

需要修：

- 识别 declined 行更准确；
- 点击 `>` 后必须验证进入新申请表单，而不是只看 panel；
- 如果进入半加载 panel，走 close-panel + re-Apply；
- 如果进入 Draft，交给 Draft 续接流程。

---

## 二、高优先级

### 4. StateLoopExecutor 暂不适合默认生产启用

阶段 1-4 架构已经落地，但目前仍是：

> 代码已实现 / Mock 测试通过 / 真实 Seller Central 验证不足 / 默认关闭

主要问题：

- StateLoop 真实点击判断曾经过于乐观；
- `Apply to sell` 没点到也可能被当成成功；
- `form_ready` / `gtin_exemption` 仍可能误判；
- 新旧 UI / 半加载 / 429 的状态转换还要更多真实样本。

建议改进：

- 每个 action 后必须有验证条件；
- 没有真实字段 / 上传控件，不能进入 `form_ready`；
- click action 必须验证页面变化；
- 429 / CloudFront chunk failed 要进入明确状态；
- 先让 StateLoop 只做“诊断 / fallback”，不要默认接管主流程。

---

### 5. 429 健康状态分类细化

现在很多失败都归成：

> Apply to sell 后未加载真实 5461 字段/上传控件

应该拆成更明确的状态：

- `restriction_endpoint_429`
- `cloudfront_chunk_failed`
- `panel_shell_only`
- `form_fields_missing`
- `upload_input_missing`
- `submit_no_case_id`
- `dashboard_navigation_failed`
- `dashboard_not_found`
- `draft_detected`
- `account_rate_limited`

好处：

- 一眼知道是脚本问题还是 Amazon 限流；
- 后面自动决策更容易；
- 状态账本更清楚；
- 不会把所有失败都混成一个 vague failed。

---

### 6. Dashboard / Selling Applications 复核真实验证

刚加固了 Dashboard navigation，但还没真实跑验证。

后续要确认：

- US / UK / BE / MX 等站点 URL 是否都正确；
- `ERR_ABORTED` 后 salvage 是否真的有效；
- `about:blank` reset 是否会丢 session；
- Dashboard 空白时是否还能补查 Selling Applications；
- Dashboard 文本分析是否能正确区分：
  - Draft
  - Under review
  - Approved
  - Declined
  - Not found

项目现在的安全边界是：

> 没 Case ID / 失败 / 429 / submit ambiguous 后，必须查 Dashboard 再决定是否重试。

所以这块必须可靠。

---

## 三、中高优先级

### 7. Case ID 提取增强

当前已有 Case ID 提取和 declined 附近文字判断，但仍可能漏：

- success 页面有提示但不显示 Case ID；
- submit 后页面跳转慢；
- `/sq/approvalrequest` 提交后 Case ID 格式不同；
- Dashboard 有 case，但页面内没提取到；
- 页面里出现旧 declined Case ID，被误识别成新 Case ID。

建议：

- 页面内 Case ID 提取继续保留；
- Dashboard Case ID 作为二次确认；
- Case ID 附近 30-100 字符检查 `declined/rejected/denied`；
- 如果页面 success 但无 Case ID，状态标记为 `submitted_no_case_id_pending_dashboard`，不要简单 failed。

---

### 8. 真实交互层继续收口

刚刚已经改了：

- `fill_katal_input()`
- `click_katal_button()`

但项目里还有一些地方可能仍大量使用：

- `page.evaluate(... el.click())`
- 直接 `el.value = ...`
- 直接设置 `checked = true`
- 强制 `display = block`
- 强制隐藏 panel

后续可以统一工具层：

- `human_click()`
- `human_type()`
- `safe_katal_fill()`
- `safe_checkbox_click()`
- `safe_panel_close()`

目标：主流程不再到处散落 JS 操作。

---

### 9. 账号自动补全体检命令

现在已有：

- AdsPower profile 匹配；
- marketplace_configs 自动生成；
- 邮箱从 profile name/remark 解析；
- 自动回填 email。

建议新增账号体检命令：

```bash
python scripts/audit_account_config.py us_store_590 --site US
```

输出：

- AdsPower profile 是否存在；
- profile name / remark / email；
- accounts.json email 是否为空；
- marketplace_configs 是否完整；
- 当前 site entry_url 是否正确；
- brand pack 文案是否存在；
- 图片是否齐；
- Seller Central 是否登录 / 有权限。

跑前先 audit，能减少很多中途失败。

---

## 四、中优先级

### 10. 旧脚本能力系统性合并到主流程

项目里仍有一些旧脚本 / scratch 脚本有价值能力，例如：

- `submit_5461.py` 里的旧邮箱获取；
- `_submit_mp_mall_draft.py` 的 Draft 续接经验；
- `check_case_dashboard_v2.py` / `check_case_dashboard_v3.py` 的 Dashboard 探测经验；
- `_scratch/diagnose_*` 的新 UI 诊断经验。

需要整理：

- 有价值的函数迁进 `src/`；
- scratch 只保留实验脚本；
- 主流程只依赖 `src/` 里的稳定能力；
- 避免同一件事有 3 套实现。

---

### 11. Evidence / ledger / batch_state 同步统一

现在状态散在：

- `data/batch_state_*.json`
- SQLite / Excel
- evidence 截图和日志
- `memory/status/amazon-5461-ledger.md`
- Dashboard result json

建议固定状态写入顺序：

1. 页面/提交结果；
2. Case ID / Dashboard 状态；
3. SQLite / Excel；
4. batch_state；
5. evidence summary；
6. ledger 摘要。

并明确优先级：

- Dashboard 当天实测 > batch_state 初始返回；
- Case ID 明确 > 页面按钮消失；
- Draft ≠ failed；
- no Case ID ≠ success。

---

### 12. BE / EU 站点单独 SOP

BE / EU 和 US 不完全一样：

- BE 站点可能没有 email 输入框；
- EU 站点使用 `sellercentral.amazon.co.uk` 作为入口；
- marketplace switch / `mons_sel_mkid` 更容易出问题；
- Case ID 有时不直接显示；
- fonts loading / screenshot timeout 更常见；
- submit 按钮 disabled 后有时需要特殊处理。

建议单独整理：

```text
docs/eu-be-5461-sop.md  # TODO: 当前尚未创建；若仍需要，应新建，否则从路线图中移除。
```

包括：

- BE/NL/SE/DE/FR/ES/IT marketplace config；
- email 处理规则；
- Dashboard URL；
- 常见失败和恢复；
- 哪些情况允许重试，哪些必须停。

---

## 五、低优先级但值得做

### 13. 清理 scratch / batch_state / evidence 索引

现在项目里有很多：

- `_scratch/diagnose_*`
- `data/batch_state_*.json`
- `check_case_dashboard_v2.py`
- `check_case_dashboard_v3.py`
- 临时 retry 脚本

建议不要直接删除，而是归档：

```text
_archive/experiments/2026-05/
_archive/batch_states/2026-05/
```

保留最新稳定脚本，减少误用。

---

### 14. 文档和 skill 更新

刚刚新增/改动了：

- email resolver
- Dashboard resilient navigation
- ItemType 判断
- 真实交互输入层

需要同步更新：

- `skills/amazon-5461-automation/SKILL.md`
- `memory/capabilities.md`
- `memory/troubleshooting.md`
- `memory/checklist.md`

否则未来可能按旧规则操作。

---

### 15. 加最小自动化测试

目前很多修复靠真实 Amazon 验证，成本高且容易触发 429。

建议加离线测试：

- email resolver 单测；
- Dashboard text analyzer 单测；
- Case ID 提取单测；
- ItemType URL 判断单测；
- No Product ID state parser 单测；
- monitor 429 分类单测。

目标：每次改代码至少先跑：

```bash
python -m pytest tests/unit
```

---

## 六、建议下一步顺序

如果继续改造，推荐顺序：

1. 给刚才四项改造做 no-submit 诊断脚本验证；
2. 实现 Draft 续接产品化；
3. 强化 429 / 失败分类；
4. 完善 Dashboard / Selling Applications 真实复核；
5. 收口 declined-old-application 流程；
6. 整理旧脚本能力进 `src/`；
7. 做账号 audit 脚本；
8. 补测试和文档。

---

## 七、核心判断

主流程已经能跑，但现在项目的主要风险是异常状态：

- 半加载；
- Draft；
- 无 Case ID；
- Dashboard 空白；
- declined 旧申请；
- 429 / CloudFront chunk failed；
- StateLoop 误判成功。

后续重点应从“继续堆填表能力”转向：

> **异常状态判断、Dashboard 复核、Draft 续接、失败分类、避免重复提交。**

---

## 2026-06-01 文档复核补充

当前项目对照后，以下条目已部分落地或需要按现状修正：

- `src/email_resolver.py`、`src/draft_resume.py`、`src/failure_classifier.py`、`src/human_interaction.py`、`src/status_sync.py`、`src/case_dashboard_checker.py` 均已存在。
- `scripts/run_full_5461_batch.py` 已有 `--use-state-loop`，默认仍关闭；真实执行默认启用旁路 monitor，可用 `--disable-monitor` 关闭。
- `src/flow_submit_5461.py` 已在不确定/失败/成功但无 Case ID/429/410001/半加载等情况下集成 Dashboard 复核逻辑，并按 Dashboard 状态覆盖为 success/draft/declined/not_found。
- 旧 `verify_brand.py`、`scripts/scan_adspower_accounts.py`、`scripts/auto_discover_accounts.py`、`scripts/smoke_verify_add_product.py`、`scripts/run_verify_add_product.py` 当前不在项目树；相关文档应标记为历史入口或改用 `scripts/diagnose_no_submit_add_product.py` / `scripts/audit_account_config.py`。
- `docs/eu-be-5461-sop.md` 当前不存在；若仍需要，应新建，否则从路线图中移除。
- `skills/amazon-5461-automation/references/ai-decision.md`、`auto-fix.md`、`page-adapter.md` 引用的 `src/ai_analyzer.py`、`src/auto_fix_engine.py`、`src/page_adapter.py` 等当前不在项目树，应标记为历史设计资料。
