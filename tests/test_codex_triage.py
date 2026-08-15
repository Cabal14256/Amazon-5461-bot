"""Stage-6 Codex read-only triage: subprocess client + DB helpers.

A fake ``spawn`` factory replaces the real Codex CLI in every test — the real
``codex`` executable is never invoked.  The fake parses the fixed argv
(``-o`` output path) to script what the CLI would write.
"""

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from src.codex_client.auto_triage import run_auto_triage_scan
from src.codex_client.availability import CodexAvailability, check_availability
from src.codex_client.triage import (
    extract_last_agent_message,
    parse_events,
    parse_json_payload,
    run_triage,
    validate_triage_result,
)
from src.db import (
    count_triage_jobs_today,
    create_repair_job,
    get_incident,
    get_latest_triage_job,
    init_db,
    list_repair_jobs,
    record_incident,
)
from src.incidents import build_evidence_bundle
from src.web.config import WebSettings

AVAILABLE = CodexAvailability(True, version="codex-cli 0.147.0-test")

VALID_RESULT = {
    "classification": "selector_change",
    "confidence": 0.82,
    "reason": "submit button selector no longer matches",
    "affected_components": ["config/selectors/us.yaml"],
    "recommended_scope": "selector update",
    "safe_to_generate_patch": True,
    "requires_human_review": False,
    "missing_evidence": [],
}


@pytest.fixture()
def env(tmp_path):
    runtime = tmp_path / "runtime"
    db_path = runtime / "state" / "ledger.db"
    init_db(str(db_path))
    settings = WebSettings(
        db_path=db_path,
        evidence_root=runtime / "evidence",
        logs_root=runtime / "logs",
        state_root=runtime / "state",
        codex_logs_root=runtime / "logs" / "repair",
        codex_state_root=runtime / "state" / "repair",
        repo_root=Path(__file__).resolve().parent.parent,
        session_secret="0" * 64,
        codex_enabled=True,
        codex_command="codex-test-fixture",
        codex_timeout_sec=5.0,
    )
    return settings


def _incident(env, **overrides):
    kwargs = {
        "signature": "abc123def4567890",
        "scope_type": "account",
        "flow_type": "5461",
        "account_id": "us_store_999",
        "marketplace": "US",
        "brand_name": "TESTBRAND",
        "detector_type": "batch",
        "classification": "selector_missing",
        "confidence": 0.70,
    }
    kwargs.update(overrides)
    incident, _ = record_incident(str(env.db_path), **kwargs)
    bundle = build_evidence_bundle(
        incident["id"],
        env.evidence_root,
        page_evidence={"visible_text": "Apply to sell"},
        run_context={"account_id": "us_store_999"},
    )
    from src.db import set_incident_evidence_bundle

    set_incident_evidence_bundle(str(env.db_path), incident["id"], str(bundle))
    return get_incident(str(env.db_path), incident["id"])


class FakeProc:
    """Scriptable stand-in for subprocess.Popen."""

    def __init__(self, argv, *, stdout_lines=(), out_payload=None, returncode=0,
                 hang=False, kill_log=None):
        self.argv = argv
        self._stdout = "\n".join(stdout_lines)
        self._out_payload = out_payload
        self.returncode = returncode
        self._hang = hang
        self._calls = 0
        self._kill_log = kill_log
        self.pid = 43210
        self.stdin_data = b""

    def communicate(self, input=None, timeout=None):
        self._calls += 1
        if input:
            self.stdin_data = input
        if self._hang and self._calls == 1:
            raise subprocess.TimeoutExpired(cmd=self.argv, timeout=timeout)
        if self._hang:
            # Post-kill reap.
            self.returncode = -9
        if self._out_payload is not None:
            out_path = Path(self.argv[self.argv.index("-o") + 1])
            out_path.write_text(self._out_payload, encoding="utf-8")
        return self._stdout.encode("utf-8"), b""

    def kill(self):
        if self._kill_log is not None:
            self._kill_log.append("kill")
        self.returncode = -9


def _spawn_factory(**kwargs):
    procs = []

    def factory(argv, cwd=None):
        proc = FakeProc(argv, **kwargs)
        procs.append(proc)
        return proc

    factory.procs = procs
    return factory


def _jsonl_event(payload=None, thread_id="thr_test"):
    lines = [json.dumps({"type": "thread.started", "thread_id": thread_id})]
    if payload is not None:
        lines.append(json.dumps({
            "type": "item.completed",
            "item": {"id": "item_0", "type": "agent_message", "text": payload},
        }))
    return lines


def _audits(db_path):
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT result, detail FROM web_audit_events WHERE action='incident_triage' ORDER BY id"
    ).fetchall()
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# JSONL parsing / result extraction units
# ---------------------------------------------------------------------------

def test_parse_events_handles_truncated_and_non_json_lines():
    good = json.dumps({"type": "thread.started", "thread_id": "t1"})
    stdout = f"{good}\nthis is not json\n{{\"type\": \"item.completed\", TRUNCATED\n"
    jsonl_lines, events = parse_events(stdout)
    assert events == [{"type": "thread.started", "thread_id": "t1"}]
    assert len(jsonl_lines) == 3
    assert jsonl_lines[0] == good
    markers = [json.loads(line) for line in jsonl_lines[1:]]
    assert all(marker["type"] == "raw_output" for marker in markers)
    assert markers[0]["raw"] == "this is not json"
    assert "TRUNCATED" in markers[1]["raw"]
    # The persisted events file stays valid JSONL.
    for line in jsonl_lines:
        json.loads(line)


def test_extract_last_agent_message_prefers_latest():
    events = [
        {"type": "item.completed", "item": {"type": "agent_message", "text": "first"}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "last"}},
    ]
    assert extract_last_agent_message(events) == "last"
    assert extract_last_agent_message([{"type": "other"}]) == ""


def test_parse_json_payload_tolerates_wrapped_text():
    assert parse_json_payload('{"a": 1}') == {"a": 1}
    assert parse_json_payload('prologue {"a": 1} epilogue') == {"a": 1}
    assert parse_json_payload("not json at all") is None
    assert parse_json_payload("") is None


def test_validate_triage_result_schema():
    assert validate_triage_result(VALID_RESULT) is True
    # Strict structured-output schema: every property key is required.
    minimal = {
        "classification": "insufficient_evidence",
        "confidence": 0.1,
        "reason": "x",
        "affected_components": [],
        "recommended_scope": "",
        "safe_to_generate_patch": False,
        "requires_human_review": True,
        "missing_evidence": [],
    }
    assert validate_triage_result(minimal) is True
    # Optional-in-spirit keys are still required by the strict schema.
    sparse = {k: v for k, v in minimal.items() if k != "affected_components"}
    assert validate_triage_result(sparse) is False
    # Missing required key.
    bad = dict(VALID_RESULT)
    del bad["reason"]
    assert validate_triage_result(bad) is False
    # Enum violation.
    assert validate_triage_result({**VALID_RESULT, "classification": "nope"}) is False
    # Confidence out of range / wrong types.
    assert validate_triage_result({**VALID_RESULT, "confidence": 1.5}) is False
    assert validate_triage_result({**VALID_RESULT, "safe_to_generate_patch": "yes"}) is False
    assert validate_triage_result("not-a-dict") is False


# ---------------------------------------------------------------------------
# run_triage flows
# ---------------------------------------------------------------------------

def test_triage_success_marks_incident_triaged(env):
    incident = _incident(env)
    spawn = _spawn_factory(
        stdout_lines=_jsonl_event(),
        out_payload=json.dumps(VALID_RESULT),
    )
    outcome = run_triage(env, incident["id"], spawn=spawn, availability=AVAILABLE)

    assert outcome["outcome"] == "ok"
    assert outcome["triage"]["classification"] == "selector_change"
    job = outcome["job"]
    assert job["status"] == "succeeded"
    assert job["codex_session_id"] == "thr_test"
    result_path = Path(job["result_json_path"])
    assert result_path.is_file()
    assert json.loads(result_path.read_text(encoding="utf-8"))["confidence"] == pytest.approx(0.82)
    jsonl_path = Path(job["jsonl_log_path"])
    assert jsonl_path.is_file()

    refreshed = get_incident(str(env.db_path), incident["id"])
    assert refreshed["status"] == "triaged"
    # The detector's own classification is never overwritten.
    assert refreshed["classification"] == "selector_missing"

    audits = _audits(env.db_path)
    assert len(audits) == 1
    assert audits[0][0] == "ok"
    detail = json.loads(audits[0][1])
    assert detail["trigger"] == "manual"
    assert detail["job_id"] == job["id"]

    # Prompt went to stdin and mentions the incident, not raw secrets.
    prompt = spawn.procs[0].stdin_data.decode("utf-8")
    assert incident["signature"] in prompt
    assert "UNTRUSTED" in prompt


def test_triage_falls_back_to_last_agent_message(env):
    incident = _incident(env)
    spawn = _spawn_factory(stdout_lines=_jsonl_event(payload=json.dumps(VALID_RESULT)))
    outcome = run_triage(env, incident["id"], spawn=spawn, availability=AVAILABLE)
    assert outcome["outcome"] == "ok"
    assert outcome["job"]["status"] == "succeeded"


def test_triage_timeout_kills_and_keeps_incident_open(env, monkeypatch):
    incident = _incident(env)
    kill_log = []
    spawn = _spawn_factory(hang=True, kill_log=kill_log)
    monkeypatch.setattr(
        "src.codex_client.triage._kill_process_tree",
        lambda proc: kill_log.append("tree_kill"),
    )
    outcome = run_triage(env, incident["id"], spawn=spawn, availability=AVAILABLE)
    assert outcome["outcome"] == "timeout"
    assert outcome["job"]["status"] == "timeout"
    assert "tree_kill" in kill_log
    assert get_incident(str(env.db_path), incident["id"])["status"] == "open"
    assert _audits(env.db_path)[0][0] == "timeout"


def test_triage_nonzero_exit_is_error(env):
    incident = _incident(env)
    spawn = _spawn_factory(stdout_lines=["boom"], returncode=2)
    outcome = run_triage(env, incident["id"], spawn=spawn, availability=AVAILABLE)
    assert outcome["outcome"] == "error"
    assert outcome["job"]["status"] == "failed"
    assert get_incident(str(env.db_path), incident["id"])["status"] == "open"
    assert _audits(env.db_path)[0][0] == "error"


def test_triage_schema_invalid_retries_once_then_succeeds(env):
    incident = _incident(env)
    attempts = {"n": 0}

    def spawn(argv, cwd=None):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return FakeProc(argv, out_payload=json.dumps({"wrong": "shape"}))
        return FakeProc(argv, stdout_lines=_jsonl_event(), out_payload=json.dumps(VALID_RESULT))

    outcome = run_triage(env, incident["id"], spawn=spawn, availability=AVAILABLE)
    assert outcome["outcome"] == "ok"
    jobs = list_repair_jobs(str(env.db_path), incident["id"])
    assert [job["status"] for job in jobs] == ["succeeded", "schema_invalid"]
    assert get_incident(str(env.db_path), incident["id"])["status"] == "triaged"


def test_triage_double_schema_invalid_closes(env):
    incident = _incident(env)
    spawn = _spawn_factory(out_payload=json.dumps({"wrong": "shape"}))
    outcome = run_triage(env, incident["id"], spawn=spawn, availability=AVAILABLE)
    assert outcome["outcome"] == "schema_invalid"
    assert len(spawn.procs) == 2  # exactly one retry
    jobs = list_repair_jobs(str(env.db_path), incident["id"])
    assert [job["status"] for job in jobs] == ["schema_invalid", "schema_invalid"]
    assert get_incident(str(env.db_path), incident["id"])["status"] == "open"
    assert _audits(env.db_path)[0][0] == "schema_invalid"


def test_triage_unavailable_degrades(env):
    incident = _incident(env)
    env.codex_command = str(env.state_root / "no-such-codex-binary")
    outcome = run_triage(env, incident["id"])
    assert outcome["outcome"] == "unavailable"
    assert outcome["job"]["status"] == "unavailable"
    assert outcome["job"]["finished_at"]  # closed immediately
    assert get_incident(str(env.db_path), incident["id"])["status"] == "open"
    assert _audits(env.db_path)[0][0] == "unavailable"


def test_triage_daily_quota(env):
    incident = _incident(env)
    env.codex_daily_call_limit = 1
    first = run_triage(
        env, incident["id"],
        spawn=_spawn_factory(out_payload=json.dumps(VALID_RESULT)),
        availability=AVAILABLE,
    )
    assert first["outcome"] == "ok"
    assert count_triage_jobs_today(str(env.db_path)) == 1

    second = run_triage(
        env, incident["id"],
        spawn=_spawn_factory(out_payload=json.dumps(VALID_RESULT)),
        availability=AVAILABLE,
    )
    assert second["outcome"] == "quota_exceeded"
    assert second["job"]["status"] == "quota_exceeded"
    # Quota rows never consume the allowance themselves.
    assert count_triage_jobs_today(str(env.db_path)) == 1
    assert [row[0] for row in _audits(env.db_path)] == ["ok", "quota_exceeded"]


def test_triage_unknown_incident_audited(env):
    outcome = run_triage(env, 424242, availability=AVAILABLE)
    assert outcome["outcome"] == "unknown_incident"
    assert _audits(env.db_path)[0][0] == "unknown_incident"


def test_triage_disabled_master_switch(env):
    incident = _incident(env)
    env.codex_enabled = False
    outcome = run_triage(env, incident["id"], availability=AVAILABLE)
    assert outcome["outcome"] == "disabled"
    assert outcome["job"] is None
    assert list_repair_jobs(str(env.db_path), incident["id"]) == []
    assert _audits(env.db_path)[0][0] == "unavailable"


def test_audit_covers_all_result_values(env):
    """Every terminal path leaves exactly one incident_triage audit row."""
    seen = set()
    incident = _incident(env)

    env.codex_command = str(env.state_root / "missing-binary")
    run_triage(env, incident["id"])
    seen.add(_audits(env.db_path)[-1][0])

    env.codex_command = "codex-test-fixture"
    env.codex_daily_call_limit = 0
    run_triage(env, incident["id"], availability=AVAILABLE)
    seen.add(_audits(env.db_path)[-1][0])

    env.codex_daily_call_limit = 50
    monkey_spawn = _spawn_factory(hang=True, kill_log=[])
    import src.codex_client.triage as triage_mod

    original_kill = triage_mod._kill_process_tree
    triage_mod._kill_process_tree = lambda proc: None
    try:
        run_triage(env, incident["id"], spawn=monkey_spawn, availability=AVAILABLE)
    finally:
        triage_mod._kill_process_tree = original_kill
    seen.add(_audits(env.db_path)[-1][0])

    run_triage(env, incident["id"], spawn=_spawn_factory(returncode=1), availability=AVAILABLE)
    seen.add(_audits(env.db_path)[-1][0])

    run_triage(env, incident["id"], spawn=_spawn_factory(out_payload="garbage"),
               availability=AVAILABLE)
    seen.add(_audits(env.db_path)[-1][0])

    run_triage(env, incident["id"],
               spawn=_spawn_factory(out_payload=json.dumps(VALID_RESULT)),
               availability=AVAILABLE)
    seen.add(_audits(env.db_path)[-1][0])

    assert seen == {"ok", "timeout", "unavailable", "quota_exceeded", "schema_invalid", "error"}


# ---------------------------------------------------------------------------
# Auto-triage scan
# ---------------------------------------------------------------------------

def test_auto_scan_triggers_only_eligible_incidents(env, monkeypatch):
    eligible = _incident(env, signature="aaaa1111bbbb2222", confidence=0.70)
    _incident(env, signature="cccc3333dddd4444", confidence=0.40)  # below threshold
    _incident(env, signature="eeee5555ffff6666", classification="captcha",
              confidence=0.90)  # excluded class must never auto-trigger

    def fake_run_triage(settings, incident_id, *, trigger, **kwargs):
        assert trigger == "auto"
        job = create_repair_job(str(settings.db_path), incident_id, status="succeeded")
        from src.db import mark_incident_triaged

        mark_incident_triaged(str(settings.db_path), incident_id)
        return {"outcome": "ok", "job": job, "triage": None,
                "incident": get_incident(str(settings.db_path), incident_id)}

    monkeypatch.setattr("src.codex_client.auto_triage.run_triage", fake_run_triage)
    outcomes = run_auto_triage_scan(env, availability=AVAILABLE)
    assert len(outcomes) == 1
    assert outcomes[0]["job"]["incident_id"] == eligible["id"]
    assert get_incident(str(env.db_path), eligible["id"])["status"] == "triaged"

    # Second scan: nothing left to do.
    assert run_auto_triage_scan(env, availability=AVAILABLE) == []


def test_auto_scan_respects_daily_quota(env, monkeypatch):
    _incident(env, signature="aaaa1111bbbb2222", confidence=0.70)
    env.codex_daily_call_limit = 0
    called = []
    monkeypatch.setattr(
        "src.codex_client.auto_triage.run_triage",
        lambda *args, **kwargs: called.append(1),
    )
    assert run_auto_triage_scan(env, availability=AVAILABLE) == []
    assert called == []


def test_auto_scan_disabled_or_unavailable_is_noop(env, monkeypatch):
    _incident(env, signature="aaaa1111bbbb2222", confidence=0.70)
    monkeypatch.setattr(
        "src.codex_client.auto_triage.run_triage",
        lambda *args, **kwargs: pytest.fail("must not run"),
    )
    env.codex_enabled = False
    assert run_auto_triage_scan(env, availability=AVAILABLE) == []
    env.codex_enabled = True
    assert run_auto_triage_scan(
        env, availability=CodexAvailability(False, detail="command_not_found")
    ) == []


# ---------------------------------------------------------------------------
# availability probe
# ---------------------------------------------------------------------------

def test_check_availability_missing_command():
    probe = check_availability("definitely-not-a-real-codex-binary-xyz")
    assert probe.available is False
    assert "command_not_found" in probe.detail


def test_check_availability_parses_version():
    class Proc:
        returncode = 0
        stdout = "codex-cli 0.147.0\n"

    def runner(argv, **kwargs):
        assert argv[-1] == "--version"
        return Proc()

    probe = check_availability("python", runner=runner)
    assert probe.available is True
    assert probe.version == "codex-cli 0.147.0"


def test_get_latest_triage_job_helper(env):
    incident = _incident(env)
    assert get_latest_triage_job(str(env.db_path), incident["id"]) is None
    create_repair_job(str(env.db_path), incident["id"], status="quota_exceeded")
    latest = get_latest_triage_job(str(env.db_path), incident["id"])
    assert latest["status"] == "quota_exceeded"
    assert latest["stage"] == "triage"
