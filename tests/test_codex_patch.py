"""Stage-7 isolated patch generation (src/codex_client/patch.py).

A scriptable ``FakeProc`` replaces the real Codex CLI (never invoked).  It
parses the fixed argv (``-C`` worktree, ``-o`` output path) and applies file
edits into the worktree exactly the way a workspace-write agent would.
Git operations run for real against a throwaway fixture repository.
"""

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.codex_client.availability import CodexAvailability  # noqa: E402
from src.codex_client.patch import run_patch_generation  # noqa: E402
from src.db import (  # noqa: E402
    close_incident,
    create_patch_job,
    create_repair_job,
    finish_repair_job,
    get_incident,
    init_db,
    list_repair_jobs,
    mark_incident_triaged,
    record_incident,
    set_incident_evidence_bundle,
)
from src.incidents.evidence_bundle import (  # noqa: E402
    assess_evidence_bundle,
    build_evidence_bundle,
)
from src.web.config import WebSettings  # noqa: E402
from tests.evidence_fixtures import dom_contract, previous_success  # noqa: E402
from tests.test_repair_worktree import INCIDENT, git, make_git_repo  # noqa: E402

AVAILABLE = CodexAvailability(True, version="codex-cli 0.147.0-test")

VALID_RESULT = {
    "summary": "update submit selector fallback",
    "changed_files": ["config/selectors/us.yaml"],
    "risk_level": "R0",
    "requires_human_review": False,
    "tests_added": ["tests/test_selector_regression.py"],
    "offline_replay_tests": ["tests/test_selector_regression.py"],
    "offline_fixture_paths": ["tests/fixtures/selector-regression.html"],
    "tests_ran": True,
    "tests_passed": True,
    "notes": "",
}

SELECTOR_EDIT = {
    "config/selectors/us.yaml": "submit_button: '#submit-fixed'\n",
    "tests/test_selector_regression.py": "def test_selector():\n    assert True\n",
    "tests/fixtures/selector-regression.html": "<button id='submit-fixed'>Submit</button>\n",
}


@pytest.fixture()
def env(tmp_path):
    runtime = tmp_path / "runtime"
    db_path = runtime / "state" / "ledger.db"
    init_db(str(db_path))
    repo = make_git_repo(tmp_path / "repo")
    return WebSettings(
        db_path=db_path,
        evidence_root=runtime / "evidence",
        repo_root=repo,
        codex_worktree_root=tmp_path / "worktrees",
        codex_logs_root=runtime / "logs" / "repair",
        codex_state_root=runtime / "state" / "repair",
        session_secret="0" * 64,
        codex_enabled=True,
        codex_command="codex-test-fixture",
        codex_patch_timeout_sec=5.0,
    )


def _seed_incident(env, triage_payload=None, **overrides):
    """Incident in status 'triaged' with a succeeded triage job + result.json."""
    kwargs = {
        "signature": INCIDENT["signature"],
        "scope_type": "account",
        "flow_type": "5461",
        "account_id": "us_store_999",
        "marketplace": "US",
        "brand_name": "TESTBRAND",
        "detector_type": "batch",
        "classification": "selector_missing",
        "confidence": 0.80,
    }
    kwargs.update(overrides)
    incident, _ = record_incident(str(env.db_path), **kwargs)
    bundle = build_evidence_bundle(
        incident["id"],
        env.evidence_root,
        page_evidence={
            "recognized_state": {"page_type": "product_identity"},
            "evidence_node": "product_identity",
            "visible_text": "Apply to sell",
        },
        run_context={"flow_type": "5461", "marketplace": "US"},
        selectors={
            "declared_candidates": ["button[data-testid='apply-to-sell']"],
            "probes": {"button[data-testid='apply-to-sell']": {"match_count": 1}},
        },
        dom_contract=dom_contract(),
        previous_success=previous_success(),
    )
    gate = assess_evidence_bundle(bundle)
    set_incident_evidence_bundle(
        str(env.db_path),
        incident["id"],
        str(bundle),
        evidence_status=gate["status"],
        missing_evidence=gate["missing"],
    )
    if triage_payload is not None:
        mark_incident_triaged(str(env.db_path), incident["id"])
        triage_job = create_repair_job(
            str(env.db_path), incident["id"], stage="triage", status="succeeded"
        )
        result_dir = Path(env.codex_state_root) / str(triage_job["id"])
        result_dir.mkdir(parents=True, exist_ok=True)
        result_path = result_dir / "result.json"
        result_path.write_text(json.dumps(triage_payload), encoding="utf-8")
        finish_repair_job(
            str(env.db_path), triage_job["id"], "succeeded",
            result_json_path=str(result_path),
        )
    return get_incident(str(env.db_path), incident["id"])


def _triage_payload(**overrides):
    payload = {
        "classification": "selector_change",
        "confidence": 0.82,
        "reason": "submit selector no longer matches",
        "affected_components": ["config/selectors/us.yaml"],
        "recommended_scope": "selector update",
        "safe_to_generate_patch": True,
        "requires_human_review": False,
        "missing_evidence": [],
    }
    payload.update(overrides)
    return payload


class FakeProc:
    """Scriptable stand-in for subprocess.Popen (patch generation)."""

    def __init__(self, argv, *, edits=None, out_payload=None, stdout_lines=(),
                 returncode=0, hang=False):
        self.argv = argv
        self._edits = edits
        self._out_payload = out_payload
        self._stdout = "\n".join(stdout_lines)
        self.returncode = returncode
        self._hang = hang
        self._calls = 0
        self.pid = 43210
        self.stdin_data = b""

    def communicate(self, input=None, timeout=None):
        self._calls += 1
        if input:
            self.stdin_data = input
        if self._hang and self._calls == 1:
            raise subprocess.TimeoutExpired(cmd=self.argv, timeout=timeout)
        if self._hang:
            self.returncode = -9
        if self._calls == 1 and self._edits:
            worktree = Path(self.argv[self.argv.index("-C") + 1])
            for rel, content in self._edits.items():
                target = worktree / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
        if self._out_payload is not None:
            out_path = Path(self.argv[self.argv.index("-o") + 1])
            out_path.write_text(self._out_payload, encoding="utf-8")
        return self._stdout.encode("utf-8"), b""

    def kill(self):
        self.returncode = -9


def _spawn_factory(**kwargs):
    procs = []

    def factory(argv, cwd=None):
        proc = FakeProc(argv, **kwargs)
        procs.append(proc)
        return proc

    factory.procs = procs
    return factory


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


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_patch_ready_end_to_end(env):
    incident = _seed_incident(env, _triage_payload())
    repo_state_before = _repo_state(env.repo_root)
    spawn = _spawn_factory(edits=SELECTOR_EDIT, out_payload=json.dumps(VALID_RESULT))

    outcome = run_patch_generation(env, incident["id"], spawn=spawn, availability=AVAILABLE)

    assert outcome["outcome"] == "patch_ready"
    job = outcome["job"]
    assert job["stage"] == "patch"
    assert job["status"] == "patch_ready"
    assert job["risk_level"] == "R0"
    assert job["tests_passed"] == 1
    assert job["changed_files"] == [
        "config/selectors/us.yaml", "tests/fixtures/selector-regression.html",
        "tests/test_selector_regression.py",
    ]
    assert job["baseline_sha"] and job["patch_sha"]
    assert job["patch_sha"] != job["baseline_sha"]
    assert job["branch_name"] == "codex/repair-1-abc123de"
    assert Path(job["worktree_path"]).is_dir()
    # patch.diff persisted inside the codex state root.
    diff_path = Path(env.codex_state_root) / str(job["id"]) / "patch.diff"
    diff_text = diff_path.read_text(encoding="utf-8")
    assert "diff --git a/config/selectors/us.yaml" in diff_text
    assert "#submit-fixed" in diff_text
    # result.json and JSONL persisted.
    assert Path(job["result_json_path"]).is_file()
    assert Path(job["jsonl_log_path"]).is_file()
    # Incident moved triaged -> patch_ready.
    assert outcome["incident"]["status"] == "patch_ready"
    # Prompt went to stdin with the safety scaffolding and allowed paths.
    prompt = spawn.procs[0].stdin_data.decode("utf-8")
    assert incident["signature"] in prompt
    assert "workspace-write" in prompt or "UNTRUSTED" in prompt
    assert "config/selectors/" in prompt
    # argv: workspace-write sandbox scoped to the repair worktree.
    argv = spawn.procs[0].argv
    assert argv[argv.index("-s") + 1] == "workspace-write"
    assert argv[argv.index("-C") + 1] == str(Path(job["worktree_path"]).resolve())
    # The production tree never changed.
    assert _repo_state(env.repo_root) == repo_state_before
    # Exactly one patch_generate audit.
    audits = _patch_audits(env.db_path)
    assert [row[0] for row in audits] == ["patch_ready"]
    detail = json.loads(audits[0][1])
    assert detail["trigger"] == "manual"
    assert detail["job_id"] == job["id"]
    assert detail["allow_r2"] is False


def test_patch_reuses_same_job_row_on_schema_retry(env):
    incident = _seed_incident(env, _triage_payload())
    attempts = {"n": 0}

    def spawn(argv, cwd=None):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return FakeProc(argv, out_payload=json.dumps({"wrong": "shape"}))
        return FakeProc(argv, edits=SELECTOR_EDIT, out_payload=json.dumps(VALID_RESULT))

    outcome = run_patch_generation(env, incident["id"], spawn=spawn, availability=AVAILABLE)
    assert outcome["outcome"] == "patch_ready"
    assert attempts["n"] == 2
    jobs = list_repair_jobs(str(env.db_path), incident["id"], stage="patch")
    assert len(jobs) == 1  # the unique index forbids a second active row


def test_patch_double_schema_invalid(env):
    incident = _seed_incident(env, _triage_payload())
    spawn = _spawn_factory(out_payload=json.dumps({"wrong": "shape"}))
    outcome = run_patch_generation(env, incident["id"], spawn=spawn, availability=AVAILABLE)
    assert outcome["outcome"] == "schema_invalid"
    assert len(spawn.procs) == 2  # exactly one retry
    assert outcome["job"]["status"] == "schema_invalid"
    assert outcome["incident"]["status"] == "triaged"  # retryable
    assert _patch_audits(env.db_path)[0][0] == "schema_invalid"


def test_patch_timeout_returns_incident_to_triaged(env, monkeypatch):
    incident = _seed_incident(env, _triage_payload())
    kill_log = []
    monkeypatch.setattr(
        "src.codex_client.patch._kill_process_tree", lambda proc: kill_log.append("tree_kill")
    )
    spawn = _spawn_factory(hang=True)
    outcome = run_patch_generation(env, incident["id"], spawn=spawn, availability=AVAILABLE)
    assert outcome["outcome"] == "timeout"
    assert outcome["job"]["status"] == "timeout"
    assert "tree_kill" in kill_log
    assert outcome["incident"]["status"] == "triaged"
    assert _patch_audits(env.db_path)[0][0] == "timeout"


# ---------------------------------------------------------------------------
# Preconditions (blueprint §16.1B)
# ---------------------------------------------------------------------------

def test_unknown_incident(env):
    outcome = run_patch_generation(env, 424242, availability=AVAILABLE)
    assert outcome["outcome"] == "unknown_incident"
    assert _patch_audits(env.db_path)[0][0] == "unknown_incident"


def test_disabled_master_switch(env):
    incident = _seed_incident(env, _triage_payload())
    env.codex_enabled = False
    outcome = run_patch_generation(env, incident["id"], availability=AVAILABLE)
    assert outcome["outcome"] == "disabled"
    assert _patch_audits(env.db_path)[0][0] == "unavailable"


def test_incident_not_triaged_refused(env):
    incident = _seed_incident(env)  # no triage job -> still 'open'
    outcome = run_patch_generation(env, incident["id"], availability=AVAILABLE)
    assert outcome["outcome"] == "incident_not_triaged"
    assert _patch_audits(env.db_path)[0][0] == "incident_not_triaged"


def test_closed_incident_refused(env):
    incident = _seed_incident(env, _triage_payload())
    close_incident(str(env.db_path), incident["id"], "human handled")
    outcome = run_patch_generation(env, incident["id"], availability=AVAILABLE)
    assert outcome["outcome"] == "incident_closed"
    assert _patch_audits(env.db_path)[0][0] == "incident_closed"


def test_no_successful_triage_refused(env):
    incident = _seed_incident(env)
    mark_incident_triaged(str(env.db_path), incident["id"])  # triaged, no triage job
    outcome = run_patch_generation(env, incident["id"], availability=AVAILABLE)
    assert outcome["outcome"] == "unsafe_to_patch"


def test_unsafe_triage_refused(env):
    incident = _seed_incident(env, _triage_payload(safe_to_generate_patch=False))
    outcome = run_patch_generation(env, incident["id"], availability=AVAILABLE)
    assert outcome["outcome"] == "unsafe_to_patch"
    assert list_repair_jobs(str(env.db_path), incident["id"], stage="patch") == []


def test_semantic_triage_is_r3_refused_without_spawning(env):
    incident = _seed_incident(
        env, _triage_payload(classification="workflow_semantic_change")
    )
    spawn = _spawn_factory(edits=SELECTOR_EDIT, out_payload=json.dumps(VALID_RESULT))
    outcome = run_patch_generation(env, incident["id"], spawn=spawn, availability=AVAILABLE)
    assert outcome["outcome"] == "r3_refused"
    assert spawn.procs == []  # Codex never started
    assert list_repair_jobs(str(env.db_path), incident["id"], stage="patch") == []
    assert _patch_audits(env.db_path)[0][0] == "r3_refused"


def test_active_patch_conflict(env):
    incident = _seed_incident(env, _triage_payload())
    create_patch_job(str(env.db_path), incident["id"])  # holds the slot
    outcome = run_patch_generation(env, incident["id"], availability=AVAILABLE)
    assert outcome["outcome"] == "active_patch_conflict"
    assert _patch_audits(env.db_path)[0][0] == "active_patch_conflict"


def test_unavailable_cli(env):
    incident = _seed_incident(env, _triage_payload())
    outcome = run_patch_generation(
        env, incident["id"], availability=CodexAvailability(False, detail="command_not_found")
    )
    assert outcome["outcome"] == "unavailable"
    assert _patch_audits(env.db_path)[0][0] == "unavailable"


def test_empty_patch_fails(env):
    incident = _seed_incident(env, _triage_payload())
    spawn = _spawn_factory(edits=None, out_payload=json.dumps(VALID_RESULT))
    outcome = run_patch_generation(env, incident["id"], spawn=spawn, availability=AVAILABLE)
    assert outcome["outcome"] == "empty_patch"
    assert outcome["job"]["status"] == "failed"
    assert outcome["incident"]["status"] == "triaged"
    assert _patch_audits(env.db_path)[0][0] == "empty_patch"


# ---------------------------------------------------------------------------
# Risk gate (blueprint §17.3): stricter of Codex self-assessment and backend
# ---------------------------------------------------------------------------

def test_backend_review_raises_risk_above_self_assessment(env):
    incident = _seed_incident(env, _triage_payload())
    payload = dict(VALID_RESULT, risk_level="R0",
                   changed_files=["src/executor/probe.py"])
    spawn = _spawn_factory(
        edits={"src/executor/probe.py": "def probe():\n    return 2\n"},
        out_payload=json.dumps(payload),
    )
    outcome = run_patch_generation(env, incident["id"], spawn=spawn, availability=AVAILABLE)
    assert outcome["outcome"] == "patch_ready"
    assert outcome["job"]["risk_level"] == "R1"  # backend overruled the model


def test_r2_requires_allow_r2(env):
    incident = _seed_incident(env, _triage_payload())
    payload = dict(VALID_RESULT, risk_level="R2")
    spawn = _spawn_factory(edits=SELECTOR_EDIT, out_payload=json.dumps(payload))

    outcome = run_patch_generation(env, incident["id"], spawn=spawn, availability=AVAILABLE)
    assert outcome["outcome"] == "r2_not_allowed"
    assert outcome["job"]["status"] == "validation_failed"
    assert outcome["job"]["risk_level"] == "R2"
    assert outcome["incident"]["status"] == "triaged"
    assert _patch_audits(env.db_path)[0][0] == "r2_not_allowed"

    # Retried with the operator's explicit allowance -> patch_ready at R2.
    spawn2 = _spawn_factory(edits=SELECTOR_EDIT, out_payload=json.dumps(payload))
    outcome2 = run_patch_generation(
        env, incident["id"], allow_r2=True, spawn=spawn2, availability=AVAILABLE
    )
    assert outcome2["outcome"] == "patch_ready"
    assert outcome2["job"]["risk_level"] == "R2"


def test_r3_self_assessment_refused_even_with_allow_r2(env):
    incident = _seed_incident(env, _triage_payload())
    payload = dict(VALID_RESULT, risk_level="R3")
    spawn = _spawn_factory(edits=SELECTOR_EDIT, out_payload=json.dumps(payload))
    outcome = run_patch_generation(
        env, incident["id"], allow_r2=True, spawn=spawn, availability=AVAILABLE
    )
    assert outcome["outcome"] == "r3_refused"
    assert outcome["job"]["status"] == "validation_failed"
    assert outcome["incident"]["status"] == "triaged"
    assert _patch_audits(env.db_path)[0][0] == "r3_refused"


# ---------------------------------------------------------------------------
# Diff scan integration
# ---------------------------------------------------------------------------

def test_sensitive_diff_rejected(env):
    incident = _seed_incident(env, _triage_payload())
    spawn = _spawn_factory(
        edits={"config/selectors/us.yaml": "contact: leaked@example.com\n"},
        out_payload=json.dumps(VALID_RESULT),
    )
    outcome = run_patch_generation(env, incident["id"], spawn=spawn, availability=AVAILABLE)
    assert outcome["outcome"] == "validation_failed"
    assert outcome["job"]["status"] == "validation_failed"
    rules = {violation["rule_id"] for violation in outcome["violations"]}
    assert "sensitive_content" in rules
    # No patch.diff is stored for a rejected patch; incident is retryable.
    assert not (Path(env.codex_state_root) / str(outcome["job"]["id"]) / "patch.diff").exists()
    assert outcome["incident"]["status"] == "triaged"
    assert _patch_audits(env.db_path)[0][0] == "validation_failed"


def test_out_of_scope_diff_rejected(env):
    incident = _seed_incident(env, _triage_payload())
    spawn = _spawn_factory(
        edits={"src/web/app.py": "# touched by rogue agent\n"},
        out_payload=json.dumps(dict(VALID_RESULT, changed_files=["src/web/app.py"])),
    )
    outcome = run_patch_generation(
        env, incident["id"], allow_r2=True, spawn=spawn, availability=AVAILABLE
    )
    assert outcome["outcome"] == "validation_failed"
    rules = {violation["rule_id"] for violation in outcome["violations"]}
    assert "path_outside_allowed" in rules


def test_second_patch_attempt_after_failure_gets_fresh_job(env):
    """A failed patch job frees the exclusive slot for a retry."""
    incident = _seed_incident(env, _triage_payload())
    spawn = _spawn_factory(hang=True)
    import src.codex_client.patch as patch_mod

    original_kill = patch_mod._kill_process_tree
    patch_mod._kill_process_tree = lambda proc: None
    try:
        first = run_patch_generation(env, incident["id"], spawn=spawn, availability=AVAILABLE)
    finally:
        patch_mod._kill_process_tree = original_kill
    assert first["outcome"] == "timeout"

    spawn2 = _spawn_factory(edits=SELECTOR_EDIT, out_payload=json.dumps(VALID_RESULT))
    second = run_patch_generation(env, incident["id"], spawn=spawn2, availability=AVAILABLE)
    assert second["outcome"] == "patch_ready"
    jobs = list_repair_jobs(str(env.db_path), incident["id"], stage="patch")
    assert [job["status"] for job in jobs] == ["patch_ready", "timeout"]
