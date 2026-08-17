"""Eight-step Stage-8 validation gate for an isolated repair worktree."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

from src.capture.redact import redact_text
from src.codex_client.review import run_diff_review
from src.codex_client.triage import _kill_process_tree
from src.repair.diff_scan import path_is_allowed, scan_diff
from src.windows_subprocess import no_window_kwargs

VALIDATION_STEP_NAMES = (
    "allowed_paths",
    "diff_safety_scan",
    "compileall",
    "full_pytest",
    "ruff_changed_python",
    "contract_tests",
    "offline_fixture_replay",
    "codex_read_only_review",
)

_OFFLINE_FORBIDDEN_RE = re.compile(
    r"(?i)(https?://|sellercentral|\brequests\.|urllib\.request|\bsocket\.|"
    r"playwright|runtime/private|(?:^|[\\/])\.env\b|brand_packs|(?:^|[\\/])data[\\/])"
)


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _safe_relative(path: str) -> bool:
    candidate = Path(str(path))
    return bool(str(path).strip()) and not candidate.is_absolute() and ".." not in candidate.parts


def _write_log(log_dir: Path, index: int, name: str, text: str) -> str:
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"{index:02d}-{name}.log"
    # Validation logs are durable but bounded and redacted before persistence.
    path.write_text(redact_text(str(text or ""))[-1_000_000:], encoding="utf-8")
    return str(path)


def _run_command(
    command: list[str],
    *,
    cwd: Path,
    deadline: float,
    heartbeat: Callable[[], None] | None,
    env: dict[str, str] | None = None,
) -> tuple[int | None, str, str]:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None, "", "validation_timeout"
    proc = subprocess.Popen(
        command,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        **no_window_kwargs(),
    )
    output = b""
    while True:
        try:
            output, _ = proc.communicate(timeout=max(0.1, min(2.0, deadline - time.monotonic())))
            break
        except subprocess.TimeoutExpired:
            if heartbeat:
                heartbeat()
            if time.monotonic() >= deadline:
                _kill_process_tree(proc)
                output, _ = proc.communicate()
                return None, output.decode("utf-8", errors="replace"), "validation_timeout"
    return int(proc.returncode), output.decode("utf-8", errors="replace"), ""


def _load_patch_result(path: object) -> dict:
    try:
        payload = json.loads(Path(str(path)).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def run_validation_request(
    request: dict,
    *,
    heartbeat: Callable[[], None] | None = None,
    reviewer: Callable[..., dict] = run_diff_review,
) -> dict:
    """Execute all Stage-8 gates and persist validation.json after each step."""
    worktree = Path(str(request["worktree_path"])).resolve()
    output_path = Path(str(request["validation_json_path"])).resolve()
    log_dir = Path(str(request["validation_log_dir"])).resolve()
    baseline_sha = str(request.get("baseline_sha") or "")
    patch_sha = str(request.get("patch_sha") or "")
    declared_changed = [
        str(path).replace("\\", "/") for path in request.get("changed_files") or []
    ]
    allowed_paths = [str(path).replace("\\", "/") for path in request.get("allowed_paths") or []]
    timeout_sec = max(1, int(request.get("timeout_sec") or 900))
    started_wall = time.time()
    deadline = time.monotonic() + timeout_sec
    report = {
        "version": 1,
        "job_id": int(request["job_id"]),
        "status": "running",
        "baseline_sha": baseline_sha,
        "patch_sha": patch_sha,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "finished_at": None,
        "failure_reason": "",
        "steps": [],
    }
    _write_json_atomic(output_path, report)

    def finish_step(name: str, started: float, passed: bool, *, exit_code=None,
                    output: str = "", reason: str = "", detail: dict | None = None) -> bool:
        index = len(report["steps"]) + 1
        log_path = _write_log(log_dir, index, name, output)
        report["steps"].append({
            "name": name,
            "status": "passed" if passed else "failed",
            "duration_sec": round(time.monotonic() - started, 3),
            "exit_code": exit_code,
            "log_path": log_path,
            "failure_reason": "" if passed else str(reason or name),
            "detail": detail or {},
        })
        if not passed:
            report["status"] = "failed"
            report["failure_reason"] = str(reason or name)
        _write_json_atomic(output_path, report)
        if heartbeat:
            heartbeat()
        return passed

    python = str(request.get("python_executable") or sys.executable)

    # 1. Git-derived changed files must match the persisted list and allowlist.
    started = time.monotonic()
    rc, output, reason = _run_command(
        ["git", "diff", "--name-only", f"{baseline_sha}..{patch_sha}"],
        cwd=worktree, deadline=deadline, heartbeat=heartbeat,
    )
    actual_changed = [line.strip().replace("\\", "/") for line in output.splitlines() if line.strip()]
    invalid_paths = [
        path for path in actual_changed
        if not _safe_relative(path) or not path_is_allowed(path, allowed_paths)
    ]
    passed = (
        rc == 0
        and not reason
        and bool(actual_changed)
        and set(actual_changed) == set(declared_changed)
        and not invalid_paths
    )
    if not finish_step(
        "allowed_paths", started, passed, exit_code=rc, output=output,
        reason=reason or "changed_file_scope_mismatch",
        detail={"changed_files": actual_changed, "invalid_paths": invalid_paths},
    ):
        return _finish_report(report, output_path, started_wall)

    # 2. Repeat the complete private/safety diff scan on the committed patch.
    started = time.monotonic()
    rc, diff_text, reason = _run_command(
        ["git", "diff", "--binary", f"{baseline_sha}..{patch_sha}"],
        cwd=worktree, deadline=deadline, heartbeat=heartbeat,
    )
    scan = scan_diff(diff_text, allowed_paths=allowed_paths) if rc == 0 and not reason else None
    violations = [] if scan is None else [
        {"rule_id": item.rule_id, "file": item.file, "detail": item.detail}
        for item in scan.violations
    ]
    passed = rc == 0 and not reason and scan is not None and scan.passed
    if not finish_step(
        "diff_safety_scan", started, passed, exit_code=rc,
        output=json.dumps(violations, ensure_ascii=False, indent=2),
        reason=reason or "diff_safety_violation", detail={"violations": violations},
    ):
        return _finish_report(report, output_path, started_wall)

    # 3. Compile tracked Python scopes.
    started = time.monotonic()
    rc, output, reason = _run_command(
        [python, "-m", "compileall", "-q", "src", "tests"],
        cwd=worktree, deadline=deadline, heartbeat=heartbeat,
    )
    if not finish_step(
        "compileall", started, rc == 0 and not reason, exit_code=rc,
        output=output, reason=reason or "compileall_failed",
    ):
        return _finish_report(report, output_path, started_wall)

    # 4. Full offline project test suite.
    started = time.monotonic()
    rc, output, reason = _run_command(
        [python, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=worktree, deadline=deadline, heartbeat=heartbeat,
    )
    if not finish_step(
        "full_pytest", started, rc == 0 and not reason, exit_code=rc,
        output=output, reason=reason or "full_pytest_failed",
    ):
        return _finish_report(report, output_path, started_wall)

    # 5. Ruff only the Python files changed by this patch.
    started = time.monotonic()
    changed_python = [path for path in actual_changed if path.endswith(".py")]
    if changed_python:
        rc, output, reason = _run_command(
            [python, "-m", "ruff", "check", *changed_python],
            cwd=worktree, deadline=deadline, heartbeat=heartbeat,
        )
    else:
        rc, output, reason = 0, "no changed Python files\n", ""
    if not finish_step(
        "ruff_changed_python", started, rc == 0 and not reason, exit_code=rc,
        output=output, reason=reason or "ruff_failed",
        detail={"files": changed_python},
    ):
        return _finish_report(report, output_path, started_wall)

    result = _load_patch_result(request.get("result_json_path"))

    # 6. Codex must declare and add meaningful contract tests.
    started = time.monotonic()
    contract_tests = [str(path) for path in result.get("tests_added") or []]
    valid_contracts = bool(contract_tests) and all(
        _safe_relative(path)
        and path.startswith("tests/")
        and path.endswith(".py")
        and path in actual_changed
        and (worktree / path).is_file()
        for path in contract_tests
    )
    if valid_contracts:
        rc, output, reason = _run_command(
            [python, "-m", "pytest", "-q", "-p", "no:cacheprovider", *contract_tests],
            cwd=worktree, deadline=deadline, heartbeat=heartbeat,
        )
    else:
        rc, output, reason = None, "", "missing_or_invalid_contract_tests"
    if not finish_step(
        "contract_tests", started, valid_contracts and rc == 0 and not reason,
        exit_code=rc, output=output, reason=reason or "contract_tests_failed",
        detail={"tests": contract_tests},
    ):
        return _finish_report(report, output_path, started_wall)

    # 7. Re-run declared replay tests against changed, sanitized fixtures.
    started = time.monotonic()
    replay_tests = [str(path) for path in result.get("offline_replay_tests") or []]
    fixtures = [str(path) for path in result.get("offline_fixture_paths") or []]
    offline_paths = replay_tests + fixtures
    valid_offline = (
        bool(replay_tests)
        and bool(fixtures)
        and all(_safe_relative(path) and path in actual_changed for path in offline_paths)
        and all(path.startswith("tests/fixtures/") for path in fixtures)
        and all(path in contract_tests for path in replay_tests)
        and all((worktree / path).is_file() for path in offline_paths)
    )
    forbidden_hits: list[str] = []
    if valid_offline:
        for path in offline_paths:
            text = (worktree / path).read_text(encoding="utf-8", errors="replace")
            if _OFFLINE_FORBIDDEN_RE.search(text):
                forbidden_hits.append(path)
        valid_offline = not forbidden_hits
    if valid_offline:
        offline_env = dict(os.environ)
        offline_env.update({
            "NO_NETWORK": "1",
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "NO_PROXY": "",
        })
        rc, output, reason = _run_command(
            [python, "-m", "pytest", "-q", "-p", "no:cacheprovider", *replay_tests],
            cwd=worktree, deadline=deadline, heartbeat=heartbeat, env=offline_env,
        )
    else:
        rc, output, reason = None, "", "offline_fixture_contract_failed"
    if not finish_step(
        "offline_fixture_replay", started, valid_offline and rc == 0 and not reason,
        exit_code=rc, output=output, reason=reason or "offline_replay_failed",
        detail={"tests": replay_tests, "fixtures": fixtures, "forbidden_hits": forbidden_hits},
    ):
        return _finish_report(report, output_path, started_wall)

    # 8. A schema-constrained, read-only Codex review is the final gate.
    started = time.monotonic()
    if not bool(request.get("diff_review_enabled", True)):
        review = {"passed": False, "exit_code": None, "reason": "diff_review_disabled", "log": ""}
    else:
        review_settings = SimpleNamespace(
            codex_command=str(request.get("codex_command") or "codex"),
            codex_model=str(request.get("codex_model") or ""),
        )
        review = reviewer(
            review_settings,
            worktree_path=worktree,
            baseline_sha=baseline_sha,
            patch_sha=patch_sha,
            changed_files=actual_changed,
            output_dir=log_dir,
            timeout_sec=max(1.0, deadline - time.monotonic()),
        )
    if not finish_step(
        "codex_read_only_review", started, bool(review.get("passed")),
        exit_code=review.get("exit_code"), output=str(review.get("log") or ""),
        reason=str(review.get("reason") or "review_rejected"),
        detail={"result": review.get("payload") or {}},
    ):
        return _finish_report(report, output_path, started_wall)

    report["status"] = "pass"
    return _finish_report(report, output_path, started_wall)


def _finish_report(report: dict, output_path: Path, started_wall: float) -> dict:
    report["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    report["duration_sec"] = round(time.time() - started_wall, 3)
    if report["status"] == "running":
        report["status"] = "failed"
        report["failure_reason"] = "validation_incomplete"
    for name in VALIDATION_STEP_NAMES[len(report["steps"]):]:
        report["steps"].append({
            "name": name,
            "status": "skipped",
            "duration_sec": 0.0,
            "exit_code": None,
            "log_path": None,
            "failure_reason": "prior_step_failed",
            "detail": {},
        })
    _write_json_atomic(output_path, report)
    return report
