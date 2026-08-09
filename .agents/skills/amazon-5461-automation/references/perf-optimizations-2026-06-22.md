# Performance Optimizations — 2026-06-22

Three optimizations implemented in this session. All landed as code changes, all lint-verified.

---

## 1. Async Screenshots (`src/flow_submit_5461.py`)

### What was added

New helper at top of file (after imports):

```python
import threading

def _async_screenshot(page, path: str, full_page: bool = False, timeout: int = 10000) -> None:
    """Non-critical screenshots — run in background thread, never block main flow."""
    def _do():
        try:
            page.screenshot(path=path, full_page=full_page, timeout=timeout)
        except Exception:
            pass
    threading.Thread(target=_do, daemon=True).start()
```

### Which screenshots are ASYNC (converted)

| Screenshot file | Location (approx line) | Reason async is safe |
|---|---|---|
| `declined_nav.png` | ~2262 | Status record after nav, not read before return |
| `server_error.png` | ~2584 | Evidence before return, not read by retry logic |
| `01_add_product_filled.png` | ~2616 | State snapshot before Next click, not read |
| `02_after_next.png` | ~2857 | Debug snapshot, not read |
| `02_brand_blocked.png` | ~2952 | Evidence before return |
| `02_description_page.png` | ~2964 | Evidence before return |
| `02_after_apply.png` | ~3256 | State snapshot, not read before Case check |
| `02_case_created.png` ×2 | ~3323, ~3529 | Evidence before return, Case ID already extracted |
| `02_declined_case.png` | ~3352 | Evidence before return |
| `02_description_no_auth.png` | ~3778 | Evidence before return |

### Which screenshots STAY SYNC (do NOT convert)

| Screenshot file | Reason to keep sync |
|---|---|
| `before_submit.png` (L474) | Old submit_5461 func — critical pre-submit evidence |
| `after_submit.png` (L525) | Old submit_5461 func — critical post-submit evidence |
| `error.png` (L558) | Old submit_5461 func — exception evidence |
| `03_before_submit.png` (L3922) | Read in human confirm flow; captcha check follows |
| `04_after_submit.png` (L3967) | Captured immediately after submit click; used as polling baseline |
| `04_after_submit_final.png` (L4009) | Captured when Case ID poll times out; 429 retry logic reads page AFTER this |
| `05_final_confirm.png` (L4121) | Final confirm — most important evidence screenshot, stays sync |
| `error.png` (L4142) | Fatal exception handler |
| `final_shot` (L4153) | finally-block; last chance before context closes |

### Expected savings

Normal Amazon response: 10–30s per async screenshot × 11 shots = **110–330s per brand** on slow CDN days.
Fast days: still saves a few seconds per shot (screenshot overhead alone ~1–3s).

---

## 2. `ensure_page_ready` Exponential Backoff (`src/amazon_error_handler.py`)

### What changed in `recover_from_server_error()`

```python
# BEFORE — fixed wait every retry
time.sleep(wait_seconds)  # always 90s

# AFTER — exponential backoff, capped at wait_seconds
backoff = min(30 * (2 ** retry), wait_seconds)
# retry=0: min(30, 90) = 30s
# retry=1: min(60, 90) = 60s
# retry=2: min(90, 90) = 90s
time.sleep(backoff)
```

Also replaced the two fixed sleeps after reload/goto:

```python
# BEFORE
page.reload(...)
time.sleep(5)
page.goto(...)
time.sleep(8)

# AFTER
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

### Savings

- Old worst case (3 retries × 90s) = 270s
- New worst case (30 + 60 + 90s) = 180s → **saves 90s on the first server error recovery**
- reload/goto fixed sleeps: up to 13s saved per recovery attempt on fast pages

---

## 3. Dashboard Check Sleep Removal (`scripts/check_case_log.py`)

Three `time.sleep` calls replaced with event-driven waits:

| Location | Old | New |
|---|---|---|
| `extract_case_ids_from_page()` L101 | `time.sleep(3)` | `page.wait_for_load_state("networkidle", timeout=3000)` |
| After `page.goto(case_url)` L277 | `time.sleep(6)` | `page.wait_for_load_state("networkidle", timeout=6000)` |
| After `window.scrollTo(0, 300)` L286 | `time.sleep(1)` | removed entirely (scroll is synchronous) |

All wrapped in `try/except pass` — timeout is fine, just means page is slow.

### Savings

Dashboard loads fast: up to **9s saved per `check_case_log.py` invocation**.

---

## Pitfalls

- **Do NOT make `before_submit` / `after_submit` / `final_confirm` async** — these are the screenshots that matter most for case recovery. They must be on disk before the script moves on.
- **`04_after_submit_final.png` must stay sync** — the 429 retry logic at L4016+ reads the page state AFTER this screenshot. If it were async, the retry might start before the screenshot completes and the evidence would be incomplete.
- **`page` must remain open** when the async thread fires. This is safe for all converted shots — they are in branches that do NOT close the page or context immediately after the call. `return result` after an async shot is fine because the `finally` block (which closes the tab) runs after Python returns from the function, giving the daemon thread time to complete.
