from types import SimpleNamespace

import pytest

from src import browser_manager
from src.adspower_client import AdsPowerClient
from src.browser_manager import BrowserConnectionError, BrowserManager


class _FakePage:
    url = "https://sellercentral.amazon.co.uk/home?secret=query"


class _FakeContext:
    def __init__(self):
        self.pages = [_FakePage()]

    def new_page(self):
        page = _FakePage()
        self.pages.append(page)
        return page


class _FakeBrowser:
    def __init__(self):
        self.contexts = [_FakeContext()]
        self.close_calls = 0

    def new_context(self):
        context = _FakeContext()
        self.contexts.append(context)
        return context

    def close(self):
        self.close_calls += 1


class _FakeChromium:
    def __init__(self, outcomes, calls):
        self.outcomes = outcomes
        self.calls = calls

    def connect_over_cdp(self, ws_url, timeout):
        self.calls.append({"ws_url": ws_url, "timeout": timeout})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _FakePlaywright:
    def __init__(self, outcomes, calls, stopped):
        self.chromium = _FakeChromium(outcomes, calls)
        self.stopped = stopped

    def stop(self):
        self.stopped.append(True)


class _FakeStarter:
    def __init__(self, outcomes, calls, stopped):
        self.outcomes = outcomes
        self.calls = calls
        self.stopped = stopped

    def start(self):
        return _FakePlaywright(self.outcomes, self.calls, self.stopped)


class _FakeClient:
    def __init__(self):
        self.restart_calls = 0
        self.stop_calls = 0

    def ensure_profile_started(self, **_kwargs):
        return {"ok": True, "ws_endpoint": "ws://127.0.0.1:9222/devtools/browser/private"}

    def restart_profile(self, **_kwargs):
        self.restart_calls += 1
        return {"ok": True, "ws_endpoint": "ws://127.0.0.1:9333/devtools/browser/private"}

    def stop_profile(self, **_kwargs):
        self.stop_calls += 1
        return {"ok": True}


def _install_fake_playwright(monkeypatch, outcomes):
    calls = []
    stopped = []
    monkeypatch.setattr(
        browser_manager,
        "sync_playwright",
        lambda: _FakeStarter(outcomes, calls, stopped),
    )
    return calls, stopped


def test_browser_manager_uses_short_explicit_cdp_timeout_and_detaches(monkeypatch):
    fake_browser = _FakeBrowser()
    calls, stopped = _install_fake_playwright(monkeypatch, [fake_browser])
    manager = BrowserManager(client=_FakeClient(), cdp_connect_timeout_ms=30_000)

    page = manager.connect("profile-private")
    manager.close()

    assert page.url.startswith("https://sellercentral")
    assert calls == [
        {"ws_url": "ws://127.0.0.1:9222/devtools/browser/private", "timeout": 30_000}
    ]
    assert stopped == [True]
    assert fake_browser.close_calls == 0


def test_browser_manager_restarts_once_after_cdp_handshake_failure(monkeypatch):
    recovered_browser = _FakeBrowser()
    calls, _stopped = _install_fake_playwright(
        monkeypatch,
        [TimeoutError("stale endpoint"), recovered_browser],
    )
    client = _FakeClient()
    manager = BrowserManager(
        client=client,
        cdp_connect_timeout_ms=10_000,
        cdp_restart_attempts=1,
        profile_restart_wait_sec=0,
    )

    manager.connect("profile-private")

    assert client.restart_calls == 1
    assert [call["timeout"] for call in calls] == [10_000, 10_000]


def test_browser_manager_raises_sanitized_error_after_recovery_is_exhausted(monkeypatch):
    _install_fake_playwright(
        monkeypatch,
        [TimeoutError("ws://127.0.0.1:9222/devtools/browser/secret")],
    )
    manager = BrowserManager(client=_FakeClient(), cdp_restart_attempts=0)

    with pytest.raises(BrowserConnectionError) as caught:
        manager.connect("profile-private")

    assert caught.value.error_code == "cdp_connect_failed"
    assert "ws://" not in str(caught.value)
    assert "profile-private" not in str(caught.value)


def test_adspower_cdp_health_requires_tcp_and_devtools_version(monkeypatch):
    class _Socket:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr("src.adspower_client.socket.create_connection", lambda *_args, **_kwargs: _Socket())
    monkeypatch.setattr(
        "src.adspower_client.requests.get",
        lambda *_args, **_kwargs: SimpleNamespace(
            status_code=200,
            json=lambda: {"Browser": "Chrome", "webSocketDebuggerUrl": "ws://local/devtools/browser/id"},
        ),
    )

    health = AdsPowerClient().check_cdp_health("ws://127.0.0.1:9222/devtools/browser/private")

    assert health == {"ok": True, "tcp_open": True, "devtools_http_ok": True}
