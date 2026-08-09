# Batch Result Reporting Format

## User-facing report convention

When presenting batch results to the user, use a **markdown table** with
these exact columns (in Chinese):

| 站点 | 品牌 | Case ID | 提交时间 | 备注 |
|:---:|:---|:---:|:---|:---|

**Timestamp format**: `年/月/日 时:分` (e.g. `2026/07/12 14:16`).
Use the log's `[HH:MM:SS] 开始:` timestamp, converted to the full
`年/月/日 时:分` format. Do not use evidence file mtime when the
same account ran multiple sites on the same day (evidence folders collide).

**Example report:**

| 站点 | 品牌 | Case ID | 提交时间 | 备注 |
|:---:|:---|:---:|:---|:---|
| US | WILLONE | <CASE_ID_REDACTED> | 2026/07/12 11:03 | 成功 |
| US | JZG | <CASE_ID_REDACTED> | 2026/07/12 11:07 | 成功 |
| MX | V-PORYADKU | <CASE_ID_REDACTED> | 2026/07/12 11:31 | 成功 |

## What to include in the 备注 column

- `成功` — normal Case ID extracted.
- `失败 — <reason>` — only when vision analysis or log confirms genuine
  failure (e.g. "5461 panel never triggered", "Apply to sell popup loading
  timeout").
- `Under review` — when vision analysis of a "failed" brand shows the popup
  already displays **Under review** (script false-negative).
- `已有权限` — if the page shows the brand is already approved.

## Report structure

1. **Header line**: account ID, site(s), execution date/time range.
2. **Summary counts**: total, success count, failure count.
3. **Table**: one row per brand, grouped by site (US first, then MX/UK/BE/etc).
4. **Key notes section**: call out any retries, false-negatives verified by
   vision analysis, 429 occurrences, or brands left for manual review.
5. **Evidence path**: `runtime/evidence/<date>/<account>/`
6. **State file path**: `data/batch_<account>_<site>_<date>.json`

## Timestamp source priority

1. **Log start timestamp** (`[HH:MM:SS] 开始: us_store_NNN / BRAND`) — most
   reliable, always present, unaffected by evidence-folder collisions.
2. **Log Case ID line** — has no timestamp; use the nearest preceding start
   timestamp.
3. **Evidence screenshot mtime** — only use when no log is available (rare).
   Beware same-day multi-site collisions.

## Cross-session consistency

The user has repeatedly received reports in this format and expects it for
all future batch completions. Do not deviate to bullet lists or plain text
unless explicitly requested.
