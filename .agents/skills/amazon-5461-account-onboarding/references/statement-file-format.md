# Statement File Format Reference

## Canonical field order and format (from 技术部日常表格_申请表.xlsx, confirmed 2026-07-19)

All account-specific statement files follow **exactly** this structure — single `\n`
line endings, full-width colon `：` (U+FF1A) on every field, no blank lines between
fields, no trailing blank lines except one final `\n`.

```
Brand：{BRAND}
Manufacturer：{BRAND}
Item name：{LOCALIZED_TITLE}
Item Category：{LOCALIZED_CATEGORY}
Color：Transparent
Item Specification：{SPEC}
Item desrciption：{LOCALIZED_DESC}
SKU：{ACCOUNT}-{SITE}-{BRAND}-{MODEL}
Item model：{MODEL}
The brand has been officially added and the brand's authorisation certificate has been uploaded.
```

**Notes:**
- `Item desrciption` — intentional misspelling, preserve it exactly.
- `Color` is always `Transparent` for all brands/sites.
- `Item Specification` is `0.6kg，14cm×17cm×3cm` for phone accessories; `44mm` for VASG (smartwatch).
- The final line has no colon/field name — it is a plain sentence.
- When reading from the spreadsheet, replace `\\n` with real `\n` before writing.

---

## Per-site localization templates (standard phone accessory brands)

### UK
- **Item name**: `{B} Screen Protector 6.10 Inch,Tempered Glass Film, 2+2Pack, HD-Clear`
- **Item Category**: `Mobile Phones & Communication›Accessories›Maintenance, Upkeep & Repairs›Screen Protectors`
- **Item desrciption**: `Screen Protector for {MODEL}  6.10 Inch,  2+2Pack, Tempered Glass Film`

### DE
- **Item name**: `{B} Schutzfolie 6.10 Zoll Schutz Glas,HD Displayschutzfolie, 2+2 Stück, 9H Folie, HD-Clear`
- **Item Category**: `Elektronik & Foto > Handys & Zubehör > Zubehör > Wartung, Instandhaltung & Reparaturen > Displayschutzfolien`
- **Item desrciption**: `Schutzfolie Mit {MODEL}  6.10 Zoll Schutz Glas, HD Displayschutzfolie, 2+2 Stück 9H Folie`

### FR
- **Item name**: `{B} Verre Trempé 6.10 Pouces Haute Sensibilité  Couverture  Protection Verre Trempé, Sans Bulle, HD-Clear 2+2 Pièces`
- **Item Category**: `High-Tech›Téléphones portables et accessoires›Accessoires téléphones portables›Entretien, maintenance et réparations›Protecteurs d'écran`
- **Item desrciption**: `Verre Trempé pour {MODEL} 6.10 Inch,2+2Pièces, Haute Sensibilité  Couverture  Protection Verre Trempé, Sans Bulle`

### ES
- **Item name**: `{B} Protector de Pantalla Compatible de 6.10 Pulgadas, Sin Burbujas, HD-Clear 2+2 piezas`
- **Item Category**: `Mobile Phones & Communication›Accessories›Maintenance, Upkeep & Repairs›Screen Protectors`
- **Item desrciption**: `Protector de Pantalla Compatible con {MODEL}  6.10 Inch,2+2piezas, Sin Burbujas, HD clear`

### IT
- **Item name**: `{B} Protezione Compatibile 6.10  Pollici, Vetro Temperato, HD-Clear 2+2 Pack`
- **Item Category**: `Mobile Phones & Communication›Accessories›Maintenance, Upkeep & Repairs›Screen Protectors`
- **Item desrciption**: `Protezione Compatibile con {MODEL} 6.10 Inch,2+2Pack, Case Friendly, Vetro Temperato`

---

## VASG special templates (smartwatch — `Item Specification: 44mm`)

### UK
- **Item name**: `VASG smartwatch screen protectors 44 mm,smart watch screen protectors, 2+2 Pack, HD-Clear`
- **Item Category**: `Electronics & Photo > Mobile Phones & Communication > Accessories > Smartwatch Accessories > Screen Protectors & Foils`
- **Item desrciption**: `smart-watch-screen-protectors for 44 mm,Smartwatch screen protectors,2+2Pack, Tempered Glass Film`

### DE
- **Item name**: `VASG smartwatch screen protectors 44 mm,HD Displayschutzfolie, 2+2 Stück, 9H Folie, HD-Clear`
- **Item Category**: `Elektronik & Foto > Handys & Zubehör > Zubehör > Smartwatch Zubehör > Schutzfolien`
- **Item desrciption**: `smart-watch-screen-protectors 44 mm, HD Displayschutzfolie, 2+2 Stück 9H Folie`

### FR
- **Item name**: `VASG protections d'écran pour montre connectée 44 mm,Film en verre trempé, 2+2 Pièces, HD-Clair`
- **Item desrciption**: `protections-d'écran-pour-montre-connectée pour 44 mm, 2+2Pack, Film en verre trempé`

### ES
- **Item name**: `VASG protectores de pantalla para smartwatch 44 mm, 2+2 piezas, película de vidrio templado, HD`
- **Item desrciption**: `smartwatch-protectores-de-pantalla para 44 mm, 2+2piezas, película de vidrio`

### IT
- **Item name**: `VASG pellicole protettive per smartwatch 44 mm, 2+2 Pack, Vetro Temperato, HD`
- **Item desrciption**: `pellicole-protettive-per-smartwatch 44 mm, 2+2Pack, Vetro Temperato`

### MX (confirmed 2026-07-20, account 648)
- **Item name**: `VASG Protector de Pantalla para Reloj Inteligente 44 mm, 2+2 piezas, HD-Clear`
- **Item Category**: `Electrónica > Celulares y Accesorios > Accesorios > Mantenimiento, Cuidados y Reparaciones > Protectores de Pantalla`
- **Item Specification**: `44mm`
- **Item desrciption**: `Protector de Pantalla para Reloj Inteligente 44 mm, 2+2 piezas, Vidrio Templado`
- **Item model**: `AL12`
- **Note**: The MX generic file (`5461_statement_mx.txt`) is **empty** — always build from this template for any new account.

### SE (Swedish) — standard phone accessory brands\n- **Item name**: `{B} Skärmskydd 6.10 Tum,Härdat glas film, 2+2Pack, HD-Klar`\n- **Item Category**: `Elektronik > Mobiler & tillbehör > Tillbehör > Underhåll, vård & reparationer > Skärmskydd`\n- **Item desrciption**: `Skärmskydd för {MODEL} 6.10 Tum, 2+2Pack, Härdat glas film`\n\n**Known SE file gaps (2026-08-01):** JZG has NO generic or account-specific SE file at all.\nAlways build JZG SE from scratch using this template with model code `Q66Q`.\nHOMEMO SE exists from account 603 onward.\nOther brands in the 7-brand EU pack — check coverage before launching:\n```bash\nls amazon-5461-bot/brand_packs/*/docs/*se* 2>/dev/null\n```\n\n---\n\n## Gap detection — find missing brand/site combinations

Run from `projects/amazon-5461-bot/` to see the coverage matrix:

```python
import os

base = 'amazon-5461-bot/brand_packs'
brands = ['HOMEMO','JZG','V-PORYADKU','VASG','WILLONE','uShield','JavoYion']
eu_sites = ['uk','de','fr','es','it']
acct = '649'   # change per account

print(f"{'Brand':15s} " + " ".join(f"{s.upper():5s}" for s in eu_sites))
for brand in brands:
    row = f"{brand:15s} "
    for site in eu_sites:
        f = f"{base}/{brand}/docs/5461_statement_{site}.account_{acct}.txt"
        row += ("Y    " if os.path.exists(f) else "---  ")
    print(row)
```

---

## Generating missing files from spreadsheet

When the spreadsheet has no row for a brand/site combo, generate from the templates
above. Assign a unique 4-char model code (e.g. `A54H`) — check the full spreadsheet
to avoid collisions.

**CRITICAL — writing multi-line Python strings with `\r\n` in them:**
Shell `echo`, heredoc (`<< 'EOF'`), and `-c "..."` ALL expand `\r` and `\n` before
the string reaches the file. The only reliable approach for embedding literal
backslash sequences is a `.py` script file written via `write_file`, then run with
`env -u PYTHONPATH .venv/Scripts/python.exe <script>.py`. This applies to any regex
or string containing `\r`, `\n`, `\s`, `\w`, etc.

```python
# Build content using join — avoids the \\n escape issue entirely
def build_content(brand, site, model, tmpl):
    sku = f"{acct}-{site}-{brand}-{model}"
    lines = [
        f"Brand：{brand}",
        f"Manufacturer：{brand}",
        f"Item name：{tmpl['item_name'](brand)}",
        f"Item Category：{tmpl['item_category']}",
        f"Color：{tmpl['color']}",
        f"Item Specification：{tmpl['spec']}",
        f"Item desrciption：{tmpl['item_desc'](model)}",
        f"SKU：{sku}",
        f"Item model：{model}",
        "The brand has been officially added and the brand's authorisation certificate has been uploaded.",
    ]
    return '\n'.join(lines) + '\n'
```
