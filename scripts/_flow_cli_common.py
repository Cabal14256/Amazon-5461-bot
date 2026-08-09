from dotenv import load_dotenv

from src.config_loader import load_yaml, load_account, load_brand_manifest
from src.adspower_backend import create_adspower_client
from src.db import init_db, get_procedure_flow_by_code
from src.flow_catalog import resolve_entry_url


def load_runtime(account_id: str, brand_name: str):
    load_dotenv()
    settings = load_yaml("config/settings.yaml")
    init_db(settings["paths"]["db_path"])
    acc = load_account("config/accounts.json", account_id)
    manifest = load_brand_manifest("brand_packs", brand_name)
    marketplace = acc["marketplace"]
    mkt_cfg = load_yaml(f"config/marketplaces/{marketplace.lower()}.yaml")
    adsp = create_adspower_client(settings)
    cdp_url = adsp.ensure_profile_started(profile_id=acc["adspower_profile_id"])["ws_endpoint"]
    return settings, acc, manifest, marketplace, mkt_cfg, cdp_url


def get_entry_url_for_flow_code(settings: dict, mkt_cfg: dict, flow_code: str):
    row = get_procedure_flow_by_code(settings["paths"]["db_path"], flow_code)
    if not row:
        raise RuntimeError(f"流程不存在: {flow_code}")
    return row, resolve_entry_url(mkt_cfg, row["flow_type"])
