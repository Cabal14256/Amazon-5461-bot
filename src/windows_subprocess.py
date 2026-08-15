"""Small helpers for spawning background commands without Windows consoles."""

from __future__ import annotations

import os
import subprocess
from typing import Any


def no_window_kwargs() -> dict[str, Any]:
    """Return portable ``subprocess`` kwargs that suppress Windows consoles."""
    if os.name != "nt":
        return {}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    startupinfo = None
    startupinfo_type = getattr(subprocess, "STARTUPINFO", None)
    if startupinfo_type is not None:
        startupinfo = startupinfo_type()
        startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
        startupinfo.wShowWindow = 0  # SW_HIDE
    kwargs: dict[str, Any] = {"creationflags": flags}
    if startupinfo is not None:
        kwargs["startupinfo"] = startupinfo
    return kwargs
