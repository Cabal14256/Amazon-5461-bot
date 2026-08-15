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


def _creation_fields():
    return [
        {"field_name": "账号", "type": 1},
        {"field_name": "国家", "type": 1},
        {
            "field_name": "国家EU",
            "type": 4,
            "property": {
                "options": [
                    {"name": "英国"},
                    {"name": "比利时"},
                    {"name": "德国"},
                ]
            },
        },
        {"field_name": "品牌", "type": 1},
        {"field_name": "SKU", "type": 1},
        {"field_name": "标题", "type": 1},
        {"field_name": "发信内容", "type": 1},
        {
            "field_name": "5461进度",
            "type": 3,
            "property": {
                "options": [
                    {"name": "申请中"},
                    {"name": "假过"},
                    {"name": "拒绝"},
                    {"name": "通过"},
                ]
            },
        },
        {"field_name": "备注", "type": 1},
    ]


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
            _record("rec_eu", account="007-EU", eu=["比利时"]),
            _record("rec_us", account="007-US", country="US", eu=[]),
        ]
    )

    result = client.find_record_binding("us_store_007", "BE", "ExampleBrand", "SKU-1")

    assert result.status == "bound"
    assert result.record_id == "rec_eu"


def test_region_prefix_account_format_also_matches_local_store_id():
    client = _client_with_records([_record("rec_eu", account="EU-007", country="ES", eu=[])])

    result = client.find_record_binding("us_store_007", "ES", "ExampleBrand", "SKU-1")

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
        account="正常号-EU-002",
        brand="DEMO_HOME",
        sku="002-UK-DEMO_HOME-Q66Q",
        country="UK",
        eu=["英国", "比利时"],
    )
    client.search_candidate_records = (
        lambda _account, _site, _brand, sku="": [] if sku else [reused_row]
    )

    result = client.find_record_binding(
        "us_store_002",
        "BE",
        "DEMO_HOME",
        "002-BE-DEMO_HOME-BG42",
        country_option="比利时",
    )

    assert result.status == "bound"
    assert result.record_id == "rec_be"
    assert "shared EU progress target" in result.reason


def test_non_uk_eu_application_prefers_unique_uk_progress_record():
    client = FeishuBitableClient(_config())
    uk_row = _record(
        "rec_uk",
        account="正常号-EU-002",
        brand="DEMO_JUNO",
        sku="002-UK-DEMO_JUNO-OLD",
        country="UK",
        eu=["英国", "比利时"],
    )
    de_row = _record(
        "rec_de",
        account="正常号-EU-002",
        brand="DEMO_JUNO",
        sku="002-DE-DEMO_JUNO-NEW",
        country="DE",
        eu=["德国"],
    )
    client.search_candidate_records = (
        lambda _account, _site, _brand, sku="": [de_row] if sku else [uk_row, de_row]
    )

    result = client.find_record_binding(
        "us_store_002",
        "DE",
        "DEMO_JUNO",
        "002-DE-DEMO_JUNO-NEW",
        country_option="德国",
    )

    assert result.status == "bound"
    assert result.record_id == "rec_uk"
    assert result.country_option == "德国"
    assert "shared EU progress target" in result.reason


def test_multiple_uk_progress_records_remain_ambiguous():
    client = _client_with_records(
        [
            _record("rec_uk_1", country="UK", eu=["英国"]),
            _record("rec_uk_2", country="UK", eu=["英国1"]),
        ]
    )

    result = client.find_record_binding(
        "667EU", "DE", "ExampleBrand", "SKU-DE", country_option="德国"
    )

    assert result.status == "ambiguous"


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


def test_missing_detail_row_is_created_without_progress_and_shared_options_are_extended():
    client = FeishuBitableClient(
        _config(create_missing_records=True, write_enabled=True)
    )
    client._fields_cache = _creation_fields()
    uk_row = _record(
        "rec_uk",
        account="正常号-EU-002",
        brand="DEMO_JUNO",
        sku="002-UK-DEMO_JUNO-A00A",
        country="UK",
        eu=["英国", "比利时"],
    )
    client.search_candidate_records = lambda *_args, **_kwargs: [uk_row]
    created = []
    updated = []
    client._create_record = lambda fields, *, dry_run: (
        created.append(dict(fields)) or {"status": "created", "record_id": "rec_de"}
    )
    client._update_record_fields = lambda record_id, fields, *, dry_run: (
        updated.append((record_id, dict(fields)))
        or {"status": "updated", "record_id": record_id}
    )

    result = client.ensure_submission_records(
        "us_store_002",
        "DE",
        "DEMO_JUNO",
        "002-DE-DEMO_JUNO-N11N",
        "DEMO_JUNO Displayschutzfolie",
        "Brand：DEMO_JUNO\nSKU：002-DE-DEMO_JUNO-N11N",
        "德国",
    )

    assert result["status"] == "ready"
    assert created[0]["国家"] == "DE"
    assert "国家EU" not in created[0]
    assert "5461进度" not in created[0]
    assert "父记录" not in created[0]
    assert updated == [("rec_uk", {"国家EU": ["英国", "比利时", "德国"]})]


def test_missing_detail_and_shared_rows_are_created_from_exact_site_materials():
    client = FeishuBitableClient(
        _config(create_missing_records=True, write_enabled=True)
    )
    client._fields_cache = _creation_fields()
    client.search_candidate_records = lambda *_args, **_kwargs: []
    created = []

    def create(fields, *, dry_run):
        created.append(dict(fields))
        return {"status": "created", "record_id": f"rec_{len(created)}"}

    client._create_record = create

    result = client.ensure_submission_records(
        "us_store_002",
        "DE",
        "DEMO_JUNO",
        "002-DE-DEMO_JUNO-N11N",
        "DEMO_JUNO Displayschutzfolie",
        "German application body",
        "德国",
        uk_sku="002-UK-DEMO_JUNO-A00A",
        uk_title="DEMO_JUNO Screen Protector",
        uk_content="UK application body",
        dry_run=False,
    )

    assert result["status"] == "ready"
    assert len(created) == 2
    assert created[0]["账号"] == "正常号-EU-002"
    assert created[0]["国家"] == "DE"
    assert created[1]["国家"] == "UK"
    assert created[1]["国家EU"] == ["英国", "德国"]
    assert created[1]["SKU"] == "002-UK-DEMO_JUNO-A00A"
    assert all("5461进度" not in fields for fields in created)


def test_missing_uk_materials_never_fabricate_a_shared_row():
    client = FeishuBitableClient(
        _config(create_missing_records=True, write_enabled=True)
    )
    client._fields_cache = _creation_fields()
    client.search_candidate_records = lambda *_args, **_kwargs: []
    created = []
    client._create_record = lambda fields, *, dry_run: (
        created.append(dict(fields)) or {"status": "created", "record_id": "rec_de"}
    )

    result = client.ensure_submission_records(
        "us_store_002",
        "DE",
        "DEMO_JUNO",
        "002-DE-DEMO_JUNO-N11N",
        "DEMO_JUNO Displayschutzfolie",
        "German application body",
        "德国",
        dry_run=False,
    )

    assert result["status"] == "partial"
    assert result["shared"]["status"] == "missing_uk_materials"
    assert len(created) == 1


def test_case_terminal_results_map_to_existing_progress_options():
    assert CASE_RESULT_PROGRESS_MAP == {
        "approved": "通过",
        "false_approved": "假过",
        "declined": "拒绝",
    }


def test_mexico_application_binds_to_unique_us_shared_progress_row():
    client = FeishuBitableClient(_config())
    us_row = _record(
        "rec_us",
        account="正常号-US-002",
        brand="DEMO_HOME",
        sku="002-US-DEMO_HOME-A1",
        country="US",
    )
    mx_row = _record(
        "rec_mx",
        account="正常号-MX-002",
        brand="DEMO_HOME",
        sku="002-MX-DEMO_HOME-B2",
        country="MX",
    )
    client.search_candidate_records = lambda *_args, **_kwargs: [us_row, mx_row]

    result = client.find_record_binding(
        "us_store_002",
        "MX",
        "DEMO_HOME",
        "002-MX-DEMO_HOME-B2",
    )

    assert result.status == "bound"
    assert result.record_id == "rec_us"
    assert "shared NA progress target" in result.reason


def test_mexico_does_not_fall_back_to_detail_row_when_us_shared_row_is_missing():
    client = FeishuBitableClient(_config())
    mx_row = _record(
        "rec_mx",
        account="正常号-MX-002",
        brand="DEMO_HOME",
        sku="002-MX-DEMO_HOME-B2",
        country="MX",
    )
    client.search_candidate_records = lambda *_args, **_kwargs: [mx_row]

    result = client.find_record_binding(
        "us_store_002",
        "MX",
        "DEMO_HOME",
        "002-MX-DEMO_HOME-B2",
    )

    assert result.status == "not_found"
    assert result.record_id == ""


def test_missing_mexico_and_us_rows_use_separate_account_specific_materials():
    client = FeishuBitableClient(
        _config(create_missing_records=True, write_enabled=True)
    )
    client._fields_cache = _creation_fields()
    client.search_candidate_records = lambda *_args, **_kwargs: []
    created = []
    client._create_record = lambda fields, *, dry_run: (
        created.append(dict(fields))
        or {"status": "created", "record_id": f"rec_{len(created)}"}
    )

    result = client.ensure_submission_records(
        "us_store_002",
        "MX",
        "DEMO_HOME",
        "002-MX-DEMO_HOME-B2",
        "DEMO_HOME Protector de pantalla",
        "Mexico application body",
        us_sku="002-US-DEMO_HOME-A1",
        us_title="DEMO_HOME Screen Protector",
        us_content="US application body",
        dry_run=False,
    )

    assert result["status"] == "ready"
    assert len(created) == 2
    assert created[0]["账号"] == "正常号-MX-002"
    assert created[0]["国家"] == "MX"
    assert created[1]["账号"] == "正常号-US-002"
    assert created[1]["国家"] == "US"
    assert created[1]["SKU"] == "002-US-DEMO_HOME-A1"
