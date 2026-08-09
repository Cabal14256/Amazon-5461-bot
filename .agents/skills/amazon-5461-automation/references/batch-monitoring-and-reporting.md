# Monitoring a Running Batch & Extracting Report Timestamps

## Scheduling a batch as a one-time cron job (confirmed 2026-07-17)

When the user asks to delay a batch until a specific time (e.g. "19:30 开始执行"), use `cronjob(action='create')` with these fields set **in the same create call** — do not rely on a follow-up update:

- `schedule`: ISO timestamp, e.g. `2026-07-17T19:30:00`
- `attach_to_session=True` — keeps the job continuable and delivers results back to this chat
- `deliver='origin'` — **required in desktop/GUI sessions**. The default `deliver='local'` silently saves output but never routes it back to the conversation. Forgetting this means no notification and you must manually poll via `cronjob(action='list')`.
- `enabled_toolsets=['terminal', 'web', 'file']` — minimises token overhead; batch jobs don't need browser or delegation tools
- `skills=['amazon-5461-automation']` — loads the skill into the scheduled agent's context

```python
cronjob(
    action='create',
    name='648EU UK 批次 — 7品牌',
    schedule='2026-07-17T19:30:00',
    attach_to_session=True,
    deliver='origin',
    skills=['amazon-5461-automation'],
    enabled_toolsets=['terminal', 'web', 'file'],
    prompt='...'  # self-contained — see below
)
```

**The cron prompt must be fully self-contained.** The scheduled agent has zero memory of the current conversation. Include:
- Account ID + AdsPower profile ID
- Full brand list and site
- Onboarding steps (in case the account isn't yet in accounts.json)
- Log path and state-file path (use date-stamped names)
- Reporting format reminder (table, year/month/day hh:mm timestamps)
- Retry rule (auto-retry once on failure before reporting)

---

## Script path and key flags (confirmed 2026-07-08, flag corrected 2026-07-17)

`run_full_5461_batch.py` lives under **`scripts/`**, NOT the project root.

Key flags:
- `--accounts` (plural) — account ID, e.g. `us_store_647` or bare number `647`
- `--brands` — comma-separated brand list, e.g. `JZG,WILLONE,uShield,JavoYion`
- `--site` — marketplace code, e.g. `BE`, `UK`, `US`, `MX`
- `--dry-run` — simulate without submitting (omit for real submission — submission is the DEFAULT)
- `--no-confirm` — skip interactive confirmations (useful for manual foreground runs)
- `--state-file` — optional; use for retries to avoid overwriting prior state

⚠️ **`--submit` does NOT exist on `run_full_5461_batch.py`** — it is only valid for the single-brand
`cli/amazon5461.py run --submit`. Passing `--submit` to the batch script causes immediate exit_code 2
(`unrecognized arguments: --submit`). The batch script submits by default; use `--dry-run` to opt out.

Canonical invocation (already-onboarded account, background run):
```bash
cd projects/amazon-5461-bot
python scripts/run_full_5461_batch.py \
  --accounts us_store_647 \
  --brands JZG,WILLONE,uShield,JavoYion \
  --site BE
```
No `--yes` or `--no-confirm` required for background runs — stdin EOF triggers non-interactive auto-skip automatically.

Calling `python.exe run_full_5461_batch.py` from the project root (without `scripts/`) fails
immediately with `No such file or directory` (exit_code 2). The log will
contain only the Python error and nothing else — easy to mistake for a
logging problem rather than a wrong path.

---

Applies when a `run_full_5461_batch.py` run is launched in the background
(`background=true`, `notify_on_complete=true`, output redirected to
`runtime/logs/<name>.log`) and you need to (a) track live progress and
(b) produce a final report with per-brand Case ID timestamps.

## Known noise lines — do NOT treat as failures

These lines appear consistently in clean, fully-successful runs. Skip past
them when grepping for real problems:

```
[警告] 截图失败: str() got an unexpected keyword argument 'timeout'
```
Appears 2–3 times at the start of each brand. The screenshot helper
receives an unexpected kwarg from its caller. Does **not** affect form
filling or submission — evidence screenshots (`submit_full/final_state.png`)
are still captured normally on success.

```
Page.screenshot: Timeout 10000ms exceeded.
Call log:
  - taking page screenshot
  - waiting for fonts to load...
  - fonts loaded
```
Appears occasionally during form fill or just before submit. The screenshot
helper times out waiting for font rendering. Does **not** block submission;
the script continues and can still extract a Case ID. Confirmed harmless
2026-07-12 (WILLONE MX). Do NOT treat as a failure signal.

```
greenlet.error: Cannot switch to a different thread
	Current:  <greenlet.greenlet object at ...>
	Expected: <greenlet.greenlet object at ...>
```
Playwright internal thread-switching noise from `_sync_base.py`. Appears
multiple times per brand in the background. Does **not** indicate a failure.
Judge success or failure **only** by whether `[✓] 提取到 Case ID: <id>`
appears in the log. Confirmed across all accounts; never correlated with
actual submission failures.

```
[FormFiller:new_ui] React onBlur item_name-0-value 失败: Page.evaluate: TypeError: e.querySelector is not a function
[FormFiller:new_ui] React onBlur brand-0-value 失败: Page.evaluate: TypeError: e.querySelector is not a function
```
The React onBlur trigger fails silently; the JS-write path
(`fill_katal_input`) handles the actual value injection. No action needed.

```
[fill_katal_input] JS 写入校验失败: expected=NNN actual=MMM result={'ok': True, ...}
```
The character-count check shows a small mismatch (typically ~10 chars), but\n`result['ok']` is `True` — the write succeeded. The mismatch is caused by\ninvisible whitespace normalisation in the DOM. Confirmed harmless across\naccounts 636 and 640, sites BE and UK (2026-07-11). The `actual` field in\nthe log contains the full form text for manual verification if needed.\n\n```\n[ServerError] 刷新/导航失败: Page.reload: Timeout 30000ms exceeded.\n```\nCan appear right before a brand's `开始:` line. The reload attempt inside\nthe pre-brand page-reset step times out, but the flow proceeds anyway and\nform filling / submission continue normally. Confirmed harmless 2026-07-17\n(account 646 US, uShield) — brand still produced a valid Case ID\n(<CASE_ID_REDACTED>) despite this line. Do not treat as a failure signal; judge\nsuccess only by `[✓] 提取到 Case ID` as always.\n\nThe recommended grep filter already suppresses these:
```bash
grep -E "^\\[.*\\] 开始:|^\\[✓\\] 提取到 Case ID:|执行摘要|失败:" runtime/logs/<name>.log \
  | grep -v "截图\\|PageCapture\\|JS 写入\\|fill_katal"
```

---

## connect_over_cdp hang — diagnosis tree (2026-07-17)

**Symptom:** The batch script starts, gets a CDP URL from AdsPower, calls
`sync_playwright().start()` and then `p.chromium.connect_over_cdp(ws://...)` — and hangs
indefinitely. Last log line:

```
[DBG] playwright started, calling connect_over_cdp(ws://127.0.0.1:NNNNN/devtools/browser/...)
```

Process stays `running` for 150+ seconds with no further output. There is no timeout
parameter on `connect_over_cdp`, so the process never self-recovers.

**Three distinct root causes — check in this order:**

### Cause 1: Browser has only about:blank tabs

`curl http://127.0.0.1:<PORT>/json/version` succeeds (browser alive), but
`/json/list` shows only `about:blank` + `chrome://...` URLs.

Fix:
1. Kill stuck batch.
2. In AdsPower browser, navigate manually to a real URL — `https://sellercentral.amazon.co.uk/home` (EU) or `https://sellercentral.amazon.com/home` (US/MX).
3. Confirm page loads (not login/redirect).
4. Relaunch batch.

### Cause 2: Zombie CDP connection from a prior killed batch

The CDP port is alive and `/json/list` shows real pages, but a previous batch process
that was killed (SIGKILL / `process(action='kill')`) left an open Playwright WebSocket
connection that still holds the CDP session. New `connect_over_cdp` calls block waiting
for the session to become available.

**Symptoms that distinguish this cause:**
- `curl http://127.0.0.1:<PORT>/json/version` returns valid JSON ✓
- `python -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.connect_over_cdp('ws://...'); print('ok'); p.stop()"` succeeds from a **foreground** terminal ✓
- But the **batch script** run in background still hangs ✗
- The same CDP URL (`browser/xxxxxxxx-...`) has been used by one or more previously-killed batch processes

Fix — stop and restart the AdsPower profile to get a new CDP URL:
```bash
curl -s "http://local.adspower.net:50325/api/v1/browser/stop?user_id=<profile_id>"
sleep 5
curl -s "http://local.adspower.net:50325/api/v1/browser/start?user_id=<profile_id>&open_tabs=1"
# → note the new ws://127.0.0.1:<NEW_PORT>/devtools/browser/<NEW_UUID>
sleep 6
# Verify new CDP works:
python -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.connect_over_cdp('ws://127.0.0.1:<NEW_PORT>/devtools/browser/<NEW_UUID>'); print('ok',len(b.contexts)); p.stop()"
```
Then relaunch the batch. `load_runtime` will call `ensure_profile_started` which returns
the new CDP URL automatically.

**Important:** if you kill multiple batch processes in a row against the same profile,
each kill increases zombie connections. After 3+ kills, even a stop/restart may not
fully clear the state — the browser itself becomes unstable (see Cause 3).

### Cause 3: Browser process corruption after repeated kills

`connect_over_cdp` succeeds (6+ tabs returned in foreground test), but immediately
after, operations like `context.new_page()` fail with:

```
BrowserContext.new_page: Connection closed while reading from the driver
socket.send() raised exception.
```

The browser process was restarted too many times via kill cycles and its internal
Chrome DevTools state is corrupted.

Fix — completely close the AdsPower profile via the **AdsPower UI** (not the API),
wait ~10 seconds, then reopen it from the UI. After reopening, verify with:
```bash
curl -s http://127.0.0.1:<PORT>/json/list | python -c \
  "import sys,json; pages=[p['url'] for p in json.load(sys.stdin) if p.get('type')=='page']; print([p['url'] for p in pages])"
```
Then relaunch the batch.

### Quick pre-launch diagnostic
```bash
# 1. Check browser is active and get port
curl -s "http://local.adspower.net:50325/api/v1/browser/active?user_id=<profile_id>"
# 2. Check pages are real (not all blank)
curl -s http://127.0.0.1:<PORT>/json/list | python -c \
  "import sys,json; print([p['url'] for p in json.load(sys.stdin) if p.get('type')=='page'])"
# 3. Quick connect test
python -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.connect_over_cdp('ws://127.0.0.1:<PORT>/devtools/browser/<UUID>'); print('ok',len(b.contexts)); p.stop()"
```

Confirmed 2026-07-17 (account 647 BE):
- First hang: Cause 1 (about:blank). Fixed by manual navigation.
- Subsequent hangs: Cause 2 (zombie connections from multiple killed processes).
  Fixed by stop/restart profile via API.
- Final failure: Cause 3 (browser corruption after 6+ kill cycles).
  Required manual close+reopen via AdsPower UI.

---

## Polling loop

**Critical constraints (confirmed 2026-07-08):**
- **Do NOT use `execute_code` for batch polling loops.** The sandbox has a
  hard 300s timeout — a loop doing `sleep(30)` ten times will be killed
  mid-run with no result. Use sequential `terminal()` calls instead.
- **Do NOT chain `sleep N && cmd` in one terminal call when N ≥ 60.**
  The terminal tool has a 60s timeout; the combined call exits 124 before
  the command runs. Keep sleep ≤ 30s if chaining, or split into two calls:
  `sleep 30` alone first, then the check command in the next call.
- **Do NOT use shell `&` inside `terminal(background=true)`.**
  Doing `.venv/Scripts/python.exe ... > log 2>&1 &` inside a background terminal
  call causes Hermes to track the shell process (which exits immediately with
  "bash: no job control"), not the Python process. `notify_on_complete` fires
  instantly (shell exit), not when Python finishes. Run the Python command
  directly with no `&`; let `background=true` handle backgrounding.

- Use `process(action='wait', timeout=60)` repeatedly instead of one long
  wait — each brand takes roughly 3-5 minutes, so a full batch needs many
  60s polls. This is normal, not a stall.
- Between waits, do NOT rely on `tail -n N logfile` to inspect progress.
  In practice `tail` through the terminal tool intermittently reports
  `"N lines output"` with the actual content hidden (truncated/cached),
  even though the command exited 0. Re-running the exact same `tail`
  sometimes surfaces content, sometimes doesn't — it's not deterministic.
- **Reliable alternative**: use targeted `grep` for the marker lines you
  care about. Recommended pattern (filters out noisy fill/screenshot lines):
  ```bash
  grep -E "^\[.*\] 开始:|^\[✓\] 提取到 Case ID:|执行摘要|失败:" runtime/logs/<name>.log \
    | grep -v "截图\|PageCapture\|JS 写入\|fill_katal"
  ```
  Use `grep -c "提取到 Case ID"` for a quick success count check.
  The old pattern below is still valid for broader context:
  ```bash
  grep -n "开始:\|提取到 Case ID\|已跳转主页\|失败:\|Traceback\|429\|410001" runtime/logs/<name>.log | tail -20
  ```
  Piping `grep` output through `tail -N` at the end (not `tail` on the raw
  log) reliably returns visible content in this environment. If it still
  comes back empty, retry once — do not conclude the batch failed from a
  single empty-looking tool result.
- A `429` retry sequence (close-panel recovery, "关闭表单→延迟→重试") can
  take several extra minutes for one brand. This is expected recovery
  behavior (see `troubleshooting.md` / close-panel fix), not a hang. Only
  worry if the process itself exits or the log shows a hard exception with
  no subsequent retry attempt.
- The batch script itself prints a final summary block ("执行摘要" /
  "批次详情") right before exiting — that's the authoritative pass/fail
  source, not intermediate log noise.

## Under Review Case ID recovery (429 close-panel path)

When a brand hits 429 during submission, the close-panel recovery loop
re-opens the panel and checks for an existing Under Review Case ID inside
it. If detected, the script logs:

```
[5461Panel] ✅ panel 内已检测到 Under Review Case ID: <id>，立即退出重试循环
[阶段 3/3] ✅ 429重试 recover 检测到 Under Review Case ID: <id>，跳过重填，直接记录成功
```

This is a **successful submission** — the brand is marked `completed` and
the Case ID is valid. The preceding warning line
`[警告] 未能在规定时间内提取到 Case ID` is noise, not a failure. Report
these the same as normal Case IDs in the completion table.

Confirmed 2026-07-07: even after this Under Review recovery path,
`submit_full/final_state.png` was present (not missing). The primary
evidence path still applies; the fallback path is a safety net.

## Extracting per-brand Case ID timestamps for reporting

Memory rule: batch completion reports must include the Case
appearance/submission timestamp per brand. Two gotchas discovered
2026-07-04:

1. Only the `[HH:MM:SS] 开始: <account> / <brand>` line carries an inline
   timestamp. The `[✓] 提取到 Case ID: <id>` line does **not** — so you
   cannot regex a timestamp directly off the success line.
2. The actual timestamp comes from the evidence screenshot's mtime, but
   the evidence folder layout differs depending on whether the brand hit
   the normal path or a 429-retry path:
   - Normal completion: `runtime/evidence/<date>/<account>/<brand>/submit_full/final_state.png`
   - Brand that went through 429 close-panel recovery: `submit_full/` may
     be missing; fall back to
     `runtime/evidence/<date>/<account>/<brand>/09_after_submit/09_after_submit_screenshot.png`
     (or `_summary.json` next to it).
   - Confirmed 2026-07-07: 429 + Under Review recovery still produced
     `submit_full/final_state.png` — fallback is a safety net, not
     always needed.

Practical recipe:
```bash
for f in runtime/evidence/<date>/<account>/<brand>/submit_full/final_state.png \
         runtime/evidence/<date>/<account>/<brand>/09_after_submit/09_after_submit_screenshot.png; do
  [ -f "$f" ] && stat -c '%y %n' "$f"
done
```
Use whichever file exists. Do not assume `submit_full/final_state.png` is
always present — check both paths before declaring "no evidence found".

## Non-interactive mode auto-skip on 429 failure

When the batch runs in background (stdin EOF / non-interactive), a brand that
exhausts all 429 retry attempts and still has no Case ID hits this prompt:

```
======================================================================
[!] 品牌 <BRAND> 触发了 429/410001 限流
    页面保持打开，可在 AdsPower 中手动查看当前状态
    选择下一步操作：
      Enter  — 跳过此品牌，继续下一个
      r      — 重试此品牌（立即）
      q      — 终止整个批次
======================================================================
>> [非交互模式] stdin EOF，自动跳过，继续下一个品牌
```

This is **expected behavior** — the batch does NOT hang. It auto-skips the
failed brand, marks it `failed` in the summary, and continues with the next
brand. The final `执行摘要` will show `失败: N`. The skipped brand's tab is
left open in AdsPower for manual review.

Action: after the batch finishes, rerun the failed brands as a separate
`--brands` batch (e.g. `--brands "VASG,WILLONE"`) after a cooldown period
(wait at least 30-60 min before retrying rate-limited brands).

## UK-default account running US batches (account 641 pattern)

Some accounts have `"marketplace": "UK"` as their top-level default in
`accounts.json` but also carry a `marketplace_configs.US` entry. Running
`--site US` on these accounts works correctly — the script logs two override
lines and uses the US config:

```
[OVERRIDE] 使用 marketplace_configs.US 的站点配置
[OVERRIDE] 使用指定站点: US (覆盖账号默认的 UK)
```

No special handling is needed; `--site US` is sufficient. The AdsPower
profile (ID redacted for account 641) still uses
`domain_name: amazon.com` and a US proxy (`ip_country: us`), so the
Seller Central session is already scoped to amazon.com.

For accounts that need both US and MX batches, a wrapper script
`_run_641_batch_wrapper.py` in the project root launches them sequentially:
US first, then MX (V-PORYADKU), with separate `--state-file` paths per site.
Use this as a template when adding new sequential multi-site wrappers.

---

## Retry workflow for user-requested reruns (2026-07-12)

When the user says something like "BRAND1 BRAND2 … SITE重新开始" after a
batch finishes with failures, they want a **targeted retry** of only those
brands on the specified site. Do NOT rerun the full original list.

Steps:
1. Confirm the AdsPower profile is still active (or restart it):
   ```bash
   curl -s "http://localhost:50325/api/v1/browser/start?user_id=<profile_id>&headless=0"
   ```
2. Launch a focused batch with **only** the failed brands:
   ```bash
   .venv/Scripts/python.exe scripts/run_full_5461_batch.py \
     --accounts <account_id> \
     --brands BRAND1,BRAND2 \
     --site <SITE> \
     --state-file data/batch_<account>_<site>_<date>_retry.json \
     --yes
   ```
3. Use a distinct state file (suffix `_retry.json`) to avoid overwriting
the original batch state.
4. If the retry batch still marks a brand `failed`, immediately run
`vision_analyze` on `runtime/evidence/<date>/<account>/<brand>/submit_full/final_state.png`
to check whether the screenshot shows **Under Review** (script false-negative)
or a genuine failure.
5. Report the merged result: original successes + retry successes + any
remaining failures with vision-analysis classification.

Confirmed 2026-07-12 (account 641 US): JavoYion and MoShieldwish failed in\nthe initial US batch. Retry batch got Case IDs for both (<CASE_ID_REDACTED> and\n21089055971). MoShieldwish succeeded cleanly; JavoYion was marked `failed`\nby the script but vision analysis showed **Under review**, meaning the\nsubmission had actually gone through on the retry.\n\n**Immediate retry (no cooldown) can still fail on 429-exhausted brands —\nthis is expected, not a bug.** Confirmed 2026-07-17 (account 646 US):\nJavoYion failed the initial batch with a 429 skip. Per the auto-retry rule\n(fire one retry batch before reporting to the user), a retry was launched\n~5 minutes after the original batch exited — same brand, no cooldown. It\nhit 429 again (`MONITOR 429=4`) and landed on a blank \"Add a product\" page\nwith no 5461 panel triggered at all (vision-confirmed: no Case ID, no Under\nReview, no Apply to sell button — a fresh, mostly-empty listing draft).\nAfter reporting this 2nd failure to the user, they explicitly asked for a\n3rd attempt on the same brand only ~6 minutes later (\"JavoYion重新开始\").\n\n**Do not refuse or stall on an explicit user-requested retry just because\nthe cooldown window is short.** State the short elapsed time in one\nsentence for context, then run it immediately — the user's explicit\nper-brand retry request overrides the general 30-60 min cooldown\nrecommendation. Confirmed 2026-07-17: this 3rd attempt hit 429 again\ninitially, but the batch script's own close-panel recovery loop reopened\nthe panel, detected an existing Under Review Case ID inside it\n(`[5461Panel] ✅ panel 内已检测到 Under Review Case ID: ...，立即退出重试循环`\n/ `[阶段 3/3] ✅ 429重试 recover 检测到 Under Review Case ID: ...，跳过重填，\n直接记录成功`), and completed successfully with 0 failures in the summary.\nLesson: a brand that failed twice can still succeed on a 3rd attempt via\nthe same in-batch 429-recovery mechanism, because the recovery is detecting\na submission that already went through server-side on an earlier attempt —\nit doesn't require a fresh rate-limit window to succeed. Don't tell the\nuser \"needs a real cooldown\" as a hard blocker once they've asked for the\nretry; just run it and let the recovery path do its job.

---

## 会话过期 → 市场切换失败（全品牌 `navigation_failed` + `/ap/signin` 重定向）

Symptoms in log — first clue appears on the **second or third brand** at stage 0:

```
[市场切换] 尝试 1/3
[市场切换] [FAIL] 切换操作失败
[市场切换] 尝试 2/3
[市场切换] [FAIL] 切换操作失败
[市场切换] 尝试 3/3
[市场切换] [FAIL] 切换操作失败
[阶段 0] ⚠️ 市场切换失败: 重试 3 次后仍失败
[DashboardCheck] 导航失败，跳过文本分析: status=navigation_failed
  url=https://sellercentral.amazon.com/ap/signin?...
```

Key diagnostic: **every brand fails at stage 0**; the URL in the log is `/ap/signin`.  
This is NOT a 429/rate-limit issue — there is no `MONITOR 429=N` line; the failure is a browser redirect to the login page.

Root cause: the AdsPower profile's browser session cookie has expired. The browser is not logged in to Seller Central.

**Immediate action — stop early, do NOT wait for all brands to fail:**
1. Once **even one brand** shows `navigation_failed` + `/ap/signin` at stage 0, **kill the batch immediately** (`process(action='kill', session_id=...)`). Do not wait for a second brand to confirm — session expiry affects all brands and every extra brand wastes ~90 seconds.
2. Open AdsPower → open the affected profile's browser.
3. Navigate to `sellercentral.amazon.com` and complete the login manually.
4. Confirm the Seller Central dashboard loads (not a login/CAPTCHA page).
5. **Rerun the full original brand list** as a fresh batch.

Do NOT rerun only the failed brands — the entire batch produced zero Case IDs; all brands need to be resubmitted.

Confirmed 2026-07-10 (account 640 US, 10 brands): WILLONE and JZG both failed at market-switch with `navigation_failed` + `/ap/signin`. Batch was left running; human re-login required before rerun of all 10 brands.

---

## "Apply to sell" button not-visible failure (non-429 path)

Distinct from 429 close-panel recovery. Symptoms in log:

```
[HumanPacer] 点击 Apply to sell 失败: Locator.click: Timeout 10000ms exceeded.
  - element is not visible
[MONITOR] 监控摘要: events=0, requests=NNN, 429=0
[警告] 未能在规定时间内提取到 Case ID，保存最终截图...
```

Key diagnostic: `429=0` in MONITOR summary — this is NOT a rate-limit issue.
The `Apply to sell` button exists in the DOM but is rendered not-visible,
typically because the page is in a state where the product_identity workflow
has already progressed past that button (e.g. a prior submission is pending
review, or the page pre-rendered into a different panel state).

What to check via AdsPower:
1. Open the retained tab — the page should show the current state clearly.
2. If the Case Dashboard shows the brand already Under Review from a prior
   attempt → the submission went through; just record that Case ID.
3. If the page is in a broken/stale state → manually click Apply to sell or
   navigate to the entry URL fresh.

Confirmed 2026-07-07 (636EU BE): WILLONE hit this path with `429=0`, marked
failed. Human review required. VASG hit the 429-exhausted path (auto-skip).

## "Description page, no 5461 entry" failure (non-429 path, variant 2)

Distinct from the Apply-to-sell timeout above. Symptoms in log:

```
[警告] 到达 Description 页面但未检测到 5461 表单或 Apply to sell 按钮。
[警告] 该品牌可能：(1) 已有权限 或 (2) 需要人工检查其他申请路径。
```

The script navigates past the product_identity entry but the 5461 panel / Apply
to sell button is completely absent — not timed-out, not hidden, just not there.
The brand is marked `failed` in the 执行摘要.

Observed brands: WILLONE (636EU BE, 2026-07-07; 636 US, 2026-07-08).

What to check via AdsPower:
1. Open the retained tab and look at the Case Dashboard for this brand.
2. If a prior submission is already Under Review → record that Case ID, no rerun needed.
3. If the page shows a different form path (e.g. GTIN Exemption popup instead
   of the normal 5461 panel) → navigate manually to confirm product type.
4. If the brand legitimately already has selling permission → mark as "already authorized", no action.

Key distinction from Apply-to-sell timeout: no `Locator.click: Timeout` line,
no `MONITOR 429=0` line — the script exits the brand cleanly after the warning,
rather than failing on a click.

## Evidence folder collision on same-day multi-site batches (2026-07-08)

Evidence paths are scoped by `<date>/<account>/<brand>` — NOT by site. When
the same account runs US in the morning and MX in the afternoon on the same
calendar day, both batches write into the same folder for overlapping brands:

```
runtime/evidence/2026-07-08/us_store_635/IKABO/submit_full/final_state.png
```

The MX batch overwrites (or coexists with) evidence from the US batch for
any brand that appears in both runs. Consequence: the `stat -c '%y'` timestamp
extraction loop picks up whichever file was written last — which may be the
wrong site's timestamp.

**Workaround for reporting:** for brands that ran on multiple sites same day,
cross-reference the log's `[HH:MM:SS] 开始:` timestamp directly rather than
relying on the evidence file mtime. The log timestamp is always correct.

**Long-term fix:** evidence paths should include site in the folder name (not
yet implemented as of 2026-07-08).

## Sequencing same-account multi-market batches

When the same AdsPower profile/account needs to submit brands on a second
marketplace (e.g. UK then BE, or US then MX), do not run both site batches
concurrently — same profile means shared browser session/tabs, and concurrent
runs will race. Launch the second `--site` batch only after the first batch's
background process reports `completion_reason: "exited"` with exit_code 0.

Confirmed 2026-07-07: UK (7 brands) → BE (5 brands) sequential run on account
636 completed cleanly. The pattern is: run UK, wait for completion, then run BE.
Confirmed 2026-07-08: US (10 brands) → MX (1 brand) sequential run on account
635 completed cleanly, 11/11 success. US batch ran first; MX batch launched
only after US log showed "全部完成". MX used the same AdsPower profile with
`--site MX` and the marketplace_configs.MX entry_url.
Confirmed 2026-07-08: US (9+1 brands) → MX (V-PORYADKU) sequential run on
account 636 completed cleanly, MX 1/1 success. Same pattern — wait for US
exit_code 0 before launching MX batch.
Confirmed 2026-07-10: US (10 brands) → MX (V-PORYADKU + 8 more) sequential
run on account 640 completed cleanly, 19/19 success across three sub-batches.
Note: session had expired at start — batch was let run through 3 brands before
being killed (wasted ~4 min); rule tightened to kill on first `navigation_failed`
+ `/ap/signin`. After manual re-login: US 10/10, then MX 1/1, then MX 8/8.

2026-07-12 (account 641): US (10 brands) and MX (V-PORYADKU, 1 brand) were
launched concurrently — MX started while US was still in progress. Both
batches reached the form-fill stage without crashing. Outcome not yet
confirmed in this session (both still running at session end). If both
complete cleanly, the strict sequential rule may be relaxed for single-brand
MX batches; until confirmed, still prefer sequential execution.
See `references/brand-pack-manifest-pitfalls.md` §"Concurrent same-account
multi-site batches" for analysis.

---

## batch_state.json does NOT capture Case IDs (2026-07-12)

The JSON state file (`data/batch_641_us_20250712.json`, etc.) marks items as
`"completed"` but leaves `"case_id"` empty and `"note"` empty even when the
log clearly shows `[✓] 提取到 Case ID: NNNNNNNNNNN`. Do not rely on the
batch state JSON for the final report.

**Reliable extraction:** parse the raw process log (or the cached terminal
output at `C:/Users/Admin/AppData/Local/hermes/cache/terminal/hermes-results/process_<id>.txt`)
for the `[✓] 提取到 Case ID: <id>` lines. Match each Case ID to its brand by
finding the nearest preceding `开始: <account> / <brand>` line.

Practical Python recipe:
```python
import re
with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
    text = f.read()
brand_starts = list(re.finditer(r'开始: us_store_\d+ / ([A-Za-z0-9\-]+)', text))
case_ids = list(re.finditer(r'\[✓\] 提取到 Case ID: (\d+)', text))
for case in case_ids:
    brand = 'UNKNOWN'
    for b in reversed(brand_starts):
        if b.start() < case.start():
            brand = b.group(1)
            break
    print(f'{brand}: {case.group(1)}')
```

Also grep for `未能在规定时间内提取到 Case ID` to identify failed brands.
The batch script's final `执行摘要` block is a secondary source, but log
parsing is authoritative because the JSON state is incomplete.

---

## Declined recovery: Description-page "Apply to sell" loading timing bug (2026-07-12)

**Symptom:** Brand hits 429, is auto-skipped. Next rerun reaches Description page,
triggers Product Identity retrigger, but `check_page_state()` still returns
`has_5461_form = False` → logged as:
```
[警告] 到达 Description 页面，Product Identity 触发后仍未检测到 5461 入口，请人工复核该品牌是否仍需授权
```
The final screenshot shows **Apply to sell panel spinning/loading** — the panel
became `panel-visible="true"` but its inner content (Declined status, form fields)
had not yet rendered when `check_page_state()` was called.

**Root cause (two layers):**

1. **Loading timing** — The `wait_for_function` after the Product Identity retrigger
   only waited 6 s for the panel to become visible, but not for its content to render.
   `check_page_state()` fired before the Declined card loaded, so `has_5461_form`
   was `False` and the Declined-recovery path was never reached.

2. **Missing fallback** — The `has_5461_form == False` guard at the end of the
   Description block only checked brand-selection and generic paths; it had no
   detection for the "panel-visible but content still loading → Declined" state.

**Fix applied 2026-07-12 to `src/flow_submit_5461.py`:**

*Fix 1* — Added a second `wait_for_function` (max 15 s) after the 6 s panel-visible
wait. This one polls for panel inner content: `declined`, `under review`,
`application submitted`, `listing approval`, or real form inputs:
```python
page.wait_for_function("""() => {
    const panel = document.querySelector('kat-panel-wrapper[panel-visible="true"]');
    if (!panel) return false;
    const body = (panel.innerText || panel.textContent || '').toLowerCase();
    return body.includes('declined') || body.includes('under review') ||
           body.includes('application submitted') || body.includes('listing approval') ||
           panel.querySelector('kat-input, input[type="file"], kat-button#submit_button') !== null;
}""", timeout=15000)
```

*Fix 2* — Added a `declined_check` block just before the final "no 5461 entry" fallback.
When `declined` + `apply to sell` text both appear on the page, it calls
`handle_declined_case_application()` directly, fills the new form, submits, and
extracts the new Case ID. This handles the case where `check_page_state()` saw
a half-loaded panel but by the time the fix-1 wait completes, the Declined state
is readable.

**Confirmed working 2026-07-12 (account 641 / JZG / US):** after two failed attempts
that hit the old "no 5461 entry" path, the fix caused the script to detect the
Declined card, click `>`, open the new 5461 form, fill it, and get Case ID
**<CASE_ID_REDACTED>** successfully.

**Pattern for future debugging:** if a brand logs "未检测到 5461 入口" but the
screenshot shows an "Apply to sell" spinner, the panel loaded too slowly. This
fix covers that scenario; if it regresses, check that both `wait_for_function`
calls are still in the Description-page retrigger block.

---

## Vision analysis for failed-brand verification (2026-07-12)

When a brand is marked `failed` in the batch summary, do not report it as
"failed — no further info". Instead, inspect the evidence screenshot with
`vision_analyze` to determine what actually happened. Two distinct failure
modes have been observed:

### Failure mode A: "Blank loading popup" (JavoYion pattern)

Symptoms: the log shows the form was filled and submitted, but ends with:
```
[警告] 未能在规定时间内提取到 Case ID，保存最终截图...
```

Vision analysis of `runtime/evidence/<date>/<account>/<brand>/submit_full/final_state.png`
shows an **"Apply to sell" popup that is completely blank except for two grey
skeleton placeholder rectangles** (loading state). The popup never finished
loading during the script's wait window.

Action: report as "failed — Apply to sell popup loading timeout". The brand
likely needs a manual retry or a longer wait configuration. Do NOT mark as
"unknown".

### Failure mode B: "Normal Add Product page, no 5461 panel" (MoShieldwish pattern)

Symptoms: the log does NOT show a 5461 form being reached. The final lines
are ordinary Product Identity page navigation. Vision analysis of
`final_state.png` shows a **standard Amazon "Add a product" page** with
Product Identity marked "Ready", Item Name and Brand Name filled, and **no
Apply to sell popup, no Under Review banner, no Case ID anywhere**.

Action: report as "failed — 5461 panel never triggered". The brand may need
manual navigation to a different entry URL or may already have permission
(via a prior submission not visible in this flow). Check the Case Dashboard
manually before rerunning.

### General vision-analysis recipe for any failed brand

```bash
ls runtime/evidence/<date>/<account>/<brand>/submit_full/final_state.png
```

If present, run `vision_analyze` on it with:
> "Describe the page state. Is there a Case ID, Under Review, Apply to sell
> button, or any sign of a submitted application? Extract key text."

Use the vision result to classify the failure into one of the known modes
above, or flag as "needs manual review" if the screenshot shows an unexpected
state (e.g. CAPTCHA, login page, entirely different UI).

**Path format gotcha (confirmed 2026-07-17):** `vision_analyze` on this
Windows host rejects MSYS/git-bash style paths (`/c/Users/Admin/projects/...`)
with `image file not found`, even though the identical path works fine with
`terminal()`/`ls`. Always pass the native Windows path with backslashes
(`C:\Users\Admin\projects\amazon-5461-bot\runtime\evidence\...\final_state.png`)
to `vision_analyze`. If the forward-slash form fails, retry immediately with
the backslash form before concluding the screenshot is missing.
