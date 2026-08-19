from __future__ import annotations

from src.auth_guard import AuthBlockedError, AuthState
from src.auth_recovery import SubmissionReconciliationRequired
from src.executor.legacy_bridge import LegacyBridge


def _bridge_raising(exc: Exception) -> LegacyBridge:
    bridge = object.__new__(LegacyBridge)
    bridge.ACTIONS = {"fixture": "_fixture"}

    def raise_fixture():
        raise exc

    bridge._fixture = raise_fixture
    return bridge


def test_state_loop_propagates_pre_submit_login_stop():
    error = AuthBlockedError(
        AuthState("login_required", True, "login", "https://sellercentral.amazon.com/ap/signin", True),
        phase="state_loop_before_submit",
    )
    error.block_id = 42
    result = _bridge_raising(error).call("fixture")
    assert result == {
        "ok": False,
        "result": None,
        "error": "waiting_login",
        "auth_block_id": 42,
        "stop": True,
    }


def test_state_loop_propagates_fenced_reconciliation_stop():
    result = _bridge_raising(SubmissionReconciliationRequired({"id": 7})).call("fixture")
    assert result["error"] == "waiting_reconciliation"
    assert result["stop"] is True
