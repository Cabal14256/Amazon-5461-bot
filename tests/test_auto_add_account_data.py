import auto_add_account_data as module


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
