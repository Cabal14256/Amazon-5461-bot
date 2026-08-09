---
name: amazon-5461-troubleshooting
description: Troubleshoot Amazon 5461 batch execution failures — ghost panel recovery, onboarding pitfalls, PYTHONPATH pollution, process-kill recovery. Load alongside amazon-5461-automation when diagnosing batch failures or onboarding new accounts.
---

# Amazon 5461 Troubleshooting

Operational troubleshooting patterns for `run_full_5461_batch.py` execution. Load alongside `amazon-5461-automation` skill.

See `references/statement-file-generation-adjacent-account.md` for generating account-specific UK statement files from a numerically adjacent account's files when no spreadsheet rows exist.

---

## Launch Requirement: `env -u PYTHONPATH`

**Confirmed 2026-07-24 (account 655 US):** Running without `env -u PYTHONPATH` picks up Hermes's own venv's playwright (broken `greenlet._greenlet` on Windows). Error:

```
ModuleNotFoundError: No module named 'greenlet._greenlet'
```

Always use:
```bash
env -u PYTHONPATH .venv/Scripts/python.exe scripts/run_full_5461_batch.py ...
```

The `greenlet.error` noise lines in the log are harmless (see `amazon-5461-automation` skill); this is about the *launch* failing entirely.

---

## Ghost Panel: Apply to Sell Opens Empty (Transient)

**Symptom:** Apply to sell panel opens but is empty — no real fields. Log:

```
[FormFiller] 5461 弹窗状态: {'found': False, 'hasRealFields': False}
[FormFiller] Apply to sell 后未加载真实字段；关闭 panel … 等待 Ns 后重试
[FormFiller] kat-link View playwright点击失败: Locator.click: Timeout
  - <kat-panel slot="panel" visible="true" …> intercepts pointer events
```

**Confirmed 2026-07-24 (account 655, WILLONE):** This is transient. The script retries up to 6 times. On one run, attempts 1–4 failed with ghost panels; attempt 5 succeeded and the form was filled normally.

**What to do:**
- Do NOT kill the batch. The retry loop handles this.
- Each retry cycle takes ~30–90 seconds.
- If all 6 retries exhaust → brand auto-skipped (expected behavior).
- On a fresh re-run of the same brand, first attempts may still ghost — persistence matters.

**Not the same as:**
- Apply-to-sell not-visible (`429=0`, `element is not visible`)
- 429 recovery (happens AFTER form submission, during Case ID wait)

---

## Known noise line: image upload timeout (2026-07-29)

```
[警告] 图片上传失败: Locator.set_input_files: Timeout 30000ms exceeded.
```

Appears during form fill when the file-input element takes too long to respond.
**Does NOT block submission** — both 661EU/JavoYion (SE) and 662EU/WILLONE (BE)
hit this line and still received valid Case IDs in the same run. Treat the same
as other noise lines: judge success only by `[✓] 提取到 Case ID`. Confirmed
2026-07-29.

---

## Confirmed EU site codes (2026-07-29)

`--site` values confirmed working for EU accounts:

| Code | Marketplace |
|------|-------------|
| `UK` | United Kingdom |
| `BE` | Belgium |
| `SE` | Sweden ← confirmed 2026-07-29 (account 661EU) |

Use the two-letter ISO country code. SE behaves identically to BE/UK — no
special flags or config required.

---

## Cross-account concurrent batches (2026-07-29)

The sequential-batches rule applies **per AdsPower profile** (= per account).
Two batches on **different** accounts (different `user_id` / different profile)
can run concurrently without conflict:

- 661EU + 662EU (distinct profile IDs, redacted) launched simultaneously.
- Both completed 5/5 brands with 0 failures.

Rule summary:
- **Same profile → strictly sequential** (shared browser/tabs race).
- **Different profiles → concurrent OK** (separate browser processes).

---

## accounts.json structure pitfall — dict, not bare list (2026-07-29)

`runtime/private/accounts.json` is a **dict** with a single top-level key:

```json
{ "accounts": [ { "account_id": "us_store_661", ... } ] }
```

Always index `data["accounts"]`, not `data` directly. Iterating `for a in data`
yields the string key `"accounts"` and raises:

```
AttributeError: 'str' object has no attribute 'get'
```

Correct one-liner pattern:
```python
data = json.load(open("runtime/private/accounts.json"))
matches = [a for a in data["accounts"] if "661" in str(a.get("account_id", ""))]
```

---

## Same-day second batch on same site — state file naming (2026-07-29)

When running a **second fresh batch** on the same site on the same calendar day
(e.g. MX run #1 in the morning, MX run #2 in the afternoon), use a numbered
suffix to avoid overwriting the first state file:

```
data/batch_661_mx_20260729.json    ← first MX batch
data/batch_661_mx2_20260729.json   ← second MX batch, same day
```

Reserve `_retry` suffix for retrying **failed brands** from a prior batch.
`_mx2_` / `_us2_` signals a distinct second run, not a failure retry.

---

## Onboarding Pitfall: `adspower_profile_id` Empty After `--all-brands`

**Confirmed 2026-07-24 (account 655):**
`auto_add_account_data.py --all-brands --site US 655` wrote all brand docs correctly but left `adspower_profile_id: ""` and `status: "pending_setup"` in the account record.

**Fix:** Query AdsPower directly, then patch `runtime/private/accounts.json`:

```bash
# 1. Find profile ID by name
curl -s "http://local.adspower.net:50325/api/v1/user/list?page=1&page_size=100" \
  | python -c "import json,sys; [print(u['name'],u['user_id'],u.get('username','')) for u in json.load(sys.stdin)['data']['list'] if u['name']=='N']"

# 2. Patch the record
python -c "
import json, urllib.request
with open('runtime/private/accounts.json') as f: data = json.load(f)
for a in data['accounts']:
    if a['account_id'] == 'us_store_N':
        a['adspower_profile_id'] = 'PROFILE_ID'
        a['status'] = 'active'
        resp = urllib.request.urlopen('http://local.adspower.net:50325/api/v1/user/list?user_id=PROFILE_ID')
        for p in json.loads(resp.read()).get('data',{}).get('list',[]):
            if p.get('username'): a['email'] = p['username']
        break
with open('runtime/private/accounts.json','w') as f: json.dump(data,f,indent=2,ensure_ascii=False)
"
```

**Before launching batch, verify both fields:**
```bash
python -c "import json; d=json.load(open('runtime/private/accounts.json')); [print(a['account_id'],a['status'],a['adspower_profile_id']) for a in d['accounts'] if 'us_store_N' in a['account_id']]"
# Must show: us_store_N active k1xxxxxx
```

---

## One-Shot Cron Job Silent Failure — Post-Schedule Verification (2026-07-29)

One-shot `cronjob(action='create')` jobs CAN trigger and disappear from the list
without the cron agent actually running the batch script. Confirmed 2026-07-29
(account 662 UK): job `46822fb30643` was created, appeared in list, fired at 18:00,
and vanished — but zero log file, zero evidence folder, zero batch output.

**Diagnosis:** The cron agent spawned but failed before issuing any terminal call
(model routing, toolset init, prompt error). The job is marked "ran" and removed
from the queue regardless.

**Warning at creation time:** Even with `attach_to_session=True` and `deliver='origin'`,
desktop app sessions may report:
```
"This is a local-only cron job: its output will NOT be delivered back into this session"
```
This warning signals delivery is unreliable for this session type. Do not rely on a
notification arriving — treat no-show after 5 minutes as a failure.

**Verification procedure (run at schedule time + 5 min):**
```bash
ls runtime/logs/batch_NNN_<site>_<date>.log 2>/dev/null || echo "LOG MISSING — batch did not run"
ls runtime/evidence/<date>/us_store_NNN/ 2>/dev/null || echo "NO EVIDENCE — batch did not run"
```

If both are absent → launch manually immediately:
```bash
cd /c/Users/Admin/projects/amazon-5461-bot
env -u PYTHONPATH .venv/Scripts/python.exe scripts/run_full_5461_batch.py \
  --accounts us_store_NNN \
  --brands "BRAND1,BRAND2,..." \
  --site UK \
  --state-file data/batch_NNN_<site>_<date>.json \
  > runtime/logs/batch_NNN_<site>_<date>.log 2>&1
```
Use `terminal(background=true, notify_on_complete=true)`.

---

## Multi-account simultaneous "no 5461 entry" on MX (2026-07-30)

When **multiple accounts** on the same MX batch session all hit "Description page, no
5461 entry" (`Apply to sell 按钮检测: {'found': False}`, `429=0`) simultaneously, the
failure is likely **not per-brand** — it points to one of:

1. **Existing MX submissions** — brands were previously submitted under these accounts
   and the Case Dashboard already shows Under Review. Check AdsPower retained tabs.
2. **MX platform-side UI change** — `Apply to sell` selector or triggering logic shifted.
3. **Account session expired during Description page** (check for `navigation_failed` +
   `/ap/signin` in the log for stage 0).

Evidence screenshot at this failure: `submit_full/02_description_no_auth.png`
(alongside `final_state.png`). Always check both.

**Action:** Before retrying, manually verify AdsPower retained tabs for each account.
If any brand shows Under Review in the Case Dashboard → record those Case IDs, no rerun.
If all tabs show a clean Description page with no panel → the MX entry detection logic
may need updating.

Confirmed 2026-07-30: accounts 659 (VASG) and 661 (V-PORYADKU, JavoYion) all failed
this way simultaneously. Manual AdsPower review required.

---

## `vision_analyze` quota exhausted — fallback for screenshot review

When `vision_analyze` fails with:
```
Error code: 429 - {'code': 'API_KEY_QUOTA_EXHAUSTED', 'message': 'API key 额度已用完'}
```

The auxiliary vision model's API quota is exhausted. Do NOT retry `vision_analyze`.

**Fallback — deliver screenshots directly to the user:**
```bash
cp /c/Users/Admin/projects/amazon-5461-bot/runtime/evidence/<date>/<account>/<brand>/submit_full/final_state.png \
   /c/Users/Admin/AppData/Local/Temp/<brand>_final.png
```

Then include `MEDIA:C:\Users\Admin\AppData\Local\Temp\<brand>_final.png` in the reply.
The user sees the image inline and can narrate what they observe.

For the `02_description_no_auth.png` screenshot at the Description-page failure point,
the same copy+MEDIA approach applies.

---

## Freshly-Onboarded Account: Stale CDP on First Launch (2026-07-31)

**Symptom:** Browser shows `status=Active` in the AdsPower API and the WS URL is
returned, but `connect_over_cdp` times out with:

```
playwright._impl._errors.TimeoutError: BrowserType.connect_over_cdp: Timeout 180000ms exceeded.
Call log:
  - <ws connecting> ws://127.0.0.1:NNNNN/devtools/browser/<uuid>
  - <ws connected> ws://127.0.0.1:NNNNN/devtools/browser/<uuid>
```

Note the WS *does* connect (TCP handshake succeeds) but then the CDP protocol
handshake stalls — the endpoint is stale from a previous session.

**Confirmed 2026-07-31 (account 663EU, first-ever batch run):** The profile had
been opened manually in AdsPower before the batch was launched. The WS endpoint
it reported was from that earlier manual session and the underlying Chrome process
had drifted.

**Fix — stop + restart before first launch:**
```bash
curl -s "http://local.adspower.net:50325/api/v1/browser/stop?user_id=<PROFILE_ID>"
sleep 5
curl -s "http://local.adspower.net:50325/api/v1/browser/start?user_id=<PROFILE_ID>&open_tabs=1"
sleep 8
# Confirm new port is live
curl -s "http://local.adspower.net:50325/api/v1/browser/active?user_id=<PROFILE_ID>"
```

Then relaunch the batch immediately — the fresh CDP endpoint is valid.

**Distinguishing from process-kill restart (below):** The process-kill case starts
from `exit_code: -15` mid-batch. This stale-endpoint case starts from a clean
first launch where the batch never started processing any brand.

---

## Under Review False Negative — Panel Open, No Case ID (2026-07-31)

**Symptom:** Batch log shows `[警告] 未能在规定时间内提取到 Case ID` followed by
`[清理] 执行失败（failed），保留标签页用于调试`. The final batch summary lists the
brand as `failed`. But the screenshot shows the 5461 panel is still open with the
sidebar displaying:

```
Under review — Decision expected by D MMM YYYY
```

**Root cause:** Submission succeeded and Amazon accepted the application, but the
5461 panel did not navigate away to a Case ID page within the timeout window. The
Case ID extraction logic times out waiting for the post-submission URL/element
that never appears because the panel stays open in the "Under Review" state.

**Confirmed 2026-07-31 (account 663EU UK):** V-PORYADKU, WILLONE, JavoYion all
showed this pattern in the same batch. The other 4 brands (HOMEMO, JZG, VASG,
uShield) returned Case IDs normally.

**Diagnosis steps:**
1. Check `final_state.png` for the brand under
   `runtime/evidence/<date>/us_store_NNN/<BRAND>/submit_full/final_state.png`
2. Look for the right-side "Apply to sell" panel — if it shows a clock icon and
   "Under review", the submission was accepted.
3. `DashboardCheck navigation_failed` in the log is a **red herring** here — the
   panel being open blocks navigation to the Case Dashboard, not a submission failure.
4. No retry needed. Record as success with note "Under Review, no Case ID visible".

**Reporting:** Record Case ID as `—` in the results table with note
`✅ Under Review (DATE)`. Do not add to a retry batch.

---

## Process Killed → Restart Browser → Relaunch

When a background batch process is killed (`exit_code: -15`, `completion_reason: "killed"`), restart the AdsPower profile to clear any zombie CDP connection before relaunching:

```bash
curl -s "http://local.adspower.net:50325/api/v1/browser/stop?user_id=<PROFILE_ID>"
sleep 5
curl -s "http://local.adspower.net:50325/api/v1/browser/start?user_id=<PROFILE_ID>&open_tabs=1"
sleep 8
# Verify CDP is reachable
PORT=$(curl -s "http://local.adspower.net:50325/api/v1/browser/active?user_id=<PROFILE_ID>" | python -c "import sys,json; print(json.load(sys.stdin)['data']['debug_port'])")
python -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.connect_over_cdp('ws://127.0.0.1:$PORT/devtools/browser/...'); print('ok',len(b.contexts)); p.stop()"
```

Then relaunch with `--state-file` to preserve prior state.
