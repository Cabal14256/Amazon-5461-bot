"""Shared fixtures for web console tests.

Everything runs against temporary directories.  The real
``runtime/private/accounts.json`` is never read — a minimal copy fixture is
built per test instead.
"""

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.db import add_web_user, init_db  # noqa: E402
from src.web.app import create_app  # noqa: E402
from src.web.auth import hash_password  # noqa: E402
from src.web.config import WebSettings  # noqa: E402

TEST_SESSION_SECRET = "0123456789abcdef" * 4
TEST_PASSWORD = "test-only-password-123"


@pytest.fixture()
def web_settings(tmp_path):
    runtime = tmp_path / "runtime"
    db_path = runtime / "state" / "ledger.db"
    init_db(str(db_path))

    evidence_root = runtime / "evidence"
    logs_root = runtime / "logs"
    evidence_root.mkdir(parents=True)
    logs_root.mkdir(parents=True)

    private = runtime / "private"
    private.mkdir(parents=True)
    accounts_path = private / "accounts.json"
    accounts_path.write_text(json.dumps({
        "accounts": [
            {
                "account_id": "us_store_999",
                "marketplace": "US",
                "status": "active",
                "note": "fixture account",
                "domain": "amazon.com",
                "item_type_keyword": "screen protector",
                # Secret fields — must never appear in any API response:
                "username": "fixture-secret-user@example.com",
                "password": "fixture-account-password",
                "adspower_profile_id": "fixture-profile-id-xyz",
                "entry_url": "https://fixture-secret-entry.example.com/login",
            },
            {
                "account_id": "uk_store_998",
                "marketplace": "UK",
                "status": "paused",
                "note": "",
                "domain": "amazon.co.uk",
                "item_type_keyword": "screen protector",
                "username": "fixture-other@example.com",
                "adspower_profile_id": "fixture-profile-id-2",
            },
        ]
    }, ensure_ascii=False), encoding="utf-8")

    marketplaces_dir = tmp_path / "marketplaces"
    marketplaces_dir.mkdir()
    (marketplaces_dir / "us.yaml").write_text('marketplace: "US"\n', encoding="utf-8")
    brand_packs_root = tmp_path / "brand_packs"
    (brand_packs_root / "TESTBRAND").mkdir(parents=True)

    return WebSettings(
        db_path=db_path,
        accounts_path=accounts_path,
        marketplaces_dir=marketplaces_dir,
        brand_packs_root=brand_packs_root,
        evidence_root=evidence_root,
        logs_root=logs_root,
        state_root=runtime / "state",
        data_root=tmp_path / "data",
        frontend_dist=tmp_path / "no_frontend_dist",
        codex_signal_path=runtime / "codex_signal.json",
        session_secret=TEST_SESSION_SECRET,
        # Never let a test app probe or spawn a real Codex CLI.
        codex_enabled=False,
    )


@pytest.fixture()
def app(web_settings):
    return create_app(web_settings)


@pytest.fixture()
def client(app):
    return TestClient(app)


def create_user(settings, username: str, password: str, role: str = "viewer") -> int:
    return add_web_user(str(settings.db_path), username, hash_password(password), role=role)


def login(client: TestClient, username: str, password: str) -> None:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text


@pytest.fixture()
def viewer_client(client, web_settings):
    create_user(web_settings, "viewer1", TEST_PASSWORD, "viewer")
    login(client, "viewer1", TEST_PASSWORD)
    return client


@pytest.fixture()
def admin_client(client, web_settings):
    create_user(web_settings, "admin1", TEST_PASSWORD, "admin")
    login(client, "admin1", TEST_PASSWORD)
    return client
