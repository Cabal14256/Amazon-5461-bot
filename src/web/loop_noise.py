"""Narrowly suppress benign peer-disconnect errors from asyncio transports."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

_BENIGN_TEARDOWN_ERRORS = (
    ConnectionResetError,
    ConnectionAbortedError,
    BrokenPipeError,
)
_INSTALLED_MARKER = "_amazon5461_loop_noise_filter_installed"


def _is_benign_connection_teardown(context: dict[str, Any]) -> bool:
    """Return whether *context* is a peer hangup during transport teardown."""

    exception = context.get("exception")
    if not isinstance(exception, _BENIGN_TEARDOWN_ERRORS):
        return False

    marker = "_call_connection_lost"
    return marker in repr(context.get("callback")) or marker in repr(context.get("handle"))


def install_loop_noise_filter(loop: asyncio.AbstractEventLoop) -> None:
    """Suppress only benign connection-teardown noise on *loop*.

    Every other event-loop error is forwarded to the handler that was active
    before installation, or to asyncio's default handler when none was set.
    """

    if getattr(loop, _INSTALLED_MARKER, False):
        return

    previous_handler = loop.get_exception_handler()

    def _handler(current_loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        if _is_benign_connection_teardown(context):
            logger.debug(
                "peer disconnected during asyncio transport teardown (suppressed): %s",
                context.get("exception"),
            )
            return
        if previous_handler is not None:
            previous_handler(current_loop, context)
        else:
            current_loop.default_exception_handler(context)

    loop.set_exception_handler(_handler)
    try:
        setattr(loop, _INSTALLED_MARKER, True)
    except (AttributeError, TypeError):  # pragma: no cover - exotic loop implementation
        pass
