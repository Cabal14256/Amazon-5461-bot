"""Catalog tests — above all: secret fields must never leave the API."""

SECRET_MARKERS = [
    "fixture-secret-user@example.com",
    "fixture-account-password",
    "fixture-profile-id-xyz",
    "fixture-secret-entry",
    "fixture-other@example.com",
    "fixture-profile-id-2",
    "username",
    "password",
    "adspower_profile_id",
    "entry_url",
]


def test_accounts_sanitized(viewer_client):
    response = viewer_client.get("/api/catalog/accounts")
    assert response.status_code == 200
    body = response.text
    for marker in SECRET_MARKERS:
        assert marker not in body, f"secret leaked in /api/catalog/accounts: {marker}"
    accounts = response.json()["accounts"]
    assert response.json()["total"] == 2
    first = accounts[0]
    assert set(first.keys()) == {
        "account_id", "marketplace", "status", "note", "domain", "item_type_keyword",
    }
    assert first["account_id"] == "us_store_999"


def test_accounts_filters(viewer_client):
    us = viewer_client.get("/api/catalog/accounts", params={"marketplace": "us"})
    assert us.json()["total"] == 1
    paused = viewer_client.get("/api/catalog/accounts", params={"status": "paused"})
    assert paused.json()["total"] == 1
    assert paused.json()["accounts"][0]["account_id"] == "uk_store_998"


def test_sites_and_brands(viewer_client):
    sites = viewer_client.get("/api/catalog/sites")
    assert sites.status_code == 200
    assert any(s["code"] == "us" and s["marketplace"] == "US" for s in sites.json()["sites"])
    brands = viewer_client.get("/api/catalog/brands")
    assert any(b["name"] == "TESTBRAND" for b in brands.json()["brands"])


def _patch_adspower(monkeypatch, web_settings, fake_profiles):
    """Point the enroll logic at the fixture accounts file and fake AdsPower scan."""
    import auto_add_account_data as aad

    monkeypatch.setattr(aad, "ACCOUNTS_JSON_PATH", web_settings.accounts_path)
    monkeypatch.setattr(aad, "_query_adspower_profiles_direct", lambda account_id=None: fake_profiles)
    # try_match_adspower_profile must use the cache, never the real API.
    monkeypatch.setattr(aad, "_PROFILE_CACHE", {"loaded": True, "profiles": fake_profiles})


def test_sync_accounts_forbidden_for_viewer(viewer_client):
    response = viewer_client.post("/api/catalog/accounts/sync")
    assert response.status_code == 403


def test_sync_accounts_enrolls_new_profile(admin_client, web_settings, monkeypatch):
    fake_profiles = [
        {"user_id": "fixture-pid-new-777", "name": "777", "remark": "", "status": "active"},
    ]
    _patch_adspower(monkeypatch, web_settings, fake_profiles)

    response = admin_client.post("/api/catalog/accounts/sync")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["profiles_scanned"] == 1
    assert [e["account_id"] for e in body["enrolled"]] == ["us_store_777"]
    assert body["failed"] == []
    assert body["total"] == 3
    for marker in SECRET_MARKERS:
        assert marker not in response.text, f"secret leaked in sync response: {marker}"

    # 写入 fixture accounts.json，且默认覆盖 US + EU 站点
    import json as _json

    payload = _json.loads(web_settings.accounts_path.read_text(encoding="utf-8"))
    new_acc = next(a for a in payload["accounts"] if a["account_id"] == "us_store_777")
    assert new_acc["marketplace"] == "US"
    assert new_acc["adspower_profile_id"] == "fixture-pid-new-777"
    assert {"US", "UK", "DE", "FR"} <= set(new_acc["marketplace_configs"].keys())


def test_sync_accounts_idempotent(admin_client, web_settings, monkeypatch):
    fake_profiles = [
        {"user_id": "fixture-profile-id-xyz", "name": "999", "remark": "", "status": "active"},
    ]
    _patch_adspower(monkeypatch, web_settings, fake_profiles)

    response = admin_client.post("/api/catalog/accounts/sync")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["enrolled"] == []
    assert body["total"] == 2
