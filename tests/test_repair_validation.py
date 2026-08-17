"""Stage-8 eight-step validation gate in a throwaway Git repository."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from src.repair.validation import (
    VALIDATION_STEP_NAMES,
    _run_command,
    run_validation_request,
)
from tests.test_repair_worktree import git, make_git_repo


def _request(tmp_path, *, tests_added=True):
    repo = make_git_repo(tmp_path / "repo")
    baseline = git(repo, "rev-parse", "HEAD").strip()
    (repo / "config" / "selectors" / "us.yaml").write_text(
        "submit_button: '#submit-fixed'\n", encoding="utf-8"
    )
    fixture = repo / "tests" / "fixtures" / "selector.html"
    fixture.parent.mkdir(parents=True, exist_ok=True)
    fixture.write_text("<button id='submit-fixed'>Submit</button>\n", encoding="utf-8")
    contract = repo / "tests" / "test_selector_contract.py"
    contract.write_text(
        "from pathlib import Path\n\n\n"
        "def test_offline_selector_fixture():\n"
        "    text = (Path(__file__).parent / 'fixtures' / 'selector.html').read_text()\n"
        "    assert \"id='submit-fixed'\" in text\n",
        encoding="utf-8",
    )
    git(repo, "add", "config", "tests")
    git(repo, "commit", "-m", "repair patch")
    patch_sha = git(repo, "rev-parse", "HEAD").strip()
    changed = [
        line.strip()
        for line in git(repo, "diff", "--name-only", f"{baseline}..{patch_sha}").splitlines()
        if line.strip()
    ]
    result = {
        "tests_added": ["tests/test_selector_contract.py"] if tests_added else [],
        "offline_replay_tests": ["tests/test_selector_contract.py"] if tests_added else [],
        "offline_fixture_paths": ["tests/fixtures/selector.html"] if tests_added else [],
    }
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(result), encoding="utf-8")
    return {
        "job_id": 7,
        "worktree_path": str(repo),
        "baseline_sha": baseline,
        "patch_sha": patch_sha,
        "changed_files": changed,
        "allowed_paths": ["config/selectors/", "src/executor/", "src/capture/", "tests/"],
        "timeout_sec": 120,
        "result_json_path": str(result_path),
        "validation_json_path": str(tmp_path / "state" / "validation.json"),
        "validation_log_dir": str(tmp_path / "logs"),
        "python_executable": sys.executable,
        "codex_command": "codex-test",
        "codex_model": "",
        "diff_review_enabled": True,
    }


def _passing_review(*_args, **_kwargs):
    return {
        "passed": True,
        "exit_code": 0,
        "reason": "",
        "log": "reviewer leaked@example.com then passed",
        "payload": {"verdict": "pass", "summary": "safe", "findings": []},
    }


def test_eight_step_validation_passes_and_redacts_logs(tmp_path):
    request = _request(tmp_path)
    heartbeats = []

    report = run_validation_request(
        request, heartbeat=lambda: heartbeats.append(1), reviewer=_passing_review
    )

    assert report["status"] == "pass"
    assert [step["name"] for step in report["steps"]] == list(VALIDATION_STEP_NAMES)
    assert all(step["status"] == "passed" for step in report["steps"])
    assert heartbeats
    review_log = Path(report["steps"][-1]["log_path"]).read_text(encoding="utf-8")
    assert "leaked@example.com" not in review_log
    assert "[REDACTED_EMAIL]" in review_log
    persisted = json.loads(Path(request["validation_json_path"]).read_text(encoding="utf-8"))
    assert persisted["status"] == "pass"


def test_missing_regression_contract_fails_closed(tmp_path):
    request = _request(tmp_path, tests_added=False)

    report = run_validation_request(request, reviewer=_passing_review)

    assert report["status"] == "failed"
    contract = next(step for step in report["steps"] if step["name"] == "contract_tests")
    assert contract["status"] == "failed"
    assert contract["failure_reason"] == "missing_or_invalid_contract_tests"
    assert report["steps"][-1]["status"] == "skipped"


def test_read_only_review_must_return_structured_pass(tmp_path):
    request = _request(tmp_path)

    report = run_validation_request(
        request,
        reviewer=lambda *_args, **_kwargs: {
            "passed": False,
            "exit_code": 0,
            "reason": "schema_invalid",
            "log": "",
        },
    )

    assert report["status"] == "failed"
    assert report["failure_reason"] == "schema_invalid"


def test_validation_command_timeout_kills_child(tmp_path):
    exit_code, _output, reason = _run_command(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        cwd=tmp_path,
        deadline=time.monotonic() + 0.2,
        heartbeat=None,
    )

    assert exit_code is None
    assert reason == "validation_timeout"
