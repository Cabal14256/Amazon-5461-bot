#!/usr/bin/env python3
"""Fail only when repository Ruff debt increases by file and rule."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = PROJECT_ROOT / "config" / "ruff_baseline.json"


def current_counts() -> Counter[str]:
    proc = subprocess.run(
        [sys.executable, "-m", "ruff", "check", ".", "--output-format", "json"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    try:
        findings = json.loads(proc.stdout or "[]")
    except ValueError as exc:
        print(proc.stderr[-2000:], file=sys.stderr)
        raise RuntimeError("ruff_json_invalid") from exc
    counts: Counter[str] = Counter()
    for finding in findings:
        filename = Path(str(finding.get("filename") or ""))
        try:
            relative = filename.resolve().relative_to(PROJECT_ROOT).as_posix()
        except (OSError, ValueError):
            relative = filename.as_posix()
        counts[f"{relative}|{finding.get('code') or 'unknown'}"] += 1
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--update", action="store_true", help="replace the baseline with current counts")
    args = parser.parse_args()
    counts = current_counts()
    baseline_path = args.baseline if args.baseline.is_absolute() else PROJECT_ROOT / args.baseline
    if args.update:
        payload = {"version": 1, "counts": dict(sorted(counts.items()))}
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"updated {baseline_path.relative_to(PROJECT_ROOT)}: {sum(counts.values())} findings")
        return 0
    try:
        payload = json.loads(baseline_path.read_text(encoding="utf-8"))
        baseline = {str(key): int(value) for key, value in payload["counts"].items()}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"invalid Ruff baseline: {baseline_path}: {exc}", file=sys.stderr)
        return 2
    increases = {
        key: (baseline.get(key, 0), count)
        for key, count in counts.items()
        if count > baseline.get(key, 0)
    }
    if increases:
        print("Ruff debt increased:", file=sys.stderr)
        for key, (before, after) in sorted(increases.items()):
            print(f"  {key}: {before} -> {after}", file=sys.stderr)
        return 1
    print(f"Ruff baseline OK: {sum(counts.values())} current findings; no file/rule increase")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
