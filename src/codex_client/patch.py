"""Stage-7 isolated Codex patch generation.

Reuses the stage-6 subprocess plumbing (argv construction, stdin prompt,
timeout tree-kill, JSONL persistence, ``-o``-first result extraction, minimal
schema validation) but runs the Codex CLI with ``-s workspace-write -C
<repair_worktree>`` so the model can only modify the isolated worktree:

    codex exec --json --output-schema <repair-result-schema.json> -o <tmp-out>
               -s workspace-write -C <worktree> -

Flow (stage-7 plan §7.3–§7.5):
1. Preconditions (blueprint §16.1B): latest triage succeeded with a
   code/selector classification and ``safe_to_generate_patch=true``; the
   incident is ``triaged`` (never closed); no active patch job exists (the
   partial unique index is the hard backstop).  Every refusal is audited.
2. The repair job row is created first (holding the exclusive slot), then
   the worktree with its baseline overlay commit; failure closes the job.
3. Codex runs in the worktree.  A schema-invalid result is retried exactly
   once (same job row — the unique index forbids a second active row).
4. The generated diff is risk-reviewed (Codex self-assessment vs the
   backend's file-path classification, stricter wins) and scanned
   (``src/repair/diff_scan.py``).  R3 is always refused; R2 requires the
   operator's explicit ``allow_r2``.
5. A passing diff is committed as the branch's second commit, stored as
   ``runtime/state/repair/<job_id>/patch.diff``; job -> ``patch_ready``,
   incident ``patching -> patch_ready``.  Any failure returns the incident
   to ``triaged`` so it stays retryable.

Every path writes exactly one ``patch_generate`` web audit event.
"""

from __future__ import annotations

import functools
import json
import logging
import subprocess
from pathlib import Path

from src.codex_client.availability import check_availability, resolve_command_argv
from src.codex_client.triage import (
    _kill_process_tree,
    _spawn,
    _validate,
    extract_last_agent_message,
    extract_session_id,
    parse_events,
    parse_json_payload,
)
from src.db import (
    create_patch_job,
    finish_repair_job,
    get_incident,
    get_latest_triage_job,
    get_repair_job,
    mark_incident_patch_failed,
    mark_incident_patch_ready,
    mark_incident_patching,
    record_web_audit,
    set_repair_job_worktree,
)
from src.repair.diff_scan import backend_risk_level, scan_diff, stricter_risk
from src.repair.worktree import (
    WorktreeError,
    commit_patch,
    copy_evidence_into_worktree,
    create_repair_worktree,
    stage_patch_changes,
    staged_diff_against,
)

logger = logging.getLogger(__name__)

REPAIR_SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "repair-result-schema.json"
PATCH_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent.parent / "knowledge" / "prompts" / "codex_patch_prompt.md"
)
AUDIT_ACTION = "patch_generate"

# Triage classifications eligible for patch generation (blueprint §16.1B:
# "判因为代码或 selector 问题").  workflow_semantic_change is the R3 case:
# analysis only, never a patch.
CODE_OR_SELECTOR_CLASSES = {"selector_change", "shadow_dom_change", "page_state_change"}
SEMANTIC_CLASS = "workflow_semantic_change"

# job status -> audit result vocabulary, following the stage-6 style.
_AUDIT_RESULT_BY_STATUS = {
    "patch_ready": "patch_ready",
    "timeout": "timeout",
    "unavailable": "unavailable",
    "quota_exceeded": "quota_exceeded",
    "schema_invalid": "schema_invalid",
    "failed": "error",
    "validation_failed": "validation_failed",
}


@functools.lru_cache(maxsize=1)
def _load_repair_schema() -> dict:
    return json.loads(REPAIR_SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_repair_result(payload) -> bool:
    return isinstance(payload, dict) and _validate(payload, _load_repair_schema())


def build_patch_prompt(settings, incident: dict, triage: dict, evidence_files: list[str]) -> str:
    template = PATCH_PROMPT_PATH.read_text(encoding="utf-8")
    evidence_listing = (
        "\n".join(f"- `{name}`" for name in evidence_files)
        if evidence_files
        else "- (no evidence files copied)"
    )
    allowed_listing = "\n".join(f"- `{path}`" for path in settings.codex_patch_allowed_paths)
    return (
        template
        .replace("{{SIGNATURE}}", str(incident.get("signature") or ""))
        .replace("{{CLASSIFICATION}}", str(incident.get("classification") or ""))
        .replace("{{TRIAGE_CLASSIFICATION}}", str(triage.get("classification") or ""))
        .replace("{{TRIAGE_REASON}}", str(triage.get("reason") or ""))
        .replace("{{TRIAGE_RECOMMENDED_SCOPE}}", str(triage.get("recommended_scope") or ""))
        .replace("{{EVIDENCE_FILES}}", evidence_listing)
        .replace("{{ALLOWED_PATHS}}", allowed_listing)
    )


def build_patch_argv(settings, worktree_path: Path, out_path: Path) -> list[str]:
    argv = [
        *resolve_command_argv(settings.codex_command),
        "exec",
        "--json",
        "--output-schema",
        str(REPAIR_SCHEMA_PATH),
        "-o",
        str(out_path),
        "-s",
        "workspace-write",
        "-C",
        str(Path(worktree_path).resolve()),
    ]
    if str(settings.codex_model or "").strip():
        argv += ["-m", str(settings.codex_model).strip()]
    argv.append("-")
    return argv


def _load_triage_payload(settings, job: dict) -> dict | None:
    """Validated result.json of a succeeded triage job, confined to the
    codex state root (same allowlist mechanism as the API reads)."""
    raw_path = str(job.get("result_json_path") or "").strip()
    if not raw_path:
        return None
    try:
        resolved = Path(raw_path).resolve()
        state_root = Path(settings.codex_state_root).resolve()
        if not resolved.is_file() or (resolved != state_root and state_root not in resolved.parents):
            return None
        return json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _run_single_attempt(settings, incident: dict, job: dict, worktree,
                        evidence_files: list[str], triage: dict, *, spawn=None) -> tuple[dict, dict | None]:
    """Run one Codex subprocess inside the repair worktree.

    Returns ``(job, payload)``; ``payload`` is the validated result on
    success.  The job row is finished on timeout / failure / schema_invalid;
    a ``running`` job with a payload means success.
    """
    db_path = str(settings.db_path)
    job_id = int(job["id"])

    log_dir = Path(settings.codex_logs_root) / str(job_id)
    state_dir = Path(settings.codex_state_root) / str(job_id)
    log_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = log_dir / "codex-events.jsonl"
    result_path = state_dir / "result.json"
    out_path = state_dir / "last-message.txt"

    def _finish(status: str, **fields) -> tuple[dict, None]:
        return (finish_repair_job(db_path, job_id, status, **fields) or job), None

    prompt = build_patch_prompt(settings, incident, triage, evidence_files)
    argv = build_patch_argv(settings, worktree.path, out_path)

    spawn_fn = spawn or _spawn
    try:
        proc = spawn_fn(argv, Path(worktree.path))
    except FileNotFoundError:
        return _finish("unavailable")
    except OSError as exc:
        logger.warning("[codex] 补丁子进程启动失败 job=%s: %s", job_id, exc)
        return _finish("failed")

    timed_out = False
    try:
        stdout_bytes, _stderr = proc.communicate(
            input=prompt.encode("utf-8"), timeout=float(settings.codex_patch_timeout_sec)
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
    if payload is None or not validate_repair_result(payload):
        return _finish("schema_invalid", **base_fields)

    result_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    artifact_fields = {"result_json_path": str(result_path), **base_fields}
    # Keep the job running; artifact paths are returned so later failure
    # stages (scan/risk refusal) still carry them.
    refreshed = get_repair_job(db_path, job_id) or job
    return refreshed, (payload, artifact_fields)


def run_patch_generation(
    settings,
    incident_id: int,
    *,
    allow_r2: bool = False,
    trigger: str = "manual",
    actor_id: int | None = None,
    ip_address: str | None = None,
    spawn=None,
    availability=None,
) -> dict:
    """Generate an isolated repair patch for a triaged incident.

    Returns ``{"outcome", "job", "result", "incident", "violations"}`` where
    ``outcome`` is one of patch_ready / validation_failed / r2_not_allowed /
    r3_refused / timeout / unavailable / schema_invalid / error /
    empty_patch / worktree_failed / active_patch_conflict /
    incident_not_triaged / incident_closed / unsafe_to_patch /
    unknown_incident / disabled.  Every path writes one ``patch_generate``
    audit event.
    """
    db_path = str(settings.db_path)

    def _audit(result: str, job_id: int | None = None, **extra) -> None:
        detail = {"trigger": trigger, "allow_r2": bool(allow_r2)}
        if job_id is not None:
            detail["job_id"] = job_id
        detail.update(extra)
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
        return {"outcome": "unknown_incident", "job": None, "result": None,
                "incident": None, "violations": []}

    def _done(outcome: str, job: dict | None, result=None, violations=None) -> dict:
        return {
            "outcome": outcome,
            "job": job,
            "result": result,
            "incident": get_incident(db_path, incident_id),
            "violations": violations or [],
        }

    if not bool(getattr(settings, "codex_enabled", True)):
        _audit("unavailable")
        return _done("disabled", None)
    status = str(incident.get("status") or "")
    if status not in ("open", "triaged", "patching", "patch_ready"):
        _audit("incident_closed")
        return _done("incident_closed", None)
    if status != "triaged":
        _audit("incident_not_triaged")
        return _done("incident_not_triaged", None)
    if incident.get("evidence_status") != "ready":
        _audit("evidence_incomplete", missing_evidence=incident.get("missing_evidence") or [])
        return _done("evidence_incomplete", None)

    triage_job = get_latest_triage_job(db_path, incident_id)
    triage = _load_triage_payload(settings, triage_job) if triage_job else None
    if triage_job is None or triage_job.get("status") != "succeeded" or triage is None:
        _audit("unsafe_to_patch")
        return _done("unsafe_to_patch", None)

    triage_class = str(triage.get("classification") or "")
    if triage_class == SEMANTIC_CLASS:
        # R3 by triage: analysis only, never start patch generation.
        _audit("r3_refused")
        return _done("r3_refused", None)
    if triage_class not in CODE_OR_SELECTOR_CLASSES or not triage.get("safe_to_generate_patch"):
        _audit("unsafe_to_patch")
        return _done("unsafe_to_patch", None)

    probe = availability or check_availability(settings.codex_command)
    if not probe.available:
        _audit("unavailable")
        return _done("unavailable", None)

    # Concurrency dedup: same-transaction check-and-insert, partial unique
    # index as backstop (blueprint §17.4).
    job = create_patch_job(db_path, incident_id)
    if job is None:
        _audit("active_patch_conflict")
        return _done("active_patch_conflict", None)
    job_id = int(job["id"])

    try:
        worktree = create_repair_worktree(settings, job_id=job_id, incident=incident)
    except (WorktreeError, subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("[repair] worktree 创建失败 job=%s: %s", job_id, exc)
        job = finish_repair_job(db_path, job_id, "failed") or job
        _audit("worktree_failed", job_id=job_id)
        return _done("worktree_failed", job)
    set_repair_job_worktree(
        db_path, job_id,
        worktree_path=str(worktree.path), branch_name=worktree.branch,
        baseline_sha=worktree.baseline_sha,
    )
    mark_incident_patching(db_path, incident_id)
    evidence_files = copy_evidence_into_worktree(incident, worktree.path)

    # One attempt plus exactly one retry on schema-invalid results (same job
    # row — the unique index forbids a second active patch row).
    payload = None
    artifact_fields: dict = {}
    for attempt in (1, 2):
        try:
            job, attempt_outcome = _run_single_attempt(
                settings, incident, job, worktree, evidence_files, triage, spawn=spawn
            )
        except Exception:  # noqa: BLE001 — patch generation must never break the caller
            logger.exception("[codex] 补丁生成异常 incident=%s", incident_id)
            job = finish_repair_job(db_path, job_id, "failed") or job
            mark_incident_patch_failed(db_path, incident_id)
            _audit("error", job_id=job_id)
            return _done("error", job)
        if attempt_outcome is not None:
            payload, artifact_fields = attempt_outcome
            break
        if job["status"] != "schema_invalid" or attempt == 2:
            break

    if payload is None:
        status = str(job["status"])
        audit_result = _AUDIT_RESULT_BY_STATUS.get(status, "error")
        _audit(audit_result, job_id=job_id)
        mark_incident_patch_failed(db_path, incident_id)
        return _done(audit_result, job)

    # The git-derived file list is the source of truth, not the model's
    # self-report.
    allowed_paths = [str(p) for p in settings.codex_patch_allowed_paths]
    try:
        changed_files = stage_patch_changes(worktree.path, worktree.baseline_sha)
    except WorktreeError as exc:
        logger.warning("[repair] 补丁暂存失败 job=%s: %s", job_id, exc)
        job = finish_repair_job(db_path, job_id, "failed", **artifact_fields) or job
        mark_incident_patch_failed(db_path, incident_id)
        _audit("error", job_id=job_id)
        return _done("error", job)

    if not changed_files:
        job = finish_repair_job(db_path, job_id, "failed", **artifact_fields) or job
        mark_incident_patch_failed(db_path, incident_id)
        _audit("empty_patch", job_id=job_id)
        return _done("empty_patch", job)

    # Risk gate (blueprint §17.3): stricter of Codex self-assessment and the
    # backend's file-path review wins.
    final_risk = stricter_risk(
        str(payload.get("risk_level") or ""), backend_risk_level(changed_files, allowed_paths)
    )
    finish_fields = {
        **artifact_fields,
        "changed_files_json": json.dumps(changed_files, ensure_ascii=False),
        "risk_level": final_risk,
        "tests_passed": int(bool(payload.get("tests_passed"))),
    }

    if final_risk == "R3":
        job = finish_repair_job(db_path, job_id, "validation_failed", **finish_fields) or job
        mark_incident_patch_failed(db_path, incident_id)
        _audit("r3_refused", job_id=job_id, risk_level=final_risk)
        return _done("r3_refused", job, result=payload)
    if final_risk == "R2" and not allow_r2:
        job = finish_repair_job(db_path, job_id, "validation_failed", **finish_fields) or job
        mark_incident_patch_failed(db_path, incident_id)
        _audit("r2_not_allowed", job_id=job_id, risk_level=final_risk)
        return _done("r2_not_allowed", job, result=payload)

    diff_text = staged_diff_against(worktree.path, worktree.baseline_sha)
    report = scan_diff(diff_text, allowed_paths=allowed_paths)
    if not report.passed:
        job = finish_repair_job(db_path, job_id, "validation_failed", **finish_fields) or job
        mark_incident_patch_failed(db_path, incident_id)
        violations = [
            {"rule_id": v.rule_id, "detail": v.detail, "file": v.file}
            for v in report.violations
        ]
        _audit("validation_failed", job_id=job_id, risk_level=final_risk,
               rules=[v["rule_id"] for v in violations])
        return _done("validation_failed", job, result=payload, violations=violations)

    patch_sha = commit_patch(worktree.path, incident_id)
    state_dir = Path(settings.codex_state_root) / str(job_id)
    state_dir.mkdir(parents=True, exist_ok=True)
    diff_path = state_dir / "patch.diff"
    diff_path.write_text(diff_text, encoding="utf-8")

    job = finish_repair_job(
        db_path, job_id, "patch_ready", patch_sha=patch_sha, **finish_fields
    ) or job
    mark_incident_patch_ready(db_path, incident_id)
    _audit("patch_ready", job_id=job_id, risk_level=final_risk)
    return _done("patch_ready", job, result=payload)
