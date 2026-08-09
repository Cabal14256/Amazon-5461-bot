# Dashboard Checker & Batch Runner — Bug Fixes (2026-06-13)

Five bugs found and fixed in a single 608US batch session.
All fixes are in production as of this date.
`dashboard-misread-fix.md` content is consolidated here.

---

## Bug 1 — GTIN Exemption prefix match (false `draft`)

**File:** `src/case_dashboard_checker.py`  
**Symptom:** Brand `MP-MALL` (Catalog Authorization = Under Review) returned `status=draft`
because `MP-MALL-Cell Phones & Accessories` (GTIN Exemption = Draft) was matched first
by the old `text.find(brand_lower)` substring scan.

**Root cause:** `_brand_windows()` found the GTIN row first; `analyze_dashboard_text`
read "Draft" out of that window and returned it without checking application type.

**Fix:** Added `_parse_dashboard_rows()` — structural line-by-line parser that identifies
`catalog authorization` / `gtin exemption` rows and their status. `analyze_dashboard_text`
now:
1. Exact-matches brand name (case-insensitive, full token — not substring).
2. Prefers `catalog authorization` rows over other types.
3. Within a type, sorts by priority: `under_review > approved > draft > declined`.
4. Falls back to old substring-window path only when no structured rows are found.

**Dashboard text layout confirmed (innerText):**
```
MP-MALL
\tCatalog Authorization\t
Jun 13, 2026
\t
Under review
Expected decision date: Jun 16, 2026

MP-MALL-Cell Phones & Accessories
\tGTIN Exemption\t
Jun 13, 2026
\t
Draft
```

---

## Bug 2 — `navigation_failed` reads wrong page (false `draft`)

**File:** `src/case_dashboard_checker.py` — `check_case_dashboard_for_brand()`  
**Symptom:** `page.goto(dashboard_url)` returned `ERR_ABORTED` (Amazon page still
processing after submit). Old code ignored `nav_ok=False`, continued reading the
Add Product form page text, found brand name in form fields, found "Draft" keyword
in the listing save notice → returned `status=draft`.

**Fix:** When `nav_ok=False`, set `status=navigation_failed` and `return result`
immediately — skip all text analysis. Callers (batch runner, flow) treat
`navigation_failed` as unknown and do not halt the batch.

**Key condition that triggers `navigation_failed`:**
Amazon's Add Product page holds an open network request after the 5461 panel submit
button is clicked. Any `page.goto()` within ~5–15 s of that click will `ERR_ABORTED`.
The page never leaves the Add Product URL. `dashboard_navigation.final_url` will be
the Add Product URL, not the dashboard URL.

**How Bug 1 + Bug 2 compounded:**
- Before fix: `navigation_failed` → code fell through to text analysis on the wrong page
  → GTIN prefix match found "Draft" → false batch halt.
- After fix: `navigation_failed` → early return → batch continues; next brand's
  Dashboard check (after the page settles) reads correctly.

---

## Bug 3 — `result["case_id"]` not set in stage-3 path

**File:** `src/flow_submit_5461.py` — around line 3436  
**Symptom:** Stage 3 polled successfully, printed `[✓] 提取到 Case ID: XXXXXXXXXX`,
but the tab was kept open and the batch log said "标记为成功但未提取到 Case ID".

**Root cause:**
```python
if case_id:
    result["submit_result"] = "success"
    result["note"] = f"5461 提交成功，Case ID: {case_id}"   # ← case_id in note
    # result["case_id"] = case_id  <-- MISSING
```
`result["case_id"]` was never assigned. The note string contained `"Case ID"`,
which triggered the Dashboard check guard (line ~3497). Dashboard nav failed →
`navigation_failed` → `result["case_id"]` still `None` → tab kept open.

**Fix:** Add `result["case_id"] = case_id` before setting the note.

**How to spot this in logs:**
```
[✓] 提取到 Case ID: <CASE_ID_REDACTED>
...
[DashboardCheck] 导航失败，跳过文本分析: status=navigation_failed
[清理] 标记为成功但未提取到 Case ID，保留标签页供人工核对...
```
The tab stays open; the batch continues but the brand is `completed` with `case_id=null`
in `batch_state.json`.

---

## Bug 4 — Stage 1.5 (ConnectBrand) submit wait too short

**File:** `src/flow_submit_5461.py` — `[阶段 1.5]` block  
**Symptom:** Brands that go through the ConnectBrand / brand-selection path
(e.g. VASG, V-PORYADKU, MP-MALL on MX) consistently got
`status=failed / "Connect brand 后提交未获得 Case ID"`
even though Amazon showed the case under review immediately after.

**Root cause:** The 1.5 path used `time.sleep(12)` + one `extract_case_id()` call.
Amazon takes 15–60 s to render the Case ID on the confirmation panel.

**Symptom in logs (before fix):**
```
[阶段 1.5] 点击提交...
[HumanPacer] 点击后等待 kat-button#submit_button: 0.6s
[清理] 已保存最终状态截图: ...
[DashboardCheck] 打开 Case Dashboard: ...   ← jumps straight here, no polling
```

**Fix:** Replace with the same 90-s polling loop used in stage 3:
```python
print("[阶段 1.5] 等待 Case ID 出现（最多90秒）...")
case_id = None
max_wait, poll_interval, elapsed = 90, 3, 0
while elapsed < max_wait:
    case_id = extract_case_id(page)
    if case_id:
        print(f"[✓] 提取到 Case ID: {case_id}")
        break
    ...
    time.sleep(poll_interval); elapsed += poll_interval
```

**Verification (V-PORYADKU MX, after fix):**
```
[阶段 1.5] 等待 Case ID 出现（最多90秒）...
[✓] 提取到 Case ID: <CASE_ID_REDACTED>
[清理] 批量模式：执行成功（Case ID: <CASE_ID_REDACTED>），保持浏览器开启
[页面] V-PORYADKU 页面已关闭（已确认成功/Case ID）
```

---

## Bug 5 — Background batch killed by EOFError on 429 prompt

**File:** `scripts/run_full_5461_batch.py` — 429/410001 interactive prompt  
**Symptom:** A 429 during a background (unattended) batch triggered `input(">> ")`,
which raised `EOFError` (stdin closed). The old handler:
```python
except (EOFError, KeyboardInterrupt):
    choice = "q"   # terminates entire batch
```
silently killed the remaining brands.

**Why `isatty()` returns True in a background Hermes process:**
`terminal(background=True)` does not redirect stdin to `/dev/null`; the process
inherits the terminal's stdin. `isatty()` returns True, so the `else` branch
(auto-skip) is never reached. The `input()` call then blocks until EOF.

**Fix:** Split the except:
```python
except EOFError:
    print("[非交互模式] stdin EOF，自动跳过，继续下一个品牌")
    choice = ""    # skip this brand, continue batch
except KeyboardInterrupt:
    choice = "q"   # only explicit Ctrl-C kills the batch
```

---

## Bug 6 — `pick_best_row` silent empty-content (brand copy generation)

**File:** `auto_add_account_data.py` — `pick_best_row()`  
**Symptom:** `5461_statement_mx.txt` for VASG was empty (2 bytes). Batch submitted
with a blank Product Description field.

**Root cause:** Excel row `(MX, VASG, content="", sku="")` exists. `pick_best_row`
found `site_rows` non-empty, sorted by `content_completeness` (all zeros), returned
the empty row. `ValueError` was never raised so the `fallback_site` block was skipped.

**Detection:** `source_row.json` contains `"content": ""` and `"sku": ""`.

**Fix:** After sorting `site_rows`, check if `best` has no content and no SKU;
if so, search other rows for a populated one (US first). Return that so
`adapt_content_for_site` can translate it for the target market.

**Temporary fix for VASG MX:** manually wrote `5461_statement_mx.txt` and
`5461_statement_mx.account_608/594/602.txt` from the US copy, with:
- Item Category → Spanish MX category
- Item name → `VASG Protector de Pantalla para Reloj Inteligente 44 mm, Película de Vidrio Templado, Paquete 2+2, Transparente HD`
- Item desrciption → Spanish equivalent
- SKU → `{account}-MX-VASG-AL12`

---

## Monitoring pattern (how to tell if the process is stuck vs. buffered)

- `total_lines` not growing for **3+ consecutive `log` calls at 60 s intervals** → genuinely stuck.
- `total_lines` growing but slowly → output buffering; process is alive.
- `uptime_seconds` increasing but `output_preview` unchanged in `poll` → use `log` with
  explicit `start_line` to read past the buffer boundary.
- `evidence/<date>/<account>/<brand>/` directory empty after 5+ minutes → stuck before
  first screenshot (possibly at a Playwright wait).
- **Screenshot timeout (30 s)** is NOT a hang — it's normal on slow pages. Logs resume
  after the timeout. Do not restart the process for this alone.
- Confirmed pattern (2026-06-13): `total_lines` stayed at 82 for multiple polls while
  `uptime_seconds` grew — process was not stuck, it was in a Playwright screenshot wait.
  Logs jumped 82→147→219 once the screenshot timeout resolved.
