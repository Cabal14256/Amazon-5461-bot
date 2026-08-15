"""Health endpoint structure tests."""

from src.codex_signal import write_pending_signal


def test_health_structure(viewer_client):
    response = viewer_client.get("/api/health")
    assert response.status_code == 200
    payload = response.json()
    assert set(payload.keys()) == {"ok", "checks", "time"}
    assert set(payload["checks"].keys()) == {
        "db", "disk", "evidence_dir", "logs_dir", "adspower", "codex_signal",
    }
    for check in payload["checks"].values():
        assert "ok" in check and "detail" in check
    assert payload["checks"]["db"]["ok"] is True
    assert payload["checks"]["db"]["detail"]["journal_mode"].lower() == "wal"


def test_health_reflects_pending_codex_signal(viewer_client, web_settings):
    write_pending_signal(
        reason="stuck_at_form_loading",
        brand_data={"account_id": "us_store_999", "brand_name": "TESTBRAND", "site": "US"},
        signal_path=web_settings.codex_signal_path,
    )
    payload = viewer_client.get("/api/health").json()
    assert payload["checks"]["codex_signal"]["detail"]["pending"] is True
    assert payload["checks"]["codex_signal"]["ok"] is False
