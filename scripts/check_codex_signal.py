#!/usr/bin/env python3
"""Inspect or acknowledge the local Codex diagnosis signal."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.codex_signal import DEFAULT_SIGNAL_PATH, mark_signal_handled, read_signal  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect runtime/codex_signal.json without invoking a model")
    parser.add_argument("--signal", type=Path, default=DEFAULT_SIGNAL_PATH, help="alternate signal JSON path")
    parser.add_argument("--mark-handled", action="store_true", help="acknowledge the current signal")
    parser.add_argument("--note", default="", help="short audit note used with --mark-handled")
    args = parser.parse_args()

    if args.note and not args.mark_handled:
        parser.error("--note requires --mark-handled")

    signal = (
        mark_signal_handled(note=args.note, signal_path=args.signal) if args.mark_handled else read_signal(args.signal)
    )
    if signal is None:
        output = {"pending": False, "status": "absent", "signal_path": str(args.signal.resolve())}
    else:
        output = dict(signal)
        output["pending"] = signal.get("status") == "pending"
        output["signal_path"] = str(args.signal.resolve())
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
