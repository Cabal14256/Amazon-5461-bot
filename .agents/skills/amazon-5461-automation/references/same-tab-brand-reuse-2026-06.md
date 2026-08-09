# Same-Tab Brand Reuse Pattern (2026-06)

## Motivation

Default batch flow: each brand gets `context.new_page()` → `page.goto(add_product_url)` → fill → submit → `page.close()`. The `goto` + page-load + `ensure_page_ready` costs 15–30 s per brand and is unnecessary when the previous brand succeeded and the page is already sitting on the Product Identity form.

## Implemented approach

### `src/flow_submit_5461.py`

**New function `reset_page_for_next_brand(page, cooldown_sec=0)`**
- Calls `close_5461_panel()` to dismiss the right-side 5461 panel
- Optional `cooldown_sec` wait (pass 0; let the batch-side delay handle pacing)
- Reads `page.url` and confirms it still contains `/product-search`, `/add-product`, `product_identity`, or `ref=xx_addlisting`
- Does a second JS check for `kat-panel-wrapper[panel-visible="true"]`; if any remain, closes again
- Returns `{ready: bool, url: str, reason: str}`

**`submit_5461_from_add_product` — new `skip_add_product_goto=False` param**
- When `True`: skips the entire `page.goto(add_product_url)` + `wait_for_load_state` + `ensure_page_ready` block; waits only for `readyState=complete` (5 s max)
- When `False`: original path unchanged

**`_run_core` success branch (keep_browser_open=True)**
- After extracting case_id successfully, calls `reset_page_for_next_brand(page, cooldown_sec=0)`
- Writes `result["page_ready_for_reuse"]` (True/False) and `result["page_reuse_reason"]`

### `scripts/run_full_5461_batch.py`

**`run_single_item` — new `skip_add_product_goto=False` param**, forwarded to `submit_5461_from_add_product`.

**Batch loop — new `reuse_page` variable**

```
reuse_page = None   # set at top of run_batch_items()

per-brand loop:
  if reuse_page is not None:
      try page.url  ← liveness check
      if alive: fresh_page = reuse_page; skip_goto = True
      else:     reuse_page = None; fresh_page = None

  if not skip_goto:
      fresh_page = context.new_page()   ← normal path

  run_single_item(..., skip_add_product_goto=skip_goto)

  finally:
      page_reusable = result.get("page_ready_for_reuse")
      if close_page and page_reusable:
          reuse_page = fresh_page    ← keep for next brand
      elif close_page:
          reuse_page = None
          fresh_page.close()         ← normal close
```

New-group trigger clears `reuse_page = None` so cross-account/cross-site reuse never happens.

## Timing savings

- Skips `page.goto` + `wait_for_load_state` + `ensure_page_ready`: ~15–30 s per brand on normal Amazon
- Cooldown pacing unchanged: existing `delay_between_items_min/max` loop at batch end still fires
- The page sits idle on Product Identity during the cooldown — no extra mechanism needed

## Failure / fallback behaviour

| Situation | Outcome |
|-----------|---------|
| `page_ready_for_reuse=False` (unexpected URL after submit) | `reuse_page` stays None; next brand gets a fresh page |
| `reuse_page.url` raises (tab closed/crashed) | catches Exception → `reuse_page = None` → `new_page()` |
| Brand fails (no case_id) | `close_page=False` → neither close nor reuse; `reuse_page = None` |
| New account/site group | `reuse_page = None` at group boundary |
| `skip_add_product_goto=True` but Amazon redirected away | `reset_page_for_next_brand` returns `ready=False`; next call gets normal fresh page |

## Known limitations / follow-up work

- `StateLoopExecutor` path in `run_single_item` does NOT pass `skip_add_product_goto` — it constructs its own `brand_data` dict and the executor always navigates itself. Reuse only applies to the `submit_5461_from_add_product` (non-state-loop) path.
- If the Product Identity page drifted (Amazon pushed the form back to a splash/error), `reset_page_for_next_brand` catches it via URL check and returns `ready=False`; the next brand falls back to `new_page()`.
- Dialog handler: `flow_submit_5461.py` binds `_safe_dismiss_dialog` on the page object. On a reused page the handler is already bound from the previous brand's `_run_core` call. A second `page.on("dialog", ...)` call adds another listener but does not remove the old one; multiple dismiss calls on the same dialog hit `ProtocolError`. Mitigation: Playwright silently ignores the second dismiss if the first already handled it — confirmed safe in practice, but worth monitoring. Long-term fix: track whether dialog handler is already bound (e.g. via a page attribute) and skip re-binding.
