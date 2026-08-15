"""Stage-7 /api/incidents/{id}/generate-patch + /api/repair-jobs/* endpoints.

A mock Codex CLI (Python fixture script injected through ``codex.command``,
never PATH) answers ``--version`` and ``exec``: it applies a real selector
edit inside the isolated worktree (``-C`` argument) and writes the schema
result to ``-o``.  Git runs for real against a throwaway fixture repository;
the production repository is never touched.  The core assertion: the
fixture "production" tree's ``git status`` and HEAD never change.
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.db import (  # noqa: E402
    create_patch_job,
    create_repair_job,
    finish_repair_job,
    get_incident,
    mark_incident_triaged,
    record_incident,
)
from tests.test_repair_worktree import git, make_git_repo  # noqa: E402
from tests.web.conftest import TEST_PASSWORD, create_user, login  # noqa: E402

MOCK_CODEX = r'''
import json
import sys
from pathlib import Path


def main():
    argv = sys.argv[1:]
    if "--version" in argv:
        print("codex-cli 0.147.0-mock")
        return 0
    sys.stdin.read()  # prompt on stdin
    worktree = Path(argv[argv.index("-C") + 1])
    out_path = argv[argv.index("-o") + 1]
    # The "agent": a real selector change inside the isolated worktree only.
    selector = worktree / "config" / "selectors" / "us.yaml"
    selector.write_text("submit_button: '#submit-fixed'\n", encoding="utf-8")
    (worktree / "tests" / "test_selector_regression.py").write_text(
        "def test_selector():\n    assert True\n", encoding="utf-8"
    )
    payload = json.dumps({
        "summary": "mock selector patch",
        "changed_files": ["config/selectors/us.yaml", "tests/test_selector_regression.py"],
        "risk_level": "R0",
        "requires_human_review": False,
        "tests_added": ["tests/test_selector_regression.py"],
        "tests_ran": True,
        "tests_passed": True,
        "notes": "",
    })
    print(json.dumps({"type": "thread.started", "thread_id": "thr_patch_mock"}))
    print(json.dumps({
        "type": "item.completed",
        "item": {"id": "item_0", "type": "agent_message", "text": payload},
    }))
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(payload)
    return 0


sys.exit(main())
'''

TRIAGE_PAYLOAD = {
    "classification": "selector_change",
    "confidence": 0.85,
    "reason": "submit selector no longer matches",
    "affected_components": ["config/selectors/us.yaml"],
    "recommended_scope": "selector update",
    "safe_to_generate_patch": True,
    "requires_human_review": False,
    "missing_evidence": [],
}


@pytest.fixture()
def patch_env(web_settings, tmp_path):
    """web_settings wired to a fixture git repo + the mock Codex CLI."""
    repo = make_git_repo(tmp_path / "repo")
    script = tmp_path / "mock_codex_patch.py"
    script.write_text(MOCK_CODEX, encoding="utf-8")
    web_settings.repo_root = repo
    web_settings.codex_worktree_root = tmp_path / "worktrees"
    web_settings.codex_enabled = True
    web_settings.codex_command = f'"{sys.executable}" "{script.as_posix()}"'
    web_settings.codex_logs_root = web_settings.logs_root / "repair"
    web_settings.codex_state_root = web_settings.state_root / "repair"
    web_settings.codex_patch_timeout_sec = 60.0
    return web_settings


def _seed_triaged(settings, **overrides):
    kwargs = {
        "signature": "abc123def4567890",
        "scope_type": "account",
        "flow_type": "5461",
        "account_id": "us_store_999",
        "marketplace": "US",
        "brand_name": "TESTBRAND",
        "detector_type": "batch",
        "classification": "selector_missing",
        "confidence": 0.85,
    }
    kwargs.update(overrides)
    incident, _ = record_incident(str(settings.db_path), **kwargs)
    mark_incident_triaged(str(settings.db_path), incident["id"])
    triage_job = create_repair_job(
        str(settings.db_path), incident["id"], stage="triage", status="succeeded"
    )
    result_dir = Path(settings.codex_state_root) / str(triage_job["id"])
    result_dir.mkdir(parents=True, exist_ok=True)
    result_path = result_dir / "result.json"
    result_path.write_text(json.dumps(TRIAGE_PAYLOAD), encoding="utf-8")
    finish_repair_job(
        str(settings.db_path), triage_job["id"], "succeeded",
        result_json_path=str(result_path),
    )
    return get_incident(str(settings.db_path), incident["id"])


def _operator_client(client, settings):
    create_user(settings, "operator1", TEST_PASSWORD, "operator")
    login(client, "operator1", TEST_PASSWORD)
    return client


def _patch_audits(db_path):
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT result, detail FROM web_audit_events WHERE action='patch_generate' ORDER BY id"
    ).fetchall()
    conn.close()
    return rows


def _repo_state(repo):
    return (
        git(repo, "status", "--porcelain"),
        git(repo, "rev-parse", "HEAD").strip(),
        git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip(),
    )


def test_generate_patch_requires_operator(viewer_client, patch_env):
    incident = _seed_triaged(patch_env)
    response = viewer_client.post(f"/api/incidents/{incident['id']}/generate-patch")
    assert response.status_code == 403


def test_generate_patch_end_to_end(client, patch_env):
    incident = _seed_triaged(patch_env)
    operator = _operator_client(client, patch_env)
    repo_state_before = _repo_state(patch_env.repo_root)

    response = operator.post(f"/api/incidents/{incident['id']}/generate-patch")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["outcome"] == "patch_ready"
    job = payload["job"]
    assert job["status"] == "patch_ready"
    assert job["stage"] == "patch"
    assert job["risk_level"] == "R0"
    assert job["tests_passed"] == 1
    assert job["changed_files"] == [
        "config/selectors/us.yaml", "tests/test_selector_regression.py",
    ]
    assert job["branch_name"] == f"codex/repair-{incident['id']}-abc123de"
    assert job["baseline_sha"] and job["patch_sha"]
    assert payload["incident"]["status"] == "patch_ready"
    assert payload["result"]["summary"] == "mock selector patch"

    # The production tree was never modified: status, HEAD, branch identical.
    assert _repo_state(patch_env.repo_root) == repo_state_before

    audits = _patch_audits(patch_env.db_path)
    assert [row[0] for row in audits] == ["patch_ready"]

    # viewer+ read endpoints: full job + result.json, and the raw diff.
    job_response = operator.get(f"/api/repair-jobs/{job['id']}")
    assert job_response.status_code == 200
    detail = job_response.json()
    assert detail["job"]["status"] == "patch_ready"
    assert detail["result"]["risk_level"] == "R0"

    diff_response = operator.get(f"/api/repair-jobs/{job['id']}/diff")
    assert diff_response.status_code == 200
    assert "diff --git a/config/selectors/us.yaml" in diff_response.text
    assert "#submit-fixed" in diff_response.text
    # The untracked evidence directory never leaks into the diff.
    assert ".repair-evidence" not in diff_response.text


def test_generate_patch_unknown_incident_404(client, patch_env):
    operator = _operator_client(client, patch_env)
    response = operator.post("/api/incidents/424242/generate-patch")
    assert response.status_code == 404
    assert response.json()["detail"] == "unknown_incident"
    assert _patch_audits(patch_env.db_path)[0][0] == "unknown_incident"


def test_generate_patch_disabled_503(client, web_settings):
    incident = _seed_triaged(web_settings)
    operator = _operator_client(client, web_settings)
    response = operator.post(f"/api/incidents/{incident['id']}/generate-patch")
    assert response.status_code == 503
    assert response.json()["detail"] == "codex_disabled"
    assert _patch_audits(web_settings.db_path)[0][0] == "unavailable"


def test_generate_patch_not_triaged_409(client, patch_env):
    kwargs = {
        "signature": "abc123def4567890",
        "scope_type": "account",
        "classification": "selector_missing",
        "confidence": 0.5,
    }
    incident, _ = record_incident(str(patch_env.db_path), **kwargs)
    operator = _operator_client(client, patch_env)
    response = operator.post(f"/api/incidents/{incident['id']}/generate-patch")
    assert response.status_code == 409
    assert response.json()["detail"] == "incident_not_triaged"
    assert _patch_audits(patch_env.db_path)[0][0] == "incident_not_triaged"


def test_generate_patch_conflict_409(client, patch_env):
    incident = _seed_triaged(patch_env)
    create_patch_job(str(patch_env.db_path), incident["id"])  # active slot held
    operator = _operator_client(client, patch_env)
    response = operator.post(f"/api/incidents/{incident['id']}/generate-patch")
    assert response.status_code == 409
    assert response.json()["detail"] == "active_patch_conflict"
    assert _patch_audits(patch_env.db_path)[0][0] == "active_patch_conflict"


def test_repair_job_endpoints_viewer_ok_and_404s(viewer_client, patch_env):
    incident = _seed_triaged(patch_env)
    job = create_patch_job(str(patch_env.db_path), incident["id"])
    finish_repair_job(
        str(patch_env.db_path), job["id"], "patch_ready",
        changed_files_json='["config/selectors/us.yaml"]', risk_level="R0",
    )
    response = viewer_client.get(f"/api/repair-jobs/{job['id']}")
    assert response.status_code == 200
    assert response.json()["job"]["risk_level"] == "R0"
    # No patch.diff stored for this synthetic job.
    assert viewer_client.get(f"/api/repair-jobs/{job['id']}/diff").status_code == 404
    assert viewer_client.get("/api/repair-jobs/424242").status_code == 404
    assert viewer_client.get("/api/repair-jobs/424242/diff").status_code == 404


def test_generate_patch_r3_triage_refused_422(client, patch_env):
    payload = dict(TRIAGE_PAYLOAD, classification="workflow_semantic_change")
    incident = _seed_triaged(patch_env)
    # Overwrite the triage result with a semantic classification.
    jobs_dir = Path(patch_env.codex_state_root)
    for result_path in jobs_dir.glob("*/result.json"):
        result_path.write_text(json.dumps(payload), encoding="utf-8")
    operator = _operator_client(client, patch_env)
    response = operator.post(
        f"/api/incidents/{incident['id']}/generate-patch", json={"allow_r2": True}
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "r3_refused"
    assert _patch_audits(patch_env.db_path)[0][0] == "r3_refused"
