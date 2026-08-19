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


def test_restart_profile_retries_transient_stop_start_handoff(monkeypatch):
    client = AdsPowerClient(min_interval_sec=0)
    starts = []
    sleeps = []

    def fail_stop(**_kwargs):
        raise AdsPowerAPIError("profile is transitioning")

    def start_after_transition(**kwargs):
        starts.append(kwargs)
        if len(starts) == 1:
            raise AdsPowerAPIError("profile is still stopping")
        return {"ws_endpoint": "ws://127.0.0.1:9222/devtools/browser/example"}

    monkeypatch.setattr(client, "stop_profile", fail_stop)
    monkeypatch.setattr(client, "start_profile", start_after_transition)
    monkeypatch.setattr(
        client,
        "wait_for_cdp_ready",
        lambda *_args, **_kwargs: {"ok": True},
    )
    monkeypatch.setattr("src.adspower_client.time.sleep", sleeps.append)

    result = client.restart_profile("profile-private", restart_wait_sec=0.25)

    assert len(starts) == 2
    assert sleeps == [0.25, 0.25]
    assert starts[0]["launch_args"] == ["--remote-allow-origins=*"]
    assert result["restarted"] is True
    assert result["started_now"] is True


def test_restart_profile_stops_after_bounded_start_retries(monkeypatch):
    client = AdsPowerClient(min_interval_sec=0)
    starts = []

    monkeypatch.setattr(client, "stop_profile", lambda **_kwargs: None)

    def fail_start(**_kwargs):
        starts.append(True)
        raise AdsPowerAPIError("temporarily unavailable")

    monkeypatch.setattr(client, "start_profile", fail_start)
    monkeypatch.setattr("src.adspower_client.time.sleep", lambda _seconds: None)

    try:
        client.restart_profile("profile-private", restart_wait_sec=0)
    except AdsPowerAPIError as exc:
        assert "bounded retry" in str(exc)
    else:
        raise AssertionError("persistent start failure should be surfaced")

    assert len(starts) == 2
