import openpyxl

from src.registration import RegistrationManager


def test_false_approved_is_supported_and_styled(tmp_path, capsys):
    registry_path = tmp_path / "registration.xlsx"
    registry = RegistrationManager(str(registry_path))
    registry.add_record(
        {
            "site": "US",
            "account": "us_store_000",
            "brand": "ExampleBrand",
            "sku": "EXAMPLE-SKU",
            "case_id": "19999999999",
            "status": "申请中",
        }
    )

    assert "假过" in registry.STATUS_OPTIONS
    assert registry.update_status("19999999999", "假过", "approval was not effective")
    assert "未知状态" not in capsys.readouterr().out

    workbook = openpyxl.load_workbook(registry_path)
    status_cell = workbook.active.cell(row=2, column=8)
    assert status_cell.value == "假过"
    assert status_cell.fill.fill_type == "solid"
    assert status_cell.fill.fgColor.rgb.endswith("F4B183")
