"""Atomic JSON state file helpers shared by batch workers and the web console.

State files (batch state, codex signal, worker metadata) may be read by the
web console while a worker is rewriting them.  Writers use
``atomic_write_json`` (temp file + ``os.replace``) so readers never see a
truncated file; readers use ``read_json_tolerant`` which briefly retries on a
parse failure and returns ``None`` instead of raising.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def atomic_write_json(path: Path | str, payload: Any) -> None:
    """Write ``payload`` as JSON to ``path`` atomically."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def read_json_tolerant(
    path: Path | str,
    *,
    retries: int = 3,
    delay_sec: float = 0.2,
) -> Any | None:
    """Read a JSON file, tolerating a transient half-written file.

    Returns ``None`` when the file does not exist or still fails to parse
    after the retry budget, instead of raising ``json.JSONDecodeError``.
    """
    path = Path(path)
    if not path.exists():
        return None
    for attempt in range(max(1, int(retries))):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            if attempt < retries - 1:
                time.sleep(max(0.0, float(delay_sec)))
    return None
