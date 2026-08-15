"""Web console settings: ``config/settings.yaml`` ``web:`` section + ``.env``.

Only non-secret configuration lives in YAML.  The session signing secret is
read from the ``WEB_SESSION_SECRET`` environment variable (``.env``); when it
is missing an ephemeral random secret is generated so the console still runs
for local development, but all sessions die on restart.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.yaml"


@dataclass
class WebSettings:
    host: str = "127.0.0.1"
    port: int = 8080
    db_path: Path = PROJECT_ROOT / "runtime" / "state" / "ledger.db"
    accounts_path: Path = PROJECT_ROOT / "runtime" / "private" / "accounts.json"
    marketplaces_dir: Path = PROJECT_ROOT / "config" / "marketplaces"
    brand_packs_root: Path = PROJECT_ROOT / "brand_packs"
    evidence_root: Path = PROJECT_ROOT / "runtime" / "evidence"
    logs_root: Path = PROJECT_ROOT / "runtime" / "logs"
    state_root: Path = PROJECT_ROOT / "runtime" / "state"
    data_root: Path = PROJECT_ROOT / "data"
    frontend_dist: Path = PROJECT_ROOT / "frontend" / "dist"
    codex_signal_path: Path = PROJECT_ROOT / "runtime" / "codex_signal.json"
    adspower_api_base_url: str = "http://local.adspower.net:50325"
    session_secret: str = ""
    session_cookie: str = "web_session"
    session_ttl_hours: float = 12.0
    ephemeral_secret: bool = False
    ssl_cert_path: str = ""
    ssl_key_path: str = ""
    # SSE job-event stream poll interval (2s per the stage-3 plan; tests lower it).
    sse_poll_seconds: float = 2.0
    # Job dispatcher tick interval (2s per the stage-3 plan; tests lower it).
    job_tick_seconds: float = 2.0
    # Informational only — actual restriction is enforced by the OS firewall.
    allowed_cidrs: list[str] = field(default_factory=list)
    # Stage-4 real-submit gate. Real submission is enabled by default, but it
    # still requires the explicit POST /api/jobs/submit entry (reviewer+),
    # preflight and audit. Setting this to False remains the emergency/master
    # disable switch.
    submit_enabled: bool = True
    submit_max_brands: int = 5
    # Stage-5 persisted anomaly detection (repair incident queue).
    incidents_enabled: bool = True
    # Below this confidence an incident needs manual review; at or above the
    # auto-triage threshold it becomes a fix candidate (stage 6 consumes this).
    incidents_manual_review_below: float = 0.60
    incidents_auto_triage_at_or_above: float = 0.80
    # Stage-6 Codex read-only triage (repair incident queue).
    codex_enabled: bool = True
    # Command line for the Codex CLI; split with shlex so tests can point it
    # at a quoted interpreter + fixture script without PATH tricks.
    codex_command: str = "codex"
    codex_model: str = ""  # empty = CLI default model
    codex_timeout_sec: float = 120.0
    codex_auto_triage_min_confidence: float = 0.60
    codex_daily_call_limit: int = 50
    codex_auto_triage_poll_seconds: float = 15.0
    codex_logs_root: Path = PROJECT_ROOT / "runtime" / "logs" / "repair"
    codex_state_root: Path = PROJECT_ROOT / "runtime" / "state" / "repair"
    # Stage-7 isolated patch generation (git worktree + Codex workspace-write).
    codex_patch_timeout_sec: float = 600.0
    codex_worktree_root: Path = PROJECT_ROOT / "runtime" / "worktrees"
    # released/rejected worktrees are kept this long for rollback forensics.
    codex_worktree_retention_days: int = 14
    # R0/R1 allowed modification scope (repo-root relative prefixes).
    codex_patch_allowed_paths: list[str] = field(
        default_factory=lambda: ["config/selectors/", "src/executor/", "src/capture/", "tests/"]
    )
    # Stage-8 validation gate / diff review / release.  release_enabled is the
    # master switch (off by default, mirroring the web.submit_enabled
    # precedent); canary pins the single canary target for a staged release.
    codex_validation_timeout_sec: int = 900
    codex_diff_review_enabled: bool = True
    codex_release_enabled: bool = False
    codex_canary: dict = field(default_factory=dict)
    # Repository root handed to `codex exec -C` (read-only sandbox scope).
    repo_root: Path = PROJECT_ROOT

    @property
    def allowed_roots(self) -> list[Path]:
        return [self.evidence_root, self.logs_root]

    @property
    def tls_enabled(self) -> bool:
        return bool(self.ssl_cert_path and self.ssl_key_path)

    @property
    def cookie_secure(self) -> bool:
        # Secure attribute only when serving over HTTPS; browsers reject
        # Secure cookies on plain HTTP localhost otherwise.
        return self.tls_enabled


def _resolve(root: Path, value: object, fallback: Path) -> Path:
    text = str(value or "").strip()
    if not text:
        return fallback
    path = Path(text)
    return path if path.is_absolute() else (root / path)


def load_settings(settings_path: Path | None = None) -> WebSettings:
    load_dotenv(PROJECT_ROOT / ".env")
    settings_path = Path(settings_path) if settings_path else DEFAULT_SETTINGS_PATH
    raw: dict = {}
    if settings_path.exists():
        raw = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
    web = raw.get("web") or {}
    paths = raw.get("paths") or {}
    adspower = raw.get("adspower") or {}

    import os

    settings = WebSettings()
    settings.host = str(web.get("host") or settings.host)
    settings.port = int(web.get("port") or settings.port)
    settings.db_path = _resolve(PROJECT_ROOT, paths.get("db_path"), settings.db_path)
    settings.accounts_path = _resolve(
        PROJECT_ROOT,
        web.get("accounts_path") or os.getenv("AMAZON5461_ACCOUNTS_PATH"),
        settings.accounts_path,
    )
    settings.evidence_root = _resolve(PROJECT_ROOT, paths.get("evidence_root"), settings.evidence_root)
    settings.logs_root = _resolve(PROJECT_ROOT, paths.get("logs_root"), settings.logs_root)
    settings.state_root = _resolve(PROJECT_ROOT, web.get("state_root"), settings.state_root)
    settings.data_root = _resolve(PROJECT_ROOT, web.get("data_root"), settings.data_root)
    settings.frontend_dist = _resolve(PROJECT_ROOT, web.get("frontend_dist"), settings.frontend_dist)
    settings.marketplaces_dir = _resolve(PROJECT_ROOT, web.get("marketplaces_dir"), settings.marketplaces_dir)
    settings.brand_packs_root = _resolve(PROJECT_ROOT, web.get("brand_packs_root"), settings.brand_packs_root)
    settings.adspower_api_base_url = str(
        adspower.get("api_base_url") or settings.adspower_api_base_url
    )
    settings.session_ttl_hours = float(web.get("session_ttl_hours") or settings.session_ttl_hours)
    settings.ssl_cert_path = str(web.get("ssl_cert_path") or "")
    settings.ssl_key_path = str(web.get("ssl_key_path") or "")
    cidrs = web.get("allowed_cidrs") or []
    settings.allowed_cidrs = [str(c) for c in cidrs]
    settings.submit_enabled = bool(web.get("submit_enabled", settings.submit_enabled))
    settings.submit_max_brands = int(web.get("submit_max_brands") or settings.submit_max_brands)

    incidents = raw.get("incidents") or {}
    settings.incidents_enabled = bool(incidents.get("enabled", settings.incidents_enabled))
    settings.incidents_manual_review_below = float(
        incidents.get("manual_review_below") or settings.incidents_manual_review_below
    )
    settings.incidents_auto_triage_at_or_above = float(
        incidents.get("auto_triage_at_or_above") or settings.incidents_auto_triage_at_or_above
    )

    codex = raw.get("codex") or {}
    settings.codex_enabled = bool(codex.get("enabled", settings.codex_enabled))
    settings.codex_command = str(codex.get("command") or settings.codex_command)
    settings.codex_model = str(codex.get("model") or "")
    settings.codex_timeout_sec = float(codex.get("timeout_sec") or settings.codex_timeout_sec)
    settings.codex_auto_triage_min_confidence = float(
        codex.get("auto_triage_min_confidence") or settings.codex_auto_triage_min_confidence
    )
    settings.codex_daily_call_limit = int(
        codex.get("daily_call_limit") or settings.codex_daily_call_limit
    )
    settings.codex_auto_triage_poll_seconds = float(
        codex.get("auto_triage_poll_seconds") or settings.codex_auto_triage_poll_seconds
    )
    settings.codex_logs_root = _resolve(
        PROJECT_ROOT, codex.get("logs_root"), settings.codex_logs_root
    )
    settings.codex_state_root = _resolve(
        PROJECT_ROOT, codex.get("state_root"), settings.codex_state_root
    )
    settings.codex_patch_timeout_sec = float(
        codex.get("patch_timeout_sec") or settings.codex_patch_timeout_sec
    )
    settings.codex_worktree_root = _resolve(
        PROJECT_ROOT, codex.get("worktree_root"), settings.codex_worktree_root
    )
    settings.codex_worktree_retention_days = int(
        codex.get("worktree_retention_days") or settings.codex_worktree_retention_days
    )
    allowed_paths = codex.get("patch_allowed_paths")
    if isinstance(allowed_paths, list) and allowed_paths:
        settings.codex_patch_allowed_paths = [str(p) for p in allowed_paths]
    settings.codex_validation_timeout_sec = int(
        codex.get("validation_timeout_sec") or settings.codex_validation_timeout_sec
    )
    settings.codex_diff_review_enabled = bool(
        codex.get("diff_review_enabled", settings.codex_diff_review_enabled)
    )
    settings.codex_release_enabled = bool(
        codex.get("release_enabled", settings.codex_release_enabled)
    )
    canary = codex.get("canary")
    if isinstance(canary, dict):
        settings.codex_canary = {
            "account_id": str(canary.get("account_id") or ""),
            "marketplace": str(canary.get("marketplace") or ""),
            "brand_name": str(canary.get("brand_name") or ""),
        }

    secret = os.getenv("WEB_SESSION_SECRET", "").strip()
    if not secret:
        # Ephemeral development secret: sessions invalidate on restart.
        settings.session_secret = secrets.token_hex(32)
        settings.ephemeral_secret = True
    else:
        settings.session_secret = secret
    return settings
