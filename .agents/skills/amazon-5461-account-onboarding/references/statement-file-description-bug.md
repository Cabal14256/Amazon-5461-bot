# Statement File Bug — Empty Description Grabs SKU Line

## Discovery: 2026-07-19 (account 649EU, UK batch)

### Root cause

The `fill_5461_form` extraction regex in `src/flow_submit_5461.py` used `\s*(.+)`.
`\s*` matches whitespace including `\r\n`, so when the description field is blank,
the regex spans the line boundary and grabs the next non-empty line — always `SKU：...`.

**Critical nuance (confirmed 2026-07-19):** using `[^\r\n]+` alone is NOT sufficient
as a fix if `\s*` is still present before it. `\s*` will consume the `\r\n` and then
`[^\r\n]+` successfully matches the SKU line. The correct fix is to replace `\s*` with
`[^\S\r\n]*` (horizontal whitespace only — matches spaces/tabs but not newlines):

```python
# WRONG — \s* crosses lines:
re.search(r"Item des\w+[：:]\s*([^\r\n]+)", text)
# → still grabs SKU line when description is empty

# CORRECT — [^\S\r\n]* stays on the same line:
re.search(r"Item des\w+[：:][^\S\r\n]*([^\r\n]+)", text)
# → returns empty string for blank field; use fallback
```

### Fix applied (2026-07-19) to `src/flow_submit_5461.py`

Both the Listing Approval path (`_extract` helper, line ~1922) and the cat_auth_mo
path (`item_name_match`, line ~2108) were patched:

- `\s*(.+)` → `[^\S\r\n]*([^\r\n]+)`
- Empty match now falls back to `product_title_val` (Listing Approval path) or
  `f"{brand_name} Screen Protector"` (cat_auth_mo path).

The fix was applied as binary-level substitution using `open(path,'rb').read()` and
`data.replace(old_bytes, new_bytes)` — shell heredoc and `-c` string passing both
expand `\r\n` before reaching the file. Use a `.py` script file to avoid this.

### Generic template files fixed (2026-07-19)

Empty `Item desrciption：` fields in generic (non-account-specific) templates were
back-filled with the Item name value from the same file using:

```python
import re, os, glob

for f in glob.glob('amazon-5461-bot/brand_packs/*/docs/5461_statement_*.txt'):
    if 'account_' in f: continue
    text = open(f, encoding='utf-8').read()
    m_empty = re.search(r'(Item\s+des\w+\uff1a)\s*\r?\n', text)
    if not m_empty: continue
    m_name = re.search(r'Item name\uff1a\s*(.+)', text)
    item_name = m_name.group(1).strip() if m_name else 'Screen Protector'
    new_text = re.sub(r'(Item\s+des\w+\uff1a)\s*\r?\n', f'\\g<1>{item_name}\n', text)
    open(f, 'w', encoding='utf-8').write(new_text)
```

16 generic templates were fixed (HOMEMO de/fr/es/it × 2 types; V-PORYADKU/WILLONE/
uShield/JavoYion uk × 2 types).

### Account-649 statement files (50 files)

Generated directly from the spreadsheet (`技术部日常表格_申请表.xlsx`) rather than
from the generic template. This gave correct brand-specific SKU model codes and proper
descriptions for all brands across UK/DE/FR/ES/IT sites.

Key SKU differences vs. generic template (649 vs. prior default):
- VASG UK: `D11D` (not generic `XXX`)
- V-PORYADKU UK: `C11C` (not `BX98`)
- WILLONE UK: `D11D` (not `DC67`)
- uShield UK: `Q66Q` (not `DQ57`)
- JavoYion UK: `A00A` (not `XY11`)

---

### Affected generic statement files (confirmed 2026-07-19, before fix)

**UK — `Item desrciption：` present but value was empty:**

| Brand | Wrong description was filled |
|-------|-----------------------------|
| V-PORYADKU | `SKU：603-UK-V-PORYADKU-BX98` |
| WILLONE    | `SKU：603-UK-WILLONE-DC67` |
| uShield    | `SKU：603-UK-uShield-DQ57` |
| JavoYion   | `SKU：603-UK-JavoYion-XY11` |

**DE/FR/ES/IT — same pattern (HOMEMO only):**

| Brand  | Sites          | Wrong description |
|--------|----------------|-------------------|
| HOMEMO | de, es, fr, it | e.g. `SKU：571-DE-HOMEMO-MF56` |

**VASG MX — no description line at all:** fell back to `VASG Screen Protector`.

### Files confirmed OK (real description content present, unchanged)

HOMEMO uk/us/be/nl/se/mx · JZG uk/us/be/nl/mx · V-PORYADKU us/be/nl/se/mx ·
VASG uk/us/be/nl · WILLONE us/be/mx · uShield us/be/nl/se/mx · JavoYion us/be/nl/mx

### Impact

Amazon accepted all submissions with the wrong description — no Case IDs were
rejected. Content-quality issue only, not a functional blocker.

---

## Audit script

Run from `projects/amazon-5461-bot/` to find all problem files across all brands:

```python
import re, glob

brands = ['HOMEMO','JZG','V-PORYADKU','VASG','WILLONE','uShield','JavoYion']

for brand in brands:
    for f in sorted(glob.glob(f'amazon-5461-bot/brand_packs/{brand}/docs/5461_statement_*.txt')):
        if 'account_' in f:
            continue
        site = re.search(r'statement_(\w+)\.txt', f).group(1)
        text = open(f, encoding='utf-8').read()
        # Use correct horizontal-whitespace regex
        m = re.search(r'Item\s+des\w+[：:][^\S\r\n]*([^\r\n]*)', text, re.IGNORECASE)
        if not m:
            print(f'MISSING  {brand:15s} {site}')
        elif not m.group(1).strip():
            print(f'EMPTY    {brand:15s} {site}  (will fallback to item_name)')
        elif re.match(r'SKU[：:]', m.group(1).strip()):
            print(f'SKU_LEAK {brand:15s} {site}  -> {m.group(1).strip()[:50]}')
        # else: OK
```
