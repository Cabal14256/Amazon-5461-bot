"""Web startup stays available when persisted Git reconciliation raises."""

from __future__ import annotations

import logging

from fastapi.testclient import TestClient

from src.web.app import create_app


class _Manager:
    def __init__(self):
        self.recovered = False
        self.started = False

    def recover(self):
        self.recovered = True

    def start(self):
        self.started = True

    async def stop(self):
        return None


class _RepairRunner:
    def recover(self):
        return None

    def start(self):
        return None

    async def stop(self):
        return None


def test_startup_reconciliation_error_is_logged_without_stopping_console(
    web_settings, monkeypatch, caplog
):
    manager = _Manager()

    def fail_reconciliation(_settings):
        raise RuntimeError("fixture reconciliation failure")

    monkeypatch.setattr(
        "src.repair.release_recovery.reconcile_git_operations", fail_reconciliation
    )
    app = create_app(web_settings, job_manager=manager, repair_runner=_RepairRunner())

    with caplog.at_level(logging.ERROR), TestClient(app) as client:
        assert client.get("/api/auth/me").status_code in {200, 401}

    assert manager.recovered is True
    assert manager.started is True
    assert "Startup Git reconciliation failed" in caplog.text
