"""Stage-6 read-only Codex triage of repair incidents.

One triage run spawns the Codex CLI as a subprocess (argv list, never a
shell) in a read-only sandbox:

    codex exec --json --output-schema <triage-schema.json> -o <tmp-out>
               -s read-only -C <repo_root> -

with the prompt on stdin.  Raw JSONL events land in
``runtime/logs/repair/<job_id>/codex-events.jsonl`` (non-JSON lines are kept
verbatim inside a ``raw_output`` marker object, parsing never aborts); the
validated final result lands in
``runtime/state/repair/<job_id>/result.json``.

Outcome rules (stage-6 plan):
- success -> incident moves ``open -> triaged``; the detector's own
  classification is never overwritten;
- any failure (timeout / unavailable / quota / schema / error) leaves the
  incident ``open`` and retryable;
- a schema-invalid result is retried exactly once with a fresh subprocess
  and a fresh job row; the first job is closed as ``schema_invalid``;
- every path writes one ``incident_triage`` web audit event.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import signal
import subprocess
import zipfile
from pathlib import Path

from src.codex_client.availability import check_availability, resolve_command_argv
from src.db import (
    count_triage_jobs_today,
    create_repair_job,
    finish_repair_job,
    get_incident,
    mark_incident_triaged,
    record_web_audit,
)
from src.incidents.evidence_bundle import copy_sanitized_evidence_bundle
from src.windows_subprocess import no_window_kwargs

logger = logging.getLogger(__name__)

TRIAGE_SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "triage-schema.json"
AUDIT_ACTION = "incident_triage"

# job status -> audit result vocabulary pinned by the stage-6 plan.
_AUDIT_RESULT_BY_STATUS = {
    "succeeded": "ok",
    "timeout": "timeout",
    "unavailable": "unavailable",
    "quota_exceeded": "quota_exceeded",
    "schema_invalid": "schema_invalid",
    "failed": "error",
}


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def _evidence_file_listing(sandbox_root: Path, evidence_dir: Path | None) -> str:
    """Relative (repo-root) path list of present evidence-bundle files.

    Paths outside the repository root are never handed to the sandbox.
    """
    if evidence_dir is None or not Path(evidence_dir).is_dir():
        return "- (no evidence bundle recorded)"
    bundle_dir = Path(evidence_dir)
    manifest_path = bundle_dir / "manifest.json"
    names: list[str] = []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        files = manifest.get("files") if isinstance(manifest, dict) else None
        if isinstance(files, dict):
            names = [
                name for name, entry in sorted(files.items())
                if not (isinstance(entry, dict) and entry.get("absent"))
            ]
    except (OSError, ValueError):
        names = []
    if not names and bundle_dir.is_dir():
        names = sorted(child.name for child in bundle_dir.iterdir() if child.is_file())

    repo_root = Path(sandbox_root).resolve()
    lines: list[str] = []
    for name in names:
        try:
            rel = (bundle_dir / name).resolve().relative_to(repo_root)
        except (OSError, ValueError):
            continue
        lines.append(f"- `{rel.as_posix()}`")
    return "\n".join(lines) if lines else "- (no evidence files recorded)"


def build_prompt(
    settings,
    incident: dict,
    *,
    evidence_dir: Path | None = None,
    sandbox_root: Path | None = None,
) -> str:
    """Fill the reviewable prompt template with the incident summary."""
    template_path = (
        Path(settings.repo_root) / "knowledge" / "prompts" / "codex_triage_prompt.md"
    )
    template = template_path.read_text(encoding="utf-8")
    return (
        template
        .replace("{{SIGNATURE}}", str(incident.get("signature") or ""))
        .replace("{{CLASSIFICATION}}", str(incident.get("classification") or ""))
        .replace("{{CONFIDENCE}}", f"{float(incident.get('confidence') or 0.0):.2f}")
        .replace("{{OCCURRENCE_COUNT}}", str(int(incident.get("occurrence_count") or 0)))
        .replace(
            "{{EVIDENCE_FILES}}",
            _evidence_file_listing(
                Path(sandbox_root or settings.repo_root), evidence_dir
            ),
        )
    )


# ---------------------------------------------------------------------------
# JSONL events and result extraction
# ---------------------------------------------------------------------------

def parse_events(stdout_text: str) -> tuple[list[str], list[dict]]:
    """Split raw stdout into JSONL lines to persist and parsed event objects.

    Truncated or non-JSON lines are preserved verbatim inside a
    ``{"type": "raw_output", "raw": ...}`` marker so the events file stays
    valid JSONL and parsing never aborts.
    """
    jsonl_lines: list[str] = []
    events: list[dict] = []
    for line in stdout_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            events.append(json.loads(stripped))
            jsonl_lines.append(stripped)
        except ValueError:
            jsonl_lines.append(
                json.dumps({"type": "raw_output", "raw": line}, ensure_ascii=False)
            )
    return jsonl_lines, events


def extract_session_id(events: list[dict]) -> str:
    for event in events:
        if not isinstance(event, dict):
            continue
        for key in ("thread_id", "session_id"):
            value = event.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def extract_last_agent_message(events: list[dict]) -> str:
    """Fallback final result: the last agent message in the JSONL stream."""
    for event in reversed(events):
        if not isinstance(event, dict):
            continue
        item = event.get("item")
        if (
            isinstance(item, dict)
            and item.get("type") in ("agent_message", "assistant_message")
            and isinstance(item.get("text"), str)
        ):
            return item["text"]
        if (
            event.get("type") in ("agent_message", "assistant_message")
            and isinstance(event.get("text"), str)
        ):
            return event["text"]
    return ""


def parse_json_payload(raw: str):
    """Parse a JSON object from a final-message text (tolerates wrapping)."""
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else None
    except ValueError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if 0 <= start < end:
        try:
            payload = json.loads(text[start : end + 1])
            return payload if isinstance(payload, dict) else None
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# Minimal JSON-Schema validation (no third-party jsonschema dependency).
# Supports exactly the constructs used by triage-schema.json.
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def _load_triage_schema() -> dict:
    return json.loads(TRIAGE_SCHEMA_PATH.read_text(encoding="utf-8"))


def _validate(payload, schema: dict) -> bool:
    if not isinstance(schema, dict):
        return True
    expected = schema.get("type")
    if expected == "object":
        if not isinstance(payload, dict):
            return False
        for key in schema.get("required", []):
            if key not in payload:
                return False
        for key, subschema in (schema.get("properties") or {}).items():
            if key in payload and not _validate(payload[key], subschema):
                return False
        return True
    if expected == "array":
        if not isinstance(payload, list):
            return False
        item_schema = schema.get("items")
        if item_schema:
            return all(_validate(item, item_schema) for item in payload)
        return True
    if expected == "string":
        if not isinstance(payload, str):
            return False
    elif expected == "number":
        if isinstance(payload, bool) or not isinstance(payload, (int, float)):
            return False
    elif expected == "integer":
        if isinstance(payload, bool) or not isinstance(payload, int):
            return False
    elif expected == "boolean":
        if not isinstance(payload, bool):
            return False
    if "enum" in schema and payload not in schema["enum"]:
        return False
    if "minimum" in schema and isinstance(payload, (int, float)) and payload < schema["minimum"]:
        return False
    if "maximum" in schema and isinstance(payload, (int, float)) and payload > schema["maximum"]:
        return False
    return True


def validate_triage_result(payload) -> bool:
    return isinstance(payload, dict) and _validate(payload, _load_triage_schema())


# ---------------------------------------------------------------------------
# Subprocess plumbing
# ---------------------------------------------------------------------------

def _spawn(argv: list[str], cwd: Path | None):
    return subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(cwd) if cwd else None,
        start_new_session=(os.name != "nt"),
        **no_window_kwargs(),
    )


def _kill_process_tree(proc) -> None:
    """Best-effort tree kill after a timeout; errors are swallowed."""
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=10,
                **no_window_kwargs(),
            )
        except Exception:  # noqa: BLE001 — best effort only
            pass
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:  # noqa: BLE001 — fall back to the direct child
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass


def build_argv(
    settings,
    schema_path: Path,
    out_path: Path,
    *,
    sandbox_root: Path | None = None,
) -> list[str]:
    argv = [
        *resolve_command_argv(settings.codex_command),
        "exec",
        "--json",
        "--output-schema",
        str(schema_path),
        "-o",
        str(out_path),
        "-s",
        "read-only",
        "-C",
        str(Path(sandbox_root or settings.repo_root).resolve()),
    ]
    if str(settings.codex_model or "").strip():
        argv += ["-m", str(settings.codex_model).strip()]
    argv.append("-")
    return argv


def _prepare_read_only_repo_snapshot(settings, destination: Path) -> Path:
    """Extract tracked HEAD only; ignored/private working-tree data is absent."""
    repo_root = Path(settings.repo_root).resolve()
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    archive_path = destination.parent / "tracked-head.zip"
    try:
        proc = subprocess.run(
            ["git", "archive", "--format=zip", "-o", str(archive_path), "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=120,
            **no_window_kwargs(),
        )
        if proc.returncode != 0 or not archive_path.is_file():
            raise RuntimeError("git_archive_failed")
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.infolist():
                path = Path(member.filename)
                if path.is_absolute() or ".." in path.parts:
                    raise RuntimeError("unsafe_archive_path")
            archive.extractall(destination)
    finally:
        try:
            archive_path.unlink(missing_ok=True)
        except OSError:
            pass
    return destination


# ---------------------------------------------------------------------------
# One triage attempt
# ---------------------------------------------------------------------------

def _run_single_attempt(settings, incident: dict, *, spawn=None) -> dict:
    """Create one job row and run one Codex subprocess for it."""
    db_path = str(settings.db_path)
    job = create_repair_job(db_path, int(incident["id"]), stage="triage")
    job_id = int(job["id"])

    log_dir = Path(settings.codex_logs_root) / str(job_id)
    state_dir = Path(settings.codex_state_root) / str(job_id)
    log_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = log_dir / "codex-events.jsonl"
    result_path = state_dir / "result.json"
    out_path = state_dir / "last-message.txt"

    def _finish(status: str, **fields) -> dict:
        return finish_repair_job(db_path, job_id, status, **fields) or job

    try:
        safe_repo_dir = _prepare_read_only_repo_snapshot(settings, state_dir / "repo")
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        logger.warning("[codex] 创建只读 tracked 快照失败 job=%s: %s", job_id, type(exc).__name__)
        return _finish("failed")
    raw_bundle = str(incident.get("evidence_bundle_path") or "").strip()
    safe_evidence_dir = safe_repo_dir / ".repair-evidence"
    if raw_bundle:
        copy_sanitized_evidence_bundle(
            Path(raw_bundle),
            safe_evidence_dir,
            incident=incident,
        )
    prompt = build_prompt(
        settings,
        incident,
        evidence_dir=safe_evidence_dir,
        sandbox_root=safe_repo_dir,
    )
    argv = build_argv(
        settings,
        TRIAGE_SCHEMA_PATH,
        out_path,
        sandbox_root=safe_repo_dir,
    )

    spawn_fn = spawn or _spawn
    try:
        proc = spawn_fn(argv, safe_repo_dir)
    except FileNotFoundError:
        return _finish("unavailable")
    except OSError as exc:
        logger.warning("[codex] 判因子进程启动失败 job=%s: %s", job_id, exc)
        return _finish("failed")

    timed_out = False
    try:
        stdout_bytes, _stderr = proc.communicate(
            input=prompt.encode("utf-8"), timeout=float(settings.codex_timeout_sec)
        )
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_process_tree(proc)
        stdout_bytes, _stderr = proc.communicate()

    stdout_text = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
    jsonl_lines, events = parse_events(stdout_text)
    jsonl_path.write_text("\n".join(jsonl_lines) + ("\n" if jsonl_lines else ""), encoding="utf-8")
    session_id = extract_session_id(events)
    base_fields = {"codex_session_id": session_id or None, "jsonl_log_path": str(jsonl_path)}

    if timed_out:
        return _finish("timeout", **base_fields)
    if proc.returncode != 0:
        return _finish("failed", **base_fields)

    raw_result = ""
    try:
        if out_path.is_file():
            raw_result = out_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        raw_result = ""
    if not raw_result.strip():
        raw_result = extract_last_agent_message(events)

    payload = parse_json_payload(raw_result)
    if payload is None or not validate_triage_result(payload):
        return _finish("schema_invalid", **base_fields)

    result_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return _finish("succeeded", result_json_path=str(result_path), **base_fields)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_triage(
    settings,
    incident_id: int,
    *,
    trigger: str = "manual",
    actor_id: int | None = None,
    ip_address: str | None = None,
    spawn=None,
    availability=None,
) -> dict:
    """Run one read-only triage for an incident.

    Returns ``{"outcome", "job", "triage", "incident"}`` where ``outcome`` is
    one of ok / timeout / unavailable / quota_exceeded / schema_invalid /
    error / unknown_incident / disabled, ``job`` the final job row (or None),
    ``triage`` the validated result payload (or None) and ``incident`` the
    refreshed incident row (or None when unknown).  Every path writes one
    ``incident_triage`` audit event.
    """
    db_path = str(settings.db_path)

    def _audit(result: str, job_id: int | None = None) -> None:
        detail = {"trigger": trigger}
        if job_id is not None:
            detail["job_id"] = job_id
        record_web_audit(
            db_path,
            action=AUDIT_ACTION,
            actor_id=actor_id,
            target_type="repair_incident",
            target_id=str(incident_id),
            result=result,
            ip_address=ip_address,
            detail=json.dumps(detail, ensure_ascii=False),
        )

    incident = get_incident(db_path, incident_id)
    if incident is None:
        _audit("unknown_incident")
        return {"outcome": "unknown_incident", "job": None, "triage": None, "incident": None}

    def _done(outcome: str, job: dict | None, triage) -> dict:
        return {
            "outcome": outcome,
            "job": job,
            "triage": triage,
            "incident": get_incident(db_path, incident_id),
        }

    if not bool(getattr(settings, "codex_enabled", True)):
        _audit("unavailable")
        return _done("disabled", None, None)

    probe = availability or check_availability(settings.codex_command)
    if not probe.available:
        job = create_repair_job(db_path, int(incident_id), stage="triage", status="unavailable")
        _audit("unavailable", job_id=int(job["id"]))
        return _done("unavailable", job, None)

    if count_triage_jobs_today(db_path) >= int(settings.codex_daily_call_limit):
        job = create_repair_job(db_path, int(incident_id), stage="triage", status="quota_exceeded")
        _audit("quota_exceeded", job_id=int(job["id"]))
        return _done("quota_exceeded", job, None)

    try:
        job = _run_single_attempt(settings, incident, spawn=spawn)
    except Exception:  # noqa: BLE001 — triage must never break the caller
        logger.exception("[codex] 判因执行异常 incident=%s", incident_id)
        _audit("error")
        return _done("error", None, None)

    if job["status"] == "schema_invalid":
        # One retry with a fresh subprocess and a fresh job row.
        try:
            job = _run_single_attempt(settings, incident, spawn=spawn)
        except Exception:  # noqa: BLE001
            logger.exception("[codex] 判因重试异常 incident=%s", incident_id)
            _audit("error")
            return _done("error", job, None)

    status = str(job["status"])
    audit_result = _AUDIT_RESULT_BY_STATUS.get(status, "error")
    _audit(audit_result, job_id=int(job["id"]))

    triage_payload = None
    if status == "succeeded":
        mark_incident_triaged(db_path, int(incident_id))
        result_json_path = job.get("result_json_path")
        if result_json_path:
            try:
                triage_payload = json.loads(Path(result_json_path).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                triage_payload = None
    return _done(audit_result, job, triage_payload)
