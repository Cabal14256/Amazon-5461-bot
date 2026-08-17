\
import sqlite3
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Iterable, Dict, Any, List


SCHEMA_SQL = r'''
CREATE TABLE IF NOT EXISTS submissions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id TEXT NOT NULL,
  marketplace TEXT NOT NULL,
  brand_name TEXT NOT NULL,
  submitted_at TEXT NOT NULL,
  case_id TEXT,
  submit_result TEXT NOT NULL,
  note TEXT
);

CREATE TABLE IF NOT EXISTS verifications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id TEXT NOT NULL,
  marketplace TEXT NOT NULL,
  brand_name TEXT NOT NULL,
  method TEXT NOT NULL,
  verified_at TEXT NOT NULL,
  result TEXT NOT NULL,
  summary_text TEXT,
  confidence_points INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS evidence_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  related_type TEXT NOT NULL,
  related_id INTEGER NOT NULL,
  evidence_type TEXT NOT NULL,
  file_path TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS brand_status_snapshot (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id TEXT NOT NULL,
  marketplace TEXT NOT NULL,
  brand_name TEXT NOT NULL,
  current_status TEXT NOT NULL,
  confidence_score INTEGER NOT NULL DEFAULT 0,
  last_verified_at TEXT,
  last_method TEXT,
  updated_at TEXT NOT NULL,
  UNIQUE(account_id, marketplace, brand_name)
);

CREATE INDEX IF NOT EXISTS idx_submissions_acc_brand
ON submissions(account_id, marketplace, brand_name, submitted_at);
CREATE INDEX IF NOT EXISTS idx_verifications_acc_brand
ON verifications(account_id, marketplace, brand_name, verified_at);
CREATE INDEX IF NOT EXISTS idx_evidence_related
ON evidence_items(related_type, related_id);

CREATE TABLE IF NOT EXISTS case_followups (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id TEXT NOT NULL,
  marketplace TEXT NOT NULL,
  brand_name TEXT NOT NULL,
  case_id TEXT NOT NULL,
  submitted_at TEXT NOT NULL,
  scheduled_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  attempt_count INTEGER NOT NULL DEFAULT 0,
  ai_attempt_count INTEGER NOT NULL DEFAULT 0,
  last_checked_at TEXT,
  completed_at TEXT,
  case_status TEXT,
  final_result TEXT,
  decision_reason TEXT,
  evidence_path TEXT,
  error TEXT,
  feishu_record_id TEXT,
  feishu_country_option TEXT,
  feishu_binding_status TEXT,
  feishu_binding_reason TEXT,
  feishu_bound_at TEXT,
  reapplication_campaign_id INTEGER,
  reapplication_attempt_id INTEGER,
  claimed_pid INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(account_id, marketplace, case_id)
);
CREATE INDEX IF NOT EXISTS idx_case_followups_due
ON case_followups(status, scheduled_at);

CREATE TABLE IF NOT EXISTS reapplication_campaigns (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id TEXT NOT NULL,
  brand_name TEXT NOT NULL,
  region TEXT NOT NULL,
  route_json TEXT NOT NULL,
  current_route_index INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'scheduled',
  submit_authorized INTEGER NOT NULL DEFAULT 0,
  decline_delay_hours REAL NOT NULL DEFAULT 2.0,
  stop_reason TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_reapplication_campaigns_status
ON reapplication_campaigns(status, updated_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_reapplication_campaigns_one_active
ON reapplication_campaigns(account_id, brand_name, region)
WHERE status IN (
  'scheduled', 'next_scheduled', 'running', 'waiting_case_id',
  'waiting_case', 'paused', 'blocked'
);

CREATE TABLE IF NOT EXISTS reapplication_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id INTEGER NOT NULL,
  route_index INTEGER NOT NULL,
  site TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'scheduled',
  scheduled_at TEXT NOT NULL,
  started_at TEXT,
  submitted_at TEXT,
  completed_at TEXT,
  case_id TEXT,
  case_followup_id INTEGER,
  final_result TEXT,
  decision_reason TEXT,
  error TEXT,
  state_path TEXT,
  stdout_path TEXT,
  stderr_path TEXT,
  pid INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(campaign_id, route_index),
  FOREIGN KEY(campaign_id) REFERENCES reapplication_campaigns(id)
);
CREATE INDEX IF NOT EXISTS idx_reapplication_attempts_due
ON reapplication_attempts(status, scheduled_at);

CREATE TABLE IF NOT EXISTS case_id_recoveries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id TEXT NOT NULL,
  marketplace TEXT NOT NULL,
  brand_name TEXT NOT NULL,
  sku TEXT,
  submitted_at TEXT NOT NULL,
  scheduled_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  attempt_count INTEGER NOT NULL DEFAULT 0,
  last_checked_at TEXT,
  completed_at TEXT,
  case_id TEXT,
  dashboard_status TEXT,
  decision_reason TEXT,
  evidence_path TEXT,
  error TEXT,
  feishu_country_option TEXT,
  reapplication_campaign_id INTEGER,
  reapplication_attempt_id INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(account_id, marketplace, brand_name, submitted_at)
);
CREATE INDEX IF NOT EXISTS idx_case_id_recoveries_due
ON case_id_recoveries(status, scheduled_at);

CREATE TABLE IF NOT EXISTS decision_rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  flow_type TEXT NOT NULL,
  scope_type TEXT NOT NULL,
  scope_value TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  match_conditions_json TEXT NOT NULL,
  action_plan_json TEXT NOT NULL,
  risk_level TEXT NOT NULL DEFAULT 'medium',
  requires_confirm INTEGER NOT NULL DEFAULT 1,
  created_by TEXT DEFAULT 'user',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  hit_count INTEGER NOT NULL DEFAULT 0,
  last_hit_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_decision_rules_flow_scope
ON decision_rules(flow_type, scope_type, scope_value, enabled);

CREATE TABLE IF NOT EXISTS unknown_cases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  flow_type TEXT NOT NULL,
  account_id TEXT NOT NULL,
  marketplace TEXT NOT NULL,
  brand_name TEXT NOT NULL,
  page_url TEXT,
  page_title TEXT,
  screenshot_path TEXT,
  page_text_summary TEXT,
  dom_summary TEXT,
  ai_page_state TEXT,
  ai_confidence REAL,
  ai_reason TEXT,
  status TEXT NOT NULL DEFAULT 'pending_decision',
  resolved_rule_id INTEGER,
  resolution_note TEXT,
  created_at TEXT NOT NULL,
  resolved_at TEXT,
  FOREIGN KEY(resolved_rule_id) REFERENCES decision_rules(id)
);
CREATE INDEX IF NOT EXISTS idx_unknown_cases_status
ON unknown_cases(status, flow_type, account_id, brand_name);

CREATE TABLE IF NOT EXISTS procedure_flows (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  flow_code TEXT NOT NULL UNIQUE,
  flow_type TEXT NOT NULL,
  name TEXT NOT NULL,
  marketplace TEXT,
  brand_name TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  lifecycle_status TEXT NOT NULL DEFAULT 'draft',
  approved_by TEXT,
  approved_at TEXT,
  last_uat_run_id INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS procedure_steps (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  flow_id INTEGER NOT NULL,
  step_no INTEGER NOT NULL,
  step_name TEXT NOT NULL,
  match_conditions_json TEXT NOT NULL,
  action_plan_json TEXT NOT NULL,
  success_check_json TEXT,
  failure_check_json TEXT,
  risk_level TEXT NOT NULL DEFAULT 'medium',
  requires_confirm INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(flow_id, step_no),
  FOREIGN KEY(flow_id) REFERENCES procedure_flows(id)
);
CREATE INDEX IF NOT EXISTS idx_procedure_steps_flow ON procedure_steps(flow_id, step_no);

CREATE TABLE IF NOT EXISTS procedure_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  flow_id INTEGER NOT NULL,
  account_id TEXT NOT NULL,
  marketplace TEXT NOT NULL,
  brand_name TEXT NOT NULL,
  run_mode TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  note TEXT,
  FOREIGN KEY(flow_id) REFERENCES procedure_flows(id)
);

CREATE TABLE IF NOT EXISTS procedure_run_steps (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL,
  step_no INTEGER NOT NULL,
  step_name TEXT,
  page_url TEXT,
  screenshot_path TEXT,
  action_plan_json TEXT,
  result TEXT NOT NULL,
  detail TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(run_id) REFERENCES procedure_runs(id)
);

CREATE TABLE IF NOT EXISTS web_users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'viewer',
  display_name TEXT,
  disabled INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS web_audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  actor_id INTEGER,
  action TEXT NOT NULL,
  target_type TEXT,
  target_id TEXT,
  result TEXT,
  ip_address TEXT,
  detail TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_web_audit_events_created
ON web_audit_events(created_at);

-- Stage-3 automation job queue (diagnose / dry_run / submit).
CREATE TABLE IF NOT EXISTS automation_jobs (
  id TEXT PRIMARY KEY,
  job_type TEXT NOT NULL,
  run_status TEXT NOT NULL,
  created_by TEXT NOT NULL,
  account_id TEXT NOT NULL,
  marketplace TEXT,
  brands_json TEXT NOT NULL,
  pid INTEGER,
  exit_code INTEGER,
  error_class TEXT,
  state_file TEXT,
  stdout_log TEXT,
  stderr_log TEXT,
  stop_requested_at TEXT,
  started_at TEXT,
  finished_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_automation_jobs_status
ON automation_jobs(run_status, created_at);
CREATE INDEX IF NOT EXISTS idx_automation_jobs_account
ON automation_jobs(account_id, created_at);

CREATE TABLE IF NOT EXISTS automation_job_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT NOT NULL,
  account_id TEXT,
  marketplace TEXT,
  brand_name TEXT,
  run_status TEXT,
  business_status TEXT,
  case_id TEXT,
  dashboard_status TEXT,
  evidence_root TEXT,
  started_at TEXT,
  finished_at TEXT,
  note TEXT
);
CREATE INDEX IF NOT EXISTS idx_automation_job_items_job
ON automation_job_items(job_id);

-- Profile mutual exclusion. profile_key is the first 16 hex chars of the
-- SHA-256 of the AdsPower profile id, so the raw id never lands in the DB.
CREATE TABLE IF NOT EXISTS profile_locks (
  profile_key TEXT PRIMARY KEY,
  owner_type TEXT NOT NULL,
  owner_id TEXT NOT NULL,
  acquired_at TEXT,
  heartbeat_at TEXT,
  expires_at TEXT
);

-- Stage-5 persisted anomaly detection.  Failures are deduplicated by
-- (signature, scope_type, account_id, marketplace, brand_name) while the
-- incident is non-terminal; recurrence bumps occurrence_count/confidence.
-- status: open -> closed_human/closed_duplicate (stage 5); triaged /
-- patching / patch_ready / validating / validated (stages 6-8); released /
-- rejected are the stage-8 terminal states.
CREATE TABLE IF NOT EXISTS repair_incidents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  signature TEXT NOT NULL,
  scope_type TEXT NOT NULL,
  flow_type TEXT NOT NULL DEFAULT '',
  account_id TEXT NOT NULL DEFAULT '',
  marketplace TEXT NOT NULL DEFAULT '',
  brand_name TEXT NOT NULL DEFAULT '',
  detector_type TEXT NOT NULL DEFAULT '',
  classification TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 0.0,
  status TEXT NOT NULL DEFAULT 'open',
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  occurrence_count INTEGER NOT NULL DEFAULT 1,
  evidence_bundle_path TEXT NOT NULL DEFAULT '',
  codex_thread_id TEXT,
  resolution_note TEXT
);
CREATE INDEX IF NOT EXISTS idx_repair_incidents_dedup
ON repair_incidents(signature, scope_type, account_id, marketplace, status);
CREATE INDEX IF NOT EXISTS idx_repair_incidents_status_class
ON repair_incidents(status, classification);

-- Stage-6 Codex read-only triage jobs (blueprint §9.6 full field set).
-- Stage-7 fills stage='patch' rows: worktree_path / branch_name /
-- changed_files_json / risk_level / tests_passed / baseline_sha / patch_sha.
-- Stage-8 adds pre_release_sha / release_sha / validation_json_path /
-- canary_job_ids_json via migration (older DBs).
-- status: running / succeeded / failed / timeout / unavailable /
-- quota_exceeded / schema_invalid / patch_ready / validation_failed /
-- validating / awaiting_validation_approval / canary /
-- awaiting_release_approval / released / rejected / rolled_back
-- (stage-8 validation gate, canary and release lifecycle).
CREATE TABLE IF NOT EXISTS codex_repair_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  incident_id INTEGER NOT NULL,
  stage TEXT NOT NULL DEFAULT 'triage',
  status TEXT NOT NULL DEFAULT 'running',
  worktree_path TEXT,
  branch_name TEXT,
  codex_session_id TEXT,
  jsonl_log_path TEXT,
  result_json_path TEXT,
  changed_files_json TEXT,
  risk_level TEXT,
  tests_passed INTEGER,
  baseline_sha TEXT,
  patch_sha TEXT,
  pre_release_sha TEXT,
  release_sha TEXT,
  validation_json_path TEXT,
  canary_job_ids_json TEXT,
  created_at TEXT NOT NULL,
  finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_codex_repair_jobs_incident
ON codex_repair_jobs(incident_id, stage, id);
CREATE INDEX IF NOT EXISTS idx_codex_repair_jobs_created
ON codex_repair_jobs(stage, created_at);
-- Stage-7/8 concurrency dedup (blueprint §17.4): one active patch job per
-- incident.  SQLite partial unique index; inserts must check-and-insert in
-- one transaction (see create_patch_job).  Older DBs get the predicate
-- widened by _run_migrations (drop + recreate below).
CREATE UNIQUE INDEX IF NOT EXISTS idx_codex_repair_jobs_active_patch
ON codex_repair_jobs(incident_id)
WHERE stage='patch' AND status IN (
  'running','patch_ready','validating',
  'awaiting_validation_approval','canary','awaiting_release_approval'
);

-- Stage-8 human approval trail (validation gate / canary / release).  One
-- row per decision; decision: approve_validation / confirm_canary /
-- approve_release / reject.  Append-only, never updated.
CREATE TABLE IF NOT EXISTS repair_approvals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  repair_job_id INTEGER NOT NULL,
  decision TEXT NOT NULL,
  actor_id INTEGER NOT NULL,
  note TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_repair_approvals_job
ON repair_approvals(repair_job_id);
'''


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_conn(db_path: str):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=5.0)
    # WAL + busy_timeout let the read-only web console read while batch
    # workers write; foreign_keys keeps the new web tables consistent.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_column(conn, table: str, column: str, ddl_suffix: str):
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_suffix}")


def _run_migrations(conn):
    # 让旧版数据库平滑升级到“示教/验收/批准”状态机版本
    _ensure_column(conn, 'procedure_flows', 'lifecycle_status', "TEXT NOT NULL DEFAULT 'draft'")
    _ensure_column(conn, 'procedure_flows', 'approved_by', 'TEXT')
    _ensure_column(conn, 'procedure_flows', 'approved_at', 'TEXT')
    _ensure_column(conn, 'procedure_flows', 'last_uat_run_id', 'INTEGER')
    # Stage-6 triage audits carry a small non-secret JSON detail (trigger +
    # repair job id); older DBs gain the column here.
    _ensure_column(conn, 'web_audit_events', 'detail', 'TEXT')
    # Stage-7 patch jobs record the baseline overlay commit and the patch
    # commit SHAs; older DBs gain the columns here (the partial unique index
    # comes from SCHEMA_SQL, which executescript applies on every init).
    _ensure_column(conn, 'codex_repair_jobs', 'baseline_sha', 'TEXT')
    _ensure_column(conn, 'codex_repair_jobs', 'patch_sha', 'TEXT')
    # Stage-8 validation/release bookkeeping on patch jobs; older DBs gain
    # the columns here.
    _ensure_column(conn, 'codex_repair_jobs', 'pre_release_sha', 'TEXT')
    _ensure_column(conn, 'codex_repair_jobs', 'release_sha', 'TEXT')
    _ensure_column(conn, 'codex_repair_jobs', 'validation_json_path', 'TEXT')
    _ensure_column(conn, 'codex_repair_jobs', 'canary_job_ids_json', 'TEXT')
    # SQLite cannot widen a partial-index predicate in place.  Recreate the
    # active-patch dedup index so the stage-8 gate/canary/release-pending
    # statuses also hold the per-incident exclusive slot (idempotent:
    # drop + create runs on every init).
    conn.execute('DROP INDEX IF EXISTS idx_codex_repair_jobs_active_patch')
    conn.execute(
        """CREATE UNIQUE INDEX idx_codex_repair_jobs_active_patch
           ON codex_repair_jobs(incident_id)
           WHERE stage='patch' AND status IN (
             'running','patch_ready','validating',
             'awaiting_validation_approval','canary','awaiting_release_approval'
           )"""
    )
    # Delayed Case tasks may bind to an existing Feishu row.  These columns do
    # not contain app credentials, Case replies, or row contents.
    _ensure_column(conn, 'case_followups', 'feishu_record_id', 'TEXT')
    _ensure_column(conn, 'case_followups', 'feishu_country_option', 'TEXT')
    _ensure_column(conn, 'case_followups', 'feishu_binding_status', 'TEXT')
    _ensure_column(conn, 'case_followups', 'feishu_binding_reason', 'TEXT')
    _ensure_column(conn, 'case_followups', 'feishu_bound_at', 'TEXT')
    _ensure_column(conn, 'case_followups', 'reapplication_campaign_id', 'INTEGER')
    _ensure_column(conn, 'case_followups', 'reapplication_attempt_id', 'INTEGER')
    # Count Codex reply-classification attempts separately from browser Case
    # checks. A Case may have been polled several times before Amazon replies;
    # those earlier checks must not consume the bounded AI retry allowance.
    _ensure_column(conn, 'case_followups', 'ai_attempt_count', 'INTEGER NOT NULL DEFAULT 0')
    # Worker PID that claimed the task; lets crash recovery requeue stale
    # 'running' rows as soon as the claimant is verifiably dead.
    _ensure_column(conn, 'case_followups', 'claimed_pid', 'INTEGER')
    # SQLite does not alter partial-index predicates in place. Recreate this
    # local-only index so waiting_case_id remains an active campaign state.
    conn.execute('DROP INDEX IF EXISTS idx_reapplication_campaigns_one_active')
    conn.execute(
        """CREATE UNIQUE INDEX idx_reapplication_campaigns_one_active
           ON reapplication_campaigns(account_id, brand_name, region)
           WHERE status IN (
             'scheduled', 'next_scheduled', 'running', 'waiting_case_id',
             'waiting_case', 'paused', 'blocked'
           )"""
    )


def init_db(db_path: str):
    conn = get_conn(db_path)
    conn.executescript(SCHEMA_SQL)
    _run_migrations(conn)
    conn.commit()
    conn.close()


def insert_submission(db_path: str, account_id: str, marketplace: str, brand_name: str,
                      submit_result: str, case_id: Optional[str] = None, note: Optional[str] = None,
                      submitted_at: Optional[str] = None) -> int:
    submitted_at = submitted_at or now_str()
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        '''INSERT INTO submissions(account_id, marketplace, brand_name, submitted_at, case_id, submit_result, note)
           VALUES (?, ?, ?, ?, ?, ?, ?)''',
        (account_id, marketplace, brand_name, submitted_at, case_id, submit_result, note),
    )
    sid = cur.lastrowid
    conn.commit()
    conn.close()
    return sid


def upsert_submission_by_case_id(
    db_path: str,
    account_id: str,
    marketplace: str,
    brand_name: str,
    case_id: str,
    submit_result: str,
    note: Optional[str] = None,
    submitted_at: Optional[str] = None,
) -> int:
    """Insert a submission or update the existing row for the same Case ID."""
    submitted_at = submitted_at or now_str()
    conn = get_conn(db_path)
    row = conn.execute(
        """SELECT id FROM submissions
           WHERE account_id=? AND marketplace=? AND case_id=?
           ORDER BY id DESC LIMIT 1""",
        (account_id, marketplace, str(case_id)),
    ).fetchone()
    if row:
        conn.execute(
            """UPDATE submissions
               SET brand_name=?, submit_result=?, note=COALESCE(?, note)
               WHERE id=?""",
            (brand_name, submit_result, note, row["id"]),
        )
        submission_id = int(row["id"])
    else:
        cur = conn.execute(
            """INSERT INTO submissions(
                   account_id, marketplace, brand_name, submitted_at,
                   case_id, submit_result, note
               ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (account_id, marketplace, brand_name, submitted_at, str(case_id), submit_result, note),
        )
        submission_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return submission_id


def update_submission_case_outcome(
    db_path: str,
    account_id: str,
    marketplace: str,
    brand_name: str,
    case_id: str,
    final_result: str,
    note: Optional[str] = None,
) -> int:
    """Persist a Case-detail outcome without confusing Answered with Approved."""
    return upsert_submission_by_case_id(
        db_path=db_path,
        account_id=account_id,
        marketplace=marketplace,
        brand_name=brand_name,
        case_id=case_id,
        submit_result=final_result,
        note=note,
    )


def upsert_case_outcome_status(
    db_path: str,
    account_id: str,
    marketplace: str,
    brand_name: str,
    current_status: str,
    checked_at: Optional[str] = None,
    method: str = "case_reply",
) -> None:
    checked_at = checked_at or now_str()
    conn = get_conn(db_path)
    conn.execute(
        """INSERT INTO brand_status_snapshot(
               account_id, marketplace, brand_name, current_status,
               confidence_score, last_verified_at, last_method, updated_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(account_id, marketplace, brand_name)
           DO UPDATE SET
             current_status=excluded.current_status,
             confidence_score=excluded.confidence_score,
             last_verified_at=excluded.last_verified_at,
             last_method=excluded.last_method,
             updated_at=excluded.updated_at""",
        (
            account_id,
            marketplace,
            brand_name,
            current_status,
            100 if current_status in {"approved", "declined", "false_approved"} else 50,
            checked_at,
            method,
            checked_at,
        ),
    )
    conn.commit()
    conn.close()


def enqueue_case_followup(
    db_path: str,
    account_id: str,
    marketplace: str,
    brand_name: str,
    case_id: str,
    submitted_at: str,
    scheduled_at: str,
    reapplication_campaign_id: Optional[int] = None,
    reapplication_attempt_id: Optional[int] = None,
) -> tuple[int, bool]:
    """Persist one delayed Case check. Returns ``(id, created)``."""
    now = now_str()
    conn = get_conn(db_path)
    existing = conn.execute(
        """SELECT id, status, scheduled_at, reapplication_campaign_id,
                  reapplication_attempt_id FROM case_followups
           WHERE account_id=? AND marketplace=? AND case_id=?""",
        (account_id, marketplace, str(case_id)),
    ).fetchone()
    if existing:
        existing_campaign_id = existing["reapplication_campaign_id"]
        existing_attempt_id = existing["reapplication_attempt_id"]
        if (
            reapplication_campaign_id is not None
            and existing_campaign_id is not None
            and int(existing_campaign_id) != int(reapplication_campaign_id)
        ) or (
            reapplication_attempt_id is not None
            and existing_attempt_id is not None
            and int(existing_attempt_id) != int(reapplication_attempt_id)
        ):
            conn.close()
            raise ValueError("Case follow-up is already linked to another reapplication attempt")
        if reapplication_campaign_id is not None or reapplication_attempt_id is not None:
            conn.execute(
                """UPDATE case_followups
                   SET reapplication_campaign_id=COALESCE(reapplication_campaign_id, ?),
                       reapplication_attempt_id=COALESCE(reapplication_attempt_id, ?),
                       updated_at=?
                   WHERE id=?""",
                (
                    reapplication_campaign_id,
                    reapplication_attempt_id,
                    now,
                    existing["id"],
                ),
            )
        if existing["status"] in {"pending", "retry"} and scheduled_at < existing["scheduled_at"]:
            conn.execute(
                """UPDATE case_followups
                   SET scheduled_at=?, brand_name=?, updated_at=?
                   WHERE id=?""",
                (scheduled_at, brand_name, now, existing["id"]),
            )
        conn.commit()
        followup_id = int(existing["id"])
        conn.close()
        return followup_id, False

    cur = conn.execute(
        """INSERT INTO case_followups(
               account_id, marketplace, brand_name, case_id, submitted_at,
               scheduled_at, status, reapplication_campaign_id,
               reapplication_attempt_id, created_at, updated_at
           ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)""",
        (
            account_id,
            marketplace,
            brand_name,
            str(case_id),
            submitted_at,
            scheduled_at,
            reapplication_campaign_id,
            reapplication_attempt_id,
            now,
            now,
        ),
    )
    followup_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return followup_id, True


def update_case_followup_feishu_binding(
    db_path: str,
    followup_id: int,
    binding_status: str,
    record_id: Optional[str] = None,
    country_option: Optional[str] = None,
    reason: Optional[str] = None,
) -> bool:
    """Persist a read-only Feishu binding result for one Case task.

    A previously bound record is never cleared by a later transient lookup
    error or ambiguous retry.  Credentials and cell contents are not stored.
    """

    now = now_str()
    conn = get_conn(db_path)
    if binding_status == "bound" and record_id:
        cur = conn.execute(
            """UPDATE case_followups
               SET feishu_record_id=?, feishu_country_option=?,
                   feishu_binding_status='bound', feishu_binding_reason=?,
                   feishu_bound_at=?, updated_at=?
               WHERE id=?""",
            (
                str(record_id),
                str(country_option or ""),
                str(reason or "")[:500],
                now,
                now,
                int(followup_id),
            ),
        )
    else:
        cur = conn.execute(
            """UPDATE case_followups
               SET feishu_binding_status=?, feishu_binding_reason=?, updated_at=?
               WHERE id=? AND COALESCE(feishu_record_id, '')=''""",
            (str(binding_status), str(reason or "")[:500], now, int(followup_id)),
        )
    conn.commit()
    changed = cur.rowcount > 0
    conn.close()
    return changed


def requeue_stale_case_followups(db_path: str, stale_before: str, pid_alive=None) -> int:
    """Requeue interrupted 'running' tasks.

    When ``pid_alive`` is provided, rows claimed by a verifiably dead worker
    PID are recovered immediately; the time-based ``stale_before`` fallback
    only applies to rows without a recorded claimant PID.
    """
    now = now_str()
    conn = get_conn(db_path)
    conn.execute("BEGIN IMMEDIATE")
    rows = conn.execute(
        """SELECT id, claimed_pid, last_checked_at FROM case_followups
           WHERE status='running'"""
    ).fetchall()
    count = 0
    for row in rows:
        pid = row["claimed_pid"]
        if pid is not None and pid_alive is not None:
            try:
                alive = bool(pid_alive(int(pid)))
            except Exception:
                alive = True  # 无法校验时保守保留，不误恢复活 worker 的任务
            if alive:
                continue
        elif not row["last_checked_at"] or str(row["last_checked_at"]) >= stale_before:
            continue
        cur = conn.execute(
            """UPDATE case_followups
               SET status='retry', scheduled_at=?, error='stale_running_requeued',
                   claimed_pid=NULL, updated_at=?
               WHERE id=? AND status='running'""",
            (now, now, row["id"]),
        )
        count += cur.rowcount
    conn.commit()
    conn.close()
    return count


def claim_due_case_followups(
    db_path: str,
    due_at: Optional[str] = None,
    limit: int = 1,
    followup_ids: Optional[Iterable[int]] = None,
    reapplication_only: bool = False,
    oldest_group_only: bool = False,
    account_id: Optional[str] = None,
    marketplace: Optional[str] = None,
    claimed_pid: Optional[int] = None,
) -> list[Dict[str, Any]]:
    due_at = due_at or now_str()
    selected_ids = [int(value) for value in (followup_ids or [])]
    conn = get_conn(db_path)
    conn.execute("BEGIN IMMEDIATE")
    sql = """SELECT * FROM case_followups
             WHERE status IN ('pending', 'retry') AND scheduled_at <= ?"""
    params: list[Any] = [due_at]
    if reapplication_only:
        sql += " AND reapplication_campaign_id IS NOT NULL"
    if selected_ids:
        sql += " AND id IN (" + ",".join("?" for _ in selected_ids) + ")"
        params.extend(selected_ids)
    if account_id is not None:
        sql += " AND account_id=?"
        params.append(str(account_id))
    if marketplace is not None:
        sql += " AND marketplace=?"
        params.append(str(marketplace).upper())
    sql += " ORDER BY scheduled_at, id"
    if oldest_group_only:
        first = conn.execute(sql + " LIMIT 1", params).fetchone()
        if first is None:
            rows = []
        else:
            group_sql = """SELECT * FROM case_followups
                           WHERE status IN ('pending', 'retry')
                             AND scheduled_at <= ?
                             AND account_id=? AND marketplace=?"""
            group_params: list[Any] = [
                due_at,
                first["account_id"],
                first["marketplace"],
            ]
            if reapplication_only:
                group_sql += " AND reapplication_campaign_id IS NOT NULL"
            if selected_ids:
                group_sql += " AND id IN (" + ",".join("?" for _ in selected_ids) + ")"
                group_params.extend(selected_ids)
            group_sql += " ORDER BY scheduled_at, id LIMIT ?"
            group_params.append(max(1, int(limit)))
            rows = conn.execute(group_sql, group_params).fetchall()
    else:
        rows = conn.execute(
            sql + " LIMIT ?", [*params, max(1, int(limit))]
        ).fetchall()
    claimed = []
    for row in rows:
        conn.execute(
            """UPDATE case_followups
               SET status='running', attempt_count=attempt_count+1,
                   last_checked_at=?, updated_at=?, error=NULL, claimed_pid=?
               WHERE id=? AND status IN ('pending', 'retry')""",
            (due_at, due_at, claimed_pid, row["id"]),
        )
        claimed_row = dict(row)
        claimed_row["status"] = "running"
        claimed_row["attempt_count"] = int(row["attempt_count"]) + 1
        claimed_row["last_checked_at"] = due_at
        claimed_row["claimed_pid"] = claimed_pid
        claimed.append(claimed_row)
    conn.commit()
    conn.close()
    return claimed


def list_due_case_followup_groups(
    db_path: str,
    due_at: Optional[str] = None,
    followup_ids: Optional[Iterable[int]] = None,
    reapplication_only: bool = False,
) -> list[Dict[str, Any]]:
    """Read due account/site groups without claiming or changing task state."""
    due_at = due_at or now_str()
    selected_ids = [int(value) for value in (followup_ids or [])]
    sql = """SELECT account_id, marketplace, MIN(scheduled_at) AS oldest_due,
                    COUNT(*) AS task_count
             FROM case_followups
             WHERE status IN ('pending', 'retry') AND scheduled_at <= ?"""
    params: list[Any] = [due_at]
    if reapplication_only:
        sql += " AND reapplication_campaign_id IS NOT NULL"
    if selected_ids:
        sql += " AND id IN (" + ",".join("?" for _ in selected_ids) + ")"
        params.extend(selected_ids)
    sql += " GROUP BY account_id, marketplace ORDER BY oldest_due, account_id, marketplace"
    conn = get_conn(db_path)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def finish_case_followup(
    db_path: str,
    followup_id: int,
    status: str,
    final_result: Optional[str],
    case_status: Optional[str],
    decision_reason: Optional[str],
    evidence_path: Optional[str],
    error: Optional[str] = None,
) -> None:
    now = now_str()
    conn = get_conn(db_path)
    conn.execute(
        """UPDATE case_followups
           SET status=?, final_result=?, case_status=?, decision_reason=?,
               evidence_path=?, error=?, completed_at=?, updated_at=?
           WHERE id=?""",
        (
            status,
            final_result,
            case_status,
            decision_reason,
            evidence_path,
            error,
            now,
            now,
            followup_id,
        ),
    )
    conn.commit()
    conn.close()


def increment_case_followup_ai_attempt(db_path: str, followup_id: int) -> int:
    """Atomically record one Codex reply-classification attempt."""
    conn = get_conn(db_path)
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        """UPDATE case_followups
           SET ai_attempt_count=COALESCE(ai_attempt_count, 0)+1, updated_at=?
           WHERE id=?""",
        (now_str(), int(followup_id)),
    )
    row = conn.execute(
        "SELECT ai_attempt_count FROM case_followups WHERE id=?",
        (int(followup_id),),
    ).fetchone()
    conn.commit()
    conn.close()
    return int(row["ai_attempt_count"] or 0) if row else 0


def reopen_case_followup_for_retry(
    db_path: str,
    followup_id: int,
    *,
    scheduled_at: Optional[str] = None,
    reason: str = "operator_requested_retry",
) -> bool:
    """Reopen a recoverable Case task without creating a duplicate.

    This is a local recovery operation only. The subsequent worker reads the
    existing Case; it does not submit or resubmit an application. Completed
    tasks are only recoverable here when their recorded result is false approval.
    """
    scheduled_at = scheduled_at or now_str()
    now = now_str()
    conn = get_conn(db_path)
    cur = conn.execute(
        """UPDATE case_followups
           SET status='retry', scheduled_at=?, completed_at=NULL,
               error=?, claimed_pid=NULL, updated_at=?
           WHERE id=? AND (
               status IN ('manual_review', 'failed')
               OR (status='completed' AND final_result='false_approved')
           )""",
        (scheduled_at, str(reason or "operator_requested_retry")[:500], now, int(followup_id)),
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def reschedule_case_followup(
    db_path: str,
    followup_id: int,
    scheduled_at: str,
    final_result: Optional[str],
    case_status: Optional[str],
    decision_reason: Optional[str],
    evidence_path: Optional[str],
    error: Optional[str] = None,
) -> None:
    conn = get_conn(db_path)
    conn.execute(
        """UPDATE case_followups
           SET status='retry', scheduled_at=?, final_result=?, case_status=?,
               decision_reason=?, evidence_path=?, error=?, updated_at=?
           WHERE id=?""",
        (
            scheduled_at,
            final_result,
            case_status,
            decision_reason,
            evidence_path,
            error,
            now_str(),
            followup_id,
        ),
    )
    conn.commit()
    conn.close()


def defer_due_case_followups_for_account(
    db_path: str,
    account_id: str,
    scheduled_at: str,
    due_at: str,
    reason: str,
    *,
    followup_ids: Optional[Iterable[int]] = None,
    exclude_ids: Optional[Iterable[int]] = None,
    reapplication_only: bool = False,
) -> int:
    """Defer unclaimed due tasks after a profile-level connection failure."""
    selected_ids = [int(value) for value in (followup_ids or [])]
    excluded_ids = [int(value) for value in (exclude_ids or [])]
    sql = """UPDATE case_followups
             SET status='retry', scheduled_at=?, final_result='error',
                 decision_reason=?, error=?, updated_at=?
             WHERE account_id=? AND status IN ('pending', 'retry')
               AND scheduled_at <= ?"""
    params: list[Any] = [scheduled_at, reason, reason, now_str(), account_id, due_at]
    if reapplication_only:
        sql += " AND reapplication_campaign_id IS NOT NULL"
    if selected_ids:
        sql += " AND id IN (" + ",".join("?" for _ in selected_ids) + ")"
        params.extend(selected_ids)
    if excluded_ids:
        sql += " AND id NOT IN (" + ",".join("?" for _ in excluded_ids) + ")"
        params.extend(excluded_ids)
    conn = get_conn(db_path)
    cur = conn.execute(sql, params)
    count = cur.rowcount
    conn.commit()
    conn.close()
    return count


def get_next_case_followup_due(
    db_path: str,
    followup_ids: Optional[Iterable[int]] = None,
    reapplication_only: bool = False,
) -> Optional[str]:
    selected_ids = [int(value) for value in (followup_ids or [])]
    conn = get_conn(db_path)
    sql = """SELECT MIN(scheduled_at) AS scheduled_at FROM case_followups
             WHERE status IN ('pending', 'retry')"""
    params: list[Any] = []
    if reapplication_only:
        sql += " AND reapplication_campaign_id IS NOT NULL"
    if selected_ids:
        sql += " AND id IN (" + ",".join("?" for _ in selected_ids) + ")"
        params.extend(selected_ids)
    row = conn.execute(sql, params).fetchone()
    conn.close()
    return row["scheduled_at"] if row and row["scheduled_at"] else None


def list_case_followups(db_path: str, statuses: Optional[Iterable[str]] = None) -> list[Dict[str, Any]]:
    conn = get_conn(db_path)
    sql = "SELECT * FROM case_followups"
    params: list[Any] = []
    if statuses:
        statuses = list(statuses)
        sql += " WHERE status IN (" + ",".join("?" for _ in statuses) + ")"
        params.extend(statuses)
    sql += " ORDER BY scheduled_at, id"
    rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


def insert_verification(db_path: str, account_id: str, marketplace: str, brand_name: str,
                        method: str, result: str, summary_text: Optional[str] = None,
                        confidence_points: int = 0, verified_at: Optional[str] = None) -> int:
    verified_at = verified_at or now_str()
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        '''INSERT INTO verifications(account_id, marketplace, brand_name, method, verified_at, result, summary_text, confidence_points)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
        (account_id, marketplace, brand_name, method, verified_at, result, summary_text, confidence_points),
    )
    vid = cur.lastrowid
    conn.commit()
    conn.close()
    return vid


def insert_evidence_items(db_path: str, related_type: str, related_id: int,
                          file_paths: Iterable[str], evidence_type: str = "screenshot",
                          created_at: Optional[str] = None):
    created_at = created_at or now_str()
    rows = [(related_type, related_id, evidence_type, str(p), created_at) for p in file_paths]
    if not rows:
        return
    conn = get_conn(db_path)
    conn.executemany(
        '''INSERT INTO evidence_items(related_type, related_id, evidence_type, file_path, created_at)
           VALUES (?, ?, ?, ?, ?)''',
        rows,
    )
    conn.commit()
    conn.close()


def _map_verification_to_status(result: str, confidence_points: int, approved_threshold: int = 70) -> str:
    if result == "pass" and confidence_points >= approved_threshold:
        return "approved"
    if result == "fail":
        return "pending"
    if result == "unknown":
        return "unknown"
    return "pending"


def upsert_brand_status_from_verification(db_path: str, account_id: str, marketplace: str, brand_name: str,
                                          method: str, result: str, confidence_points: int,
                                          verified_at: Optional[str] = None, approved_threshold: int = 70):
    verified_at = verified_at or now_str()
    current_status = _map_verification_to_status(result, confidence_points, approved_threshold)
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        '''INSERT INTO brand_status_snapshot(account_id, marketplace, brand_name, current_status,
                                             confidence_score, last_verified_at, last_method, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(account_id, marketplace, brand_name)
           DO UPDATE SET
             current_status=CASE
                WHEN brand_status_snapshot.current_status='approved' THEN 'approved'
                ELSE excluded.current_status
             END,
             confidence_score=CASE
                WHEN excluded.confidence_score > brand_status_snapshot.confidence_score
                THEN excluded.confidence_score
                ELSE brand_status_snapshot.confidence_score
             END,
             last_verified_at=excluded.last_verified_at,
             last_method=excluded.last_method,
             updated_at=excluded.updated_at
        ''',
        (account_id, marketplace, brand_name, current_status, confidence_points, verified_at, method, now_str()),
    )
    conn.commit()
    conn.close()


def mark_brand_status_pending_after_submission(db_path: str, account_id: str, marketplace: str, brand_name: str):
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        '''INSERT INTO brand_status_snapshot(account_id, marketplace, brand_name, current_status,
                                             confidence_score, last_verified_at, last_method, updated_at)
           VALUES (?, ?, ?, 'pending', 0, NULL, 'submission', ?)
           ON CONFLICT(account_id, marketplace, brand_name)
           DO UPDATE SET
             current_status=CASE
               WHEN brand_status_snapshot.current_status='approved' THEN 'approved'
               ELSE 'pending'
             END,
             updated_at=excluded.updated_at
        ''',
        (account_id, marketplace, brand_name, now_str()),
    )
    conn.commit()
    conn.close()


def list_approved_brands(db_path: str, account_id: Optional[str] = None, marketplace: Optional[str] = None):
    conn = get_conn(db_path)
    cur = conn.cursor()
    sql = '''SELECT account_id, marketplace, brand_name, current_status, confidence_score,
                    last_verified_at, last_method
             FROM brand_status_snapshot WHERE current_status='approved' '''
    params: List[Any] = []
    if account_id:
        sql += " AND account_id=?"
        params.append(account_id)
    if marketplace:
        sql += " AND marketplace=?"
        params.append(marketplace)
    sql += " ORDER BY account_id, marketplace, brand_name"
    cur.execute(sql, params)
    rows = cur.fetchall()
    conn.close()
    return rows


def get_approved_brand_status(
    db_path: str,
    account_id: str,
    marketplace: str,
    brand_name: str,
) -> Optional[Dict[str, Any]]:
    """Return the canonical effective approval for one exact account/site/brand."""
    conn = get_conn(db_path)
    row = conn.execute(
        """SELECT account_id, marketplace, brand_name, current_status,
                  confidence_score, last_verified_at, last_method, updated_at
           FROM brand_status_snapshot
           WHERE account_id=?
             AND UPPER(marketplace)=UPPER(?)
             AND brand_name=? COLLATE NOCASE
             AND current_status='approved'
           LIMIT 1""",
        (account_id, marketplace, brand_name),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def list_brand_status(db_path: str, account_id: str, marketplace: Optional[str] = None):
    conn = get_conn(db_path)
    cur = conn.cursor()
    sql = '''SELECT account_id, marketplace, brand_name, current_status, confidence_score,
                    last_verified_at, last_method, updated_at
             FROM brand_status_snapshot WHERE account_id=?'''
    params: List[Any] = [account_id]
    if marketplace:
        sql += " AND marketplace=?"
        params.append(marketplace)
    sql += " ORDER BY marketplace, brand_name"
    cur.execute(sql, params)
    rows = cur.fetchall()
    conn.close()
    return rows


def get_latest_verification_for_brand(db_path: str, account_id: str, marketplace: str, brand_name: str):
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        '''SELECT * FROM verifications
           WHERE account_id=? AND marketplace=? AND brand_name=?
           ORDER BY datetime(verified_at) DESC, id DESC LIMIT 1''',
        (account_id, marketplace, brand_name),
    )
    row = cur.fetchone()
    conn.close()
    return row


def get_evidence_for_related(db_path: str, related_type: str, related_id: int):
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        '''SELECT evidence_type, file_path, created_at FROM evidence_items
           WHERE related_type=? AND related_id=? ORDER BY id ASC''',
        (related_type, related_id),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def insert_decision_rule(db_path: str, name: str, flow_type: str, scope_type: str, scope_value: Optional[str],
                         match_conditions: Dict[str, Any], action_plan: List[Dict[str, Any]],
                         risk_level: str = "medium", requires_confirm: bool = True,
                         created_by: str = "user") -> int:
    ts = now_str()
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        '''INSERT INTO decision_rules(name, flow_type, scope_type, scope_value, enabled,
                                      match_conditions_json, action_plan_json, risk_level, requires_confirm,
                                      created_by, created_at, updated_at)
           VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)''',
        (name, flow_type, scope_type, scope_value,
         json.dumps(match_conditions, ensure_ascii=False),
         json.dumps(action_plan, ensure_ascii=False),
         risk_level, 1 if requires_confirm else 0, created_by, ts, ts),
    )
    rid = cur.lastrowid
    conn.commit()
    conn.close()
    return rid


def list_candidate_rules(db_path: str, flow_type: str, marketplace: str, account_id: str, brand_name: str):
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        '''SELECT * FROM decision_rules
           WHERE enabled=1 AND flow_type=?
             AND (
               scope_type='global'
               OR (scope_type='marketplace' AND scope_value=?)
               OR (scope_type='account' AND scope_value=?)
               OR (scope_type='brand' AND scope_value=?)
             )
           ORDER BY CASE scope_type
                    WHEN 'brand' THEN 1
                    WHEN 'account' THEN 2
                    WHEN 'marketplace' THEN 3
                    WHEN 'global' THEN 4
                    ELSE 9 END, id DESC''',
        (flow_type, marketplace, account_id, brand_name),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def mark_rule_hit(db_path: str, rule_id: int):
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        "UPDATE decision_rules SET hit_count=hit_count+1, last_hit_at=?, updated_at=? WHERE id=?",
        (now_str(), now_str(), rule_id),
    )
    conn.commit()
    conn.close()


def insert_unknown_case(db_path: str, flow_type: str, account_id: str, marketplace: str, brand_name: str,
                        page_url: Optional[str], page_title: Optional[str], screenshot_path: Optional[str],
                        page_text_summary: Optional[str], dom_summary: Optional[str],
                        ai_page_state: Optional[str], ai_confidence: Optional[float], ai_reason: Optional[str]) -> int:
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        '''INSERT INTO unknown_cases(flow_type, account_id, marketplace, brand_name,
                                     page_url, page_title, screenshot_path, page_text_summary, dom_summary,
                                     ai_page_state, ai_confidence, ai_reason, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_decision', ?)''',
        (flow_type, account_id, marketplace, brand_name, page_url, page_title, screenshot_path,
         page_text_summary, dom_summary, ai_page_state, ai_confidence, ai_reason, now_str()),
    )
    uid = cur.lastrowid
    conn.commit()
    conn.close()
    return uid


def resolve_unknown_case(db_path: str, unknown_case_id: int, resolved_rule_id: Optional[int], resolution_note: Optional[str]):
    conn = get_conn(db_path)
    conn.execute(
        '''UPDATE unknown_cases
           SET status='resolved', resolved_rule_id=?, resolution_note=?, resolved_at=?
           WHERE id=?''',
        (resolved_rule_id, resolution_note, now_str(), unknown_case_id),
    )
    conn.commit()
    conn.close()


def list_pending_unknown_cases(db_path: str):
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute("SELECT * FROM unknown_cases WHERE status='pending_decision' ORDER BY id DESC")
    rows = cur.fetchall()
    conn.close()
    return rows


def upsert_procedure_flow(db_path: str, flow_code: str, flow_type: str, name: str,
                          marketplace: Optional[str] = None, brand_name: Optional[str] = None) -> int:
    ts = now_str()
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute("SELECT id FROM procedure_flows WHERE flow_code=?", (flow_code,))
    row = cur.fetchone()
    if row:
        flow_id = row["id"]
        cur.execute(
            '''UPDATE procedure_flows
               SET flow_type=?, name=?, marketplace=?, brand_name=?, enabled=1,
                   lifecycle_status=CASE WHEN lifecycle_status='disabled' THEN 'disabled' ELSE lifecycle_status END,
                   updated_at=?
               WHERE id=?''',
            (flow_type, name, marketplace, brand_name, ts, flow_id),
        )
    else:
        cur.execute(
            '''INSERT INTO procedure_flows(flow_code, flow_type, name, marketplace, brand_name, enabled,
                                             lifecycle_status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 1, 'draft', ?, ?)''',
            (flow_code, flow_type, name, marketplace, brand_name, ts, ts),
        )
        flow_id = cur.lastrowid
    conn.commit()
    conn.close()
    return flow_id


def get_procedure_flow_by_code(db_path: str, flow_code: str):
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute("SELECT * FROM procedure_flows WHERE flow_code=?", (flow_code,))
    row = cur.fetchone()
    conn.close()
    return row


def list_procedure_flows(db_path: str, flow_type: Optional[str] = None):
    conn = get_conn(db_path)
    cur = conn.cursor()
    if flow_type:
        cur.execute("SELECT * FROM procedure_flows WHERE flow_type=? ORDER BY updated_at DESC, id DESC", (flow_type,))
    else:
        cur.execute("SELECT * FROM procedure_flows ORDER BY updated_at DESC, id DESC")
    rows = cur.fetchall()
    conn.close()
    return rows


def set_procedure_flow_lifecycle(db_path: str, flow_code: str, lifecycle_status: str,
                                 approved_by: Optional[str] = None, note: Optional[str] = None):
    allowed = {"draft", "uat_pending", "approved", "disabled"}
    if lifecycle_status not in allowed:
        raise ValueError(f"非法流程状态: {lifecycle_status}")
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute("SELECT id FROM procedure_flows WHERE flow_code=?", (flow_code,))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise ValueError(f"流程不存在: {flow_code}")
    if lifecycle_status == 'approved':
        cur.execute(
            "UPDATE procedure_flows SET lifecycle_status=?, approved_by=?, approved_at=?, updated_at=? WHERE flow_code=?",
            (lifecycle_status, approved_by or 'user', now_str(), now_str(), flow_code),
        )
    else:
        cur.execute(
            "UPDATE procedure_flows SET lifecycle_status=?, updated_at=? WHERE flow_code=?",
            (lifecycle_status, now_str(), flow_code),
        )
    conn.commit()
    conn.close()


def mark_procedure_flow_uat_result(db_path: str, flow_code: str, run_id: int, passed: bool):
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        "UPDATE procedure_flows SET last_uat_run_id=?, lifecycle_status=?, updated_at=? WHERE flow_code=?",
        (run_id, 'uat_pending' if passed else 'draft', now_str(), flow_code),
    )
    conn.commit()
    conn.close()


def replace_procedure_step(db_path: str, flow_id: int, step_no: int, step_name: str,
                           match_conditions: Dict[str, Any], action_plan: List[Dict[str, Any]],
                           success_check: Optional[Dict[str, Any]] = None,
                           failure_check: Optional[Dict[str, Any]] = None,
                           risk_level: str = "medium", requires_confirm: bool = True):
    ts = now_str()
    conn = get_conn(db_path)
    conn.execute(
        '''INSERT INTO procedure_steps(flow_id, step_no, step_name, match_conditions_json, action_plan_json,
                                       success_check_json, failure_check_json, risk_level, requires_confirm, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(flow_id, step_no)
           DO UPDATE SET step_name=excluded.step_name,
                         match_conditions_json=excluded.match_conditions_json,
                         action_plan_json=excluded.action_plan_json,
                         success_check_json=excluded.success_check_json,
                         failure_check_json=excluded.failure_check_json,
                         risk_level=excluded.risk_level,
                         requires_confirm=excluded.requires_confirm,
                         updated_at=excluded.updated_at''',
        (flow_id, step_no, step_name,
         json.dumps(match_conditions, ensure_ascii=False),
         json.dumps(action_plan, ensure_ascii=False),
         json.dumps(success_check, ensure_ascii=False) if success_check else None,
         json.dumps(failure_check, ensure_ascii=False) if failure_check else None,
         risk_level, 1 if requires_confirm else 0, ts, ts),
    )
    conn.commit()
    conn.close()


def list_procedure_steps(db_path: str, flow_id: int):
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute("SELECT * FROM procedure_steps WHERE flow_id=? ORDER BY step_no", (flow_id,))
    rows = cur.fetchall()
    conn.close()
    return rows


def insert_procedure_run(db_path: str, flow_id: int, account_id: str, marketplace: str, brand_name: str,
                         run_mode: str, status: str = "running", note: Optional[str] = None) -> int:
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        '''INSERT INTO procedure_runs(flow_id, account_id, marketplace, brand_name, run_mode, status, started_at, note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
        (flow_id, account_id, marketplace, brand_name, run_mode, status, now_str(), note),
    )
    rid = cur.lastrowid
    conn.commit()
    conn.close()
    return rid


def update_procedure_run_status(db_path: str, run_id: int, status: str, note: Optional[str] = None):
    conn = get_conn(db_path)
    conn.execute(
        "UPDATE procedure_runs SET status=?, finished_at=?, note=? WHERE id=?",
        (status, now_str(), note, run_id),
    )
    conn.commit()
    conn.close()


def insert_procedure_run_step(db_path: str, run_id: int, step_no: int, step_name: Optional[str],
                              page_url: Optional[str], screenshot_path: Optional[str],
                              action_plan: Optional[List[Dict[str, Any]]], result: str,
                              detail: Optional[str] = None):
    conn = get_conn(db_path)
    conn.execute(
        '''INSERT INTO procedure_run_steps(run_id, step_no, step_name, page_url, screenshot_path, action_plan_json,
                                           result, detail, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (run_id, step_no, step_name, page_url, screenshot_path,
         json.dumps(action_plan, ensure_ascii=False) if action_plan is not None else None,
         result, detail, now_str()),
    )
    conn.commit()
    conn.close()


def get_decision_rules_for_flow(db_path: str, flow_type: str) -> List[Dict[str, Any]]:
    """获取指定流程类型的所有决策规则"""
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM decision_rules WHERE flow_type=? AND enabled=1 ORDER BY hit_count DESC, id DESC",
        (flow_type,),
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def save_decision_rule(db_path: str, flow_type: str, condition: str, action: str,
                       alternative_selector: Optional[str] = None,
                       risk_level: str = "medium") -> int:
    """保存新的决策规则"""
    ts = now_str()
    conn = get_conn(db_path)
    cur = conn.cursor()

    # 构建 action_plan
    action_plan = [{"type": action}]
    if alternative_selector:
        action_plan[0]["alternative_selector"] = alternative_selector

    cur.execute(
        '''INSERT INTO decision_rules(name, flow_type, scope_type, scope_value, enabled,
                                      match_conditions_json, action_plan_json, risk_level, requires_confirm,
                                      created_by, created_at, updated_at)
           VALUES (?, ?, 'global', NULL, 1, ?, ?, ?, 1, 'system', ?, ?)''',
        (f"Auto-rule: {condition[:50]}", flow_type,
         json.dumps({"error_contains": condition}, ensure_ascii=False),
         json.dumps(action_plan, ensure_ascii=False),
         risk_level, ts, ts),
    )
    rule_id = cur.lastrowid
    conn.commit()
    conn.close()
    return rule_id


def add_web_user(db_path: str, username: str, password_hash: str, role: str = "viewer",
                 display_name: Optional[str] = None) -> int:
    now = now_str()
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        '''INSERT INTO web_users(username, password_hash, role, display_name, disabled, created_at, updated_at)
           VALUES (?, ?, ?, ?, 0, ?, ?)''',
        (username, password_hash, role, display_name, now, now),
    )
    uid = cur.lastrowid
    conn.commit()
    conn.close()
    return uid


def get_web_user_by_username(db_path: str, username: str) -> Optional[Dict[str, Any]]:
    conn = get_conn(db_path)
    row = conn.execute("SELECT * FROM web_users WHERE username=?", (username,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_web_users(db_path: str) -> List[Dict[str, Any]]:
    conn = get_conn(db_path)
    rows = conn.execute(
        '''SELECT id, username, role, display_name, disabled, created_at, updated_at, last_login_at
           FROM web_users ORDER BY id'''
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def reset_web_user_password(db_path: str, username: str, password_hash: str) -> bool:
    """Set a new password hash for an existing user and re-enable the account."""
    conn = get_conn(db_path)
    cur = conn.execute(
        "UPDATE web_users SET password_hash=?, disabled=0, updated_at=? WHERE username=?",
        (password_hash, now_str(), username),
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def set_web_user_disabled(db_path: str, username: str, disabled: bool) -> bool:
    conn = get_conn(db_path)
    cur = conn.execute(
        "UPDATE web_users SET disabled=?, updated_at=? WHERE username=?",
        (1 if disabled else 0, now_str(), username),
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def update_web_user_last_login(db_path: str, user_id: int) -> None:
    conn = get_conn(db_path)
    conn.execute(
        "UPDATE web_users SET last_login_at=?, updated_at=? WHERE id=?",
        (now_str(), now_str(), int(user_id)),
    )
    conn.commit()
    conn.close()


def record_web_audit(db_path: str, action: str, actor_id: Optional[int] = None,
                     target_type: Optional[str] = None, target_id: Optional[str] = None,
                     result: Optional[str] = None, ip_address: Optional[str] = None,
                     detail: Optional[str] = None) -> int:
    """Record one non-secret web console audit event (login/logout/user mgmt/evidence download)."""
    conn = get_conn(db_path)
    cur = conn.execute(
        '''INSERT INTO web_audit_events(actor_id, action, target_type, target_id, result, ip_address, detail, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
        (actor_id, action, target_type, target_id, result, ip_address, detail, now_str()),
    )
    event_id = cur.lastrowid
    conn.commit()
    conn.close()
    return event_id


# ---------------------------------------------------------------------------
# Stage-3 automation job queue (diagnose / dry_run / submit)
# ---------------------------------------------------------------------------

AUTOMATION_JOB_TYPES = {"diagnose", "dry_run", "submit"}
AUTOMATION_JOB_TERMINAL_STATUSES = {
    "completed",
    "failed",
    "cancelled_before_start",
    "terminated_unknown_state",
}
AUTOMATION_JOB_DISPATCHABLE_STATUS = "queued"

# Columns update_automation_job is allowed to touch (defense in depth: the
# manager passes field names programmatically, never from request bodies).
_AUTOMATION_JOB_UPDATABLE = {
    "run_status", "pid", "exit_code", "error_class",
    "state_file", "stdout_log", "stderr_log",
    "stop_requested_at", "started_at", "finished_at",
}


def _automation_job_row_to_dict(row) -> Dict[str, Any]:
    data = dict(row)
    try:
        data["brands"] = json.loads(data.pop("brands_json") or "[]")
    except json.JSONDecodeError:
        data["brands"] = []
    return data


def create_automation_job(
    db_path: str,
    job_id: str,
    job_type: str,
    created_by: str,
    account_id: str,
    marketplace: Optional[str],
    brands: List[str],
    run_status: str = "queued",
) -> Dict[str, Any]:
    if job_type not in AUTOMATION_JOB_TYPES:
        raise ValueError(f"非法任务类型: {job_type}")
    # New jobs are born queued; waiting_human remains available for flows
    # that genuinely pause for human action (CAPTCHA / 2FA / login loss).
    if run_status not in {"queued", "waiting_human"}:
        raise ValueError(f"非法初始状态: {run_status}")
    now = now_str()
    conn = get_conn(db_path)
    conn.execute(
        """INSERT INTO automation_jobs(
               id, job_type, run_status, created_by, account_id, marketplace,
               brands_json, created_at, updated_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            job_id,
            job_type,
            run_status,
            created_by,
            account_id,
            marketplace,
            json.dumps(list(brands), ensure_ascii=False),
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()
    job = get_automation_job(db_path, job_id)
    assert job is not None
    return job


def get_automation_job(db_path: str, job_id: str) -> Optional[Dict[str, Any]]:
    conn = get_conn(db_path)
    row = conn.execute("SELECT * FROM automation_jobs WHERE id=?", (job_id,)).fetchone()
    conn.close()
    return _automation_job_row_to_dict(row) if row else None


def list_automation_jobs(
    db_path: str,
    run_status: Optional[str] = None,
    job_type: Optional[str] = None,
    account_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[List[Dict[str, Any]], int]:
    sql = "FROM automation_jobs WHERE 1=1"
    params: list[Any] = []
    if run_status:
        sql += " AND run_status=?"
        params.append(run_status)
    if job_type:
        sql += " AND job_type=?"
        params.append(job_type)
    if account_id:
        sql += " AND account_id=?"
        params.append(account_id)
    conn = get_conn(db_path)
    total = int(conn.execute(f"SELECT COUNT(*) {sql}", params).fetchone()[0])
    rows = conn.execute(
        f"SELECT * {sql} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
        (*params, max(1, int(limit)), max(0, int(offset))),
    ).fetchall()
    conn.close()
    return [_automation_job_row_to_dict(r) for r in rows], total


def claim_next_queued_automation_job(db_path: str) -> Optional[Dict[str, Any]]:
    """Atomically move the oldest queued job to ``starting`` and return it."""
    conn = get_conn(db_path)
    conn.execute("BEGIN IMMEDIATE")
    row = conn.execute(
        """SELECT id FROM automation_jobs
           WHERE run_status='queued' ORDER BY created_at, id LIMIT 1"""
    ).fetchone()
    claimed = None
    if row:
        now = now_str()
        cur = conn.execute(
            """UPDATE automation_jobs SET run_status='starting', updated_at=?
               WHERE id=? AND run_status='queued'""",
            (now, row["id"]),
        )
        if cur.rowcount > 0:
            claimed = conn.execute(
                "SELECT * FROM automation_jobs WHERE id=?", (row["id"],)
            ).fetchone()
    conn.commit()
    conn.close()
    return _automation_job_row_to_dict(claimed) if claimed else None


def release_automation_job_claim(db_path: str, job_id: str) -> None:
    """Return a ``starting`` job to ``queued`` (dispatch pre-check failed)."""
    conn = get_conn(db_path)
    conn.execute(
        """UPDATE automation_jobs SET run_status='queued', updated_at=?
           WHERE id=? AND run_status='starting'""",
        (now_str(), job_id),
    )
    conn.commit()
    conn.close()


def update_automation_job(db_path: str, job_id: str, **fields) -> Optional[Dict[str, Any]]:
    """Update whitelisted columns; always refreshes ``updated_at``."""
    assignments = []
    params: list[Any] = []
    for key, value in fields.items():
        if key not in _AUTOMATION_JOB_UPDATABLE:
            raise ValueError(f"不允许更新的字段: {key}")
        assignments.append(f"{key}=?")
        params.append(value)
    if not assignments:
        return get_automation_job(db_path, job_id)
    assignments.append("updated_at=?")
    params.append(now_str())
    params.append(job_id)
    conn = get_conn(db_path)
    conn.execute(
        f"UPDATE automation_jobs SET {', '.join(assignments)} WHERE id=?",
        params,
    )
    conn.commit()
    conn.close()
    return get_automation_job(db_path, job_id)


def request_automation_job_stop(db_path: str, job_id: str) -> Optional[Dict[str, Any]]:
    """Record a safe-stop request. Never signals or kills the process.

    queued  -> cancelled_before_start (dispatcher never picks it up)
    starting/running -> stop_requested (worker exits at the next brand boundary)
    terminal / unknown -> None
    """
    conn = get_conn(db_path)
    conn.execute("BEGIN IMMEDIATE")
    row = conn.execute(
        "SELECT run_status FROM automation_jobs WHERE id=?", (job_id,)
    ).fetchone()
    updated = None
    if row:
        now = now_str()
        status = row["run_status"]
        if status == "queued":
            conn.execute(
                """UPDATE automation_jobs
                   SET run_status='cancelled_before_start', stop_requested_at=?,
                       finished_at=?, updated_at=?
                   WHERE id=?""",
                (now, now, now, job_id),
            )
        elif status in ("starting", "running"):
            conn.execute(
                """UPDATE automation_jobs
                   SET run_status='stop_requested', stop_requested_at=?, updated_at=?
                   WHERE id=?""",
                (now, now, job_id),
            )
        updated = conn.execute(
            "SELECT * FROM automation_jobs WHERE id=?", (job_id,)
        ).fetchone()
    conn.commit()
    conn.close()
    if not updated:
        return None
    job = _automation_job_row_to_dict(updated)
    if job["run_status"] in AUTOMATION_JOB_TERMINAL_STATUSES and job["run_status"] != "cancelled_before_start":
        return None
    return job


def list_active_automation_jobs(db_path: str) -> List[Dict[str, Any]]:
    """Jobs in non-terminal states — used by startup recovery."""
    conn = get_conn(db_path)
    rows = conn.execute(
        """SELECT * FROM automation_jobs
           WHERE run_status IN ('starting', 'running', 'stop_requested', 'waiting_human')
           ORDER BY created_at, id"""
    ).fetchall()
    conn.close()
    return [_automation_job_row_to_dict(r) for r in rows]


def count_running_automation_jobs(db_path: str) -> int:
    conn = get_conn(db_path)
    count = int(conn.execute(
        """SELECT COUNT(*) FROM automation_jobs
           WHERE run_status IN ('queued', 'starting', 'running', 'stop_requested', 'waiting_human')"""
    ).fetchone()[0])
    conn.close()
    return count


def replace_automation_job_items(
    db_path: str, job_id: str, items: List[Dict[str, Any]]
) -> None:
    """Replace the parsed per-brand items of one job (idempotent re-parse)."""
    conn = get_conn(db_path)
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("DELETE FROM automation_job_items WHERE job_id=?", (job_id,))
    for item in items:
        conn.execute(
            """INSERT INTO automation_job_items(
                   job_id, account_id, marketplace, brand_name, run_status,
                   business_status, case_id, dashboard_status, evidence_root,
                   started_at, finished_at, note
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                job_id,
                item.get("account_id"),
                item.get("marketplace"),
                item.get("brand_name"),
                item.get("run_status"),
                item.get("business_status"),
                item.get("case_id"),
                item.get("dashboard_status"),
                item.get("evidence_root"),
                item.get("started_at"),
                item.get("finished_at"),
                item.get("note"),
            ),
        )
    conn.commit()
    conn.close()


def list_automation_job_items(db_path: str, job_id: str) -> List[Dict[str, Any]]:
    conn = get_conn(db_path)
    rows = conn.execute(
        "SELECT * FROM automation_job_items WHERE job_id=? ORDER BY id", (job_id,)
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def account_has_active_case_followup(db_path: str, account_id: str) -> bool:
    """Light mutual exclusion: an in-flight follow-up worker holds the profile."""
    conn = get_conn(db_path)
    count = int(conn.execute(
        "SELECT COUNT(*) FROM case_followups WHERE account_id=? AND status='running'",
        (account_id,),
    ).fetchone()[0])
    conn.close()
    return count > 0


# ---------------------------------------------------------------------------
# Profile locks (keyed by irreversible profile id hash)
# ---------------------------------------------------------------------------


def _lock_time_str(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def acquire_profile_lock(
    db_path: str,
    profile_key: str,
    owner_type: str,
    owner_id: str,
    ttl_seconds: float = 120.0,
    pid_alive=None,
    now: Optional[datetime] = None,
) -> bool:
    """Take the profile lock in one transaction.

    An expired lock is reclaimed only after verifying its owner PID is gone
    (``pid_alive`` callable; defaults to "no PID known -> reclaimable").
    """
    now = now or datetime.now()
    now_text = _lock_time_str(now)
    expires_text = _lock_time_str(now + timedelta(seconds=float(ttl_seconds)))
    conn = get_conn(db_path)
    conn.execute("BEGIN IMMEDIATE")
    existing = conn.execute(
        "SELECT * FROM profile_locks WHERE profile_key=?", (profile_key,)
    ).fetchone()
    acquired = False
    if existing is None:
        conn.execute(
            """INSERT INTO profile_locks(profile_key, owner_type, owner_id,
                                         acquired_at, heartbeat_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (profile_key, owner_type, owner_id, now_text, now_text, expires_text),
        )
        acquired = True
    elif str(existing["expires_at"] or "") <= now_text:
        owner_alive = False
        if existing["owner_type"] == "automation_job":
            job = conn.execute(
                "SELECT pid FROM automation_jobs WHERE id=?", (existing["owner_id"],)
            ).fetchone()
            pid = job["pid"] if job else None
            owner_alive = bool(pid_alive(pid)) if pid_alive else False
        elif existing["owner_type"] == "case_followup_worker":
            parts = str(existing["owner_id"] or "").split(":")
            pid = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
            owner_alive = bool(pid_alive(pid)) if pid_alive else False
        if not owner_alive:
            conn.execute(
                """UPDATE profile_locks
                   SET owner_type=?, owner_id=?, acquired_at=?, heartbeat_at=?, expires_at=?
                   WHERE profile_key=?""",
                (owner_type, owner_id, now_text, now_text, expires_text, profile_key),
            )
            acquired = True
    conn.commit()
    conn.close()
    return acquired


def heartbeat_profile_lock(
    db_path: str,
    profile_key: str,
    owner_id: str,
    ttl_seconds: float = 120.0,
    now: Optional[datetime] = None,
) -> bool:
    now = now or datetime.now()
    conn = get_conn(db_path)
    cur = conn.execute(
        """UPDATE profile_locks SET heartbeat_at=?, expires_at=?
           WHERE profile_key=? AND owner_id=?""",
        (
            _lock_time_str(now),
            _lock_time_str(now + timedelta(seconds=float(ttl_seconds))),
            profile_key,
            owner_id,
        ),
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def release_profile_lock(
    db_path: str, profile_key: str, owner_id: Optional[str] = None
) -> bool:
    conn = get_conn(db_path)
    if owner_id is None:
        cur = conn.execute("DELETE FROM profile_locks WHERE profile_key=?", (profile_key,))
    else:
        cur = conn.execute(
            "DELETE FROM profile_locks WHERE profile_key=? AND owner_id=?",
            (profile_key, owner_id),
        )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def get_profile_lock(db_path: str, profile_key: str) -> Optional[Dict[str, Any]]:
    conn = get_conn(db_path)
    row = conn.execute(
        "SELECT * FROM profile_locks WHERE profile_key=?", (profile_key,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def reap_stale_profile_locks(db_path: str, pid_alive, now: Optional[datetime] = None) -> int:
    """Delete expired locks whose owner process is verifiably dead."""
    now = now or datetime.now()
    now_text = _lock_time_str(now)
    conn = get_conn(db_path)
    rows = conn.execute(
        "SELECT * FROM profile_locks WHERE expires_at <= ?", (now_text,)
    ).fetchall()
    reaped = 0
    for row in rows:
        owner_alive = False
        if row["owner_type"] == "automation_job":
            job = conn.execute(
                "SELECT pid FROM automation_jobs WHERE id=?", (row["owner_id"],)
            ).fetchone()
            pid = job["pid"] if job else None
            owner_alive = bool(pid_alive(pid))
        elif row["owner_type"] == "case_followup_worker":
            parts = str(row["owner_id"] or "").split(":")
            pid = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
            owner_alive = bool(pid_alive(pid))
        if not owner_alive:
            conn.execute(
                "DELETE FROM profile_locks WHERE profile_key=?", (row["profile_key"],)
            )
            reaped += 1
    conn.commit()
    conn.close()
    return reaped


# ---------------------------------------------------------------------------
# Stage-5 persisted anomaly detection (repair incident queue)
# ---------------------------------------------------------------------------

# Terminal statuses: an incident in one of these never aggregates further
# occurrences; a new occurrence opens a fresh incident.
INCIDENT_TERMINAL_STATUSES = {"closed_human", "closed_duplicate", "released", "rejected"}

# Fix-candidate classifications (code repair path).  Everything else is a
# human-handling classification (CAPTCHA/2FA/risk/config/platform/business).
INCIDENT_REPAIR_CLASSES = {
    "selector_missing",
    "state_unknown",
    "dom_contract_changed",
    "navigation_changed",
    "semantic_control_missing",
    "flow_loop_exhausted",
    "result_contract_changed",
}

_INCIDENT_CONFIDENCE_CAP = 0.85
_INCIDENT_RECURRENCE_BUMP = 0.10
_INCIDENT_CROSS_SCOPE_BUMP = 0.15


def _incident_row_to_dict(row) -> dict:
    return {
        "id": int(row["id"]),
        "signature": str(row["signature"] or ""),
        "scope_type": str(row["scope_type"] or ""),
        "flow_type": str(row["flow_type"] or ""),
        "account_id": str(row["account_id"] or ""),
        "marketplace": str(row["marketplace"] or ""),
        "brand_name": str(row["brand_name"] or ""),
        "detector_type": str(row["detector_type"] or ""),
        "classification": str(row["classification"] or ""),
        "confidence": float(row["confidence"] or 0.0),
        "status": str(row["status"] or ""),
        "occurrence_count": int(row["occurrence_count"] or 0),
        "first_seen_at": str(row["first_seen_at"] or ""),
        "last_seen_at": str(row["last_seen_at"] or ""),
        "evidence_bundle_path": str(row["evidence_bundle_path"] or ""),
        "resolution_note": row["resolution_note"],
        "codex_thread_id": row["codex_thread_id"],
    }


def record_incident(
    db_path: str,
    *,
    signature: str,
    scope_type: str,
    flow_type: str = "",
    account_id: str = "",
    marketplace: str = "",
    brand_name: str = "",
    detector_type: str = "",
    classification: str,
    confidence: float = 0.0,
    evidence_bundle_path: str = "",
) -> tuple[dict, bool]:
    """Insert or aggregate one repair incident.

    Deduplicates on (signature, scope_type, account_id, marketplace,
    brand_name) among non-terminal incidents.  A recurrence bumps
    occurrence_count, refreshes last_seen_at, overwrites a non-empty
    evidence_bundle_path, and — for fix-candidate classifications only —
    raises confidence by 0.10 capped at 0.85.  Returns (row, created).
    """
    now = now_str()
    conn = get_conn(db_path)
    try:
        terminal = tuple(sorted(INCIDENT_TERMINAL_STATUSES))
        row = conn.execute(
            f"""SELECT * FROM repair_incidents
                WHERE signature=? AND scope_type=? AND account_id=? AND marketplace=?
                  AND brand_name=? AND status NOT IN ({",".join("?" * len(terminal))})
                ORDER BY id DESC LIMIT 1""",
            (signature, scope_type, account_id, marketplace, brand_name, *terminal),
        ).fetchone()
        if row is None:
            cur = conn.execute(
                """INSERT INTO repair_incidents(
                     signature, scope_type, flow_type, account_id, marketplace,
                     brand_name, detector_type, classification, confidence, status,
                     first_seen_at, last_seen_at, occurrence_count, evidence_bundle_path)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, 1, ?)""",
                (
                    signature, scope_type, flow_type, account_id, marketplace,
                    brand_name, detector_type, classification, float(confidence),
                    now, now, evidence_bundle_path or "",
                ),
            )
            incident_id = int(cur.lastrowid)
            created = True
        else:
            incident_id = int(row["id"])
            new_confidence = float(row["confidence"] or 0.0)
            if classification in INCIDENT_REPAIR_CLASSES:
                new_confidence = min(
                    _INCIDENT_CONFIDENCE_CAP, new_confidence + _INCIDENT_RECURRENCE_BUMP
                )
            bundle = evidence_bundle_path or str(row["evidence_bundle_path"] or "")
            conn.execute(
                """UPDATE repair_incidents
                   SET occurrence_count=occurrence_count+1, last_seen_at=?,
                       confidence=?, evidence_bundle_path=?
                   WHERE id=?""",
                (now, new_confidence, bundle, incident_id),
            )
            created = False
        conn.commit()
    finally:
        conn.close()
    bump_confidence_cross_scope(db_path, incident_id)
    return get_incident(db_path, incident_id), created


def bump_confidence_cross_scope(db_path: str, incident_id: int) -> None:
    """+0.15 (capped 0.85) when the same signature also occurs under a
    different account or marketplace — a cross-scope failure is more likely a
    code defect than an account-specific problem."""
    conn = get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT signature, account_id, marketplace, confidence FROM repair_incidents WHERE id=?",
            (int(incident_id),),
        ).fetchone()
        if row is None:
            return
        other = conn.execute(
            """SELECT COUNT(*) FROM repair_incidents
               WHERE signature=? AND id<>?
                 AND (account_id<>? OR marketplace<>?)""",
            (str(row["signature"]), int(incident_id), str(row["account_id"]), str(row["marketplace"])),
        ).fetchone()
        if not other or not int(other[0] or 0):
            return
        conn.execute(
            "UPDATE repair_incidents SET confidence=MIN(?, confidence + ?) WHERE id=?",
            (_INCIDENT_CONFIDENCE_CAP, _INCIDENT_CROSS_SCOPE_BUMP, int(incident_id)),
        )
        conn.commit()
    finally:
        conn.close()


def list_incidents(
    db_path: str,
    status: str | None = None,
    classification: str | None = None,
    account_id: str | None = None,
    marketplace: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    sql = "FROM repair_incidents WHERE 1=1"
    params: list = []
    if status:
        sql += " AND status=?"
        params.append(status)
    if classification:
        # Comma-separated values mean "any of these" (repair-center filter).
        classes = [c.strip() for c in str(classification).split(",") if c.strip()]
        if len(classes) > 1:
            sql += f" AND classification IN ({','.join('?' * len(classes))})"
            params.extend(classes)
        elif classes:
            sql += " AND classification=?"
            params.append(classes[0])
    if account_id:
        sql += " AND account_id=?"
        params.append(account_id)
    if marketplace:
        sql += " AND marketplace=?"
        params.append(marketplace)
    conn = get_conn(db_path)
    total = int(conn.execute(f"SELECT COUNT(*) {sql}", params).fetchone()[0])
    rows = conn.execute(
        f"SELECT * {sql} ORDER BY last_seen_at DESC, id DESC LIMIT ? OFFSET ?",
        (*params, int(limit), int(offset)),
    ).fetchall()
    conn.close()
    return [_incident_row_to_dict(r) for r in rows], total


def get_incident(db_path: str, incident_id: int) -> dict | None:
    conn = get_conn(db_path)
    row = conn.execute(
        "SELECT * FROM repair_incidents WHERE id=?", (int(incident_id),)
    ).fetchone()
    conn.close()
    return _incident_row_to_dict(row) if row else None


def close_incident(db_path: str, incident_id: int, note: str) -> dict | None:
    """Terminal close as closed_human; None when missing or already terminal."""
    conn = get_conn(db_path)
    cur = conn.execute(
        f"""UPDATE repair_incidents SET status='closed_human', resolution_note=?
            WHERE id=? AND status NOT IN ({",".join("?" * len(INCIDENT_TERMINAL_STATUSES))})""",
        (str(note or ""), int(incident_id), *sorted(INCIDENT_TERMINAL_STATUSES)),
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    if not changed:
        return None
    return get_incident(db_path, incident_id)


def set_incident_evidence_bundle(db_path: str, incident_id: int, bundle_path: str) -> None:
    conn = get_conn(db_path)
    conn.execute(
        "UPDATE repair_incidents SET evidence_bundle_path=? WHERE id=?",
        (str(bundle_path or ""), int(incident_id)),
    )
    conn.commit()
    conn.close()


def count_open_incidents(db_path: str) -> int:
    """Non-terminal incidents — the work queue size shown on the overview."""
    conn = get_conn(db_path)
    count = int(conn.execute(
        f"""SELECT COUNT(*) FROM repair_incidents
            WHERE status NOT IN ({",".join("?" * len(INCIDENT_TERMINAL_STATUSES))})""",
        tuple(sorted(INCIDENT_TERMINAL_STATUSES)),
    ).fetchone()[0])
    conn.close()
    return count


# ---------------------------------------------------------------------------
# Stage-6 Codex read-only triage (codex_repair_jobs)
# ---------------------------------------------------------------------------

CODEX_JOB_STATUSES = {
    "running",
    "succeeded",
    "failed",
    "timeout",
    "unavailable",
    "quota_exceeded",
    "schema_invalid",
    # Stage-7 patch outcomes.
    "patch_ready",
    "validation_failed",
    # Stage-8 validation gate / canary / release lifecycle.
    "validating",
    "awaiting_validation_approval",
    "canary",
    "awaiting_release_approval",
    "released",
    "rejected",
    "rolled_back",
}

# Stage-7/8: a patch job in one of these statuses holds the per-incident
# exclusive slot (mirrors the partial unique index predicate).
CODEX_PATCH_ACTIVE_STATUSES = (
    "running",
    "patch_ready",
    "validating",
    "awaiting_validation_approval",
    "canary",
    "awaiting_release_approval",
)

# Jobs in these statuses never reached the Codex API, so they must not count
# against the daily call limit.
_CODEX_JOB_NO_API_CALL_STATUSES = ("unavailable", "quota_exceeded")


def _repair_job_row_to_dict(row) -> dict:
    changed_files: list = []
    raw_changed = row["changed_files_json"]
    if raw_changed:
        try:
            parsed = json.loads(str(raw_changed))
            if isinstance(parsed, list):
                changed_files = [str(item) for item in parsed]
        except ValueError:
            changed_files = []
    return {
        "id": int(row["id"]),
        "incident_id": int(row["incident_id"]),
        "stage": str(row["stage"] or ""),
        "status": str(row["status"] or ""),
        "worktree_path": row["worktree_path"],
        "branch_name": row["branch_name"],
        "codex_session_id": row["codex_session_id"],
        "jsonl_log_path": row["jsonl_log_path"],
        "result_json_path": row["result_json_path"],
        "changed_files": changed_files,
        "risk_level": row["risk_level"],
        "tests_passed": row["tests_passed"],
        "baseline_sha": row["baseline_sha"],
        "patch_sha": row["patch_sha"],
        "pre_release_sha": row["pre_release_sha"],
        "release_sha": row["release_sha"],
        "validation_json_path": row["validation_json_path"],
        "canary_job_ids_json": row["canary_job_ids_json"],
        "created_at": str(row["created_at"] or ""),
        "finished_at": row["finished_at"],
    }


def get_repair_job(db_path: str, job_id: int) -> dict | None:
    conn = get_conn(db_path)
    row = conn.execute(
        "SELECT * FROM codex_repair_jobs WHERE id=?", (int(job_id),)
    ).fetchone()
    conn.close()
    return _repair_job_row_to_dict(row) if row else None


def create_repair_job(db_path: str, incident_id: int, stage: str = "triage",
                      *, status: str = "running") -> dict:
    """Open one repair job row.  Terminal-at-birth statuses (unavailable,
    quota_exceeded) get finished_at set immediately."""
    now = now_str()
    finished_at = now if status != "running" else None
    conn = get_conn(db_path)
    cur = conn.execute(
        """INSERT INTO codex_repair_jobs(incident_id, stage, status, created_at, finished_at)
           VALUES (?, ?, ?, ?, ?)""",
        (int(incident_id), str(stage or "triage"), str(status), now, finished_at),
    )
    job_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return get_repair_job(db_path, job_id)


def finish_repair_job(db_path: str, job_id: int, status: str, *,
                      codex_session_id: str | None = None,
                      jsonl_log_path: str | None = None,
                      result_json_path: str | None = None,
                      changed_files_json: str | None = None,
                      risk_level: str | None = None,
                      tests_passed: int | None = None,
                      patch_sha: str | None = None) -> dict | None:
    """Close a running job.  Only non-None optional fields are overwritten."""
    conn = get_conn(db_path)
    assignments = ["status=?", "finished_at=?"]
    params: list = [str(status), now_str()]
    for column, value in (
        ("codex_session_id", codex_session_id),
        ("jsonl_log_path", jsonl_log_path),
        ("result_json_path", result_json_path),
        ("changed_files_json", changed_files_json),
        ("risk_level", risk_level),
        ("tests_passed", tests_passed),
        ("patch_sha", patch_sha),
    ):
        if value is not None:
            assignments.append(f"{column}=?")
            params.append(value)
    params.append(int(job_id))
    cur = conn.execute(
        f"UPDATE codex_repair_jobs SET {', '.join(assignments)} WHERE id=?",
        params,
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    if not changed:
        return None
    return get_repair_job(db_path, job_id)


def get_latest_triage_job(db_path: str, incident_id: int) -> dict | None:
    conn = get_conn(db_path)
    row = conn.execute(
        """SELECT * FROM codex_repair_jobs
           WHERE incident_id=? AND stage='triage' ORDER BY id DESC LIMIT 1""",
        (int(incident_id),),
    ).fetchone()
    conn.close()
    return _repair_job_row_to_dict(row) if row else None


def list_repair_jobs(db_path: str, incident_id: int, stage: str | None = None) -> list[dict]:
    sql = "SELECT * FROM codex_repair_jobs WHERE incident_id=?"
    params: list = [int(incident_id)]
    if stage:
        sql += " AND stage=?"
        params.append(str(stage))
    sql += " ORDER BY id DESC"
    conn = get_conn(db_path)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [_repair_job_row_to_dict(r) for r in rows]


def count_triage_jobs_today(db_path: str) -> int:
    """Triage jobs created today that actually reached (or tried to reach) the
    Codex API — unavailable/quota_exceeded rows are excluded so local
    degradation never eats the remote daily allowance."""
    today = now_str()[:10]
    conn = get_conn(db_path)
    count = int(conn.execute(
        f"""SELECT COUNT(*) FROM codex_repair_jobs
            WHERE stage='triage' AND created_at LIKE ?
              AND status NOT IN ({",".join("?" * len(_CODEX_JOB_NO_API_CALL_STATUSES))})""",
        (f"{today}%", *_CODEX_JOB_NO_API_CALL_STATUSES),
    ).fetchone()[0])
    conn.close()
    return count


def mark_incident_triaged(db_path: str, incident_id: int) -> dict | None:
    """open -> triaged on a successful triage; None when not currently open."""
    conn = get_conn(db_path)
    cur = conn.execute(
        "UPDATE repair_incidents SET status='triaged' WHERE id=? AND status='open'",
        (int(incident_id),),
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    if not changed:
        return None
    return get_incident(db_path, incident_id)


# ---------------------------------------------------------------------------
# Stage-7 isolated patch generation (stage='patch' jobs)
# ---------------------------------------------------------------------------

def create_patch_job(db_path: str, incident_id: int) -> dict | None:
    """Open one stage='patch' job row holding the per-incident exclusive slot.

    The active-check and the insert run in one IMMEDIATE transaction; the
    partial unique index idx_codex_repair_jobs_active_patch is the hard
    backstop.  Returns None when another active patch job already exists
    (caller maps this to a 409 conflict).
    """
    conn = get_conn(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        active = conn.execute(
            f"""SELECT id FROM codex_repair_jobs
                WHERE incident_id=? AND stage='patch'
                  AND status IN ({",".join("?" * len(CODEX_PATCH_ACTIVE_STATUSES))})""",
            (int(incident_id), *CODEX_PATCH_ACTIVE_STATUSES),
        ).fetchone()
        if active is not None:
            conn.rollback()
            return None
        cur = conn.execute(
            """INSERT INTO codex_repair_jobs(incident_id, stage, status, created_at)
               VALUES (?, 'patch', 'running', ?)""",
            (int(incident_id), now_str()),
        )
        job_id = int(cur.lastrowid)
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        return None
    finally:
        conn.close()
    return get_repair_job(db_path, job_id)


def set_repair_job_worktree(db_path: str, job_id: int, *,
                            worktree_path: str, branch_name: str,
                            baseline_sha: str | None = None) -> dict | None:
    """Attach the freshly created worktree to a running patch job."""
    conn = get_conn(db_path)
    cur = conn.execute(
        """UPDATE codex_repair_jobs
           SET worktree_path=?, branch_name=?, baseline_sha=? WHERE id=?""",
        (str(worktree_path), str(branch_name),
         str(baseline_sha) if baseline_sha else None, int(job_id)),
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    if not changed:
        return None
    return get_repair_job(db_path, job_id)


def list_patch_jobs_past_retention(db_path: str, cutoff: str) -> list[dict]:
    """Terminal patch jobs with a worktree, finished before ``cutoff``
    (``YYYY-MM-DD HH:MM:SS``).  Active jobs (running/patch_ready/validating)
    are never returned — they may still need the worktree."""
    conn = get_conn(db_path)
    rows = conn.execute(
        f"""SELECT * FROM codex_repair_jobs
            WHERE stage='patch' AND worktree_path IS NOT NULL
              AND finished_at IS NOT NULL AND finished_at < ?
              AND status NOT IN ({",".join("?" * len(CODEX_PATCH_ACTIVE_STATUSES))})
            ORDER BY id""",
        (str(cutoff), *CODEX_PATCH_ACTIVE_STATUSES),
    ).fetchall()
    conn.close()
    return [_repair_job_row_to_dict(r) for r in rows]


def mark_incident_patching(db_path: str, incident_id: int) -> dict | None:
    """triaged -> patching while a patch is being generated."""
    conn = get_conn(db_path)
    cur = conn.execute(
        "UPDATE repair_incidents SET status='patching' WHERE id=? AND status='triaged'",
        (int(incident_id),),
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    if not changed:
        return None
    return get_incident(db_path, incident_id)


def mark_incident_patch_ready(db_path: str, incident_id: int) -> dict | None:
    """patching -> patch_ready once the diff survives every scan."""
    conn = get_conn(db_path)
    cur = conn.execute(
        "UPDATE repair_incidents SET status='patch_ready' WHERE id=? AND status='patching'",
        (int(incident_id),),
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    if not changed:
        return None
    return get_incident(db_path, incident_id)


def mark_incident_patch_failed(db_path: str, incident_id: int) -> dict | None:
    """patching -> triaged: patch generation is retryable after any failure."""
    conn = get_conn(db_path)
    cur = conn.execute(
        "UPDATE repair_incidents SET status='triaged' WHERE id=? AND status='patching'",
        (int(incident_id),),
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    if not changed:
        return None
    return get_incident(db_path, incident_id)


# ---------------------------------------------------------------------------
# Stage-8 validation gate / approvals / release (repair_approvals + status
# transitions)
# ---------------------------------------------------------------------------

# Human approval decisions recorded on repair_approvals (append-only).
REPAIR_APPROVAL_DECISIONS = {
    "approve_validation",
    "confirm_canary",
    "approve_release",
    "reject",
}


def record_approval(db_path: str, *, repair_job_id: int, decision: str,
                    actor_id: int, note: str | None = None) -> int:
    """Append one human approval/rejection row for a repair job."""
    decision = str(decision)
    if decision not in REPAIR_APPROVAL_DECISIONS:
        raise ValueError(f"unknown approval decision: {decision!r}")
    conn = get_conn(db_path)
    cur = conn.execute(
        """INSERT INTO repair_approvals(repair_job_id, decision, actor_id, note, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (int(repair_job_id), decision, int(actor_id),
         str(note) if note is not None else None, now_str()),
    )
    approval_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return approval_id


def list_approvals(db_path: str, repair_job_id: int) -> list[dict]:
    """All approval rows for one repair job, oldest first."""
    conn = get_conn(db_path)
    rows = conn.execute(
        """SELECT * FROM repair_approvals WHERE repair_job_id=? ORDER BY id""",
        (int(repair_job_id),),
    ).fetchall()
    conn.close()
    return [
        {
            "id": int(r["id"]),
            "repair_job_id": int(r["repair_job_id"]),
            "decision": str(r["decision"] or ""),
            "actor_id": int(r["actor_id"]),
            "note": r["note"],
            "created_at": str(r["created_at"] or ""),
        }
        for r in rows
    ]


def set_repair_job_status(db_path: str, job_id: int, *,
                          from_statuses: tuple | list, to_status: str,
                          extra_cols: dict | None = None) -> dict | None:
    """Conditional status transition on one repair job.

    UPDATE ... WHERE id=? AND status IN from_statuses — returns the updated
    job row on success, None on an illegal transition (caller maps to 409).
    ``extra_cols`` sets additional columns in the same statement.
    """
    from_statuses = tuple(str(s) for s in from_statuses)
    assignments = ["status=?"]
    params: list = [str(to_status)]
    for column, value in (extra_cols or {}).items():
        assignments.append(f"{column}=?")
        params.append(value)
    params.append(int(job_id))
    params.extend(from_statuses)
    conn = get_conn(db_path)
    cur = conn.execute(
        f"""UPDATE codex_repair_jobs SET {', '.join(assignments)}
            WHERE id=? AND status IN ({",".join("?" * len(from_statuses))})""",
        params,
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    if not changed:
        return None
    return get_repair_job(db_path, job_id)


def mark_incident_validating(db_path: str, incident_id: int) -> dict | None:
    """patch_ready -> validating once the stage-8 validation suite starts."""
    conn = get_conn(db_path)
    cur = conn.execute(
        "UPDATE repair_incidents SET status='validating' WHERE id=? AND status='patch_ready'",
        (int(incident_id),),
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    if not changed:
        return None
    return get_incident(db_path, incident_id)


def mark_incident_validated(db_path: str, incident_id: int) -> dict | None:
    """validating -> validated when the validation suite passes."""
    conn = get_conn(db_path)
    cur = conn.execute(
        "UPDATE repair_incidents SET status='validated' WHERE id=? AND status='validating'",
        (int(incident_id),),
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    if not changed:
        return None
    return get_incident(db_path, incident_id)


def mark_incident_released(db_path: str, incident_id: int) -> dict | None:
    """validated -> released once the patch is merged and deployed."""
    conn = get_conn(db_path)
    cur = conn.execute(
        "UPDATE repair_incidents SET status='released' WHERE id=? AND status='validated'",
        (int(incident_id),),
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    if not changed:
        return None
    return get_incident(db_path, incident_id)


def mark_incident_rejected(db_path: str, incident_id: int) -> dict | None:
    """patch_ready/validating/validated -> rejected: a human vetoed the patch
    at any stage-8 gate.  Terminal."""
    conn = get_conn(db_path)
    cur = conn.execute(
        """UPDATE repair_incidents SET status='rejected'
           WHERE id=? AND status IN ('patch_ready', 'validating', 'validated')""",
        (int(incident_id),),
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    if not changed:
        return None
    return get_incident(db_path, incident_id)


def mark_incident_validation_failed(db_path: str, incident_id: int) -> dict | None:
    """validating -> triaged: validation failure is retryable — a fresh patch
    attempt may start over from triaged."""
    conn = get_conn(db_path)
    cur = conn.execute(
        "UPDATE repair_incidents SET status='triaged' WHERE id=? AND status='validating'",
        (int(incident_id),),
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    if not changed:
        return None
    return get_incident(db_path, incident_id)


def list_auto_triage_candidates(db_path: str, min_confidence: float,
                                limit: int = 100) -> list[dict]:
    """Open, fix-candidate incidents at or above the auto-triage confidence
    threshold.  Human-handling classifications (CAPTCHA/2FA/rate-limit/...)
    are excluded here as a second line of defense behind the detector."""
    classes = sorted(INCIDENT_REPAIR_CLASSES)
    conn = get_conn(db_path)
    rows = conn.execute(
        f"""SELECT * FROM repair_incidents
            WHERE status='open' AND confidence>=?
              AND classification IN ({",".join("?" * len(classes))})
            ORDER BY id LIMIT ?""",
        (float(min_confidence), *classes, int(limit)),
    ).fetchall()
    conn.close()
    return [_incident_row_to_dict(r) for r in rows]
