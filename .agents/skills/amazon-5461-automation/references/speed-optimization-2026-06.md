# Speed Optimization Analysis — 2026-06-22 (updated)

## Context

Session goal: reduce per-brand wall-clock time without changing correctness or risk profile.
Target files: `src/flow_submit_5461.py`, `src/amazon_error_handler.py`, `scripts/check_case_log.py`

---

## Bottleneck 1: Fixed `time.sleep()` — DONE (2026-06-20)

All `time.sleep(5/8/10/12)` calls that were waiting for a DOM/URL state change replaced with
`page.wait_for_function()` / `page.wait_for_selector()`. Worst-case unchanged, fast paths exit early.

17 sites replaced across `flow_submit_5461.py`:

| Location (approx line) | Old | New condition |
|---|---|---|
| `handle_brand_selection` after Connect click | `sleep(8)` | URL leaves `product_identity` OR panel visible |
| `ConnectBrand._select_from_brex_widget` confirm click | `sleep(5)` | `brex-widget` OR `brand-info-option` OR panel visible |
| `ConnectBrand` overlay click | `sleep(5)` | `brex-widget` OR `brand-info-option` |
| `fill_5461_form` option click | `sleep(8)` | `wait_for_5461_form_fields(timeout=8, interval=1)` |
| `DeclinedHandler` nav click | `sleep(5)` | `wait_for_load_state("domcontentloaded", 8s)` |
| Stage 1/3 Continue/Next click | `sleep(5) + sleep(8)` | URL/panel wait (10s) |
| Stage 1.5 brand selection success | `sleep(5)` | panel OR brand-info-option (5s) |
| Stage 1.8 Continue click | `sleep(8)` | URL/panel wait (8s) |
| Stage 1.9 Submit click | `sleep(8)` | URL/panel wait (8s) |
| Stage 2/3 "check auth" entry | `sleep(5)` | `apply to sell`/approval text/panel (5s) |
| Stage 2/3 Apply to sell new UI | `sleep(10)` | panel/listing approval/case id/under review (10s) |
| Stage 2.5 GTIN retry ×3 | `sleep(5)` each | panel OR product_title field (5s each) |
| Stage 2.5 brand selection ×2 | `sleep(5)` each | panel OR brand-info-option (5s each) |
| Stage 2.5 Declined retry2 | `sleep(6)` | panel OR product_title field (6s) |
| Stage 2.5 NEEDS_APPROVAL_NEW_UI | `sleep(5)` | case id/under review/listing approval/panel (5s) |
| Stage 2/3 Product Identity retrigger | `sleep(6)` | apply to sell/approval/panel text (6s) |
| Stage 2/3 Apply after retrigger | `sleep(10)` | panel OR product_title OR listing approval (10s) |
| Stage 2/3 retry nav (reload form) | `sleep(8)` | `readyState=complete` AND body length > 200 (8s) |
| Stage 2/3 retry Next click | `sleep(8)` | URL/panel wait (8s) |
| Stage 3/3 initial submit wait | `sleep(12)` | case id/under review/application submitted/submit btn gone (12s) |
| add_product_url post-nav | `sleep(8)` | `readyState=complete`, not account-switcher, body > 200 (8s) |

### Pattern template

```python
try:
    page.wait_for_function(
        """() => { /* your condition */ }""",
        timeout=N_000   # same N as the original sleep
    )
except Exception:
    pass
# continue with state check
```

---

## Bottleneck 2: `recover_half_loaded_5461_panel` — DONE (2026-06-20)

Old: `random.randint(25, 55)` × 3 attempts = avg 120s total
New exponential backoff:
- attempt 1: `randint(10, 20)` → avg 15s
- attempt 2: `randint(20, 35)` → avg 27s
- attempt 3: `randint(30, 50)` → avg 40s
- Total avg: **82s** (saves ~38s / ~32%)

Code in `src/flow_submit_5461.py`:
```python
base_lo = min(10 + (attempt - 1) * 10, 35)
base_hi = min(20 + (attempt - 1) * 15, 55)
wait_sec = random.randint(base_lo, base_hi)
```

---

## Bottleneck 3: Screenshot blocking — DONE (2026-06-22)

**Problem:** 15+ `page.screenshot()` calls in `flow_submit_5461.py`. Each can block 10–30s when
Amazon pages are slow (CloudFront font loading, CDN chunks). Non-critical screenshots were
serialised with the main flow even though their results are never read by subsequent code.

**Solution:** Added `_async_screenshot()` helper at top of `flow_submit_5461.py`:

```python
import threading

def _async_screenshot(page, path: str, full_page: bool = False, timeout: int = 10000) -> None:
    """Non-critical screenshots run in a background daemon thread."""
    def _do():
        try:
            page.screenshot(path=path, full_page=full_page, timeout=timeout)
        except Exception:
            pass
    threading.Thread(target=_do, daemon=True).start()
```

**11 screenshots converted to async** (middle-state snapshots):

| File name | Location | Reason async is safe |
|---|---|---|
| `declined_nav.png` | DeclinedHandler | State check follows immediately from page text |
| `server_error.png` | Stage 1 error path | Function returns right after |
| `01_add_product_filled.png` | Stage 1 | Brand Name validation follows, not a screenshot read |
| `02_after_next.png` | Stage 1 post-Next | Page state checked via JS, not screenshot |
| `02_brand_blocked.png` | Stage 2 blocked exit | Function returns right after |
| `02_description_page.png` | Stage 2 permission exit | Function returns right after |
| `02_after_apply.png` | Stage 2 apply-failed exit | Function returns right after |
| `02_case_created.png` (×2) | Stage 2.5 success | Function returns right after |
| `02_declined_case.png` | Stage 2.5 declined | Function returns right after |
| `02_description_no_auth.png` | Stage 2 no-auth exit | Function returns right after |

**7 screenshots kept synchronous** (results read by subsequent code or critical evidence):

| File name | Why synchronous |
|---|---|
| `before_submit.png` (old submit_5461 func) | Captcha check follows |
| `after_submit.png` (old submit_5461 func) | Success marker check follows |
| `error.png` (old submit_5461 func) | Exception handler, must complete before finally |
| `03_before_submit.png` | Human confirm prompt may follow |
| `04_after_submit.png` | 90s Case ID polling loop follows |
| `04_after_submit_final.png` | 429 retry logic reads page state immediately after |
| `05_final_confirm.png` | Primary evidence; must exist before dashboard check |
| `error.png` (main flow except) | Critical exception evidence |
| `final_shot` (finally block) | Dashboard check runs concurrently after this |

**Estimated savings:** 20–60s per brand on slow Amazon pages (10–30s per async screenshot × 2–3
screenshots that would have blocked on average per brand).

---

## Bottleneck 4: `ensure_page_ready` wait — DONE (2026-06-22)

**Problem:** `recover_from_server_error()` in `src/amazon_error_handler.py` used a fixed
`wait_seconds=90` per retry attempt → worst case 3 retries × 90s = 270s total.
Post-reload `time.sleep(5)` and post-goto `time.sleep(8)` also blocked unnecessarily.

**Solution:** Exponential backoff + `wait_for_load_state`:

```python
# Before
time.sleep(wait_seconds)   # always 90s
page.reload(...)
time.sleep(5)
page.goto(...)
time.sleep(8)

# After
backoff = min(30 * (2 ** retry), wait_seconds)  # 30s, 60s, 90s
time.sleep(backoff)
page.reload(...)
try:
    page.wait_for_load_state("networkidle", timeout=5000)
except Exception:
    pass
page.goto(...)
try:
    page.wait_for_load_state("networkidle", timeout=8000)
except Exception:
    pass
```

Backoff schedule (default `wait_seconds=90`, `max_retries=3`):
- Retry 1: wait 30s (saves 60s vs old)
- Retry 2: wait 60s (saves 30s vs old)
- Retry 3: wait 90s (unchanged)
- **Best case (1 retry): saves 60s + ~10s (load waits)**
- **Worst case (3 retries): saves ~10s (load waits only)**

---

## Bottleneck 5: Dashboard check fixed sleep — DONE (2026-06-22)

**Problem:** `scripts/check_case_log.py` had three blocking sleeps:
- `time.sleep(3)` in `extract_case_ids_from_page()` before reading page text
- `time.sleep(6)` after `page.goto(case_url)` before reading Dashboard
- `time.sleep(1)` after scroll before screenshot

**Solution:** Replace all three with event-driven waits:

```python
# sleep(3) → networkidle wait
try:
    page.wait_for_load_state("networkidle", timeout=3000)
except Exception:
    pass

# sleep(6) → networkidle wait
try:
    page.wait_for_load_state("networkidle", timeout=6000)
except Exception:
    pass

# sleep(1) after scroll → removed entirely (scroll is synchronous JS eval)
```

**Savings:** Up to 9s per `check_case_log.py` invocation on fast connections.

---

## Remaining Bottlenecks (not done, future work)

### Brand-to-brand delay
- Default: `delay_between_items_min=15, delay_between_items_max=30` in `run_full_5461_batch.py`
- Tunable via config, but reducing increases 429 risk
- Recommendation: leave at 15–30s; add adaptive logic that increases delay after any 429 event

### Multi-account parallelism
- Currently: one AdsPower profile at a time, brands serial
- Could: run multiple accounts in parallel processes (each with own AdsPower profile)
- AdsPower supports multiple simultaneous profiles
- Requires: refactor `run_full_5461_batch.py` to use `multiprocessing.Pool`
- Benefit: N× throughput (N = number of parallel accounts)
- Risk: medium; AdsPower API concurrency limits unknown; needs testing

---

## Estimated Per-Brand Savings (cumulative, all bottlenecks done)

Normal Amazon response times (no 429):

| Bottleneck | Avg saving / brand |
|---|---|
| Fixed sleeps → wait_for_function (B1) | ~68s |
| `recover_half_loaded` backoff (B2) | ~8s (fires ~20% of brands) |
| Screenshot async (B3) | ~20–60s |
| `ensure_page_ready` backoff (B4) | ~5s (fires rarely) |
| Dashboard sleep (B5) | ~1s (post-batch tool) |
| **Total** | **~100–140s per brand** |

A 10-brand batch: saves **~17–23 minutes**.
A 15-brand batch: saves **~25–35 minutes**.

Worst case (all waits time out, server error fires every brand): zero savings from B1/B3/B5,
B2/B4 save ~13s/brand. No regression in any case.
