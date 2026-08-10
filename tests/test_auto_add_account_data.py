import auto_add_account_data as module


def test_normalize_uk_statement_description_replaces_legacy_field():
    content = (
        "Brand：HOMEMO\n"
        "Item description：Screen Protector for an unrelated model\n"
        "SKU：672-UK-HOMEMO-SP-001\n"
        "Item model：SP-001\n"
    )

    result = module.normalize_uk_statement_description(content, "HOMEMO")

    assert "Item description：" not in result
    assert (
        "Item desrciption：Screen Protector for SP-001  6.10 Inch,  "
        "2+2Pack, Tempered Glass Film"
    ) in result


def test_normalize_uk_statement_description_uses_vasg_template():
    content = (
        "Brand：VASG\n"
        "Item desrciption：\n"
        "SKU：672-UK-VASG-SP-001\n"
        "Item model：SP-001\n"
    )

    result = module.normalize_uk_statement_description(content, "VASG")

    assert "Item desrciption：smart-watch-screen-protectors for 44 mm" in result


def test_empty_site_row_reuses_existing_statement(monkeypatch, tmp_path):
    brand = "V-PORYADKU"
    docs = tmp_path / brand / "docs"
    docs.mkdir(parents=True)
    existing = docs / "5461_statement_be.txt"
    existing.write_text(
        "Brand：V-PORYADKU\n"
        "Manufacturer：V-PORYADKU\n"
        "Item desrciption：Protection d'écran\n"
        "SKU：662-BE-V-PORYADKU-C11C\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "BRAND_PACKS_ROOT", tmp_path)
    monkeypatch.setattr(
        module,
        "load_brand_rows",
        lambda _brand: (
            [{"brand": brand, "country": "BE", "content": "", "sku": ""}],
            "662EU",
            module.ROOT / "5461信息模版.xlsx",
        ),
    )

    result = module.sync_brand_pack_for_account(brand, "669", site="BE")

    generated = docs / "5461_statement_be.account_669.txt"
    text = generated.read_text(encoding="utf-8")
    assert "SKU：669-BE-V-PORYADKU-C11C" in text
    assert "Item desrciption：Protection d'écran" in text
    assert result["sku"] == "669-BE-V-PORYADKU-C11C"
