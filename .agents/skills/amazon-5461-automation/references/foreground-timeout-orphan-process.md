# Foreground timeout does not guarantee the batch process died

## When this applies

You (or a prior turn) launched `run_full_5461_batch.py` in the foreground
(no `background=true`) — against the explicit pitfall already documented in
the main SKILL.md — and the `terminal()` call returned `exit_code: 124`
after the tool's timeout window. The log file shows the run got partway
through a brand (e.g. mid `[FormFiller]` step) and then just stops.

## The mistake to avoid

`exit_code 124` only tells you the **tool call** gave up waiting for output.
It does NOT confirm the underlying `python.exe` process — and the live
Playwright browser automation it's driving against the target AdsPower
profile — was actually killed. Do not assume "the run failed, let me delete
the state file and start a fresh background run" without checking first.

If an orphaned process from the timed-out call is still alive and you start
a second run against the same account/brand/site, you get two automations
racing on the same browser profile/tab. This can corrupt the in-progress
form fill, double-submit, or leave the page in a state the new run's
selectors don't expect.

## Correct sequence after a foreground timeout

1. Check for a still-running process before touching state:
   ```bash
   tasklist | grep -i python.exe   # Windows
   ```
   or use `process(action='list')` if the timed-out call was itself started
   with `background=true` (it wasn't, in the pitfall this documents, but
   check anyway in case of a prior background leftover).
2. If a matching orphaned process is found, kill it before proceeding
   (`taskkill /PID <pid> /F` or `process(action='kill')`), and note in the
   batch/ledger notes that a foreground timeout happened so the eventual
   Case ID (if any) can be cross-checked against dashboard truth rather than
   trusted blindly.
3. Only after confirming no orphan is running: delete/replace the batch
   state file and relaunch with `terminal(..., background=true,
   notify_on_complete=true)`.

## Prevention

Never use a foreground call as a "let's just try it and see" probe for this
script. Launch with `background=true` from the very first attempt for a
given batch — the main SKILL.md already mandates this; the failure mode
this file documents is what happens when that rule gets skipped even once.
