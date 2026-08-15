"""Stage-4 submit preflight checks (synchronous, never opens a browser).

``run_submit_preflight`` runs before a real-submission job is created.
Blockers reject the prepare request outright (422 preflight_blocked);
warnings are returned for the operator/reviewer to read but do not block.
"""

from __future__ import annotations

import json
import socket
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from src.db import account_has_active_case_followup, get_conn
from src.web.config import WebSettings

RECENT_DRY_RUN_WINDOW = timedelta(hours=24)


def _check(name: str, ok: bool, level: str, detail: str) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "level": level, "detail": detail}


def _check_adspower_reachable(api_base_url: str) -> bool:
    try:
        parsed = urlparse(api_base_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 50325
        with socket.create_connection((host, port), timeout=2.0):
            pass
        return True
    except Exception:
        return False


def _has_recent_dry_run(
    db_path: str, account_id: str, site: str | None, brands: list[str]
) -> bool:
    """A completed dry_run within 24h covering account/site/any of the brands.

    Read from automation_jobs joined with automation_job_items (the parsed
    per-brand results), so a bare job row without items does not count.
    """
    cutoff = (datetime.now() - RECENT_DRY_RUN_WINDOW).strftime("%Y-%m-%d %H:%M:%S")
    placeholders = ",".join("?" for _ in brands) or "''"
    conn = get_conn(db_path)
    rows = conn.execute(
        f"""SELECT DISTINCT j.id, j.marketplace FROM automation_jobs j
            JOIN automation_job_items i ON i.job_id = j.id
            WHERE j.job_type='dry_run' AND j.run_status='completed'
              AND j.account_id=? AND j.created_at >= ?
              AND i.brand_name IN ({placeholders})""",
        (account_id, cutoff, *brands),
    ).fetchall()
    conn.close()
    wanted = (site or "").upper()
    for row in rows:
        job_site = (row["marketplace"] or "").upper()
        if job_site == wanted:
            return True
    return False


def run_submit_preflight(
    settings: WebSettings,
    account_row: dict[str, Any],
    brands: list[str],
    site: str | None,
) -> list[dict[str, Any]]:
    """Return the ordered blocker/warning checks for one submit request."""
    checks: list[dict[str, Any]] = []
    account_id = str(account_row.get("account_id") or "")

    # -- blockers -----------------------------------------------------------
    profile_id = str(account_row.get("adspower_profile_id") or "").strip()
    checks.append(_check(
        "adspower_profile",
        bool(profile_id),
        "blocker",
        "账号已配置 AdsPower profile" if profile_id else "账号缺少 adspower_profile_id",
    ))

    if site:
        site_cfg = (account_row.get("marketplace_configs") or {}).get(site)
        entry_url = str(account_row.get("entry_url") or "").strip()
        ok = bool(site_cfg) or bool(entry_url)
        checks.append(_check(
            "site_config",
            ok,
            "blocker",
            f"站点 {site} 配置存在" if ok else f"账号缺少站点 {site} 的配置（marketplace_configs / entry_url）",
        ))
    else:
        checks.append(_check(
            "site_config", True, "blocker", "未指定站点，使用账号默认站点"
        ))

    for brand in brands:
        brand_dir = settings.brand_packs_root / brand
        manifest_path = brand_dir / "manifest.json"
        detail = ""
        ok = False
        if not manifest_path.is_file():
            detail = f"缺少 {brand}/manifest.json"
        else:
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                detail = f"manifest.json 解析失败: {type(exc).__name__}"
            else:
                upload_files = ((manifest.get("5461") or {}).get("upload_files")) or []
                if not upload_files:
                    detail = "manifest 5461.upload_files 为空"
                else:
                    missing = [f for f in upload_files if not (brand_dir / str(f)).is_file()]
                    if missing:
                        detail = f"上传文件缺失: {', '.join(str(m) for m in missing)}"
                    else:
                        ok = True
                        detail = f"manifest 与 {len(upload_files)} 个上传文件齐全"
        checks.append(_check(f"brand_pack:{brand}", ok, "blocker", detail))

    max_brands = int(settings.submit_max_brands)
    checks.append(_check(
        "brand_count",
        len(brands) <= max_brands,
        "blocker",
        f"{len(brands)} 个品牌（上限 {max_brands}）",
    ))

    # -- warnings -----------------------------------------------------------
    reachable = _check_adspower_reachable(settings.adspower_api_base_url)
    checks.append(_check(
        "adspower_api",
        reachable,
        "warning",
        "AdsPower 本地 API 可达" if reachable else "AdsPower 本地 API 不可达",
    ))

    followup_active = account_has_active_case_followup(str(settings.db_path), account_id)
    checks.append(_check(
        "case_followup_conflict",
        not followup_active,
        "warning",
        "账号有进行中的 Case 跟进任务" if followup_active else "无进行中的 Case 跟进任务",
    ))

    recent = _has_recent_dry_run(str(settings.db_path), account_id, site, brands)
    checks.append(_check(
        "recent_dry_run",
        recent,
        "warning",
        "近 24h 内有已完成的同参数 dry_run" if recent else "近 24h 内无已完成的同参数 dry_run",
    ))

    return checks
