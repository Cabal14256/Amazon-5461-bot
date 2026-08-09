# Project structure / formalization audit checklist

Use this when the user asks to "formalize", "productionize", "clean up", or "review the whole project" for amazon-5461-bot (as opposed to debugging one batch run). Run each check and report findings with actual command output, not assumptions.

## 1. Locate the live copy first
This project has accumulated nested duplicate directories from past migrations:
`projects/amazon-5461-bot/amazon-5461-bot/` and `projects/amazon-5461-bot/projects/amazon-5461-bot/` exist alongside the real root `projects/amazon-5461-bot/`.
- Compare mtimes (`find <dir> -newer <root>/PROJECT.md -type f`) to confirm which copy is actively updated. As of 2026-07, the outer root is live (updated through 7/14); the nested `amazon-5461-bot/` copy is a frozen snapshot from 6/10.
- Warn the user before editing anything under a nested copy — it's easy to patch the wrong (dead) tree by accident.
- `git status` in this project reports "not a git repository" even though `REFACTOR-PLAN.md` references specific commit hashes from a prior git history that no longer exists on disk. Flag this; don't assume git history is available.

## 2. Check accounts.json for real-data leakage
`PROJECT.md` / `docs/security/secret-handling.md` both mandate that `config/accounts.json` stay an empty placeholder, with real accounts only in `runtime/private/accounts.json` (gitignored). This rule has been violated in practice before — `config/accounts.json` was found containing 56 real account records (adspower_profile_id, entry_url, etc.) instead of being empty. Always verify with:
```bash
python -c "import json; d=json.load(open('config/accounts.json',encoding='utf-8')); print(len(d.get('accounts', d)))"
```
If count > 0, this is a live security violation of the project's own documented boundary — surface it as the top finding, not a footnote.

## 3. Check the Hermes watchdog cron job
There is normally a cron job named `amazon-5461-hermes-watchdog` that polls a signal file (`runtime/hermes_signal.json`, historically under the nested copy's `runtime/`) and uses browser/vision tools to analyze a stuck page via AdsPower CDP connection when the batch script gets stuck. Check its live status with the cronjob tool (`action=list`) — it has been found `paused` with `last_status: error`, meaning stuck-page monitoring silently isn't happening even though the batch scripts may still be writing signal files expecting it. Report actual paused/error state, don't assume it's running.

## 4. Structural debt checklist (recite and verify each, don't assume from docs alone)
- **No version control**: confirm via `git status`. If absent, note it as the single highest-leverage fix (enables rollback/audit); `.gitignore` is already comprehensive and ready to use once `git init` happens.
- **Root directory clutter**: loose one-off scripts, debug pngs/jsons, `_tmp_*.py` files sitting in project root. Check `find . -maxdepth 1 -type f | wc -l` — historically ~87 loose files.
- **Archive plans that were written but never executed**: `docs/archive-plan-2026-05.md` / `docs/legacy-script-migration-index.md` document a cleanup/migration plan with specific target paths (`_archive/experiments/YYYY-MM/`, etc.) that was never actually carried out. Don't assume "there's a plan" means "it happened" — verify files are still in their pre-migration locations.
- **Unbounded growth, no retention**: `data/batch_state_*.json` (many are same-batch retry/fix variants), `runtime/logs/*.log`, `runtime/evidence/<date>/<account>/` accumulate indefinitely with no archival cron. Quantify with `find <dir> | wc -l` per subdir.
- **Claimed test/architecture completion vs actual repo state mismatch**: `REFACTOR-PLAN.md` claims "88/88 tests passed, phases 1-4 complete", but the actual `tests/` directory currently holds only ~4 tests in one file. Treat any "N/N tests passed" claim in project docs as needing re-verification against the current `tests/` directory before repeating it as fact.
- **No CI/enforcement**: `pyproject.toml` has pytest+ruff configured correctly, but nothing forces them to run after a change — verification is manual-only. Note this as a gap, don't assume any hook enforces it.

## 5. Verification commands that actually work in this environment
```bash
# System python has no pytest; must use the project's own venv:
cd projects/amazon-5461-bot && .venv/Scripts/python.exe -m pytest -q
python -m compileall -q src scripts tests cli
```

## 6. Recommended prioritization when reporting findings
Report in this order (risk-first, not roadmap-doc order): (1) real secrets/account data sitting where it shouldn't, (2) no version control / no rollback ability, (3) duplicate/dead directory trees that invite editing the wrong copy, (4) stale monitoring (paused cron etc.) creating false sense of safety, (5) unbounded data growth, (6) missing automated test enforcement.
