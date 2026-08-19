# 可调整参数与前端配置参考

本文档只列公开的配置形状和示例默认值。真实账号、品牌、Case、AdsPower profile、路径、证书和密钥不得进入文档或 Web API。

## 生效规则

| 标记 | 行为 |
|---|---|
| 立即生效 | 保存后由 Web 请求立即使用，不重启服务。 |
| 新启动任务生效 | 新启动的自动化子进程读取新值；运行中的任务不变。尚未启动且没有任务级覆盖的排队任务使用启动时的最新全局值。 |
| 重启服务生效 | 已启动的 Case、恢复、重申、认证或 Codex 后台循环继续使用旧值，相关服务重启后读取新值。 |

已有 Case 或恢复任务的 `scheduled_at` 不会因配置修改而重算。Web 页面不提供自动重启服务。

## Web 可编辑白名单

以下字段由 `/api/settings/effective` 脱敏返回，只有管理员可通过 `PATCH /api/settings` 修改。

### 浏览器与 AdsPower

| YAML 键 | 示例默认值 | 单位 | 范围 | 生效 |
|---|---:|---|---|---|
| `adspower.start_profile_timeout_sec` | 60 | 秒 | 10–300 | 新启动任务 |
| `browser.action_timeout_ms` | 20000 | 毫秒 | 1000–120000 | 新启动任务 |
| `browser.page_timeout_ms` | 45000 | 毫秒 | 5000–300000 | 新启动任务 |
| `browser.cdp_connect_timeout_ms` | 30000 | 毫秒 | 5000–120000 | 新启动任务 |
| `browser.cdp_health_timeout_sec` | 5 | 秒 | 1–30 | 新启动任务 |
| `browser.cdp_ready_timeout_sec` | 30 | 秒 | 5–180 | 新启动任务 |
| `browser.cdp_restart_attempts` | 1 | 次 | 0–3 | 新启动任务 |
| `browser.profile_restart_wait_sec` | 5 | 秒 | 0–60 | 新启动任务 |

### Case 跟进

| YAML 键 | 示例默认值 | 单位 | 范围 | 生效 |
|---|---:|---|---|---|
| `case_followup.delay_hours` | 2.0 | 小时 | 0.1–168 | 新启动任务 |
| `case_followup.retry_interval_hours` | 1.0 | 小时 | 0.1–168 | 重启服务 |
| `case_followup.error_retry_interval_hours` | 1.0 | 小时 | 0.1–168 | 重启服务 |
| `case_followup.max_attempts` | 6 | 次 | 1–50 | 重启服务 |
| `case_followup.poll_interval_seconds` | 60 | 秒 | 10–3600 | 重启服务 |
| `case_followup.claim_group_limit` | 20 | 条 | 1–200 | 重启服务 |
| `case_followup.max_parallel_profiles` | 3 | 个 | 1–10，且不得大于领取上限 | 重启服务 |
| `case_followup.parallel_backlog_threshold` | 8 | 条 | 1–200 | 重启服务 |
| `case_followup.ai_reply_classification.auto_apply_min_confidence` | 0.85 | — | 0–1 | 重启服务 |
| `case_followup.ai_reply_classification.timeout_sec` | 300 | 秒 | 10–1800 | 重启服务 |
| `case_followup.ai_reply_classification.max_attempts` | 2 | 次 | 1–5 | 重启服务 |
| `case_followup.ai_reply_classification.retry_interval_minutes` | 5 | 分钟 | 1–1440 | 重启服务 |
| `case_followup.ai_reply_classification.forbidden_retry_interval_minutes` | 30 | 分钟 | 1–10080 | 重启服务 |

### Case ID 恢复与重新申请

| YAML 键 | 示例默认值 | 单位 | 范围 | 生效 |
|---|---:|---|---|---|
| `case_id_recovery.initial_delay_minutes` | 10 | 分钟 | 0–1440 | 重启服务 |
| `case_id_recovery.retry_interval_minutes` | 30 | 分钟 | 1–10080 | 重启服务 |
| `case_id_recovery.max_attempts` | 12 | 次 | 1–50 | 重启服务 |
| `case_id_recovery.poll_interval_seconds` | 60 | 秒 | 10–3600 | 重启服务 |
| `reapplication.decline_delay_hours` | 2.0 | 小时 | 0.1–168 | 重启服务 |
| `reapplication.poll_interval_seconds` | 60 | 秒 | 10–3600 | 重启服务 |
| `reapplication.busy_retry_minutes` | 10 | 分钟 | 1–1440 | 重启服务 |
| `reapplication.auto_backfill_limit` | 100 | 条 | 1–1000 | 重启服务 |

### 认证、Web 与 Codex

| YAML 键 | 示例默认值 | 单位 | 范围 | 生效 |
|---|---:|---|---|---|
| `web.session_ttl_hours` | 12.0 | 小时 | 0.5–168 | 立即 |
| `web.submit_max_brands` | 5 | 个 | 1–20 | 立即 |
| `auth_recovery.poll_interval_seconds` | 300 | 秒 | 30–86400 | 重启服务 |
| `codex.timeout_sec` | 300 | 秒 | 30–3600 | 重启服务 |
| `codex.auto_triage_min_confidence` | 0.60 | — | 0–1 | 重启服务 |
| `codex.daily_call_limit` | 50 | 次/日 | 0–1000 | 重启服务 |
| `codex.auto_triage_poll_seconds` | 15 | 秒 | 5–3600 | 重启服务 |
| `codex.patch_timeout_sec` | 600 | 秒 | 30–7200 | 重启服务 |
| `codex.worktree_retention_days` | 14 | 天 | 1–365 | 重启服务 |
| `codex.validation_timeout_sec` | 900 | 秒 | 30–7200 | 重启服务 |
| `codex.workflow_poll_seconds` | 2 | 秒 | 1–300 | 重启服务 |

## 任务级覆盖

只有真实提交任务接受以下覆盖，diagnose 和 dry-run 会拒绝这些字段。覆盖值保存在任务 `options_json` 中，任务创建后不可修改。

| API 字段 | 范围 | CLI 映射 |
|---|---|---|
| `options.case_followup_delay_hours` | 0.1–168 小时 | `--case-followup-delay-hours` |
| `options.case_followup_enabled=false` | 布尔值 | `--disable-case-followup` |

省略字段表示使用任务启动时的全局设置。真实提交仍需 reviewer 以上权限、服务端预检和最终风险确认。

## 受保护的只读设置

以下非敏感状态可在设置页查看，但 Web API 拒绝修改：

- 提交安全：`web.submit_enabled`。
- 后台工作流：`case_followup.enabled`、`case_id_recovery.enabled`、`reapplication.enabled`。
- 授权延续：`reapplication.auto_authorize_declined_cases`、`reapplication.auto_backfill_declined_cases`。
- 外部写入：`feishu_bitable.enabled`、`feishu_bitable.create_missing_records`、`feishu_bitable.write_enabled`。
- 自动修复与发布：`codex.enabled`、`codex.workflow_enabled`、`codex.release_enabled`。

Amazon 限流退避、CAPTCHA/2FA、提交点击围栏、人工确认和无限重试保护不允许通过前端弱化。

## YAML 中其他可配置但不在前端开放的值

| 类别 | 字段 | 维护方式 |
|---|---|---|
| AdsPower 连接 | `adspower.backend`、`api_base_url`、`api_key`、`cli_command` | 本机文件或环境变量；含连接信息，不通过 API 返回。 |
| 路径 | `paths.*`、Case 登记表、Codex 日志/状态/worktree 路径 | 本机文件；修改前检查目录和权限。 |
| Web 服务 | `web.host`、`port`、证书路径、CIDR 备注 | 本机文件并重启服务；网络限制以系统防火墙为准。 |
| 业务路线 | `reapplication.routes`、回填水位线 | 人工审核后在本机文件维护。 |
| 飞书映射 | 字段名、国家映射、账号后缀、进度目标 | 本机文件；凭证只放 `.env`。 |
| 批准验证 | 品牌例外、菜单文本、页面标记、类别关键词、各页面超时 | 本机文件；属于业务判定规则，不在通用设置页修改。 |
| Codex 修复 | command、model、允许修改路径、canary 目标 | 本机文件；涉及执行边界或业务目标。 |

`browser.headless`、`verification.approved_threshold`、`run.*`、`case_followup.healthcheck_interval_minutes` 和 `incidents.*` 阈值当前未被主执行链实际消费，因此设置页标为“当前未接线”且不能编辑。提交确认、验证码/2FA 暂停和自动判因仍分别由显式提交入口、认证守卫及 `codex.auto_triage_min_confidence` 强制执行。

## 环境变量

| 变量 | 用途 | 前端可调 |
|---|---|---|
| `ADSPOWER_API_BASE_URL`、`ADSPOWER_API_KEY` | AdsPower 本地服务覆盖；API key 属于秘密。 | 否 |
| `AMAZON5461_ACCOUNTS_PATH` | 私密账号配置文件位置。 | 否 |
| `AMAZON5461_DEFAULT_DRY_RUN`、`AMAZON5461_REQUIRE_CONFIRM_BEFORE_SUBMIT` | 历史运行策略变量；当前主流程以显式命令和服务端门禁为准。 | 否 |
| `FEISHU_*` | 飞书应用、Bitable 标识、绑定和写入门禁。 | 否 |
| `LLM_*`、`AMAZON5461_LLM_MODEL` | 可选页面分析模型和密钥。 | 否 |
| `WEB_SESSION_SECRET`、`WEB_USER_PASSWORD` | 会话签名和用户管理脚本的秘密输入。 | 否 |
| `AMAZON5461_STOP_FILE`、`AMAZON5461_JOB_ID`、`AMAZON5461_RESUME`、`AMAZON5461_DB_PATH` | Web 调度器注入的内部任务状态变量。 | 否 |

真实值只能保存在 `.env` 或 `runtime/private/`，不得通过设置 API、日志、截图或文档输出。

## CLI 与脚本参数

- 稳定 CLI `python -m cli.amazon5461`：账号、品牌、站点、状态文件、Case ID、worker watch/once、重申区域与起点、显式 `--submit`/`--yes`、Case 跟进覆盖和修复审计参数。
- 主批次脚本：批大小、续跑批次、dry-run、站点、状态文件、监控开关、StateLoop、Case 跟进覆盖和内部重申关联 ID。
- Case/恢复/重申 worker：once/watch、领取上限、并发 profile 数和是否登记结果；生产轮询优先使用 YAML 白名单字段。
- 诊断与采集脚本：CDP 地址、证据输出目录、加载等待、采集时长、DOM/截图频率和是否采集网络信息。
- 维护脚本：账号扫描页数、调用超时、回填是否写入、worktree 清理 dry-run、证书有效期和用户角色。

CLI 的账号、品牌、Case、路径和真实提交参数是一次性操作输入，不属于通用前端设置。任何 `--submit`、`--yes`、`--write`、`--apply` 或类似写操作仍需单独明确授权。

## 代码内常量

以下值仍由代码维护，不开放给运行人员：

- Web 任务调度 tick、SSE/详情页轮询、profile 锁 TTL 和登录限流退避。
- StateLoop 最大步骤数、410001/429/空白页/服务器错误的有界重试和冷却。
- 表单半加载恢复次数、页面稳定等待和选择器探测短等待。
- 修复流水线的模式校验、补丁 schema 重试和发布互斥锁。

这些值关系到平台限流、安全边界或程序内部一致性。若需要调整，应作为代码变更同步增加测试，而不是直接加入设置页。

## 持久化与审计

- 设置保存到 Git 忽略的 `config/settings.yaml`，仅更新白名单叶子字段。
- 写入使用同目录临时文件、`fsync` 和原子替换；旧文件备份到 `runtime/private/settings-backups/`。
- API 使用配置文件内容哈希作为版本号；旧版本保存返回 `409 settings_revision_conflict`。
- 审计只记录白名单字段的旧值和新值。路径、密钥和未开放 YAML 内容永不进入响应或审计详情。
