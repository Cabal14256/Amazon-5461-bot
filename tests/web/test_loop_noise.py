"""Tests for the web server's narrow asyncio teardown-noise filter."""

from __future__ import annotations

import asyncio

from src.web.loop_noise import (
    _is_benign_connection_teardown,
    install_loop_noise_filter,
)


class _ConnectionLostHandle:
    def __repr__(self) -> str:
        return "<Handle _ProactorBasePipeTransport._call_connection_lost(None)>"


def test_matches_connection_reset_from_connection_lost_callback():
    context = {
        "exception": ConnectionResetError(10054, "forcibly closed"),
        "handle": _ConnectionLostHandle(),
    }

    assert _is_benign_connection_teardown(context) is True


def test_matches_other_peer_hangup_errors_from_connection_lost_callback():
    for exception in (
        ConnectionAbortedError(10053, "aborted"),
        BrokenPipeError("broken pipe"),
    ):
        context = {"exception": exception, "callback": _ConnectionLostHandle()}
        assert _is_benign_connection_teardown(context) is True


def test_does_not_hide_connection_reset_from_other_code():
    context = {
        "exception": ConnectionResetError(10054, "real failure"),
        "handle": "<Handle application_callback()>",
    }

    assert _is_benign_connection_teardown(context) is False


def test_does_not_hide_unrelated_exception_from_connection_lost_callback():
    context = {
        "exception": RuntimeError("real loop failure"),
        "handle": _ConnectionLostHandle(),
    }

    assert _is_benign_connection_teardown(context) is False


def test_install_suppresses_only_teardown_noise_and_is_idempotent():
    loop = asyncio.new_event_loop()
    try:
        forwarded: list[dict] = []
        loop.set_exception_handler(lambda _loop, context: forwarded.append(context))

        install_loop_noise_filter(loop)
        installed_handler = loop.get_exception_handler()
        install_loop_noise_filter(loop)

        assert loop.get_exception_handler() is installed_handler

        loop.call_exception_handler(
            {
                "exception": ConnectionResetError(10054, "forcibly closed"),
                "handle": _ConnectionLostHandle(),
            }
        )
        assert forwarded == []

        real_context = {"exception": RuntimeError("real loop failure")}
        loop.call_exception_handler(real_context)
        assert forwarded == [real_context]
    finally:
        loop.close()
