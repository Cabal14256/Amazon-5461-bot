FLOW_URL_KEY_MAP = {
    "verify_add_product": "add_product_entry",
    "submit_5461": "flow_5461_entry",
    "submit_gtin_exemption": "flow_gtin_exemption_entry",
}


def resolve_entry_url(marketplace_cfg: dict, flow_type: str) -> str:
    key = FLOW_URL_KEY_MAP.get(flow_type)
    if not key:
        raise ValueError(f"未知 flow_type: {flow_type}")
    urls = marketplace_cfg.get("urls", {})
    if key not in urls:
        raise KeyError(f"marketplace 配置缺少入口 URL: {key}")
    return urls[key]
