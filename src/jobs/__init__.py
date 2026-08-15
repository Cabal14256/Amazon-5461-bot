"""Stage-3 automation job execution: diagnose / dry_run queue.

This package owns the per-job resource layout, hidden subprocess spawning,
profile mutual exclusion and the asyncio dispatch loop.  It deliberately
exposes only ``diagnose`` and ``dry_run`` job types — real submission is out
of scope (stage 4) and must never be reachable from the web console.
"""
