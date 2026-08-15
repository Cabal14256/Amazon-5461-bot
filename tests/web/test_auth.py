"""Auth tests: login, failure rate limiting, role gating, session expiry."""

import time

from src.web.auth import LoginRateLimiter, make_session_token
from tests.web.conftest import TEST_PASSWORD, TEST_SESSION_SECRET, create_user, login


def test_login_success_and_me(client, web_settings):
    create_user(web_settings, "alice", TEST_PASSWORD, "viewer")
    response = client.post("/api/auth/login", json={"username": "alice", "password": TEST_PASSWORD})
    assert response.status_code == 200
    cookie = response.cookies.get("web_session")
    assert cookie
    # Cookie flags: HttpOnly + SameSite=Lax
    set_cookie = response.headers["set-cookie"]
    assert "HttpOnly" in set_cookie
    assert "samesite=lax" in set_cookie.lower()

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["username"] == "alice"
    assert me.json()["user"]["role"] == "viewer"
    assert "password_hash" not in me.text
    assert TEST_PASSWORD not in me.text


def test_login_wrong_password(client, web_settings):
    create_user(web_settings, "bob", TEST_PASSWORD, "viewer")
    response = client.post("/api/auth/login", json={"username": "bob", "password": "wrong-password"})
    assert response.status_code == 401


def test_login_rate_limit(client, web_settings, app):
    create_user(web_settings, "carol", TEST_PASSWORD, "viewer")
    app.state.rate_limiter = LoginRateLimiter(max_attempts=3, base_backoff_sec=60.0)
    for _ in range(3):
        response = client.post("/api/auth/login", json={"username": "carol", "password": "nope-nope-nope"})
        assert response.status_code == 401
    blocked = client.post("/api/auth/login", json={"username": "carol", "password": "nope-nope-nope"})
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers
    # Even the correct password is rejected while the backoff window is open.
    still_blocked = client.post("/api/auth/login", json={"username": "carol", "password": TEST_PASSWORD})
    assert still_blocked.status_code == 429


def test_unauthenticated_requests_are_401(client):
    for path in ("/api/health", "/api/catalog/accounts", "/api/jobs",
                 "/api/applications", "/api/case-followups", "/api/case-id-recoveries",
                 "/api/reapplications", "/api/overview", "/api/evidence/list", "/api/users"):
        assert client.get(path).status_code == 401, path


def test_viewer_cannot_access_admin_endpoint(viewer_client):
    assert viewer_client.get("/api/users").status_code == 403


def test_admin_can_list_users(admin_client):
    response = admin_client.get("/api/users")
    assert response.status_code == 200
    users = response.json()["users"]
    assert any(u["username"] == "admin1" for u in users)
    assert "password_hash" not in response.text


def test_disabled_user_session_rejected(client, web_settings):
    from src.db import set_web_user_disabled

    uid = create_user(web_settings, "dave", TEST_PASSWORD, "viewer")
    login(client, "dave", TEST_PASSWORD)
    assert client.get("/api/health").status_code == 200
    set_web_user_disabled(str(web_settings.db_path), "dave", True)
    assert client.get("/api/health").status_code == 401
    assert uid > 0


def test_expired_session_rejected(client, web_settings):
    uid = create_user(web_settings, "erin", TEST_PASSWORD, "viewer")
    expired = make_session_token(
        TEST_SESSION_SECRET, uid, "erin", "viewer",
        ttl_hours=12.0, now=time.time() - 13 * 3600,
    )
    client.cookies.set("web_session", expired)
    assert client.get("/api/health").status_code == 401


def test_tampered_session_rejected(client, web_settings):
    uid = create_user(web_settings, "frank", TEST_PASSWORD, "viewer")
    token = make_session_token(TEST_SESSION_SECRET, uid, "frank", "viewer")
    body, _, _ = token.rpartition(".")
    client.cookies.set("web_session", body + "." + "0" * 64)
    assert client.get("/api/health").status_code == 401


def test_logout_clears_session(viewer_client):
    assert viewer_client.get("/api/health").status_code == 200
    response = viewer_client.post("/api/auth/logout")
    assert response.status_code == 200
    viewer_client.cookies.clear()
    assert viewer_client.get("/api/health").status_code == 401
