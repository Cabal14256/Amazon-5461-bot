# 前端 UI 交付记录 — Amazon 5461 内网控制台（2026-08-10）

依据 `docs/plans/plan-internal-console-codex-repair-2026-08-04.md` 的页面规划与状态模型，新增 `frontend/` 纯前端工程。本阶段**只做 UI + Mock 数据**：不触碰 Python 代码、不接真实后端、不启动任何自动化任务。

## 技术栈

- Vue 3 + TypeScript + Vite 5
- Naive UI（浅色主主题 + 深色一键切换，`localStorage` 持久化，见 `src/stores/theme.ts`）
- Vue Router 4 + Pinia（theme / overview / jobs / incidents 四个 store）
- ECharts（vue-echarts + 按需引入，仅总览页 donut 图）

## 目录

```
frontend/
  index.html  package.json  vite.config.ts  tsconfig.json
  src/
    main.ts  App.vue  styles.css  env.d.ts
    router/index.ts                 # 7 条路由 + SPA fallback
    stores/                         # theme / overview / jobs / incidents
    api/                            # client.ts（预留 /api baseURL）+ index.ts + mock/
    types/index.ts                  # JobRunStatus(9) / BusinessStatus(10) / IncidentStatus(13) 等
    theme/                          # statusColors.ts（三套状态色）+ themeOverrides
    components/                     # StatusTag HealthBadge RelativeTime LogViewer
                                    # EvidenceTimeline DiffViewer ConfirmSubmitDialog
    pages/                          # OverviewPage NewJobWizard JobListPage JobDetailPage
                                    # ApplicationsPage PendingPage RepairCenterPage
```

## 关键约束（与计划一致）

- 任务运行态（9）与业务结果态（10）始终**两个独立 tag / 两列**展示，代码中由 `StatusTag kind="run|business"` 分开渲染。
- incident 状态机 13 态在 `src/theme/statusColors.ts` 全部有中文文案与配色。
- Mock 账号一律脱敏（`ACC-****1234` 等），无任何真实邮箱/密码。
- 真实提交入口：红色系卡片 + `ConfirmSubmitDialog` 二次确认（确认按钮 3 秒倒计时），当前仅 Mock 交互，不可能触发真实提交。
- `vite.config.ts` 预留 `/api` proxy → `http://127.0.0.1:8080`；接入真实后端时只需替换 `src/api/client.ts` 的 `resolve()` 为 fetch。

## 验证

- `cd frontend && npm run build`：通过（vite 5，零错误）。
- `npx vue-tsc --noEmit`：通过（strict + noUnusedLocals/Parameters）。
- `vite preview` 冒烟：`/`、`/jobs`、`/jobs/new`、`/jobs/:id`、`/applications`、`/pending`、`/repair` 全部 200。
- 根 `.gitignore` 已追加 `frontend/node_modules/`、`frontend/dist/`，并对既有 `*.html` 规则补充 `!frontend/index.html` 例外。

## 遗留事项

- 未做浏览器端逐页面截图人工核对（本环境无浏览器自动化接入）；建议首次 `npm run dev` 后人工过一遍浅色/深色切换与各页面交互。
- npm 环境存在 allow-scripts 策略，esbuild / vue-demi 的 postinstall 被跳过；esbuild 走平台二进制包（`@esbuild/win32-x64`），实测 build 不受影响。
- 修复中心的角色切换（Reviewer/Admin）为 Mock 演示，真实权限必须由未来后端强制。
