#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
按账号号段自动适配品牌文案，并在需要时自动补 accounts.json。

能力：
1. 读取 runtime/private/templates/ 中的 5461信息模版.xlsx
2. 根据品牌找到模板文案行
3. 将文案中的账号号段 / SKU 自动替换为目标账号
4. 回写到 brand_packs/<brand>/docs/ 下
5. 账号不存在时，自动补 accounts.json（优先尝试从 AdsPower 扫描匹配 profile）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

import openpyxl

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    from src.config_loader import resolve_accounts_path
except Exception:
    def resolve_accounts_path(default_path):
        private_path = ROOT / "runtime" / "private" / "accounts.json" if "ROOT" in globals() else Path("runtime/private/accounts.json")
        return private_path if private_path.exists() else Path(default_path)

try:
    from src.email_resolver import extract_email_from_profile
except Exception:
    def extract_email_from_profile(profile):
        return ""

ROOT = Path(__file__).resolve().parent
BRAND_PACKS_ROOT = ROOT / "brand_packs"
PICTURE_ROOT = ROOT / "picture"
CONFIG_DIR = ROOT / "config"
ACCOUNTS_JSON_PATH = resolve_accounts_path(CONFIG_DIR / "accounts.json")
SETTINGS_YAML_PATH = CONFIG_DIR / "settings.yaml"
PRIVATE_TEMPLATE_DIR = ROOT / "runtime" / "private" / "templates"
WORKBOOK_PATH = next(PRIVATE_TEMPLATE_DIR.glob("5461*.xlsx"), None) or next(ROOT.glob("5461*.xlsx"), None)
LEGACY_WORKBOOK_PATH = next((ROOT / "_scratch").glob("5461*.xlsx"), None)
_PROFILE_CACHE: dict[str, Any] = {"loaded": False, "profiles": []}

# EU 国家特定的 Item Category 映射（本地语言）
ITEM_CATEGORIES_BY_SITE = {
    "UK": "Electronics & Photo > Mobile Phones & Communication > Accessories > Maintenance, Upkeep & Repairs > Screen Protectors",
    "BE": "High-tech > Téléphones portables et communication > Accessoires > Maintenance, entretien et réparations > Protections d'écran",
    "NL": "Elektronica > Mobiele telefoons & communicatie > Accessoires > Onderhoud & reparaties > Schermbeschermers",
    "SE": "Elektronik > Mobiler & tillbehör > Tillbehör > Underhåll, vård & reparationer > Skärmskydd",
    "US": "Electronics > Accessories & Supplies > Cell Phone Accessories > Maintenance, Upkeep & Repairs > Screen Protectors",
    "DE": "Elektronik > Handy- & Kommunikationszubehör > Zubehör > Wartung, Pflege & Reparaturen > Displayschutzfolien",
    "FR": "High-tech > Téléphones portables et accessoires > Accessoires > Entretien, maintenance et réparations > Protections d'écran",
    "ES": "Electrónica > Accesorios para móviles y comunicación > Accesorios > Mantenimiento, cuidado y reparaciones > Protectores de pantalla",
    "IT": "Elettronica > Accessori per cellulari e comunicazione > Accessori > Manutenzione, cura e riparazioni > Pellicole protettive per schermo",
    "MX": "Electrónica > Celulares y Accesorios > Accesorios > Mantenimiento, Cuidados y Reparaciones > Protectores de Pantalla",
}

# 常见短语的翻译映射
TRANSLATIONS = {
    "Screen Protector": {
        "BE": "Protections d'écran",
        "NL": "Schermbeschermer",
        "SE": "Skärmskydd",
        "DE": "Displayschutzfolie",
        "FR": "Protection d'écran",
        "ES": "Protector de pantalla",
        "IT": "Pellicola protettiva per schermo",
    },
    "Tempered Glass": {
        "BE": "Verre trempé",
        "NL": "Gehard glas",
        "SE": "Härdat glas",
        "DE": "Panzerglas",
        "FR": "Verre trempé",
        "ES": "Cristal templado",
        "IT": "Vetro temperato",
    },
    "Tempered Glass Film": {
        "BE": "Film en verre trempé",
        "NL": "Gehard glas film",
        "SE": "Härdat glas film",
        "DE": "Panzerglas Folie",
        "FR": "Film en verre trempé",
        "ES": "Película de cristal templado",
        "IT": "Pellicola in vetro temperato",
    },
    "for iPhone": {
        "BE": "pour iPhone",
        "NL": "voor iPhone",
        "SE": "för iPhone",
        "DE": "für iPhone",
        "FR": "pour iPhone",
        "ES": "para iPhone",
        "IT": "per iPhone",
    },
    "for Samsung": {
        "BE": "pour Samsung",
        "NL": "voor Samsung",
        "SE": "för Samsung",
        "DE": "für Samsung",
        "FR": "pour Samsung",
        "ES": "para Samsung",
        "IT": "per Samsung",
    },
    " for ": {
        "BE": " pour ",
        "NL": " voor ",
        "SE": " för ",
        "DE": " für ",
        "FR": " pour ",
        "ES": " para ",
        "IT": " per ",
    },
    "HD-Clear": {
        "BE": "HD-Clair",
        "NL": "HD-Helder",
        "SE": "HD-Klar",
        "DE": "HD-Klar",
        "FR": "HD-Clair",
        "ES": "HD-Claro",
        "IT": "HD-Chiaro",
    },
    "HD Clear": {
        "BE": "Clarté HD",
        "NL": "HD Helder",
        "SE": "HD Klar",
        "DE": "HD Klar",
        "FR": "Clarté HD",
        "ES": "Claridad HD",
        "IT": "Chiarezza HD",
    },
    "smart-watch-screen-protectors": {
        "BE": "protections-d'écran-pour-montre-connectée",
        "NL": "smartwatch-schermbeschermers",
        "SE": "smartwatch-skärmskydd",
        "DE": "smartwatch-displayschutzfolien",
        "FR": "protections-d'écran-pour-montre-connectée",
        "ES": "protectores-de-pantalla-para-smartwatch",
        "IT": "pellicole-protettive-per-smartwatch",
    },
    "smartwatch screen protectors": {
        "BE": "protections d'écran pour montre connectée",
        "NL": "smartwatch schermbeschermers",
        "SE": "smartwatch skärmskydd",
        "DE": "Smartwatch Displayschutzfolien",
        "FR": "protections d'écran pour montre connectée",
        "ES": "protectores de pantalla para smartwatch",
        "IT": "pellicole protettive per smartwatch",
    },
    "2+2 Pack": {
        "BE": "Pack 2+2",
        "NL": "2+2 Pack",
        "SE": "2+2 Pack",
        "DE": "2+2 Pack",
        "FR": "Pack 2+2",
        "ES": "Pack 2+2",
        "IT": "Pack 2+2",
    },
    "Inch": {
        "BE": "Pouce",
        "NL": "Inch",
        "SE": "Tum",
        "DE": "Zoll",
        "FR": "Pouce",
        "ES": "Pulgada",
        "IT": "Pollice",
    },
    "Compatible": {
        "BE": "Compatible",
        "NL": "Compatibel",
        "SE": "Kompatibel",
        "DE": "Kompatibel",
        "FR": "Compatible",
        "ES": "Compatible",
        "IT": "Compatibile",
    },
}


def normalize_account_id(account: str) -> str:
    account = str(account).strip()
    if account.startswith("us_store_"):
        return account
    m = re.search(r"(\d{3,})", account)
    if not m:
        raise ValueError(f"无法从账号中提取数字: {account}")
    return f"us_store_{m.group(1)}"


def normalize_account_num(account: str) -> str:
    m = re.search(r"(\d{3,})", str(account))
    if not m:
        raise ValueError(f"无法从账号中提取数字: {account}")
    return m.group(1)


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_accounts_payload() -> dict[str, Any]:
    payload = load_json(ACCOUNTS_JSON_PATH)
    payload.setdefault("accounts", [])
    return payload


def find_account(account_id: str) -> dict[str, Any] | None:
    payload = load_accounts_payload()
    for acc in payload["accounts"]:
        if acc.get("account_id") == account_id:
            return acc
    return None


def load_settings_api() -> tuple[str, str]:
    try:
        import yaml

        settings = yaml.safe_load(SETTINGS_YAML_PATH.read_text(encoding="utf-8")) or {}
        adsp = settings.get("adspower", {})
        return adsp.get("api_base_url", "http://local.adspower.net:50325"), adsp.get("api_key", "") or ""
    except Exception:
        return "http://local.adspower.net:50325", ""


def detect_template_sheet(wb: openpyxl.Workbook):
    for ws in wb.worksheets:
        headers = [ws.cell(6, c).value for c in range(1, 8)]
        if headers[:7] == ["区域", "站点", "国家", "品牌", "SKU", "标题", "发信内容"]:
            return ws
    raise ValueError("未找到 5461 文案模板工作表")


def workbook_paths_check() -> Path:
    if WORKBOOK_PATH is None or not WORKBOOK_PATH.exists():
        raise FileNotFoundError("未找到 runtime/private/templates/5461信息模版.xlsx")
    return WORKBOOK_PATH


def normalize_site(site: str | None) -> str | None:
    if site is None:
        return None
    site = str(site).strip().upper()
    # 支持 EU 国家代码
    valid_sites = {"US", "UK", "DE", "MX", "BE", "NL", "SE", "FR", "ES", "IT", "CA", "AU", "JP"}
    if site in valid_sites:
        return site
    # 兼容旧值：EU 默认映射到 UK
    if site == "EU":
        return "UK"
    return site if site else None


def load_legacy_rows(brand_name: str) -> dict[str, dict[str, Any]]:
    if LEGACY_WORKBOOK_PATH is None or not LEGACY_WORKBOOK_PATH.exists():
        return {}

    wb = openpyxl.load_workbook(LEGACY_WORKBOOK_PATH, data_only=True)
    ws = wb[wb.sheetnames[0]]
    rows: dict[str, dict[str, Any]] = {}

    for row_idx in range(2, ws.max_row + 1):
        country = ws.cell(row_idx, 5).value
        brand = ws.cell(row_idx, 6).value
        sku = ws.cell(row_idx, 8).value
        title = ws.cell(row_idx, 9).value
        eu_content = ws.cell(row_idx, 10).value
        us_content = ws.cell(row_idx, 11).value

        if not brand or str(brand).lower() != brand_name.lower() or not country:
            continue

        rows[str(country).upper()] = {
            "sku": str(sku or ""),
            "title": str(title or ""),
            "content": str(us_content or eu_content or ""),
        }

    return rows


def load_brand_rows(brand_name: str) -> tuple[list[dict[str, Any]], str, Path]:
    workbook_path = workbook_paths_check()
    wb = openpyxl.load_workbook(workbook_path, data_only=True)
    ws = detect_template_sheet(wb)
    legacy_rows = load_legacy_rows(brand_name)

    template_account_label = str(ws["B1"].value or "")
    rows: list[dict[str, Any]] = []
    for row_idx in range(7, ws.max_row + 1):
        country = ws.cell(row_idx, 3).value
        brand = ws.cell(row_idx, 4).value
        sku = ws.cell(row_idx, 5).value
        title = ws.cell(row_idx, 6).value
        content = ws.cell(row_idx, 7).value
        region = ws.cell(row_idx, 1).value
        site = ws.cell(row_idx, 2).value

        if not brand:
            continue
        if str(brand).lower() != brand_name.lower():
            continue

        country_key = str(country or "").upper()
        legacy = legacy_rows.get(country_key, {})

        rows.append(
            {
                "row": row_idx,
                "region": region,
                "site": site,
                "country": country,
                "brand": str(brand),
                "sku": str(sku or legacy.get("sku") or ""),
                "title": str(title or legacy.get("title") or ""),
                "content": str(content or legacy.get("content") or ""),
            }
        )

    if not rows:
        raise ValueError(f"模板里找不到品牌: {brand_name}")

    return rows, template_account_label, workbook_path


def pick_best_row(rows: list[dict[str, Any]], site: str | None = None) -> dict[str, Any]:
    normalized_site = normalize_site(site)

    def score(row: dict[str, Any]) -> tuple[int, int, int, int, int]:
        country = str(row.get("country") or "").upper()
        has_content = 1 if row.get("content") else 0
        has_sku = 1 if row.get("sku") else 0
        site_match = 1 if normalized_site and country == normalized_site else 0
        # 如果指定了 site 且 country 为空但有内容，视为通用行（次优先）
        generic_match = 1 if normalized_site and not country and (has_content or has_sku) else 0
        return (
            site_match,
            generic_match,
            3 if country == "US" else 2 if country in {"CA", "MX"} else 1,
            has_content,
            has_sku,
        )

    if normalized_site:
        # 优先精确匹配指定站点且有内容的行
        site_rows = [row for row in rows if str(row.get("country") or "").upper() == normalized_site]
        if site_rows:
            # 优先选择 content 更完整的行（有 Item Category 和 Item description）
            def content_completeness(row):
                content = row.get("content") or ""
                has_category = "Item Category：" in content and "Item Category：\n" not in content
                has_description = "Item desrciption：" in content and "Item desrciption：\n" not in content
                # 有 category 和 description 的行优先
                return (has_category, has_description, len(content))

            # 先按内容完整性排序，再按 score 排序
            site_rows_sorted = sorted(site_rows, key=lambda r: (content_completeness(r), score(r)), reverse=True)
            best = site_rows_sorted[0]
            # 如果精确匹配行的 content 为空，fallback 到其他有内容的行再做站点适配
            # 这覆盖了 Excel 里目标站点行存在但数据为空的情况（如 VASG MX）
            if not (best.get("content") or best.get("sku")):
                fallback_rows = [r for r in rows if (r.get("content") or r.get("sku")) and str(r.get("country") or "").upper() != normalized_site]
                if fallback_rows:
                    # 优先选 US 行（内容最完整），其次任意有内容的行
                    fallback_rows.sort(key=lambda r: (str(r.get("country") or "").upper() == "US", len(r.get("content") or "")), reverse=True)
                    return fallback_rows[0]
            return best
        # 如果没有精确匹配，检查是否有通用行（country为空但有内容）
        generic_rows = [row for row in rows if not str(row.get("country") or "").strip() and (row.get("content") or row.get("sku"))]
        if generic_rows:
            return sorted(generic_rows, key=score, reverse=True)[0]

    # 当 score 相同时，优先选择 content 更长的行（数据更完整）
    return sorted(rows, key=lambda r: (score(r), len(r.get("content", "")), len(r.get("sku", ""))), reverse=True)[0]


def replace_account_in_text(text: str, target_account_num: str, template_account_num: str | None, target_site: str | None = None) -> str:
    out = text or ""

    if template_account_num:
        out = re.sub(rf"\b{re.escape(template_account_num)}-(?=[A-Z]{{2}}-)", f"{target_account_num}-", out)
        out = out.replace(f"正常号-US-{template_account_num}号", f"正常号-US-{target_account_num}号")

    out = re.sub(r"\b\d{3,}-(?=[A-Z]{2}-)", f"{target_account_num}-", out)
    out = re.sub(r"(?im)(SKU[:：]\s*)(\d+)-", rf"\g<1>{target_account_num}-", out)

    # 关键修复：同时替换站点代码（如 UK→US），避免生成 570-UK-... 的错误 SKU
    if target_site:
        target_site = target_site.strip().upper()
        # 匹配 -XX- 格式的站点代码（排除已正确匹配的目标站点）
        # 支持 2-3 位国家代码（如 UK, BE, NL, SE, US, DE, MX）
        site_pattern = r"-([A-Z]{2,3})-(?=[A-Za-z])"

        def replace_site(match):
            current_site = match.group(1)
            if current_site != target_site:
                return f"-{target_site}-"
            return match.group(0)

        out = re.sub(site_pattern, replace_site, out)
    return out


def translate_phrases(text: str, site: str) -> str:
    """将常见英文短语翻译为目标国家语言"""
    if not site or site.upper() in ("US", "UK", "CA", "AU"):
        return text
    
    site = site.upper()
    out = text
    
    # 按短语长度降序排序，避免短匹配干扰长匹配
    sorted_phrases = sorted(TRANSLATIONS.keys(), key=len, reverse=True)
    
    for phrase in sorted_phrases:
        translations = TRANSLATIONS.get(phrase, {})
        translation = translations.get(site)
        if translation:
            # 支持大小写不敏感替换，但保留原始大小写模式
            out = re.sub(re.escape(phrase), translation, out, flags=re.IGNORECASE)
    
    return out


def adapt_content_for_site(content: str, site: str, brand_name: str) -> str:
    """
    根据目标站点适配文案内容：
    1. 替换 Item Category 为对应国家语言
    2. 翻译 Item name 和 Item description 中的常见短语
    """
    if not site:
        return content
    
    site = site.upper()
    out = content
    
    # 1. 替换 Item Category
    if site in ITEM_CATEGORIES_BY_SITE:
        category = ITEM_CATEGORIES_BY_SITE[site]
        # 匹配 Item Category：xxx 或 Item Category：\n
        out = re.sub(
            r"Item Category：.*?(?=\n|$)",
            f"Item Category：{category}",
            out
        )
    
    # 2. 翻译 Item name
    item_name_match = re.search(r"Item name：(.+?)(?=\n|$)", out)
    if item_name_match:
        original_name = item_name_match.group(1)
        translated_name = translate_phrases(original_name, site)
        if translated_name != original_name:
            out = out.replace(f"Item name：{original_name}", f"Item name：{translated_name}")
    
    # 3. 翻译 Item description
    item_desc_match = re.search(r"Item desrciption：(.+?)(?=\n|$)", out)
    if item_desc_match:
        original_desc = item_desc_match.group(1)
        # 如果 description 为空，不处理
        if original_desc.strip():
            translated_desc = translate_phrases(original_desc, site)
            if translated_desc != original_desc:
                out = out.replace(f"Item desrciption：{original_desc}", f"Item desrciption：{translated_desc}")
    
    return out


def normalize_uk_statement_description(content: str, brand_name: str) -> str:
    """Return UK statement text with the canonical non-empty description field."""
    model_match = re.search(r"(?im)^Item model[：:]\s*([^\r\n]+)", content)
    model = model_match.group(1).strip() if model_match else "XXX"
    if brand_name.upper() == "VASG":
        description = (
            "smart-watch-screen-protectors for 44 mm,Smartwatch screen "
            "protectors,2+2Pack, Tempered Glass Film"
        )
    else:
        description = (
            f"Screen Protector for {model}  6.10 Inch,  2+2Pack, "
            "Tempered Glass Film"
        )

    replacement = f"Item desrciption：{description}"
    description_line = r"(?im)^Item\s+des(?:cription|rciption)[：:][^\r\n]*"
    if re.search(description_line, content):
        return re.sub(description_line, replacement, content, count=1)

    sku_line = r"(?im)^(SKU[：:])"
    if re.search(sku_line, content):
        return re.sub(sku_line, replacement + "\n" + r"\1", content, count=1)
    return content.rstrip() + "\n" + replacement + "\n"


def update_manifest_site_statement_files(brand_name: str, site: str) -> None:
    """Ensure manifest routes a site to its site-specific statement files.

    Brand packs historically had some MX mappings pointing to *_us.txt even
    after mx documents were generated.  That caused Add Product / 5461 to use
    English titles on Mexico runs.  Keep the manifest aligned whenever we sync
    a brand pack for a requested site.
    """
    normalized_site = normalize_site(site)
    if not normalized_site:
        return

    manifest_path = BRAND_PACKS_ROOT / brand_name / "manifest.json"
    if not manifest_path.exists():
        return

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return

    site_key = normalized_site.upper()
    site_suffix = site_key.lower()
    changed = False

    for section, prefix in (
        ("5461", "5461_statement"),
        ("gtin_exemption", "gtin_exemption_statement"),
    ):
        section_obj = manifest.setdefault(section, {})
        statement_files = section_obj.setdefault("statement_files", {})
        desired = f"docs/{prefix}_{site_suffix}.txt"
        if statement_files.get(site_key) != desired:
            statement_files[site_key] = desired
            changed = True

    # Keep supported_marketplaces in sync whenever we write a site-specific
    # statement file. Historically this list was only maintained by hand,
    # which let statement_files/supported_marketplaces drift apart (see
    # brand-pack-manifest-pitfalls.md) — the batch script reads
    # statement_files directly so the brand still ran, but audits kept
    # finding false "unsupported site" gaps. Fix at the source: whichever
    # site we just synced a statement file for should always be listed.
    supported = manifest.setdefault("supported_marketplaces", [])
    if site_key not in supported:
        supported.append(site_key)
        changed = True

    if changed:
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_source_payload(brand: str, target_account_num: str, workbook_path: Path, selected_row: dict[str, Any], template_account_label: str) -> dict[str, Any]:
    return {
        "brand": brand,
        "target_account": target_account_num,
        "workbook": str(workbook_path.relative_to(ROOT)).replace('\\', '/'),
        "template_account_label": template_account_label,
        "selected_row": selected_row,
        "note": "文案来自本地私密模板 5461信息模版.xlsx，并将账号号段/SKU 自动替换为目标账号。",
    }


def ensure_dirs(brand_name: str) -> tuple[Path, Path, Path]:
    pack_dir = BRAND_PACKS_ROOT / brand_name
    docs_dir = pack_dir / "docs"
    images_dir = pack_dir / "images"
    attachments_dir = pack_dir / "attachments"
    docs_dir.mkdir(parents=True, exist_ok=True)
    images_dir.mkdir(parents=True, exist_ok=True)
    attachments_dir.mkdir(parents=True, exist_ok=True)
    return pack_dir, docs_dir, images_dir


def load_existing_site_statement(brand_name: str, site: str) -> tuple[str, Path] | None:
    """Return a non-empty existing statement for a brand/site fallback.

    Some workbook rows exist only as placeholders.  Reusing a previously
    validated same-site statement is safer than overwriting the generic and
    account-specific files with an empty document.
    """
    docs_dir = BRAND_PACKS_ROOT / brand_name / "docs"
    site_suffix = site.lower()
    generic = docs_dir / f"5461_statement_{site_suffix}.txt"
    account_files = sorted(
        docs_dir.glob(f"5461_statement_{site_suffix}.account_*.txt"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in [generic, *account_files]:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8-sig")
        if text.strip() and re.search(r"(?im)^SKU[：:]\s*\d{3,}-[A-Z]{2,3}-", text):
            return text, path
    return None


def sync_brand_pack_for_account(brand_name: str, target_account: str, site: str | None = None) -> dict[str, Any]:
    target_account_num = normalize_account_num(target_account)
    normalized_site = normalize_site(site)

    try:
        rows, template_account_label, workbook_path = load_brand_rows(brand_name)
        selected_row = pick_best_row(rows, site=normalized_site)

        template_account_num = None
        m = re.search(r"(\d{3,})", template_account_label)
        if m:
            template_account_num = m.group(1)

        adapted_content = replace_account_in_text(selected_row.get("content", ""), target_account_num, template_account_num, target_site=normalized_site)
        adapted_sku = replace_account_in_text(selected_row.get("sku", ""), target_account_num, template_account_num, target_site=normalized_site)
        
        # 根据目标站点适配文案（Item Category 替换、翻译）
        adapted_content = adapt_content_for_site(adapted_content, normalized_site, brand_name)
        if normalized_site and not adapted_content.strip():
            existing = load_existing_site_statement(brand_name, normalized_site)
            if existing:
                existing_text, existing_path = existing
                adapted_content = replace_account_in_text(
                    existing_text,
                    target_account_num,
                    template_account_num=None,
                    target_site=normalized_site,
                )
                sku_match = re.search(r"(?im)^SKU[：:]\s*([^\r\n]+)", adapted_content)
                adapted_sku = sku_match.group(1).strip() if sku_match else adapted_sku
                source_payload_note = (
                    "目标站点工作簿文案为空；已复用同品牌、同站点的非空声明模板并替换账号号段。"
                )
            else:
                source_payload_note = "目标站点工作簿文案为空，且没有可复用的同站点声明模板。"
        selected_country = selected_row.get("country")
        source_payload = build_source_payload(selected_row["brand"], target_account_num, workbook_path, selected_row, template_account_label)
        source_payload["requested_site"] = normalized_site
        if 'source_payload_note' in locals():
            source_payload["note"] = source_payload_note
    except ValueError:
        fallback_site = normalized_site or "US"
        selected_row = {"brand": brand_name, "country": fallback_site}
        selected_country = fallback_site
        adapted_sku = f"{target_account_num}-{fallback_site}-{brand_name}-XXX"
        adapted_content = (
            f"Brand：{brand_name}\n"
            f"Manufacturer：{brand_name}\n"
            f"SKU：{adapted_sku}\n"
            f"Item model：XXX\n\n"
            "The brand has been officially added and the brand's authorisation certificate has been uploaded.\n"
        )
        source_payload = {
            "brand": brand_name,
            "target_account": target_account_num,
            "workbook": str(WORKBOOK_PATH.relative_to(ROOT)).replace('\\', '/') if WORKBOOK_PATH else None,
            "requested_site": normalized_site,
            "note": "模板中未找到该品牌，已生成通用兜底文案。",
        }

    if normalized_site == "UK":
        adapted_content = normalize_uk_statement_description(adapted_content, brand_name)

    _pack_dir, docs_dir, _images_dir = ensure_dirs(selected_row["brand"])

    # 根据站点生成文件名（支持多语言）
    site_suffix = normalized_site.lower() if normalized_site else "us"
    generic_5461 = docs_dir / f"5461_statement_{site_suffix}.txt"
    generic_gtin = docs_dir / f"gtin_exemption_statement_{site_suffix}.txt"
    account_5461 = docs_dir / f"5461_statement_{site_suffix}.account_{target_account_num}.txt"
    account_gtin = docs_dir / f"gtin_exemption_statement_{site_suffix}.account_{target_account_num}.txt"
    source_json = docs_dir / "source_row.json"

    for path in [generic_5461, generic_gtin, account_5461, account_gtin]:
        path.write_text(adapted_content.strip() + "\n", encoding="utf-8")

    if normalized_site:
        update_manifest_site_statement_files(selected_row["brand"], normalized_site)

    source_json.write_text(
        json.dumps(source_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    return {
        "brand": selected_row["brand"],
        "target_account": target_account_num,
        "selected_country": selected_country,
        "requested_site": normalized_site,
        "sku": adapted_sku,
        "docs_written": [
            str(generic_5461.relative_to(ROOT)).replace('\\', '/'),
            str(generic_gtin.relative_to(ROOT)).replace('\\', '/'),
            str(account_5461.relative_to(ROOT)).replace('\\', '/'),
            str(account_gtin.relative_to(ROOT)).replace('\\', '/'),
            str(source_json.relative_to(ROOT)).replace('\\', '/'),
        ],
    }


def clean_text(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text


# 全站点模板配置（用于 marketplace_configs 自动生成）
MARKETPLACE_CONFIGS: dict[str, dict[str, Any]] = {
    "US": {
        "marketplace": "US",
        "domain": "amazon.com",
        "mons_sel_mkid": "",
        "entry_url": "https://sellercentral.amazon.com/abis/listing/create/product_identity?productType=SCREEN_PROTECTOR&itemType=cell-phone-screen-protectors&displayPath=Electronics%2FAccessories+%26+Supplies%2FCell+Phone+Accessories%2FMaintenance%2C+Upkeep+%26+Repairs%2FScreen+Protectors#product_identity",
        "item_type_keyword": "Electronics > Accessories & Supplies > Cell Phone Accessories > Maintenance, Upkeep & Repairs > Screen Protectors",
    },
    "MX": {
        "marketplace": "MX",
        "domain": "amazon.com",
        "mons_sel_mkid": "amzn1.mp.o.A1AM78C64UM0Y8",
        "entry_url": "https://sellercentral.amazon.com/abis/listing/create/product_identity?productType=SCREEN_PROTECTOR&productData=%7B%22sparseText%22%3A%22screen+protector%22%7D&recommendedBrowseNodeId=9687448011&displayPath=Electr%C3%B3nicos%2FCelulares+y+Accesorios%2FAccesorios%2FMantenimiento%2C+Cuidados+y+Reparaciones%2FProtectores+de+Pantalla#product_identity",
        "item_type_keyword": "Electrónicos > Celulares y Accesorios > Accesorios > Mantenimiento, Cuidados y Reparaciones > Protectores de Pantalla",
    },
    "UK": {
        "marketplace": "UK",
        "domain": "amazon.co.uk",
        "mons_sel_mkid": "amzn1.mp.o.A1F83G8C2ARO7P",
        "entry_url": "https://sellercentral.amazon.co.uk/abis/listing/create/product_identity?productType=SCREEN_PROTECTOR&productData=%7B%22sparseText%22%3A%22screen+protector%22%7D&recommendedBrowseNodeId=329083031&displayPath=Electronics+%26+Photo%2FMobile+Phones+%26+Communication%2FAccessories%2FMaintenance%2C+Upkeep+%26+Repairs%2FScreen+Protectors#product_identity",
        "item_type_keyword": "Electronics & Photo > Mobile Phones & Communication > Accessories > Maintenance, Upkeep & Repairs > Screen Protectors",
    },
    "BE": {
        "marketplace": "BE",
        "domain": "amazon.co.uk",
        "mons_sel_mkid": "amzn1.mp.o.AMEN7PMS3EDWL",
        "entry_url": "https://sellercentral.amazon.co.uk/abis/listing/create/product_identity?productType=SCREEN_PROTECTOR&productData=%7B%22sparseText%22%3A%22screen+protector%22%7D&recommendedBrowseNodeId=27863027031&displayPath=High-tech%2FT%C3%A9l%C3%A9phones+portables+et+communication%2FAccessoires%2FMaintenance%2C+entretien+et+r%C3%A9parations%2FProtections+d%27%C3%A9cran#product_identity",
        "item_type_keyword": "High-tech > Téléphones portables et communication > Accessoires > Maintenance, entretien et réparations > Protections d'écran",
    },
    "NL": {
        "marketplace": "NL",
        "domain": "amazon.co.uk",
        "mons_sel_mkid": "amzn1.mp.o.A1805IZSGTT6HS",
        "entry_url": "https://sellercentral.amazon.co.uk/abis/listing/create/product_identity?productType=SCREEN_PROTECTOR&productData=%7B%22sparseText%22%3A%22screen+protector%22%7D&recommendedBrowseNodeId=16366238031&displayPath=Elektronica%2FMobiele+telefoons+%26+communicatie%2FAccessoires%2FOnderhoud+%26+reparaties%2FSchermbeschermers#product_identity",
        "item_type_keyword": "Elektronica > Mobiele telefoons & communicatie > Accessoires > Onderhoud & reparaties > Schermbeschermers",
    },
    "SE": {
        "marketplace": "SE",
        "domain": "amazon.co.uk",
        "mons_sel_mkid": "amzn1.mp.o.A2NODRKZP88ZB9",
        "entry_url": "https://sellercentral.amazon.co.uk/abis/listing/create/product_identity?productType=SCREEN_PROTECTOR&productData=%7B%22sparseText%22%3A%22screen+protector%22%7D&recommendedBrowseNodeId=20637844031&displayPath=Elektronik%2FMobiler+%26+tillbeh%C3%B6r%2FTillbeh%C3%B6r%2FUnderh%C3%A5ll%2C+v%C3%A5rd+%26+reparationer%2FSk%C3%A4rmskydd#product_identity",
        "item_type_keyword": "Elektronik > Mobiler & tillbehör > Tillbehör > Underhåll, vård & reparationer > Skärmskydd",
    },
    "DE": {
        "marketplace": "DE",
        "domain": "amazon.co.uk",
        "mons_sel_mkid": "amzn1.mp.o.A1PA6795UKMFR9",
        "entry_url": "https://sellercentral.amazon.co.uk/abis/listing/create/product_identity?productType=SCREEN_PROTECTOR&productData=%7B%22sparseText%22%3A%22screen+protector%22%7D&recommendedBrowseNodeId=364934031&displayPath=Elektronik+%26+Foto%2FHandys+%26+Zubeh%C3%B6r%2FZubeh%C3%B6r%2FWartung%2C+Instandhaltung+%26+Reparaturen%2FDisplayschutzfolien#product_identity",
        "item_type_keyword": "Elektronik & Foto > Handys & Zubehör > Zubehör > Wartung, Instandhaltung & Reparaturen > Displayschutzfolien",
    },
    "FR": {
        "marketplace": "FR",
        "domain": "amazon.co.uk",
        "mons_sel_mkid": "amzn1.mp.o.A13V1IB3VIYZZH",
        "entry_url": "https://sellercentral.amazon.co.uk/abis/listing/create/product_identity?productType=SCREEN_PROTECTOR&productData=%7B%22sparseText%22%3A%22screen+protector%22%7D&recommendedBrowseNodeId=339918011&displayPath=High-Tech%2FT%C3%A9l%C3%A9phones+portables+et+accessoires%2FAccessoires+t%C3%A9l%C3%A9phones+portables%2FEntretien%2C+maintenance+et+r%C3%A9parations%2FProtecteurs+d%27%C3%A9cran#product_identity",
        "item_type_keyword": "High-Tech > Téléphones portables et accessoires > Accessoires téléphones portables > Entretien, maintenance et réparations > Protecteurs d'écran",
    },
    "ES": {
        "marketplace": "ES",
        "domain": "amazon.co.uk",
        "mons_sel_mkid": "amzn1.mp.o.A1RKKUPIHCS9HS",
        "entry_url": "https://sellercentral.amazon.co.uk/abis/listing/create/product_identity?productType=SCREEN_PROTECTOR&productData=%7B%22sparseText%22%3A%22screen+protector%22%7D&recommendedBrowseNodeId=934190031&displayPath=Electr%C3%B3nica%2FComunicaci%C3%B3n+m%C3%B3vil+y+accesorios%2FAccesorios%2FMantenimiento%2C+cuidado+y+reparaciones%2FProtectores+de+pantalla#product_identity",
        "item_type_keyword": "Electrónica > Comunicación móvil y accesorios > Accesorios > Mantenimiento, cuidado y reparaciones > Protectores de pantalla",
    },
    "IT": {
        "marketplace": "IT",
        "domain": "amazon.co.uk",
        "mons_sel_mkid": "amzn1.mp.o.APJ6JRA9NG5V4",
        "entry_url": "https://sellercentral.amazon.co.uk/abis/listing/create/product_identity?productType=SCREEN_PROTECTOR&productData=%7B%22sparseText%22%3A%22screen+protector%22%7D&recommendedBrowseNodeId=473282031&displayPath=Elettronica%2FCellulari+e+accessori%2FAccessori%2FManutenzione%2C+cura+e+riparazione%2FProtezioni+per+lo+schermo#product_identity",
        "item_type_keyword": "Elettronica > Cellulari e accessori > Accessori > Manutenzione, cura e riparazione > Protezioni per lo schermo",
    },
}


def build_default_account(account_id: str, site: str | None = None) -> dict[str, Any]:
    """创建默认账号配置，支持按站点选择模板，同时自动生成全站点 marketplace_configs"""
    normalized_site = normalize_site(site) or "US"
    payload = load_accounts_payload()
    template = None
    
    # 优先查找对应站点的模板
    for acc in payload["accounts"]:
        if acc.get("marketplace") == normalized_site:
            template = acc
            break
    
    # 回退到 US 模板
    if template is None:
        for acc in payload["accounts"]:
            if acc.get("marketplace") == "US":
                template = acc
                break

    if template is None:
        template = MARKETPLACE_CONFIGS.get(normalized_site, MARKETPLACE_CONFIGS["US"])

    account_num = normalize_account_num(account_id)
    site_cfg = MARKETPLACE_CONFIGS.get(normalized_site, MARKETPLACE_CONFIGS["US"])

    # 构建 marketplace_configs（所有支持的站点）
    marketplace_configs = {}
    for mp_code, mp_cfg in MARKETPLACE_CONFIGS.items():
        marketplace_configs[mp_code] = {
            "marketplace": mp_cfg["marketplace"],
            "domain": mp_cfg["domain"],
            "entry_url": mp_cfg["entry_url"],
            "item_type_keyword": mp_cfg["item_type_keyword"],
            "mons_sel_mkid": mp_cfg.get("mons_sel_mkid", ""),
        }

    new_acc = {
        "account_id": account_id,
        "adspower_profile_id": "",
        "marketplace": site_cfg["marketplace"],
        "status": "pending_setup",
        "note": account_num,
        "username": template.get("username", "") if template else "",
        "domain": site_cfg["domain"],
        "mons_sel_mkid": site_cfg.get("mons_sel_mkid", ""),
        "entry_url": site_cfg["entry_url"],
        "item_type_keyword": site_cfg["item_type_keyword"],
        "marketplace_configs": marketplace_configs,
    }
    for sensitive in ["password", "email_password", "email"]:
        if template and sensitive in template:
            new_acc[sensitive] = template[sensitive]
    return new_acc


def extract_profile_fields(profile: dict[str, Any], account_id: str) -> dict[str, Any]:
    account_num = normalize_account_num(account_id)
    name = clean_text(profile.get("name") or profile.get("profile_name"))
    remark = clean_text(profile.get("remark") or profile.get("note"))
    login_email = clean_text(profile.get("username") or profile.get("email"))
    parsed_email = extract_email_from_profile(profile)
    if parsed_email:
        login_email = parsed_email
    profile_id = clean_text(profile.get("user_id") or profile.get("profile_id") or profile.get("id"))
    status_raw = str(profile.get("status") or "").lower()
    if profile_id or status_raw in {"active", "open", "started", "1", "true"}:
        status = "active"
    elif status_raw:
        status = "imported"
    else:
        status = "pending_setup"

    note_parts = [part for part in [name, remark] if part]
    clean_note = " | ".join(dict.fromkeys(note_parts)) if note_parts else account_num

    return {
        "adspower_profile_id": profile_id,
        "username": login_email,
        "email": login_email,
        "note": clean_note,
        "status": status,
        "adspower_name": name,
        "adspower_remark": remark,
    }


def _query_adspower_profiles_direct(account_id: str | None = None) -> list[dict[str, Any]]:
    """Fallback AdsPower scan when scripts/scan_adspower_accounts.py is absent."""
    try:
        from src.adspower_client import AdsPowerClient
    except Exception:
        return []

    api_base_url, api_key = load_settings_api()
    client = AdsPowerClient(api_base_url=api_base_url, api_key=api_key)
    profiles: list[dict[str, Any]] = []
    account_num = normalize_account_num(account_id) if account_id else None

    search_values = [account_num] if account_num else [None]
    # Search first; if the API ignores search, this is still cheap.
    for search in search_values:
        try:
            res = client.query_profiles(search_value=search, page=1, page_size=100)
            profiles.extend(res.get("profiles") or [])
        except Exception:
            pass

    # If search returned nothing, scan first few pages as a fallback.
    if not profiles:
        for page in range(1, 6):
            try:
                res = client.query_profiles(page=page, page_size=100)
                batch = res.get("profiles") or []
                profiles.extend(batch)
                total = int(res.get("total") or 0)
                if not batch or len(profiles) >= total:
                    break
            except Exception:
                break

    return profiles


def try_match_adspower_profile(account_id: str) -> dict[str, Any] | None:
    get_adspower_profiles = None
    try:
        from scripts.scan_adspower_accounts import get_adspower_profiles as _get_adspower_profiles
        get_adspower_profiles = _get_adspower_profiles
    except Exception:
        get_adspower_profiles = None

    account_num = normalize_account_num(account_id)
    if not _PROFILE_CACHE["loaded"]:
        if get_adspower_profiles:
            api_base_url, api_key = load_settings_api()
            try:
                _PROFILE_CACHE["profiles"] = get_adspower_profiles(api_base_url, api_key)
            except Exception:
                _PROFILE_CACHE["profiles"] = []
        if not _PROFILE_CACHE["profiles"]:
            _PROFILE_CACHE["profiles"] = _query_adspower_profiles_direct(account_id)
        _PROFILE_CACHE["loaded"] = True

    profiles = _PROFILE_CACHE["profiles"] or []
    for profile in profiles:
        text = " ".join([
            str(profile.get("name") or ""),
            str(profile.get("profile_name") or ""),
            str(profile.get("remark") or ""),
            str(profile.get("note") or ""),
            str(profile.get("username") or ""),
            str(profile.get("email") or ""),
            str(profile.get("serial_number") or ""),
        ])
        if account_num in text:
            return profile
    return None


def sync_marketplace_configs_for_account(account_id: str, force: bool = False) -> dict[str, Any]:
    """为已有账号自动补全/增量 marketplace_configs"""
    payload = load_accounts_payload()
    for idx, acc in enumerate(payload["accounts"]):
        if acc.get("account_id") != account_id:
            continue
        existing = acc.get("marketplace_configs", {}) or {}
        added = []
        for mp_code, mp_cfg in MARKETPLACE_CONFIGS.items():
            if mp_code in existing and not force:
                continue
            existing[mp_code] = {
                "marketplace": mp_cfg["marketplace"],
                "domain": mp_cfg["domain"],
                "entry_url": mp_cfg["entry_url"],
                "item_type_keyword": mp_cfg["item_type_keyword"],
                "mons_sel_mkid": mp_cfg.get("mons_sel_mkid", ""),
            }
            added.append(mp_code)
        if added:
            acc["marketplace_configs"] = existing
            payload["accounts"][idx] = acc
            save_json(ACCOUNTS_JSON_PATH, payload)
            print(f"[AUTO-SYNC] 已为 {account_id} 增量补全 {len(added)} 个站点: {', '.join(added)}")
        return existing
    return {}


def ensure_account_exists(account: str, auto_create: bool = True, site: str | None = None) -> dict[str, Any]:
    account_id = normalize_account_id(account)
    payload = load_accounts_payload()

    for idx, existing in enumerate(payload["accounts"]):
        if existing.get("account_id") == account_id:
            profile = None
            refreshed = dict(existing)
            mutated = False

            if refreshed.get("adspower_profile_id") and refreshed.get("status") == "pending_setup":
                refreshed["status"] = "active"
                mutated = True

            needs_refresh = not refreshed.get("adspower_profile_id") or not refreshed.get("email") or not refreshed.get("adspower_name")
            if needs_refresh:
                profile = try_match_adspower_profile(account_id)
                if profile:
                    refreshed.update(extract_profile_fields(profile, account_id))
                    mutated = True

            # 自动增量补全 marketplace_configs（老账号可能缺少新站点）
            synced = sync_marketplace_configs_for_account(account_id)
            if synced != refreshed.get("marketplace_configs", {}):
                refreshed["marketplace_configs"] = synced
                mutated = True

            if mutated:
                payload["accounts"][idx] = refreshed
                payload["accounts"] = sorted(payload["accounts"], key=lambda x: x.get("account_id", ""))
                save_json(ACCOUNTS_JSON_PATH, payload)
                existing = refreshed

            return {"account": existing, "created": False, "matched_profile": bool(profile), "refreshed": mutated}

    if not auto_create:
        raise ValueError(f"账号不存在: {account_id}")

    new_acc = build_default_account(account_id, site=site)
    profile = try_match_adspower_profile(account_id)
    if profile:
        new_acc.update(extract_profile_fields(profile, account_id))
        if not new_acc.get("adspower_profile_id"):
            new_acc["status"] = "pending_setup"

    payload["accounts"].append(new_acc)
    payload["accounts"] = sorted(payload["accounts"], key=lambda x: x.get("account_id", ""))
    save_json(ACCOUNTS_JSON_PATH, payload)
    return {"account": new_acc, "created": True, "matched_profile": bool(profile), "refreshed": False}


def sync_brands_for_account(account: str, brands: list[str] | None = None, include_all_if_missing: bool = False, site: str | None = None) -> list[dict[str, Any]]:
    ensure_account_exists(account, auto_create=True, site=site)
    if brands is None and include_all_if_missing:
        brands = sorted([p.name for p in PICTURE_ROOT.iterdir() if p.is_dir()])
    if not brands:
        return []
    return [sync_brand_pack_for_account(brand, account, site=site) for brand in brands]


def main() -> int:
    parser = argparse.ArgumentParser(description="按目标账号同步品牌文案到 brand_packs，并按需补账号配置")
    parser.add_argument("brand", nargs="?", help="品牌名，如 HOMEMO / mocodi")
    parser.add_argument("target_account", help="目标账号，如 530 / 543 / us_store_543")
    parser.add_argument("--all-brands", action="store_true", help="同步 picture 下全部品牌")
    parser.add_argument("--site", help="指定站点/国家，如 US / UK / DE / MX")
    args = parser.parse_args()

    account_meta = ensure_account_exists(args.target_account, auto_create=True, site=args.site)

    if args.all_brands:
        results = sync_brands_for_account(args.target_account, brands=None, include_all_if_missing=True, site=args.site)
        print(json.dumps({
            "account": account_meta,
            "synced": results,
        }, ensure_ascii=False, indent=2))
        return 0

    if not args.brand:
        raise SystemExit("未提供品牌；单品牌模式下请传入 <brand>")

    result = sync_brand_pack_for_account(args.brand, args.target_account, site=args.site)
    print(json.dumps({
        "account": account_meta,
        "sync": result,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
