\
import sqlite3
import json
from pathlib import Path
from datetime import datetime
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
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(account_id, marketplace, case_id)
);
CREATE INDEX IF NOT EXISTS idx_case_followups_due
ON case_followups(status, scheduled_at);

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
'''


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_conn(db_path: str):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
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
    # Delayed Case tasks may bind to an existing Feishu row.  These columns do
    # not contain app credentials, Case replies, or row contents.
    _ensure_column(conn, 'case_followups', 'feishu_record_id', 'TEXT')
    _ensure_column(conn, 'case_followups', 'feishu_country_option', 'TEXT')
    _ensure_column(conn, 'case_followups', 'feishu_binding_status', 'TEXT')
    _ensure_column(conn, 'case_followups', 'feishu_binding_reason', 'TEXT')
    _ensure_column(conn, 'case_followups', 'feishu_bound_at', 'TEXT')


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
) -> tuple[int, bool]:
    """Persist one delayed Case check. Returns ``(id, created)``."""
    now = now_str()
    conn = get_conn(db_path)
    existing = conn.execute(
        """SELECT id, status, scheduled_at FROM case_followups
           WHERE account_id=? AND marketplace=? AND case_id=?""",
        (account_id, marketplace, str(case_id)),
    ).fetchone()
    if existing:
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
               scheduled_at, status, created_at, updated_at
           ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
        (
            account_id,
            marketplace,
            brand_name,
            str(case_id),
            submitted_at,
            scheduled_at,
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


def requeue_stale_case_followups(db_path: str, stale_before: str) -> int:
    conn = get_conn(db_path)
    cur = conn.execute(
        """UPDATE case_followups
           SET status='retry', scheduled_at=?, error='stale_running_requeued', updated_at=?
           WHERE status='running' AND last_checked_at IS NOT NULL AND last_checked_at < ?""",
        (now_str(), now_str(), stale_before),
    )
    count = cur.rowcount
    conn.commit()
    conn.close()
    return count


def claim_due_case_followups(
    db_path: str,
    due_at: Optional[str] = None,
    limit: int = 1,
    followup_ids: Optional[Iterable[int]] = None,
) -> list[Dict[str, Any]]:
    due_at = due_at or now_str()
    selected_ids = [int(value) for value in (followup_ids or [])]
    conn = get_conn(db_path)
    conn.execute("BEGIN IMMEDIATE")
    sql = """SELECT * FROM case_followups
             WHERE status IN ('pending', 'retry') AND scheduled_at <= ?"""
    params: list[Any] = [due_at]
    if selected_ids:
        sql += " AND id IN (" + ",".join("?" for _ in selected_ids) + ")"
        params.extend(selected_ids)
    sql += " ORDER BY scheduled_at, id LIMIT ?"
    params.append(max(1, int(limit)))
    rows = conn.execute(sql, params).fetchall()
    claimed = []
    for row in rows:
        conn.execute(
            """UPDATE case_followups
               SET status='running', attempt_count=attempt_count+1,
                   last_checked_at=?, updated_at=?, error=NULL
               WHERE id=? AND status IN ('pending', 'retry')""",
            (due_at, due_at, row["id"]),
        )
        claimed_row = dict(row)
        claimed_row["status"] = "running"
        claimed_row["attempt_count"] = int(row["attempt_count"]) + 1
        claimed_row["last_checked_at"] = due_at
        claimed.append(claimed_row)
    conn.commit()
    conn.close()
    return claimed


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
) -> Optional[str]:
    selected_ids = [int(value) for value in (followup_ids or [])]
    conn = get_conn(db_path)
    sql = """SELECT MIN(scheduled_at) AS scheduled_at FROM case_followups
             WHERE status IN ('pending', 'retry')"""
    params: list[Any] = []
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
