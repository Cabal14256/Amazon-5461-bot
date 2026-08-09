---
name: amazon-5461-env-debugging
description: Environment setup and debugging patterns for the amazon-5461-bot project — PYTHONPATH pollution fix, AdsPower profile recovery, connect_over_cdp hang diagnosis.
---

# Amazon 5461 Bot — Environment & Debugging Patterns

Companion to `amazon-5461-automation`. Use when batch scripts fail to start, hang at CDP connect, or throw import errors.

---

## 1. PYTHONPATH Pollution (confirmed 2026-07-19)

**Symptom:** Batch exits immediately with:
```
ModuleNotFoundError: No module named 'greenlet._greenlet'
  File "...hermes-agent\venv\Lib\site-packages\playwright\..."
```

**Cause:** Hermes injects its own venv into `PYTHONPATH`, so the project `.venv` loads Hermes's greenlet/playwright instead of its own.

**Fix — prefix every Python call with `env -u PYTHONPATH`:**
```bash
env -u PYTHONPATH .venv/Scripts/python.exe scripts/run_full_5461_batch.py \
  --accounts us_store_647 --brands VASG,V-PORYADKU --site MX \
  > runtime/logs/batch_NNN_site_brands.log 2>&1
```

Applies to **all** direct Python invocations from this project inside a Hermes terminal, including one-off `-c` test snippets.

---

## 2. connect_over_cdp Hangs — Extended Diagnosis

See `amazon-5461-automation` skill → `references/batch-monitoring-and-reporting.md` for the base diagnosis tree (Causes 1–3). Additional patterns confirmed 2026-07-19:

### Cause 3b: Profile corruption survives stop/restart — delete+re-add required

After multiple kill cycles, `connect_over_cdp` may succeed but `context.new_page()` throws immediately:
```
BrowserContext.new_page: Connection closed while reading from the driver
socket.send() raised exception.
```
API stop/restart (`/api/v1/browser/stop` then `/api/v1/browser/start`) does **not** fix this. The only reliable recovery is:

1. In AdsPower UI: delete the profile entirely and re-add it fresh.
2. Get the new `user_id` from the AdsPower API:
   ```bash
   curl -s "http://127.0.0.1:50325/api/v1/user/list?page=1&page_size=100" | python -c "
   import json,sys
   data=json.load(sys.stdin)
   for u in data['data']['list']:
       if '647' in u.get('name',''):  # adjust number
           print(u['user_id'], u.get('username',''))
   "
   ```
3. Update `runtime/private/accounts.json`:
   ```python
   import json
   path = 'runtime/private/accounts.json'
   d = json.load(open(path))
   for a in d['accounts']:
       if a['account_id'] == 'us_store_NNN':
           a['adspower_profile_id'] = '<new_user_id>'
   with open(path, 'w') as f:
       json.dump(d, f, indent=2, ensure_ascii=False)
   ```

### After profile re-add: update accounts.json adspower_profile_id

When an AdsPower profile is **deleted and re-added** (not just stop/restart), the
`user_id` changes. The old `adspower_profile_id` in `accounts.json` is now stale and
must be updated BEFORE running any batch (stale ID causes CDP URL lookup failures):

```bash
# 1. Get new user_id
curl -s "http://127.0.0.1:50325/api/v1/user/list?page=1&page_size=100" | python -c "
import json,sys
for u in json.load(sys.stdin)['data']['list']:
    if 'NNN' in u.get('name',''):  # replace NNN with account number
        print(u['user_id'], u.get('username',''))
"

# 2. Patch accounts.json
python -c "
import json
path = 'runtime/private/accounts.json'
d = json.load(open(path))
for a in d['accounts']:
    if a['account_id'] == 'us_store_NNN':
        a['adspower_profile_id'] = '<new_user_id>'
        print('updated:', a['adspower_profile_id'])
with open(path, 'w') as f:
    json.dump(d, f, indent=2, ensure_ascii=False)
"
```

### Pre-launch verification (required after any profile restart/re-add)

Both steps must pass before launching a real batch:
```bash
cd /c/Users/Admin/projects/amazon-5461-bot && env -u PYTHONPATH .venv/Scripts/python.exe -c "
from playwright.sync_api import sync_playwright
p = sync_playwright().start()
b = p.chromium.connect_over_cdp('ws://127.0.0.1:<PORT>/devtools/browser/<UUID>')
print('connected, contexts:', len(b.contexts))
ctx = b.contexts[0] if b.contexts else b.new_context()
pg = ctx.new_page()
print('new_page ok')
pg.close(); p.stop()
print('all good')
"
```

If `new_page()` fails → Cause 3b → delete+re-add profile. If `connect_over_cdp` itself hangs → Cause 2 → API stop/restart.

---

## 3. Background vs foreground asymmetry

If a test script (`python -c "..."`) connects and creates pages fine but the background batch hangs at `connect_over_cdp`, the cause is **always** a zombie WebSocket connection from a prior killed batch process (Cause 2) or browser corruption (Cause 3/3b). The pre-launch verification above distinguishes them.

Do NOT conclude the batch script has a bug when foreground tests pass — it's the connection state.

---

## 4. MX market — Listing Approval form type (confirmed 2026-07-19)

MX Seller Central presents a different form type than US/EU for some brands:
**"Listing approval for BRAND"** — a plain HTML form inside `kat-panel-wrapper`, NOT the
standard `kat-input` Shadow DOM fields (`cat_auth_mo_question_string_id_*`).

### Symptom before fix
`check_page_state()` returned `5461_PANEL_SHELL` even though the panel was fully loaded.
Log line:
```
[DEBUG] 5461 弹窗 JS 检查: {'found': True, 'visible': True, 'display': True, 'width': 1920, 'height': 0}
[DEBUG] 检测到 5461 panel shell，但真实字段/上传控件未加载；不判定为表单打开
[阶段 2/3] Declined recovery 失败: ... 后未找到新申请入口（状态: 5461_PANEL_SHELL）
```

Root cause: `has_5461_form_fields()` only checked `kat-input` selectors, but the MX
Listing Approval form uses `input`/`textarea` plain HTML elements. `offsetHeight: 0` was
a red herring — the panel uses CSS that collapses the wrapper height even when content is
fully rendered.

### Fix applied (2026-07-19, `src/flow_submit_5461.py`)

**`has_5461_form_fields()`** — added:
- Data-cy and placeholder selectors for plain `input`/`textarea` fields
- Panel innerText fallback: if panel text contains "product title" or "manufacturer" AND
  "submit"/"upload"/"select files", AND `panel.querySelectorAll('input, textarea, kat-input').length > 0` → return True

**`fill_5461_form()`** — added "Listing Approval" branch (runs after GTIN Exemption check,
before standard cat_auth_mo path):
- Detection: panel innerText or body contains "Listing approval for" / "Submit required information" / "Submit documents"
- Fills: Product title (from `Item name:` in statement), Manufacturer, Product Description
- Uploads: `input[type="file"]` (plain HTML, not `document_upload_*`)
- Checks all `input[type="checkbox"]` and `kat-checkbox` in the panel
- Fills contact email if `account_email` provided

### Form field values (from `statement_MX.txt`)
```
product_title  ← "Item name：..." line
manufacturer   ← "Manufacturer：..." line
description    ← "Item desrciption：..." line   (note: typo in key is intentional — matches the file)
```

### DOM structure — all fields are `kat-input` (NOT plain HTML despite appearance)

Even though `fill_5461_form` detects this as a "Listing Approval" form, the actual DOM
fields are **`kat-input` components**, identical to the standard cat_auth_mo form.
They have the same `name` attributes (`question-cat_auth_mo_question_string_id_*`).
The `private-light-dom` slot has a `hidden="true"` `input` — DO NOT target this.

| Field | kat-input id / name |
|-------|---------------------|
| Product ID (optional) | `question-cat_auth_mo_question_string_id_product_id` |
| Product title * | `question-cat_auth_mo_question_string_id_product_title` |
| Manufacturer * | `question-cat_auth_mo_question_string_id_manufacturer` |
| Product Description * | `question-cat_auth_mo_question_string_id_product_description` |
| Email * | `contact_info_email_input` (name=`email`) |

### ⚠️ Critical: filling kat-input in overlay panels (keyboard.type DOES NOT WORK)

`keyboard.type()` dispatches to the **background page** (Add Product form), not the
overlay panel. Three failed approaches:

1. `locator('input[name=...]').fill()` → resolves to hidden private-light-dom input → "element not visible"
2. `shadow_inp.focus(); shadow_inp.click()` + `page.keyboard.type()` → types into background page Item Name field
3. `HTMLInputElement.prototype.value` setter only → DOM value set but Katal state unchanged, renders empty

**✅ Only working approach:** set both kat-input host + shadow input, fire composed events:

```python
page.evaluate("""([name, val]) => {
    let ki = document.querySelector("kat-input[name='" + name + "']");
    if (!ki) ki = document.querySelector("kat-input#" + name);
    ki.setAttribute("value", val); ki.value = val;
    const si = ki.shadowRoot && ki.shadowRoot.querySelector("input[part='input']");
    if (si) {
        const s = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value");
        if (s && s.set) s.set.call(si, val);
        si.dispatchEvent(new Event("input",  {bubbles: true, composed: true}));
        si.dispatchEvent(new Event("change", {bubbles: true, composed: true}));
        si.dispatchEvent(new Event("blur",   {bubbles: true, composed: true}));
    }
    ki.dispatchEvent(new Event("change", {bubbles: true, composed: true}));
    ki.dispatchEvent(new CustomEvent("change", {bubbles: true, composed: true, detail: {value: val}}));
    return {ok: true, value: ki.value};
}""", [field_name, value])
```

This works because Katal listens on the **host element** for composed events and updates
its internal state when both `ki.value` and the shadow input's native setter are set.

### Pitfall: email field wrongly filling Product ID Number slot

If a naive `findShadowInput` searches for `input[name="email"]` in shadow roots,
it may match a different field first. Always target by exact `kat-input[name=...]`
selector on the host element, NOT by traversing shadow roots for inner `input[name]`.

### Known accounts using this form type
- us_store_647 / VASG / MX
- us_store_647 / V-PORYADKU / MX
- us_store_649 / VASG, uShield, JavoYion / BE (confirmed 2026-07-19 — same "Listing
  approval for BRAND" panel wording also appears on BE, not just MX)

Other MX accounts with standard `kat-input` forms exist — the new code falls through to
cat_auth_mo path if the panel text doesn't match Listing Approval markers.

### ⚠️ Known bug (unfixed as of 2026-07-19): Product Description is truncated in this branch

The "Listing Approval" branch's Product Description extraction only grabs the
single `Item desrciption：<line>` line via regex — it does NOT fill the full
`statement_text` block (Brand/Manufacturer/Category/Color/Specification/SKU/
Model/authorization sentence) the way the standard cat_auth_mo path does at
the `fill_katal_input(page, SELECTORS['product_description'], statement_text)`
call elsewhere in the same function. Product title and Manufacturer are each
filled correctly from their own dedicated statement lines — only Description
is narrowed to one sentence.

Confirmed 2026-07-19 (account 649, site BE, brands VASG/uShield/JavoYion — all
3 completed with Case IDs, all 3 had this narrow description). Detect via:
```bash
grep -A2 "检测到 Listing Approval 表单" runtime/logs/<name>.log
```
If the `description:` line that follows is a single short sentence instead of
a multi-field block, this bug is in play.

Do NOT auto-resubmit brands that already have a Case ID to "fix" this — check
Case Dashboard status first (Under Review is fine, leave it). Only fix the
extraction logic itself going forward, and confirm with the user before
changing it since it's shared across every brand/account that routes through
this "Listing Approval" panel wording (not just MX — BE hits it too).

---

## 5. Declined panel detection bugs (confirmed 2026-07-25)

Two bugs in the Declined-state handling chain. Both affect any brand that has a
prior Declined Case ID when resubmitting. Both patched in `src/form_filler.py`
and `src/flow_submit_5461.py`.

### Bug A: `_probe_5461_panel` misses Declined panel → 6× close-panel loop

**Symptom:**
```
[FormFiller] 5461 弹窗状态: {'found': False, 'hasRealFields': False}
[FormFiller] Apply to sell 后未加载真实字段；关闭 panel {...}，等待 Ns 后重试 (N/6)...
```
Repeats 6 times. Browser shows the panel is open with "Declined" status and a Case ID.

**Root cause:** `_probe_5461_panel()` only matches panels containing `Listing approval`,
`cat_auth_mo`, `product_title`, etc. A Declined-state panel has none of those — just
"Apply to sell", "Declined", "Application to create new ASINs for BRAND", Case ID.
Result: `found=False`, Declined-detection block skipped, close-panel loop runs forever.

**Fix** (`src/form_filler.py`, after `if panel_check.get('hasRealFields'): return True`):
Added broad `declined_scan` JS that queries **all** `kat-panel-wrapper` elements (no
`data-testid` filter) and `document.body.innerText` fallback. If "Declined" +
"Application to create new ASINs" or a 10-12 digit Case ID is detected, sets
`self.declined_case_id` and returns `False` to hand off to `handle_declined_case_application`.

Confirming log line:
```
[FormFiller] ⚠️ 宽泛扫描检测到 Declined 状态（Case ID: NNNNNNNNNNN），
交给 declined recovery 点击右侧 > 新建表单，停止 close-panel 重试
```

### Bug B: `handle_declined_case_application` gives up on `NEEDS_APPROVAL_NEW_UI`

**Symptom** (appears after Bug A is fixed):
```
[DeclinedHandler] 跳转后页面状态: NEEDS_APPROVAL_NEW_UI
[阶段 2/3] Declined recovery 失败: 跳转后未找到新申请入口（状态: NEEDS_APPROVAL_NEW_UI）
```

**Root cause:** After clicking `►`, Amazon navigates back to Product Identity page
with Apply to sell button visible (`NEEDS_APPROVAL_NEW_UI`). `handle_declined_case_application`
in `src/flow_submit_5461.py` only handled `5461_FORM`/`5461_FORM_OPEN` and explicit
"submit new" button text — `NEEDS_APPROVAL_NEW_UI` fell to catch-all `return {'success': False}`.

**Fix** (`src/flow_submit_5461.py`, before final `return {'success': False, 'action': 'no_new_form'}`):
Added a `NEEDS_APPROVAL_NEW_UI` branch that calls `filler.click_apply_to_sell()` a
second time. Returns success if that succeeds. Guards against infinite recursion if
the second call also hits Declined.

---

## 6. Patching Python source files containing regex literals

When a `.py` source file needs a regex change involving `\r`, `\n`, `\s`, `\w`, or
other backslash sequences, **do NOT use shell heredoc, `python -c "..."`, or the
`patch` tool**. All three expand backslash sequences before the bytes reach the file,
e.g. `[^\r\n]` becomes a literal newline, breaking the string syntax.

**Only reliable approach:** write a helper `.py` script with `write_file`, then run it:

```python
# fix_regex.py
path = 'src/flow_submit_5461.py'
data = open(path, 'rb').read()
colon = '\uff1a'.encode('utf-8')   # ：  use chr() / .encode() for non-ASCII

old = b"\\s*(.+)"                              # exact bytes in the file
new = b"[^\\S\\r\\n]*([^\\r\\n]+)"             # correct replacement

assert old in data, f"not found: {old!r}"
data = data.replace(old, new, 1)
open(path, 'wb').write(data)

import py_compile
py_compile.compile(path, doraise=True)
print("syntax OK")
```

Key rules:
- `open(path, 'rb')` / `open(path, 'wb')` — binary mode, no newline translation.
- Build byte patterns with `b"..."` literals or `chr(N).encode()`.
- Always end with `py_compile.compile(path, doraise=True)` to verify syntax.
- Clean up: `rm -f fix_regex.py` after success.

**`[^\S\r\n]*` vs `\s*` — critical distinction:**
`\s*` matches `\r\n`, so `\s*([^\r\n]+)` will still cross a blank line and match
the next line. Use `[^\S\r\n]*` (horizontal whitespace only) to stay on the same
line. This was the root cause of the empty-description → SKU-leak bug
(see `amazon-5461-account-onboarding` → `references/statement-file-description-bug.md`).

---

## 6. Canonical launch command

```bash
cd /c/Users/Admin/projects/amazon-5461-bot && env -u PYTHONPATH .venv/Scripts/python.exe scripts/run_full_5461_batch.py \
  --accounts us_store_NNN \
  --brands BRAND1,BRAND2 \
  --site MX \
  --state-file data/batch_NNN_mx_<date>.json \
  > runtime/logs/batch_NNN_mx_brands.log 2>&1
```
Launch with `terminal(background=true, notify_on_complete=true)`.

⚠️ Never add `--submit` to this command — the flag does not exist on
`run_full_5461_batch.py` and causes immediate exit_code 2. Use `--state-file`
instead (also gives you a clean per-run state artifact for retries).

## 8. Batch crash mid-run (exit_code 1, no 執行摘要) — recovery (confirmed 2026-07-25)

**Symptom:** Background batch exits with `exit_code 1` mid-way through a brand.
Log ends abruptly with Playwright `Exception in callback SyncBase._sync.<locals>.<lambda>()`
tracebacks (greenlet thread-switching noise). No `执行摘要` line appears.
The AdsPower browser may also become unresponsive.

**This is distinct from a clean brand failure** — the process itself crashed, not
just a brand that was auto-skipped.

**Recovery procedure:**
1. Stop and restart the AdsPower profile via API:
   ```bash
   curl -s "http://local.adspower.net:50325/api/v1/browser/stop?user_id=<profile_id>"
   sleep 5
   curl -s "http://local.adspower.net:50325/api/v1/browser/start?user_id=<profile_id>&open_tabs=1"
   sleep 6
   ```
2. Parse the log to identify which brands completed (have `[✓] 提取到 Case ID`) vs.
   which did not start or crashed mid-fill.
3. Rerun only the incomplete brands with a fresh `--state-file` suffix `_retry.json`.
4. Do NOT rerun brands that already got Case IDs.

**Distinguishing crash from skip:**
- Clean auto-skip: log shows `>> [非交互模式] stdin EOF，自动跳过` then `[!] 跳过 BRAND`
- Crash: log ends mid-fill with no `执行摘要`, process `exit_code=1`

Confirmed 2026-07-25 (account 655 US): batch crashed mid-JavoYion after uShield
completed cleanly. uShield Case ID was in the log; JavoYion had no Case ID.
Recovery: stop/start profile, retry only WILLONE+JavoYion.

## 9. Pre-launch AdsPower health check (cheap, avoids silent hangs)

Before every launch, confirm the profile is active and showing real pages
(not `about:blank`) — a 10-second check that avoids the `connect_over_cdp`
hang failure modes described in section 2:

```bash
curl -s "http://local.adspower.net:50325/api/v1/browser/active?user_id=<profile_id>"
# take the debug_port from the response, then:
curl -s http://127.0.0.1:<PORT>/json/list | env -u PYTHONPATH python -c \
  "import sys,json; print([p['url'] for p in json.load(sys.stdin) if p.get('type')=='page'])"
```

If the URLs are real Seller Central pages, launch directly — no restart
needed. Confirmed 2026-07-19 (account 649EU BE, VASG/uShield/JavoYion): this
check plus `--state-file` (no `--submit`) produced a clean 3/3 batch with
zero connect_over_cdp issues and zero manual recovery steps.
