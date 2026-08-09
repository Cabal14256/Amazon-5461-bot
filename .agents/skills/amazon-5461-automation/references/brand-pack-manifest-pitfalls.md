# Brand Pack Manifest Pitfalls

## `supported_marketplaces` vs `statement_files` divergence

A brand pack can have a valid `statement_files.MX` entry without listing `MX`
in `supported_marketplaces`. The batch script uses `statement_files` for the
actual run — the brand **will work** on that site even when the marketplace
list looks incomplete.

**Confirmed 2026-07-12**: V-PORYADKU `manifest.json` had
`supported_marketplaces = [US, UK, BE, NL, SE, DE, FR, ES, IT]` (MX absent)
but `5461.statement_files.MX` and `gtin_exemption.statement_files.MX` both
existed. Running `--site MX` succeeded normally.

**Confirmed 2026-07-14**: Same divergence hit again on account 642 onboarding.
V-PORYADKU MX was in `statement_files` but not `supported_marketplaces`. Fixed
by patching the manifest directly (added `"MX"` to the list) before launching
the batch. The audit script below should be run as part of any new-account
onboarding sequence to catch and fix these divergences proactively.

**Rule**: Do NOT skip a brand or remove it from a `--brands` list because
`supported_marketplaces` is missing a site. Always check `statement_files`
in the manifest first. When you find a divergence, fix the manifest immediately
(add the missing site to `supported_marketplaces`) so future audit runs are clean.

### Root cause fixed at the source (2026-07-17)

The divergence was never a data-entry mistake — it was a code bug.
`update_manifest_site_statement_files()` in `auto_add_account_data.py` (repo
root) writes `statement_files[site]` every time `sync_brand_pack_for_account()`
runs for a new site, but it never touched `supported_marketplaces`. Every
onboarding call that synced a brand for a site not already in
`supported_marketplaces` silently created a new divergence.

Patched 2026-07-17: the function now also appends the site to
`supported_marketplaces` (deduped) whenever it syncs statement files for that
site, in the same `manifest.json` write. New onboarding runs (`auto_add_account_data.py`
single-brand or `--all-brands`) will keep the two lists in sync automatically
going forward — no more manual manifest edits needed for newly-onboarded
brand+site combos.

Also swept all existing brand packs on 2026-07-17: 22 manifests had historical
drift (19 MX, 20 UK, including JZG) and were bulk-fixed by adding every site
present in `statement_files` to `supported_marketplaces`. Audit came back
clean immediately after (see script below).

**Updated guidance**: the manual "add the missing site" step above is now a
safety net, not the primary fix path. If the audit script ever finds a fresh
divergence again after 2026-07-17, that means `update_manifest_site_statement_files()`
regressed or a manifest was hand-edited outside the onboarding flow — check
the function first before assuming it's just stale data.

### Audit script — find all divergent brand packs

```bash
cd projects/amazon-5461-bot
python -c "
import json, glob
for mf in glob.glob('brand_packs/*/manifest.json'):
    d = json.load(open(mf))
    for site in ['US','MX','UK','BE','NL','SE','DE','FR','ES','IT']:
        mx_m = site in d.get('supported_marketplaces', [])
        mx_s = site in d.get('5461', {}).get('statement_files', {})
        if mx_s and not mx_m:
            print(d['brand_name'], '| site:', site, '| has statement but not in supported_marketplaces')
"
```

Any row printed = the manifest list needs updating (but the brand still runs
correctly — fix the manifest for cleanliness, not for correctness).

---

## Background process + pipe anti-pattern

**Never pipe the batch script into `tail -N` inside a `terminal(background=true)` call.**

```bash
# BAD — hides all output from process(action='log')
.venv/Scripts/python.exe scripts/run_full_5461_batch.py ... | tail -5

# GOOD — full output accessible via process(action='log', limit=N)
.venv/Scripts/python.exe scripts/run_full_5461_batch.py ...
```

When piped to `tail`, Hermes tracks the `tail` subprocess (which exits
immediately with "bash: no job control"), `notify_on_complete` fires
instantly, and `process(action='log')` only returns the tail's 5 lines —
you lose all intermediate progress output. Confirmed 2026-07-12.

---

## Concurrent same-account multi-site batches (risk)

The reference file (`batch-monitoring-and-reporting.md` §"Sequencing
same-account multi-market batches") says to run multi-site batches
**sequentially** on the same AdsPower profile.

2026-07-12: US (10 brands) and MX (V-PORYADKU) batches for account 641 were
launched concurrently (MX started while US was still running). Both appeared
to start without error. Outcome pending — if both succeed, the sequential
rule may be overly conservative for one-brand MX batches where the profile
has a separate marketplace_configs.MX entry. If either fails or shows tab
conflicts, revert to strict sequential execution.

**Until confirmed safe**: prefer launching MX batch only after US batch exits.
