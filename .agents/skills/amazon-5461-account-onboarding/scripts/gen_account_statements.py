"""
Generate account-specific 5461 + GTIN statement files for a new account.

Usage:
    Set ACCT, SRC_PFX, US_BRANDS, MX_BRANDS at the top, then run:
        env -u PYTHONPATH .venv/Scripts/python.exe scripts/gen_account_statements.py

Works by regex-substituting the account-number prefix inside the SKU line of each
generic statement file (full-width colon, U+FF1A).

Preconditions:
  - Generic files exist at brand_packs/<BRAND>/docs/5461_statement_<site>.txt
  - Run from the project root: projects/amazon-5461-bot/
"""
import os, re

# ── Configure here ────────────────────────────────────────────────────────────
ACCT    = '648'          # new account number
SRC_PFX = '645'          # prefix used in the generic files (check with grep SKU)

US_BRANDS = [
    'WILLONE', 'JZG', 'MP-MALL', 'XDesign', 'uShield',
    'OUNNE', 'VASG', 'JavoYion', 'MoShieldwish',
]
MX_BRANDS = ['V-PORYADKU']
# ─────────────────────────────────────────────────────────────────────────────

BASE = os.path.join(os.path.dirname(__file__), '..', 'amazon-5461-bot', 'brand_packs')

TASKS = (
    [(b, 'us') for b in US_BRANDS] +
    [(b, 'mx') for b in MX_BRANDS]
)

def _process(brand, site, ftype):
    docs = os.path.join(BASE, brand, 'docs')
    src  = os.path.join(docs, f'{ftype}_{site}.txt')
    dst  = os.path.join(docs, f'{ftype}_{site}.account_{ACCT}.txt')
    if not os.path.exists(src):
        return  # silently skip missing optional file
    content = open(src, encoding='utf-8').read()
    new_content = re.sub(
        r'(SKU\uff1a)' + re.escape(SRC_PFX) + r'(-)',
        r'\g<1>' + ACCT + r'\2',
        content,
    )
    label = f'{brand} {site.upper()} {ftype}'
    if new_content == content:
        print(f'[WARN] {label} — no substitution (src prefix {SRC_PFX!r} not found?)')
        return
    with open(dst, 'w', encoding='utf-8') as f:
        f.write(new_content)
    sku = (re.search(r'SKU\uff1a(\S+)', new_content) or type('', (), {'group': lambda s, n: '?'})()).group(1)
    print(f'[OK]   {label} -> {os.path.basename(dst)}  SKU: {sku}')

for brand, site in TASKS:
    _process(brand, site, '5461_statement')
    _process(brand, site, 'gtin_exemption_statement')

print('\nDone.')
