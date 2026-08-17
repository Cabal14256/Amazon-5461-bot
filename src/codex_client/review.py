"""Stage-8 read-only Codex review of a committed repair diff."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from src.capture.redact import redact_text
from src.codex_client.availability import resolve_command_argv
from src.codex_client.triage import (
    _kill_process_tree,
    _spawn,
    _validate,
    extract_last_agent_message,
    parse_events,
    parse_json_payload,
)

SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "diff-review-schema.json"
PROMPT_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "knowledge"
    / "prompts"
    / "codex_diff_review_prompt.md"
)


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def build_review_prompt(baseline_sha: str, patch_sha: str, changed_files: list[str]) -> str:
    listing = "\n".join(f"- `{path}`" for path in changed_files) or "- (none)"
    return (
        PROMPT_PATH.read_text(encoding="utf-8")
        .replace("{{BASELINE_SHA}}", str(baseline_sha))
        .replace("{{PATCH_SHA}}", str(patch_sha))
        .replace("{{CHANGED_FILES}}", listing)
    )


def run_diff_review(
    settings,
    *,
    worktree_path: Path,
    baseline_sha: str,
    patch_sha: str,
    changed_files: list[str],
    output_dir: Path,
    timeout_sec: float,
    spawn=None,
) -> dict:
    """Run one schema-constrained review; only a structured pass succeeds."""
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "review-result.json"
    argv = [
        *resolve_command_argv(settings.codex_command),
        "exec",
        "--json",
        "--output-schema",
        str(SCHEMA_PATH),
        "-o",
        str(out_path),
        "-s",
        "read-only",
        "-C",
        str(Path(worktree_path).resolve()),
    ]
    if str(settings.codex_model or "").strip():
        argv += ["-m", str(settings.codex_model).strip()]
    argv.append("-")
    try:
        proc = (spawn or _spawn)(argv, Path(worktree_path))
    except (FileNotFoundError, OSError) as exc:
        return {"passed": False, "exit_code": None, "reason": type(exc).__name__, "log": ""}
    timed_out = False
    try:
        stdout_bytes, stderr_bytes = proc.communicate(
            input=build_review_prompt(baseline_sha, patch_sha, changed_files).encode("utf-8"),
            timeout=max(1.0, float(timeout_sec)),
        )
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_process_tree(proc)
        stdout_bytes, stderr_bytes = proc.communicate()
    stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
    stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
    log = redact_text("\n".join(part for part in (stdout, stderr) if part))
    if timed_out:
        return {"passed": False, "exit_code": None, "reason": "timeout", "log": log}
    if proc.returncode != 0:
        return {"passed": False, "exit_code": int(proc.returncode), "reason": "nonzero", "log": log}
    raw = ""
    try:
        raw = out_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    if not raw.strip():
        _lines, events = parse_events(stdout)
        raw = extract_last_agent_message(events)
    payload = parse_json_payload(raw)
    if payload is None or not _validate(payload, _schema()):
        return {"passed": False, "exit_code": 0, "reason": "schema_invalid", "log": log}
    severe = any(
        str(item.get("severity") or "") in {"medium", "high"}
        for item in payload.get("findings", [])
        if isinstance(item, dict)
    )
    passed = payload.get("verdict") == "pass" and not severe
    return {
        "passed": passed,
        "exit_code": 0,
        "reason": "" if passed else "review_rejected",
        "log": log,
        "payload": payload,
    }
