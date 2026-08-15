"""Codex CLI availability probe.

The configured command (``codex.command``) is split with ``shlex`` so tests
can point it at a quoted interpreter + fixture script instead of relying on
PATH.  An unavailable CLI degrades the whole triage chain — jobs are closed
as ``unavailable`` and the main automation never notices.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from src.windows_subprocess import no_window_kwargs


@dataclass
class CodexAvailability:
    available: bool
    version: str = ""
    detail: str = ""


def split_command(command: str) -> list[str]:
    """Split a configured command line into an argv list (no shell)."""
    try:
        return shlex.split(str(command or ""), posix=True)
    except ValueError:
        return []


def resolve_command_argv(command: str) -> list[str]:
    """Split ``command`` and resolve the executable for direct spawning.

    On Windows an npm-installed CLI is typically a ``.CMD``/``.BAT`` shim,
    which ``CreateProcess`` cannot execute directly — wrap it in
    ``cmd.exe /c`` so both the availability probe and the real triage
    subprocess spawn the same way.
    """
    argv = split_command(command)
    if not argv:
        return []
    exe = argv[0]
    looks_like_path = "/" in exe or "\\" in exe
    if not looks_like_path:
        resolved = shutil.which(exe)
        if resolved:
            exe = resolved
            argv[0] = resolved
    if os.name == "nt" and Path(exe).suffix.lower() in {".cmd", ".bat"}:
        return ["cmd.exe", "/c", *argv]
    return argv


def check_availability(
    command: str,
    *,
    timeout_sec: float = 10.0,
    runner=None,
) -> CodexAvailability:
    """Probe the configured Codex CLI: resolvable executable + `--version`."""
    argv = resolve_command_argv(command)
    if not argv:
        return CodexAvailability(False, detail="empty_command")
    exe = argv[0]
    if exe.lower() == "cmd.exe":
        exe = argv[2] if len(argv) > 2 else exe
    looks_like_path = "/" in exe or "\\" in exe
    if looks_like_path:
        if not Path(exe).is_file():
            return CodexAvailability(False, detail=f"command_not_found:{exe}")
    elif shutil.which(exe) is None:
        return CodexAvailability(False, detail=f"command_not_found:{exe}")

    run = runner or subprocess.run
    try:
        run_kwargs = {
            "capture_output": True,
            "text": True,
            "timeout": float(timeout_sec),
        }
        if runner is None:
            run_kwargs.update(no_window_kwargs())
        proc = run([*argv, "--version"], **run_kwargs)
    except Exception as exc:
        return CodexAvailability(False, detail=f"probe_failed:{type(exc).__name__}")
    if int(getattr(proc, "returncode", 1) or 0) != 0:
        return CodexAvailability(False, detail=f"version_exit_{proc.returncode}")
    output = str(getattr(proc, "stdout", "") or "").strip()
    version = output.splitlines()[0].strip() if output else ""
    return CodexAvailability(True, version=version)
