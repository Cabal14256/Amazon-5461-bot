"""Pydantic response models — the API whitelist.

Every endpoint serializes through these models so secret fields (account
usernames/emails, AdsPower profile ids, entry URLs, password hashes) can
never leak into a response by accident.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class LoginRequest(_Model):
    username: str
    password: str


class WebUserOut(_Model):
    id: int
    username: str
    role: str
    display_name: str | None = None
    disabled: int = 0
    created_at: str | None = None
    updated_at: str | None = None
    last_login_at: str | None = None


class AccountOut(_Model):
    """Catalog account — deliberately excludes username/adspower_profile_id/entry_url."""

    account_id: str
    marketplace: str | None = None
    status: str | None = None
    note: str | None = None
    domain: str | None = None
    item_type_keyword: str | None = None


class SiteOut(_Model):
    code: str
    marketplace: str | None = None


class BrandOut(_Model):
    name: str


class HealthCheckOut(_Model):
    ok: bool
    detail: dict[str, Any] = {}


class HealthOut(_Model):
    ok: bool
    checks: dict[str, HealthCheckOut]
    time: str


class StatusResolution(_Model):
    status: str
    source: str
    detail: str | None = None
    checked_at: str | None = None


class ApplicationOut(_Model):
    account_id: str
    marketplace: str
    brand_name: str
    submitted_at: str | None = None
    case_id: str | None = None
    submit_result: str | None = None
    authoritative: StatusResolution


class CaseFollowupOut(_Model):
    id: int
    account_id: str
    marketplace: str
    brand_name: str
    case_id: str
    submitted_at: str | None = None
    scheduled_at: str | None = None
    status: str
    attempt_count: int = 0
    last_checked_at: str | None = None
    completed_at: str | None = None
    case_status: str | None = None
    final_result: str | None = None
    decision_reason: str | None = None
    evidence_path: str | None = None
    error: str | None = None
    reapplication_campaign_id: int | None = None
    reapplication_attempt_id: int | None = None
    created_at: str | None = None
    updated_at: str | None = None


class CaseIdRecoveryOut(_Model):
    id: int
    account_id: str
    marketplace: str
    brand_name: str
    sku: str | None = None
    submitted_at: str | None = None
    scheduled_at: str | None = None
    status: str
    attempt_count: int = 0
    last_checked_at: str | None = None
    completed_at: str | None = None
    case_id: str | None = None
    dashboard_status: str | None = None
    decision_reason: str | None = None
    evidence_path: str | None = None
    error: str | None = None
    reapplication_campaign_id: int | None = None
    reapplication_attempt_id: int | None = None
    created_at: str | None = None
    updated_at: str | None = None


class ReapplicationAttemptOut(_Model):
    id: int
    campaign_id: int
    route_index: int
    site: str
    status: str
    scheduled_at: str | None = None
    started_at: str | None = None
    submitted_at: str | None = None
    completed_at: str | None = None
    case_id: str | None = None
    final_result: str | None = None
    decision_reason: str | None = None
    error: str | None = None


class ReapplicationCampaignOut(_Model):
    id: int
    account_id: str
    brand_name: str
    region: str
    route: list[str] = []
    current_route_index: int = 0
    status: str
    stop_reason: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    completed_at: str | None = None
    attempts: list[ReapplicationAttemptOut] = []


class JobCreateRequest(_Model):
    """POST /api/jobs/diagnose|dry-run|submit body."""

    account: str
    brands: list[str]
    site: str | None = None


# 与前端 JobRunStatus 枚举（docs/plan §8 状态模型）对齐的 9 态。
JobRunStatus = Literal[
    "queued",
    "starting",
    "running",
    "stop_requested",
    "waiting_human",
    "completed",
    "failed",
    "cancelled_before_start",
    "terminated_unknown_state",
]


class AutomationJobOut(_Model):
    """Job row — local filesystem paths stay server-side on purpose."""

    id: str
    job_type: str  # "diagnose" | "dry_run" | "submit"
    run_status: JobRunStatus
    created_by: str
    account_id: str
    marketplace: str | None = None
    brands: list[str] = []
    pid: int | None = None
    exit_code: int | None = None
    error_class: str | None = None
    # Read-time annotation for queued jobs: why the dispatcher is holding back
    # (waiting_case_followup / waiting_profile_lock / waiting_serial_queue).
    queue_reason: str | None = None
    stop_requested_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class SubmitJobResponse(_Model):
    """POST /api/jobs/submit response — the queued job plus preflight checks."""

    job: AutomationJobOut
    preflight: list[dict[str, Any]] = []


class AutomationJobItemOut(_Model):
    id: int
    job_id: str
    account_id: str | None = None
    marketplace: str | None = None
    brand_name: str | None = None
    run_status: str | None = None
    business_status: str | None = None
    case_id: str | None = None
    dashboard_status: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    note: str | None = None


class EvidenceItemOut(_Model):
    path: str  # relative to the evidence/logs allowlist root
    root: str  # which allowlist root the path belongs to
    type: str  # "text" | "image" | "binary"
    size: int
    mtime: str | None = None


class IncidentOut(_Model):
    """Stage-5 repair incident — signature is a 16-hex short hash."""

    id: int
    signature: str
    scope_type: str
    flow_type: str = ""
    account_id: str = ""
    marketplace: str = ""
    brand_name: str = ""
    detector_type: str = ""
    classification: str
    confidence: float = 0.0
    status: str
    occurrence_count: int = 0
    first_seen_at: str | None = None
    last_seen_at: str | None = None
    evidence_bundle_path: str = ""
    resolution_note: str | None = None
    codex_thread_id: str | None = None


class IncidentCloseRequest(_Model):
    """POST /api/incidents/{id}/close body."""

    note: str = ""


class RepairJobOut(_Model):
    """codex_repair_jobs row (stage='triage' in stage 6, 'patch' in stage 7)."""

    id: int
    incident_id: int
    stage: str
    status: str
    worktree_path: str | None = None
    branch_name: str | None = None
    codex_session_id: str | None = None
    jsonl_log_path: str | None = None
    result_json_path: str | None = None
    changed_files: list[str] = []
    risk_level: str | None = None
    tests_passed: int | None = None
    baseline_sha: str | None = None
    patch_sha: str | None = None
    created_at: str | None = None
    finished_at: str | None = None


class GeneratePatchRequest(_Model):
    """POST /api/incidents/{id}/generate-patch body (stage 7)."""

    # R2 patches (navigation/form-step semantics) only proceed when the
    # operator explicitly allows them; R3 is never generated.
    allow_r2: bool = False
