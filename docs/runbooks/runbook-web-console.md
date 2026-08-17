# Runbook — Web 控制台（阶段 2 只读 + 阶段 3 任务队列 + 阶段 4 真实提交审批）

控制台是本地 FastAPI 应用：健康检查、目录、任务、申请、Case 跟进、
Case ID 找回、重新申请、证据查看。阶段 3 起新增**白名单任务队列**
（diagnose / dry-run，见第 8 节）；阶段 4 起新增**真实提交**
（单一 `/api/jobs/submit` 接口，reviewer 及以上权限，默认开启，见第 9 节）。

## 1. 启动

```powershell
.\.venv\Scripts\python.exe scripts\run_web_console.py
# 默认 http://127.0.0.1:8080 （读 config/settings.yaml 的 web: 段）
```

可选参数：`--host`、`--port`、`--ssl-cert`、`--ssl-key`（覆盖 settings.yaml）。

Windows 上浏览器主动关闭 HTTPS 连接时，Proactor 事件循环可能产生
`_ProactorBasePipeTransport._call_connection_lost` / `WinError 10054`。控制台只过滤
这个连接清理回调中的预期断连错误，其他 `asyncio` 异常仍正常记录。升级代码后若仍
看到旧 traceback，先重启控制台；历史日志不会被重写。

首次启动前必须创建用户（见第 4 节）。未配置 `WEB_SESSION_SECRET` 时使用
临时密钥，重启后所有会话失效；生产部署请在 `.env` 中设置：

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"
# 把输出写入 .env: WEB_SESSION_SECRET=<64位hex>
```

## 2. 局域网开放 + HTTPS

1. 生成自签证书（本地专用，输出到 `runtime/private/certs/`，不进 Git）：

   ```powershell
   .\.venv\Scripts\python.exe scripts\make_dev_cert.py --common-name <本机主机名或内网IP>
   ```

2. 在 `config/settings.yaml` 的 `web:` 段填写：

   ```yaml
   web:
     host: "192.168.x.x"          # 本机内网 IP
     ssl_cert_path: "runtime/private/certs/web-console.crt"
     ssl_key_path: "runtime/private/certs/web-console.key"
   ```

3. 重启控制台，访问 `https://192.168.x.x:8080`。客户端首次访问需手动信任
   自签证书。证书到期前重新运行 `make_dev_cert.py` 并重启即可更换。

## 3. 防火墙网段限制（系统层，管理员 PowerShell）

只允许内网网段访问 8080 端口：

```powershell
New-NetFirewallRule -DisplayName "Amazon5461-WebConsole-LAN" -Direction Inbound `
  -Protocol TCP -LocalPort 8080 -Action Allow -RemoteAddress 192.168.0.0/16,10.0.0.0/8,172.16.0.0/12
```

删除规则：`Remove-NetFirewallRule -DisplayName "Amazon5461-WebConsole-LAN"`。
等效 netsh 命令：

```powershell
netsh advfirewall firewall add rule name="Amazon5461-WebConsole-LAN" dir=in action=allow protocol=TCP localport=8080 remoteip=192.168.0.0/16,10.0.0.0/8,172.16.0.0/12
netsh advfirewall firewall delete rule name="Amazon5461-WebConsole-LAN"
```

## 4. 用户管理

```powershell
# 创建首个 Admin（密码交互输入，不进入命令行/日志）
.\.venv\Scripts\python.exe scripts\manage_web_users.py add --username admin --role admin
# 普通只读用户
.\.venv\Scripts\python.exe scripts\manage_web_users.py add --username viewer1 --role viewer
# 列表 / 禁用
.\.venv\Scripts\python.exe scripts\manage_web_users.py list
.\.venv\Scripts\python.exe scripts\manage_web_users.py disable --username viewer1
```

角色：`viewer`（全部只读端点）< `operator` < `reviewer` < `admin`
（额外可访问 `GET /api/users`）。密码只存储 PBKDF2-HMAC-SHA256 哈希
（60 万轮）；登录失败限次并指数退避。

## 5. 开机自启（任务计划）

仓库提供 `scripts/start_web_console_hidden.ps1`：它会避免重复启动，隐藏
Python 控制台窗口，并把 stdout/stderr 分别写入带时间戳的
`runtime/logs/web_console_*.out.log` / `.err.log`。

```powershell
$projectRoot = "C:\Users\Admin\Documents\Amazon-5461-bot"
$launcher = Join-Path $projectRoot "scripts\start_web_console_hidden.ps1"
$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$arguments = "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$launcher`" -WebHost 0.0.0.0 -WebPort 8080"
$action = New-ScheduledTaskAction -Execute $powershell -Argument $arguments -WorkingDirectory $projectRoot
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
$settings = New-ScheduledTaskSettingsSet -Hidden -StartWhenAvailable `
  -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
  -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName "Amazon5461-WebConsole" -Action $action `
  -Trigger $trigger -Settings $settings -Principal $principal -Force
```

注册后可在 Windows“任务计划程序”中查看 `Amazon5461-WebConsole`。
任务只负责启动控制台，不会自动创建或执行 Amazon 提交任务。
`0.0.0.0` 保留局域网访问；仅需本机访问时可改为 `127.0.0.1`。

不要再从 VS Code 的持久终端长期运行 `run_web_console.py`，否则 VS Code
重启时会复活旧终端并启动第二套服务。仓库工作区已设置
`terminal.integrated.enablePersistentSessions: false`，避免该重复入口。

## 6. 备份注意（WAL）

SQLite 已启用 WAL 模式：`runtime/state/ledger.db` 会伴随
`ledger.db-wal` / `ledger.db-shm`。备份时必须**三个文件一起拷贝**
（或先执行 `PRAGMA wal_checkpoint(TRUNCATE);` 再拷主库），否则最近的写入
可能丢失。控制台与批量 worker 可并发读写同一库（busy_timeout=5000）。

## 7. 安全边界提醒

- 控制台不调用 Codex；任务队列只能经白名单创建 diagnose / dry-run
  （会启动 AdsPower 浏览器走流程，但 dry-run 不点击最终提交）。
  真实提交（submit）另受总开关与 reviewer 权限约束，见第 9 节。
- 账号目录 API 不返回 username（登录邮箱）、adspower_profile_id、entry_url。
- 证据文件只允许 `runtime/evidence`、`runtime/logs` 根目录内的路径；
  文本内容经 `src/capture/redact.py` 二次脱敏；下载写入 `web_audit_events`。
- 登录/登出/用户管理/证据下载记录于 `web_audit_events`（不含任何秘密）。

## 8. 任务队列（阶段 3）

白名单任务：`diagnose` / `dry_run` / `submit` 三类。`submit` 走第 9 节的
两阶段审批，且受总开关 `web.submit_enabled` 控制（默认开启；显式关闭时
行为与阶段 3 完全一致）。

### 8.1 创建

- 前端「新建任务」向导，或 API：
  `POST /api/jobs/diagnose` / `POST /api/jobs/dry-run`（operator+），
  body `{account, brands[], site?}`。
- 服务端白名单校验：account 必须在 catalog 且 active；brand 必须存在
  `brand_packs/<name>/` 目录；site 必须在 `config/marketplaces/*.yaml`；
  品牌数上限 20。任一不满足返回 422，并写 `web_audit_events`（result=rejected）。
- 创建即入库 `automation_jobs`（run_status=queued），返回 job id
  （`job-YYYYMMDD-<8位hex>`，不可预测）。

### 8.2 串行调度与 profile 锁

- 调度循环是 FastAPI 启动时注册的 asyncio task，每 2s 一个 tick，
  **全局串行**：同一时刻只 dispatch 一个 queued job（跨所有 profile）。
- dispatch 前两项检查：(a) `profile_locks` 锁可获取（锁 key 是
  adspower_profile_id 的 SHA-256 前 16 位，不明文落库；运行中由 dispatcher
  心跳续租，过期锁先验证 PID 不存在再回收）；(b) 同账号无进行中的
  case_followup。冲突则留在队列，下轮再试。留在队列期间，任务读取接口
  （列表/详情/SSE）会带回 `queue_reason` 注解：`waiting_case_followup`
  （等该账号 Case 跟进完成）、`waiting_profile_lock`（等 profile 释放）或
  `waiting_serial_queue`（等其他任务完成），前端按中文说明显示。
- 子进程隐藏启动（CREATE_NO_WINDOW | DETACHED_PROCESS），stdout/stderr 落
  `runtime/logs/jobs/<job_id>/`，状态文件落 `runtime/state/jobs/<job_id>/`。

### 8.3 停止语义

- **请求安全停止**（operator+，`POST /api/jobs/{id}/request-stop`）：只写
  DB `stop_requested_at` + 落 `STOP_REQUESTED` 哨兵文件，**不强杀**。
  批次 worker 在当前品牌完成后看到哨兵，把剩余品牌标记 skipped、正常收尾
  退出；界面文案「已请求停止，将在当前品牌完成后安全停下」。diagnose
  耗时短，不接停止。
- **强制终止**（admin，`POST /api/jobs/{id}/force-terminate`）：立即
  terminate 子进程，任务记 `terminated_unknown_state` + 审计。此后本地
  状态与亚马逊侧可能不一致，**必须先人工查 Dashboard 再决定下一步**。

### 8.4 Web 重启恢复

启动时 reconcile DB 与实际进程存活：

- `starting`/`running` 但 PID 已死 → `terminated_unknown_state`
  （**不自动标记失败**，提示先查 Dashboard）；
- `queued` → 继续排队等待 dispatch；
- `stop_requested` 且进程已死 → failed(cancelled) 语义收尾。

### 8.5 SSE 实时事件

`GET /api/jobs/{id}/events`（需登录，Cookie 会话）：

- `event: job` — 任务行有变化时推送完整 AutomationJobOut；
- `event: log` — 脱敏 stdout 增量行，事件 id 即 1-based 行号，
  断线重连用 `Last-Event-ID` 续传；每连接最多 200 行；
- 任务进入终态服务端自动关流。前端断线后降级为 5s 轮询并在日志提示。
- 历史日志尾部：`GET /api/jobs/{id}/logs?stream=stdout&lines=200`（脱敏）。

## 9. 真实提交（阶段 4）

真实提交走单一显式接口，全程留有审计；不做批量/多账号提交。

### 9.1 配置开关（`config/settings.yaml` 的 `web:` 段）

```yaml
web:
  submit_enabled: true                # 默认开启；false 为紧急/总关闭
  submit_max_brands: 5                # 单次 submit 品牌数上限
```

- `submit_enabled: true` 仍不代表无条件提交：必须经显式
  `POST /api/jobs/submit` 接口、明确账号/站点/品牌、预检和审计流程。
- 显式设置 `submit_enabled: false` 时该端点一律 403（`submit_disabled`），
  行为与阶段 3 完全一致，无任何路径能创建 submit 任务。
- 2026-08-12 起已移除双人审批与一次性 token 确认流程：确认人不限
  发起人，也不再需要 prepare/confirm 两步。权限要求从 operator 提升为
  reviewer+，其余门禁（总开关、preflight、品牌上限、审计）保持不变。

### 9.2 单步流程

`POST /api/jobs/submit`（reviewer+，body 同第 8 节
`{account, brands, site?}`）：白名单校验（账号/站点/品牌，同第 8 节，
品牌上限改用 `submit_max_brands`）→ 同步 preflight → 直接创建
`submit` 任务（`run_status=queued`）→ 进入第 8 节现有串行队列，执行
命令为 `python -m cli.amazon5461 run --submit ...`。响应只含
`{job, preflight}`。

### 9.3 preflight 语义

提交时同步执行（不开浏览器，秒级），返回 `{name, ok, level, detail}`
清单：

- **blocker**（任一失败 → 返回 422 + 明细，不建任务，写审计）：
  账号 active 且有 `adspower_profile_id`；站点配置存在；品牌包目录与
  manifest 的 5461 `upload_files` 文件齐全；品牌数 ≤ `submit_max_brands`。
- **warning**（不阻断，随响应返回展示）：AdsPower 本地 API 不可达；
  账号有进行中的 case followup；近 24h 无同账号/站点/品牌的成功
  dry-run 记录。

### 9.4 调度层

`queued` 的 submit 任务与 diagnose / dry-run 共用第 8 节串行队列
（case followup 避让、profile 互斥锁、单任务串行），调度器直接 spawn
`run --submit` 子进程，不再有额外的确认记录检查。

### 9.5 审计与 unknown 终止核查义务

- submit 创建 / 各类拒绝均写 `web_audit_events`（操作者、时间、
  账号/站点/品牌参数明细、任务 ID、IP），与其他 Web 审计同表，不含
  任何秘密。
- submit 任务被强制终止或重启后落 `terminated_unknown_state` 时，本地
  状态与亚马逊侧可能不一致：**必须先到 Seller Central 的 Dashboard /
  View Selling Applications 人工核查是否已提交，再决定是否重跑**，
  防止重复提交。

## 10. 异常检测与 incident 队列（阶段 5）

自动化失败信号持久化为 `repair_incidents` 表（不再依赖单槽位
`runtime/codex_signal.json`；信号文件仍保留并双写，一个版本周期后下线）。

### 10.1 分类与分流

- **人工处理类**（永不进修复候选队列）：`captcha`、`two_fa`、
  `login_expired`、`account_risk`、`rate_limit`（429/410001/CloudFront）、
  `brand_block`、`config_missing`、`amazon_platform_error`、
  `business_uncertain`。
- **修复候选类**（疑似页面改版，进修复中心）：`selector_missing`、
  `state_unknown`、`dom_contract_changed`、`navigation_changed`、
  `semantic_control_missing`、`flow_loop_exhausted`、
  `result_contract_changed`。
- 排除类优先于修复候选类（同一失败同时命中 429 与选择器缺失时归
  `rate_limit`）。`business_uncertain` 在 dry-run 下不记录（降噪）。

### 10.2 聚合与置信度

- signature 由 flow/页面族/归一化 URL/缺失 landmark/组件类型/状态机
  原因/归一化错误类构成；账号、品牌、Case ID、长数字不进 signature。
- 同一 signature 同范围复发：`occurrence_count` 递增、`last_seen_at`
  更新，不再新建 incident；修复候选类置信度每次 +0.10（封顶 0.85），
  跨账号/站点同 signature 再 +0.15。
- 阈值配置（`config/settings.yaml` 的 `incidents:` 段）：
  `enabled: true`、`manual_review_below: 0.60`、
  `auto_triage_at_or_above: 0.80`（阶段 6 的自动判因消费这两个阈值）。

### 10.3 信号入口

- `src/executor/state_loop.py` 恢复耗尽（stuck_at_*/max_steps_exceeded）
  → `write_pending_signal` 双写 incident。
- `scripts/run_full_5461_batch.py` 每品牌失败收口处旁路记录
  （try/except 包裹，绝不中断批次）。

### 10.4 证据包

每个 incident 生成 `runtime/evidence/incidents/<id>/`：manifest、
page-summary/visible-text/run-context（均二次脱敏，页面文本包裹
UNTRUSTED 标记）、relevant-selectors、screenshot-withheld（阶段 5 默认
不提供截图，只记录原图私有路径）。下载走现有 `/api/evidence/file`
allowlist。

### 10.5 回放验证与关闭

- `python scripts/replay_incidents.py`（默认 dry，只打印）对
  `data/`、`runtime/state/` 历史失败做离线分类回放；`--write` 才写库。
  用于验证"已知 429/CAPTCHA/2FA 零误入修复候选"与给 UI 造数据。
- 关闭：`POST /api/incidents/{id}/close`（operator+，必填 note），
  写 `incident_close` 审计；已关闭再关返回 409。前端在"待人工处理"
  分组卡与修复中心详情里操作。

## 11. Stage 8 修复验证与发布

Web 生命周期会启动一个持久 Repair Workflow Runner。它一次只领取一个
`patch_ready` 补丁，验证子进程 PID、开始时间、心跳和八步结果写入 SQLite
与 `runtime/state/repair/<job_id>/validation.json`。服务重启时，PID 仍存活的
验证不重复启动；PID 已失效且没有完成报告的任务才回到 `patch_ready`。

固定八步为：允许路径、diff 私密/安全扫描、`compileall`、全量 pytest、
修改 Python 文件 Ruff、Codex 声明的 contract tests、脱敏 fixture 离线
回放、Codex read-only 结构化 diff review。任一步失败都进入
`validation_failed` 并保留 worktree/日志；只有结构化 `verdict=pass` 才到
`awaiting_validation_approval`。

审批端点及最低角色：

- `POST /api/repair-jobs/{id}/approve-validation`：Reviewer；进入 Canary。
- `POST /api/repair-jobs/{id}/confirm-canary`：Reviewer；`note` 非空且
  `evidence_reviewed=true`。
- `POST /api/repair-jobs/{id}/approve-release`：Admin；必须回传当前 patch
  SHA，且 `codex.release_enabled=true`。
- `POST /api/repair-jobs/{id}/reject`：Reviewer；`note` 必填。
- `POST /api/repair-jobs/{id}/post-release-check`：Admin；只可在显式重启后调用。
- `POST /api/repair-jobs/{id}/rollback`：Admin；`note` 必填。

Canary 先检查目标账号/profile、站点、材料、Case/profile/任务冲突，然后只
按 `diagnose → dry_run` 顺序进入全局串行队列。两项完成后仍停在 `canary`，
必须人工核对截图、日志和零 submit/零业务副作用，再确认进入
`awaiting_release_approval`。

### 11.1 本地发布和显式重启

发布前必须同时满足：总开关已开、生产工作树干净、生产 HEAD 等于补丁
baseline、repair 分支 HEAD 等于请求确认的 patch SHA、状态为
`awaiting_release_approval`。发布仅执行本地 `git merge --no-ff`，不 push、
不建 PR。合并后状态是 `release_pending_restart`，Web 进程绝不自我重启。

重启服务后，用 Admin 调用 `post-release-check`。后端拒绝与合并时相同的
进程 PID，并再次校验 HEAD/工作树和服务调度器健康，再创建一次 post-release
`diagnose`。通过才标记 `released`；失败为 `release_check_failed`。

### 11.2 安全回滚

回滚要求工作树干净且 HEAD 精确等于记录的 release SHA。系统只创建
`git revert` 提交（merge commit 使用 mainline 1），记录 rollback SHA；绝不
使用 `reset --hard`。工作树脏、HEAD 漂移、发布后 Canary 仍运行或无法安全
revert 时返回 409 并停止。真实补丁 UAT 应保持 `release_enabled=false`，安全
终点是 `awaiting_release_approval`。
