from src.adspower_client import AdsPowerAPIError, AdsPowerClient


def test_query_profiles_uses_documented_v2_post(monkeypatch):
    calls = []
    client = AdsPowerClient(min_interval_sec=0)

    def fake_request(method, path, params=None, json_body=None):
        calls.append(
            {
                "method": method,
                "path": path,
                "params": params,
                "json_body": json_body,
            }
        )
        return {
            "code": 0,
            "data": {
                "list": [{"profile_id": "profile-private", "username": "present"}],
                "total_count": 1,
                "page": 2,
                "limit": 50,
            },
        }

    monkeypatch.setattr(client, "_request_json", fake_request)

    result = client.query_profiles(
        group_id="group-private",
        search_value="671",
        page=2,
        page_size=50,
    )

    assert calls == [
        {
            "method": "POST",
            "path": "/api/v2/browser-profile/list",
            "params": None,
            "json_body": {
                "page": 2,
                "limit": 50,
                "group_id": "group-private",
                "search_value": "671",
            },
        }
    ]
    assert result["total"] == 1
    assert result["page_size"] == 50


def test_get_profile_detail_uses_exact_v2_profile_filter(monkeypatch):
    calls = []
    client = AdsPowerClient(min_interval_sec=0)

    def fake_request(method, path, params=None, json_body=None):
        calls.append((method, path, params, json_body))
        return {
            "code": 0,
            "data": {
                "list": [
                    {
                        "profile_id": "profile-private",
                        "name": "account-profile",
                        "username": "seller@example.invalid",
                        "password": "not-a-real-password",
                        "fakey": "not-a-real-2fa-key",
                    }
                ]
            },
        }

    monkeypatch.setattr(client, "_request_json", fake_request)

    result = client.get_profile_detail("profile-private")

    assert calls == [
        (
            "POST",
            "/api/v2/browser-profile/list",
            None,
            {"profile_id": ["profile-private"], "page": 1, "limit": 1},
        )
    ]
    assert result["profile_id"] == "profile-private"
    assert result["profile"]["password"] == "not-a-real-password"
    assert result["profile"]["fakey"] == "not-a-real-2fa-key"


def test_query_profiles_falls_back_to_v1_after_v2_failure(monkeypatch):
    calls = []
    client = AdsPowerClient(min_interval_sec=0)

    def fake_request(method, path, params=None, json_body=None):
        calls.append((method, path, params, json_body))
        if path == "/api/v2/browser-profile/list":
            raise AdsPowerAPIError("unsupported")
        return {
            "code": 0,
            "data": {
                "list": [{"user_id": "legacy-profile", "username": "legacy-user"}],
                "total": 1,
                "page": 1,
                "page_size": 10,
            },
        }

    monkeypatch.setattr(client, "_request_json", fake_request)

    result = client.query_profiles(search_value="671", page_size=10)

    assert calls[0][0:2] == ("POST", "/api/v2/browser-profile/list")
    assert calls[1] == (
        "GET",
        "/api/v1/user/list",
        {"page": "1", "page_size": "10", "search_value": "671"},
        None,
    )
    assert result["profiles"][0]["profile_id"] == "legacy-profile"
