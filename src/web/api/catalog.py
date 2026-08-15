"""GET /api/catalog/* — sanitized account/site/brand catalogs.

Account records come from the local private accounts.json but are serialized
through :class:`AccountOut`, which whitelists only non-secret fields
(``account_id, marketplace, status, note, domain, item_type_keyword``).
``username`` (login email), ``adspower_profile_id`` and ``entry_url`` are
never returned.
"""

from __future__ import annotations

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request

from src.db import record_web_audit
from src.state_files import read_json_tolerant
from src.web.deps import get_settings, require_role
from src.web.schemas import AccountOut, BrandOut, SiteOut

router = APIRouter(prefix="/catalog", tags=["catalog"])


@router.get("/accounts")
def list_accounts(
    request: Request,
    marketplace: str | None = None,
    status: str | None = None,
):
    settings = get_settings(request)
    payload = read_json_tolerant(settings.accounts_path)
    rows = payload.get("accounts") if isinstance(payload, dict) else []
    accounts: list[AccountOut] = []
    for row in rows or []:
        if not isinstance(row, dict) or not row.get("account_id"):
            continue
        account = AccountOut(**row)
        if marketplace and (account.marketplace or "").upper() != marketplace.upper():
            continue
        if status and (account.status or "") != status:
            continue
        accounts.append(account)
    return {"accounts": accounts, "total": len(accounts)}


@router.post("/accounts/sync")
def sync_accounts(
    request: Request,
    user: dict = Depends(require_role("operator")),  # noqa: B008 (FastAPI dependency idiom)
):
    """扫描 AdsPower 新环境并登记到本地 accounts.json（只登记账号）。

    复用 ``scripts/adspower_auto_enroll`` 的发现/登记逻辑：默认 marketplace=US，
    marketplace_configs 自动覆盖全部站点；写入前自动备份 accounts.json。
    返回脱敏报告（账号编号/状态/计数，不含环境备注全文）。
    """
    settings = get_settings(request)
    ip = request.client.host if request.client else ""

    import auto_add_account_data as aad
    from scripts.adspower_auto_enroll import backup_accounts, discover_new_accounts

    profiles = aad._query_adspower_profiles_direct(None)
    if not profiles:
        record_web_audit(
            str(settings.db_path), action="accounts_sync", actor_id=int(user["id"]),
            target_type="catalog", target_id="accounts",
            result="failed:adspower_unavailable", ip_address=ip,
        )
        raise HTTPException(status_code=502, detail="adspower_unavailable")

    report = discover_new_accounts(profiles)
    enrolled: list[dict[str, str]] = []
    failed: list[dict[str, str]] = []
    if report["new"]:
        backup_accounts()
        for item in report["new"]:
            try:
                meta = aad.ensure_account_exists(item["account_num"], auto_create=True, site=None)
                acc = meta["account"]
                enrolled.append({
                    "account_id": str(acc.get("account_id") or ""),
                    "marketplace": str(acc.get("marketplace") or ""),
                    "status": str(acc.get("status") or ""),
                })
            except Exception as exc:  # 单个失败不阻断其余登记
                failed.append({"account_id": str(item["account_id"]), "error": str(exc)[:200]})

    payload = read_json_tolerant(settings.accounts_path)
    rows = payload.get("accounts") if isinstance(payload, dict) else []
    total = len(rows or [])

    record_web_audit(
        str(settings.db_path), action="accounts_sync", actor_id=int(user["id"]),
        target_type="catalog", target_id="accounts",
        result=f"ok:enrolled={len(enrolled)},failed={len(failed)}", ip_address=ip,
    )
    return {
        "profiles_scanned": report["profiles_scanned"],
        "enrolled": enrolled,
        "failed": failed,
        "ambiguous": [
            {"account_num": a.get("account_num"), "reason": str(a.get("reason") or "")}
            for a in report["ambiguous"]
        ],
        "no_number_count": len(report["no_number"]),
        "total": total,
    }


@router.get("/sites")
def list_sites(request: Request):
    settings = get_settings(request)
    sites: list[SiteOut] = []
    directory = settings.marketplaces_dir
    if directory.is_dir():
        for path in sorted(directory.glob("*.yaml")):
            marketplace = None
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                marketplace = data.get("marketplace")
            except Exception:
                marketplace = None
            sites.append(SiteOut(code=path.stem, marketplace=marketplace))
    return {"sites": sites, "total": len(sites)}


@router.get("/brands")
def list_brands(request: Request):
    settings = get_settings(request)
    root = settings.brand_packs_root
    brands = []
    if root.is_dir():
        brands = [BrandOut(name=p.name) for p in sorted(root.iterdir()) if p.is_dir()]
    return {"brands": brands, "total": len(brands)}
