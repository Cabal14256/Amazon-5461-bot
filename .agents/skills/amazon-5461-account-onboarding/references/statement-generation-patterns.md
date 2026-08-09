# Statement File Generation Patterns

Confirmed working patterns for generating account-specific statement files
from generic templates via the fallback path.

---

## Correct generation script (confirmed 2026-07-28, account 660 UK + NL)

Write this as a `.py` file via `write_file` and run it — never use shell
heredoc or `python -c` for multi-file content (special chars break silently).

```python
"""Generate account NNN <SITE> statement files from generic templates."""
import re, os

ACCT = 'NNN'         # e.g. '660'
SITE = 'uk'          # e.g. 'uk', 'nl', 'de'
BASE = r'C:\Users\Admin\projects\amazon-5461-bot\brand_packs'
FTYPES = ['5461_statement', 'gtin_exemption_statement']

# Per-brand mapping: brand → old_prefix (auto-detected below, or hardcoded)
BRANDS_PREFIX = {}

# Auto-detect prefix from each source file
for brand in ['HOMEMO','JZG','V-PORYADKU','VASG','WILLONE','uShield','JavoYion']:
    src = os.path.join(BASE, brand, 'docs', f'5461_statement_{SITE}.txt')
    if not os.path.exists(src):
        print(f'[SKIP] {brand}: no generic template for {SITE}')
        continue
    with open(src, 'r', encoding='utf-8') as f:
        text = f.read()
    m = re.search(r'SKU\uff1a(\d+)-' + SITE.upper() + r'-', text, re.IGNORECASE)
    if not m:
        print(f'[WARN] {brand}: no SKU prefix found in generic template')
        continue
    BRANDS_PREFIX[brand] = m.group(1)

# Generate account-specific files
for brand, old_prefix in BRANDS_PREFIX.items():
    for ftype in FTYPES:
        src = os.path.join(BASE, brand, 'docs', f'{ftype}_{SITE}.txt')
        dst = os.path.join(BASE, brand, 'docs', f'{ftype}_{SITE}.account_{ACCT}.txt')
        if not os.path.exists(src):
            continue
        with open(src, 'r', encoding='utf-8') as f:
            text = f.read()
        old_pattern = f'SKU\uff1a{old_prefix}-{SITE.upper()}-'
        new_pattern = f'SKU\uff1a{ACCT}-{SITE.upper()}-'
        new_text = text.replace(old_pattern, new_pattern)
        with open(dst, 'w', encoding='utf-8') as f:
            f.write(new_text)
        m = re.search(r'SKU\uff1a(\d+)-' + SITE.upper() + r'-(\S+)', new_text, re.IGNORECASE)
        sku = f"{m.group(1)}-{SITE.upper()}-{m.group(2)}" if m else "NOT_FOUND"
        ok = m and m.group(1) == ACCT
        print(f'[{"OK" if ok else "FAIL"}] {brand:15s} {ftype:30s} → {sku}')

print('\nDone.')
```

Key implementation notes:
- Use `str.replace()` (simple string replacement), NOT `re.sub()` — the
  full-width colon `\uff1a` in a regex replacement string caused silent
  failures in this session (result was `NOT_FOUND` even after no error).
- Auto-detect the old prefix from each source file with `re.search`; do NOT
  hardcode a single shared prefix across all brands or all sites.
- Read/write files with `open(..., encoding='utf-8')`, never via `read_file`
  tool output (which prepends line numbers) or shell heredoc (which mangles
  special characters).

---

## Known prefix table (as of 2026-07-28, snapshot — always verify from source)

| Brand | UK | NL | Notes |
|-------|----|----|-------|
| HOMEMO | 650 | — | Different from the others on UK |
| JZG | 649 | 629 | Site prefix is independent of brand prefix |
| V-PORYADKU | 649 | — | |
| VASG | 649 | — | |
| WILLONE | 649 | — | |
| uShield | 649 | — | |
| JavoYion | 649 | — | |

This table is for orientation only — always grep the actual source file.
Prefixes can change when a new master batch updates the generic templates.

---

## Verification after generation

```python
import re, os, glob
BASE = r'C:\Users\Admin\projects\amazon-5461-bot\brand_packs'
ACCT = 'NNN'
SITE = 'uk'  # adjust per run

for f in glob.glob(f'{BASE}/*/docs/*_{SITE}.account_{ACCT}.txt'):
    brand = f.split(os.sep)[-3]
    text = open(f, encoding='utf-8').read()
    m = re.search(r'SKU\uff1a(\S+)', text)
    sku = m.group(1) if m else 'NOT_FOUND'
    ok = sku.startswith(f'{ACCT}-')
    print(f'[{"OK" if ok else "FAIL"}] {brand:15s} {sku}')
```
