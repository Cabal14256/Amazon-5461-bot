import json
import subprocess
from pathlib import Path

from src.codex_client.availability import CodexAvailability
from src.codex_client.case_reply import classify_with_codex


class FakeProcess:
    def __init__(self, out_path, payload, returncode=0):
        self.out_path = out_path
        self.payload = payload
        self.returncode = returncode
        self.pid = 12345
        self.prompt = b""

    def communicate(self, input=None, timeout=None):
        self.prompt = input or b""
        if self.payload is not None:
            self.out_path.write_text(json.dumps(self.payload), encoding="utf-8")
        return b'{"type":"thread.started","thread_id":"test"}\n', b""


def _settings():
    return {
        "codex": {"enabled": True, "command": "codex", "timeout_sec": 5},
        "case_followup": {
            "ai_reply_classification": {
                "enabled": True,
                "auto_apply_min_confidence": 0.85,
            }
        },
    }


def test_high_confidence_decline_is_auto_applicable(tmp_path):
    holder = {}

    def spawn(argv):
        out_path = argv[argv.index("-o") + 1]
        proc = FakeProcess(
            out_path=Path(out_path),
            payload={
                "classification": "declined",
                "confidence": 0.96,
                "reason": "The reply explicitly rejects the application.",
                "requires_human_review": False,
            },
        )
        holder["proc"] = proc
        return proc

    result = classify_with_codex(
        _settings(),
        brand_name="ExampleBrand",
        site="SE",
        case_status="Answered",
        reply="We cannot accept this request.",
        evidence_dir=tmp_path,
        spawn=spawn,
        availability=CodexAvailability(True, version="test"),
    )

    assert result["classification"] == "declined"
    assert result["auto_apply"] is True
    assert b'"case_status": "Answered"' in holder["proc"].prompt


def test_low_confidence_or_unknown_stays_manual(tmp_path):
    def spawn(argv):
        return FakeProcess(
            Path(argv[argv.index("-o") + 1]),
            {
                "classification": "unknown",
                "confidence": 0.99,
                "reason": "The reply is informational only.",
                "requires_human_review": True,
            },
        )

    result = classify_with_codex(
        _settings(),
        brand_name="ExampleBrand",
        site="SE",
        case_status="Answered",
        reply="Thank you for contacting us.",
        evidence_dir=tmp_path,
        spawn=spawn,
        availability=CodexAvailability(True, version="test"),
    )

    assert result["classification"] == "unknown"
    assert result["auto_apply"] is False


def test_schema_error_degrades_to_manual_review(tmp_path):
    def spawn(argv):
        return FakeProcess(
            Path(argv[argv.index("-o") + 1]),
            {"classification": "approved"},
        )

    result = classify_with_codex(
        _settings(),
        brand_name="ExampleBrand",
        site="SE",
        case_status="Answered",
        reply="Ambiguous reply.",
        evidence_dir=tmp_path,
        spawn=spawn,
        availability=CodexAvailability(True, version="test"),
    )

    assert result["status"] == "schema_invalid"


def test_prompt_redacts_email_and_long_ids(tmp_path):
    holder = {}

    def spawn(argv):
        proc = FakeProcess(
            Path(argv[argv.index("-o") + 1]),
            {
                "classification": "declined",
                "confidence": 0.9,
                "reason": "Explicit rejection.",
                "requires_human_review": False,
            },
        )
        holder["proc"] = proc
        return proc

    classify_with_codex(
        _settings(),
        brand_name="ExampleBrand",
        site="SE",
        case_status="Answered",
        reply="Contact person@example.com about case 13171925522.",
        evidence_dir=tmp_path,
        spawn=spawn,
        availability=CodexAvailability(True, version="test"),
    )

    assert b"person@example.com" not in holder["proc"].prompt
    assert b"13171925522" not in holder["proc"].prompt


def test_timeout_persists_partial_process_diagnostics(monkeypatch, tmp_path):
    class TimeoutProcess:
        def __init__(self):
            self.pid = 12345
            self.returncode = None
            self.calls = 0

        def communicate(self, input=None, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired(
                    cmd="codex",
                    timeout=timeout,
                    output=b'{"type":"thread.started","thread_id":"partial"}\n',
                    stderr=b"still running",
                )
            self.returncode = -9
            return (
                b'{"type":"thread.started","thread_id":"partial"}\n',
                b"still running",
            )

    proc = TimeoutProcess()
    monkeypatch.setattr(
        "src.codex_client.case_reply._kill_process_tree",
        lambda _proc: None,
    )
    result = classify_with_codex(
        _settings(),
        brand_name="ExampleBrand",
        site="US",
        case_status="Answered",
        reply="Ambiguous reply.",
        evidence_dir=tmp_path,
        spawn=lambda _argv: proc,
        availability=CodexAvailability(True, version="test"),
    )

    out_dir = tmp_path / "codex_reply_classification"
    diagnostic = json.loads((out_dir / "diagnostic.json").read_text(encoding="utf-8"))
    assert result["status"] == "timeout"
    assert diagnostic["status"] == "timeout"
    assert diagnostic["event_count"] == 1
    assert "thread.started" in (out_dir / "codex-events.jsonl").read_text(encoding="utf-8")
    assert "still running" in (out_dir / "stderr.log").read_text(encoding="utf-8")


def test_cloudflare_403_is_classified_and_persisted_as_forbidden(tmp_path):
    proc = FakeProcess(
        out_path=tmp_path / "unused.txt",
        payload=None,
        returncode=1,
    )

    def communicate(input=None, timeout=None):
        proc.prompt = input or b""
        return (
            b"",
            b"unexpected status 403 Forbidden, cf-ray: redacted-HKG",
        )

    proc.communicate = communicate
    result = classify_with_codex(
        _settings(),
        brand_name="ExampleBrand",
        site="US",
        case_status="Answered",
        reply="Ambiguous reply.",
        evidence_dir=tmp_path,
        spawn=lambda _argv: proc,
        availability=CodexAvailability(True, version="test"),
    )

    out_dir = tmp_path / "codex_reply_classification"
    diagnostic = json.loads((out_dir / "diagnostic.json").read_text(encoding="utf-8"))
    assert result["status"] == "forbidden"
    assert diagnostic["status"] == "forbidden"
