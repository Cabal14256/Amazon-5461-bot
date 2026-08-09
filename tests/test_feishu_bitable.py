from src.feishu_bitable import (
    CASE_RESULT_PROGRESS_MAP,
    FeishuBitableClient,
    FeishuBitableConfig,
    country_option_to_site,
    split_country_option,
)


def _config(**overrides):
    values = {
        "enabled": True,
        "app_id": "cli_test",
        "app_secret": "secret_test",
        "wiki_node_token": "wiki_test",
        "table_id": "tbl_test",
    }
    values.update(overrides)
    return FeishuBitableConfig(**values)


def _record(record_id, *, account="667EU", brand="ExampleBrand", sku="SKU-1", country="UK", eu=None):
    return {
        "record_id": record_id,
        "fields": {
            "账号": account,
            "品牌": brand,
            "SKU": sku,
            "国家": country,
            "国家EU": list(eu or []),
        },
    }


def _client_with_records(records):
    client = FeishuBitableClient(_config())
    client.search_candidate_records = lambda *_args, **_kwargs: list(records)
    return client


def test_country_option_keeps_repeat_marker_but_resolves_marketplace():
    assert split_country_option("荷兰2") == ("荷兰", "2")
    assert country_option_to_site("荷兰2") == "NL"
    assert country_option_to_site("比利时1") == "BE"


def test_binds_unique_eu_record_and_preserves_original_option():
    client = _client_with_records([_record("rec_1", eu=["比利时1", "荷兰"])])

    result = client.find_record_binding("667EU", "BE", "ExampleBrand", "SKU-1")

    assert result.status == "bound"
    assert result.record_id == "rec_1"
    assert result.country_option == "比利时1"


def test_local_store_id_matches_feishu_region_account_format():
    client = _client_with_records(
        [
            _record("rec_eu", account="667-EU", eu=["比利时"]),
            _record("rec_us", account="667-US", country="US", eu=[]),
        ]
    )

    result = client.find_record_binding("us_store_667", "BE", "ExampleBrand", "SKU-1")

    assert result.status == "bound"
    assert result.record_id == "rec_eu"


def test_region_prefix_account_format_also_matches_local_store_id():
    client = _client_with_records([_record("rec_eu", account="EU-667", country="ES", eu=[])])

    result = client.find_record_binding("us_store_667", "ES", "ExampleBrand", "SKU-1")

    assert result.status == "bound"
    assert result.record_id == "rec_eu"


def test_repeat_options_for_same_site_are_ambiguous_without_explicit_option():
    client = _client_with_records([_record("rec_1", eu=["荷兰", "荷兰1"])])

    result = client.find_record_binding("667EU", "NL", "ExampleBrand", "SKU-1")

    assert result.status == "ambiguous"
    assert result.record_id == ""


def test_explicit_repeat_option_resolves_ambiguous_eu_record():
    client = _client_with_records([_record("rec_1", eu=["荷兰", "荷兰1"])])

    result = client.find_record_binding(
        "667EU", "NL", "ExampleBrand", "SKU-1", country_option="荷兰1"
    )

    assert result.status == "bound"
    assert result.country_option == "荷兰1"


def test_explicit_country_allows_unique_binding_when_existing_row_reuses_uk_sku():
    client = FeishuBitableClient(_config())
    reused_row = _record(
        "rec_be",
        account="正常号-EU-671",
        brand="HOMEMO",
        sku="671-UK-HOMEMO-Q66Q",
        country="UK",
        eu=["英国", "比利时"],
    )
    client.search_candidate_records = (
        lambda _account, _site, _brand, sku="": [] if sku else [reused_row]
    )

    result = client.find_record_binding(
        "us_store_671",
        "BE",
        "HOMEMO",
        "671-BE-HOMEMO-BG42",
        country_option="比利时",
    )

    assert result.status == "bound"
    assert result.record_id == "rec_be"
    assert "explicit country option" in result.reason


def test_direct_marketplace_uses_country_when_country_eu_is_empty():
    client = _client_with_records([_record("rec_us", account="667", country="US", eu=[])])

    result = client.find_record_binding("667", "US", "ExampleBrand", "SKU-1")

    assert result.status == "bound"
    assert result.record_id == "rec_us"
    assert result.country_option == "US"


def test_multiple_matching_rows_are_never_guessed():
    client = _client_with_records(
        [
            _record("rec_1", eu=["比利时"]),
            _record("rec_2", eu=["比利时"]),
        ]
    )

    result = client.find_record_binding("667EU", "BE", "ExampleBrand", "SKU-1")

    assert result.status == "ambiguous"


def test_progress_update_defaults_to_dry_run_without_network_write():
    client = FeishuBitableClient(_config(write_enabled=False))
    client._fields_cache = [
        {"field_name": "账号", "type": 1},
        {"field_name": "国家", "type": 1},
        {"field_name": "国家EU", "type": 4},
        {"field_name": "品牌", "type": 1},
        {"field_name": "SKU", "type": 1},
        {
            "field_name": "5461进度",
            "type": 3,
            "property": {"options": [{"name": "申请中"}, {"name": "假过"}, {"name": "拒绝"}, {"name": "通过"}]},
        },
    ]

    result = client.update_progress("rec_1", "申请中")

    assert result == {"status": "dry_run", "record_id": "rec_1", "field": "5461进度", "value": "申请中"}


def test_case_terminal_results_map_to_existing_progress_options():
    assert CASE_RESULT_PROGRESS_MAP == {
        "approved": "通过",
        "false_approved": "假过",
        "declined": "拒绝",
    }
