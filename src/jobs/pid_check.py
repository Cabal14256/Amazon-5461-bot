"""Best-effort local PID liveness check.

``os.kill(pid, 0)`` is POSIX-only; on Windows it raises ``SystemError``, so
use ``OpenProcess`` there, keeping the project free of a psutil dependency.
Moved here from ``src/web/api/jobs.py`` so both the web layer and the job
manager share one implementation.
"""

from __future__ import annotations

import os


def pid_running(pid: int | None) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        return _pid_running_windows(int(pid))
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _pid_running_windows(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    ERROR_ACCESS_DENIED = 5

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        # Access denied still proves the process exists.
        return ctypes.GetLastError() == ERROR_ACCESS_DENIED
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)
