"""Finite, auditable marketplace reapplication campaigns.

The state machine only advances after a linked Case follow-up produces a
business result.  It never treats browser, login, rate-limit, missing Case ID,
or other technical failures as a rejection.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .config_loader import load_yaml
from .db import get_conn, init_db, now_str

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROUTES = {
    "NA": ("US", "MX"),
    "EU": ("UK", "BE", "DE", "SE", "NL", "FR"),
}
ADVANCE_RESULTS = {"declined"}
PASS_RESULTS = {"approved"}
WAIT_RESULTS = {"false_approved", "pending", "verification_pending"}
PAUSE_RESULTS = {"action_required", "answered_unknown", "blocked"}
ACTIVE_CAMPAIGN_STATUSES = {
    "scheduled",
    "next_scheduled",
    "running",
    "waiting_case_id",
    "waiting_case",
    "paused",
    "blocked",
}


class ReapplicationStartError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _db_path(settings: Mapping[str, Any]) -> str:
    return str((settings.get("paths") or {}).get("db_path") or "./runtime/state/ledger.db")


def _db_datetime(value: datetime) -> str:
    return value.replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")


def _parse_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.now()
    normalized = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        parsed = datetime.strptime(normalized, "%Y-%m-%d %H:%M:%S")
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def _safe_reason(value: Any, limit: int = 1000) -> str:
    text = re.sub(
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        "[REDACTED_EMAIL]",
        str(value or ""),
    )
    return text[:limit]


def get_reapplication_config(settings: Mapping[str, Any] | None = None) -> dict[str, Any]:
    settings = settings or load_yaml(str(PROJECT_ROOT / "config" / "settings.yaml"))
    configured = dict(settings.get("reapplication") or {})
    raw_routes = configured.get("routes") or DEFAULT_ROUTES
    routes: dict[str, tuple[str, ...]] = {}
    for region, sites in raw_routes.items():
        normalized = tuple(str(site).strip().upper() for site in sites if str(site).strip())
        if normalized:
            routes[str(region).strip().upper()] = normalized
    return {
        "enabled": bool(configured.get("enabled", True)),
        "decline_delay_hours": max(0.0, float(configured.get("decline_delay_hours", 2.0))),
        "poll_interval_seconds": max(5, min(60, int(configured.get("poll_interval_seconds", 60)))),
        "busy_retry_minutes": max(1, int(configured.get("busy_retry_minutes", 10))),
        "auto_start_worker": bool(configured.get("auto_start_worker", True)),
        "routes": routes,
    }


def resolve_route(settings: Mapping[str, Any], region: str) -> tuple[str, ...]:
    region_code = str(region or "").strip().upper()
    route = get_reapplication_config(settings)["routes"].get(region_code)
    if not route:
        raise ValueError(f"Unsupported reapplication region: {region}")
    return tuple(route)


def create_campaign(
    settings: Mapping[str, Any],
    account_id: str,
    brand_name: str,
    region: str,
    *,
    submit_authorized: bool,
    start_site: str | None = None,
    scheduled_at: str | None = None,
) -> dict[str, Any]:
    """Create one finite campaign and its first scheduled attempt."""

    config = get_reapplication_config(settings)
    if not config["enabled"]:
        raise ValueError("Reapplication campaigns are disabled")
    account = str(account_id or "").strip()
    brand = str(brand_name or "").strip()
    region_code = str(region or "").strip().upper()
    if not account or not brand:
        raise ValueError("account_id and brand_name are required")
    route = resolve_route(settings, region_code)
    first_site = str(start_site or route[0]).strip().upper()
    if first_site not in route:
        raise ValueError(f"Start site {first_site} is not in the {region_code} route")
    route_index = route.index(first_site)
    due = _db_datetime(_parse_datetime(scheduled_at))
    now = now_str()
    db_path = _db_path(settings)
    init_db(db_path)
    conn = get_conn(db_path)
    conn.execute("BEGIN IMMEDIATE")
    existing = conn.execute(
        """SELECT * FROM reapplication_campaigns
           WHERE account_id=? AND brand_name=? AND region=?
             AND status IN ('scheduled', 'next_scheduled', 'running',
                            'waiting_case_id', 'waiting_case', 'paused', 'blocked')
           ORDER BY id DESC LIMIT 1""",
        (account, brand, region_code),
    ).fetchone()
    if existing:
        payload = dict(existing)
        attempt = conn.execute(
            """SELECT * FROM reapplication_attempts
               WHERE campaign_id=? ORDER BY route_index DESC LIMIT 1""",
            (existing["id"],),
        ).fetchone()
        conn.commit()
        conn.close()
        payload["created"] = False
        payload["route"] = json.loads(payload.pop("route_json"))
        payload["attempt"] = dict(attempt) if attempt else None
        return payload

    cur = conn.execute(
        """INSERT INTO reapplication_campaigns(
               account_id, brand_name, region, route_json, current_route_index,
               status, submit_authorized, decline_delay_hours,
               created_at, updated_at
           ) VALUES (?, ?, ?, ?, ?, 'scheduled', ?, ?, ?, ?)""",
        (
            account,
            brand,
            region_code,
            json.dumps(route),
            route_index,
            1 if submit_authorized else 0,
            float(config["decline_delay_hours"]),
            now,
            now,
        ),
    )
    campaign_id = int(cur.lastrowid)
    attempt_cur = conn.execute(
        """INSERT INTO reapplication_attempts(
               campaign_id, route_index, site, status, scheduled_at,
               created_at, updated_at
           ) VALUES (?, ?, ?, 'scheduled', ?, ?, ?)""",
        (campaign_id, route_index, first_site, due, now, now),
    )
    attempt_id = int(attempt_cur.lastrowid)
    conn.commit()
    conn.close()
    return {
        "id": campaign_id,
        "created": True,
        "account_id": account,
        "brand_name": brand,
        "region": region_code,
        "route": list(route),
        "current_route_index": route_index,
        "status": "scheduled",
        "submit_authorized": 1 if submit_authorized else 0,
        "decline_delay_hours": float(config["decline_delay_hours"]),
        "attempt": {
            "id": attempt_id,
            "campaign_id": campaign_id,
            "route_index": route_index,
            "site": first_site,
            "status": "scheduled",
            "scheduled_at": due,
        },
    }


def _route_for_site(settings: Mapping[str, Any], site: str) -> tuple[str, tuple[str, ...]]:
    normalized = str(site or "").strip().upper()
    matches = [
        (region, tuple(route))
        for region, route in get_reapplication_config(settings)["routes"].items()
        if normalized in route
    ]
    if len(matches) != 1:
        raise ReapplicationStartError("unsupported_source_site")
    return matches[0]


def declined_case_candidate(settings: Mapping[str, Any], followup_id: int) -> dict[str, Any]:
    """Return a server-derived declined Case start candidate."""
    config = get_reapplication_config(settings)
    if not config["enabled"]:
        raise ReapplicationStartError("reapplication_disabled")
    db_path = _db_path(settings)
    init_db(db_path)
    conn = get_conn(db_path)
    row = conn.execute(
        "SELECT * FROM case_followups WHERE id=?", (int(followup_id),)
    ).fetchone()
    conn.close()
    if row is None:
        raise ReapplicationStartError("case_followup_not_found")
    case = dict(row)
    if case.get("status") != "completed" or case.get("final_result") != "declined":
        raise ReapplicationStartError("case_not_explicitly_declined")
    region, route = _route_for_site(settings, str(case.get("marketplace") or ""))
    source_index = route.index(str(case["marketplace"]).upper())
    if source_index >= len(route) - 1:
        raise ReapplicationStartError("route_exhausted")
    return {
        "id": int(case["id"]),
        "account_id": str(case["account_id"]),
        "brand_name": str(case["brand_name"]),
        "marketplace": str(case["marketplace"]).upper(),
        "case_id": str(case.get("case_id") or ""),
        "completed_at": case.get("completed_at"),
        "last_checked_at": case.get("last_checked_at"),
        "decision_reason": case.get("decision_reason"),
        "region": region,
        "route": list(route),
        "source_route_index": source_index,
        "remaining_route": list(route[source_index + 1 :]),
        "next_site": route[source_index + 1],
        "reapplication_campaign_id": case.get("reapplication_campaign_id"),
    }


def list_eligible_declined_cases(settings: Mapping[str, Any], limit: int = 100) -> list[dict[str, Any]]:
    db_path = _db_path(settings)
    init_db(db_path)
    conn = get_conn(db_path)
    rows = conn.execute(
        """SELECT id FROM case_followups
           WHERE status='completed' AND final_result='declined'
           ORDER BY completed_at DESC, id DESC LIMIT ?""",
        (max(1, min(500, int(limit))),),
    ).fetchall()
    conn.close()
    eligible: list[dict[str, Any]] = []
    for row in rows:
        try:
            candidate = declined_case_candidate(settings, int(row["id"]))
        except ReapplicationStartError:
            continue
        if candidate.get("reapplication_campaign_id"):
            continue
        eligible.append(candidate)
    return eligible


def create_campaign_from_declined_case(
    settings: Mapping[str, Any],
    followup_id: int,
    *,
    confirmed_remaining_route: list[str] | tuple[str, ...],
    authorize_submit: bool,
) -> dict[str, Any]:
    """Authorize one declined Case and schedule only its next route site."""
    if not authorize_submit:
        raise ReapplicationStartError("submit_authorization_required")
    candidate = declined_case_candidate(settings, followup_id)
    confirmed = [str(site).strip().upper() for site in confirmed_remaining_route]
    if confirmed != candidate["remaining_route"]:
        raise ReapplicationStartError("route_confirmation_mismatch")

    config = get_reapplication_config(settings)
    db_path = _db_path(settings)
    now_dt = datetime.now()
    declined_at = _parse_datetime(candidate.get("completed_at") or candidate.get("last_checked_at"))
    due_dt = max(now_dt, declined_at + timedelta(hours=float(config["decline_delay_hours"])))
    now = now_str()
    conn = get_conn(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        source = conn.execute(
            """SELECT * FROM case_followups
               WHERE id=? AND status='completed' AND final_result='declined'""",
            (int(followup_id),),
        ).fetchone()
        if source is None:
            raise ReapplicationStartError("case_not_explicitly_declined")
        existing_source = conn.execute(
            """SELECT * FROM reapplication_campaigns
               WHERE source_case_followup_id=? ORDER BY id DESC LIMIT 1""",
            (int(followup_id),),
        ).fetchone()
        if existing_source is not None:
            payload = dict(existing_source)
            attempts = conn.execute(
                """SELECT * FROM reapplication_attempts
                   WHERE campaign_id=? ORDER BY route_index""",
                (int(existing_source["id"]),),
            ).fetchall()
            conn.commit()
            payload["created"] = False
            payload["route"] = json.loads(payload.pop("route_json"))
            payload["attempts"] = [dict(row) for row in attempts]
            return payload
        active = conn.execute(
            """SELECT id FROM reapplication_campaigns
               WHERE account_id=? AND brand_name=? AND region=?
                 AND status IN ('scheduled','next_scheduled','running','waiting_case_id',
                                'waiting_case','paused','blocked')
               ORDER BY id DESC LIMIT 1""",
            (candidate["account_id"], candidate["brand_name"], candidate["region"]),
        ).fetchone()
        if active is not None:
            raise ReapplicationStartError("active_campaign_conflict")

        next_index = int(candidate["source_route_index"]) + 1
        cur = conn.execute(
            """INSERT INTO reapplication_campaigns(
                   account_id, brand_name, region, route_json, current_route_index,
                   status, submit_authorized, decline_delay_hours,
                   source_case_followup_id, source_marketplace, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'scheduled', 1, ?, ?, ?, ?, ?)""",
            (
                candidate["account_id"], candidate["brand_name"], candidate["region"],
                json.dumps(candidate["route"]), next_index,
                float(config["decline_delay_hours"]), int(followup_id),
                candidate["marketplace"], now, now,
            ),
        )
        campaign_id = int(cur.lastrowid)
        source_attempt = conn.execute(
            """INSERT INTO reapplication_attempts(
                   campaign_id, route_index, site, status, scheduled_at,
                   submitted_at, completed_at, case_id, case_followup_id,
                   final_result, decision_reason, created_at, updated_at)
               VALUES (?, ?, ?, 'completed', ?, ?, ?, ?, ?, 'declined', ?, ?, ?)""",
            (
                campaign_id, int(candidate["source_route_index"]), candidate["marketplace"],
                str(source["scheduled_at"] or source["submitted_at"] or now),
                source["submitted_at"], source["completed_at"], source["case_id"],
                int(followup_id), source["decision_reason"], now, now,
            ),
        )
        source_attempt_id = int(source_attempt.lastrowid)
        next_attempt = conn.execute(
            """INSERT INTO reapplication_attempts(
                   campaign_id, route_index, site, status, scheduled_at, created_at, updated_at)
               VALUES (?, ?, ?, 'scheduled', ?, ?, ?)""",
            (
                campaign_id, next_index, candidate["next_site"],
                _db_datetime(due_dt), now, now,
            ),
        )
        conn.execute(
            """UPDATE case_followups
               SET reapplication_campaign_id=?, reapplication_attempt_id=?, updated_at=?
               WHERE id=? AND status='completed' AND final_result='declined'""",
            (campaign_id, source_attempt_id, now, int(followup_id)),
        )
        conn.commit()
        return {
            "id": campaign_id,
            "created": True,
            "account_id": candidate["account_id"],
            "brand_name": candidate["brand_name"],
            "region": candidate["region"],
            "route": candidate["route"],
            "current_route_index": next_index,
            "status": "scheduled",
            "submit_authorized": 1,
            "decline_delay_hours": float(config["decline_delay_hours"]),
            "source_case_followup_id": int(followup_id),
            "source_marketplace": candidate["marketplace"],
            "created_at": now,
            "updated_at": now,
            "attempts": [
                dict(conn.execute("SELECT * FROM reapplication_attempts WHERE id=?", (source_attempt_id,)).fetchone()),
                dict(conn.execute("SELECT * FROM reapplication_attempts WHERE id=?", (int(next_attempt.lastrowid),)).fetchone()),
            ],
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def attach_case_followup(
    settings: Mapping[str, Any],
    campaign_id: int,
    attempt_id: int,
    followup_id: int,
    case_id: str,
    site: str,
) -> dict[str, Any]:
    """Attach the submitted Case to the exact campaign attempt."""

    db_path = _db_path(settings)
    init_db(db_path)
    now = now_str()
    conn = get_conn(db_path)
    conn.execute("BEGIN IMMEDIATE")
    attempt = conn.execute(
        """SELECT a.*, c.status AS campaign_status
           FROM reapplication_attempts a
           JOIN reapplication_campaigns c ON c.id=a.campaign_id
           WHERE a.id=? AND a.campaign_id=?""",
        (int(attempt_id), int(campaign_id)),
    ).fetchone()
    if not attempt:
        conn.rollback()
        conn.close()
        raise ValueError("Unknown reapplication attempt")
    if str(attempt["site"]).upper() != str(site).upper():
        conn.rollback()
        conn.close()
        raise ValueError("Case site does not match the reapplication attempt")
    if attempt["case_followup_id"] not in {None, int(followup_id)}:
        conn.rollback()
        conn.close()
        raise ValueError("Reapplication attempt is already linked to another Case")
    conn.execute(
        """UPDATE reapplication_attempts
           SET status='waiting_case', case_id=?, case_followup_id=?,
               submitted_at=COALESCE(submitted_at, ?), updated_at=?
           WHERE id=?""",
        (str(case_id), int(followup_id), now, now, int(attempt_id)),
    )
    conn.execute(
        """UPDATE reapplication_campaigns
           SET status='waiting_case', current_route_index=?, updated_at=?
           WHERE id=?""",
        (int(attempt["route_index"]), now, int(campaign_id)),
    )
    conn.commit()
    conn.close()
    return {
        "status": "waiting_case",
        "campaign_id": int(campaign_id),
        "attempt_id": int(attempt_id),
        "case_followup_id": int(followup_id),
        "case_id": str(case_id),
    }


def attach_case_id_recovery(
    settings: Mapping[str, Any],
    campaign_id: int,
    attempt_id: int,
    recovery_id: int,
    site: str,
) -> dict[str, Any]:
    """Keep an attempt active while Selling Applications recovers its Case ID."""

    db_path = _db_path(settings)
    init_db(db_path)
    now = now_str()
    conn = get_conn(db_path)
    conn.execute("BEGIN IMMEDIATE")
    attempt = conn.execute(
        """SELECT a.*, c.status AS campaign_status
           FROM reapplication_attempts a
           JOIN reapplication_campaigns c ON c.id=a.campaign_id
           WHERE a.id=? AND a.campaign_id=?""",
        (int(attempt_id), int(campaign_id)),
    ).fetchone()
    if not attempt:
        conn.rollback()
        conn.close()
        raise ValueError("Unknown reapplication attempt")
    if str(attempt["site"]).upper() != str(site).upper():
        conn.rollback()
        conn.close()
        raise ValueError("Case-ID recovery site does not match the reapplication attempt")
    conn.execute(
        """UPDATE reapplication_attempts
           SET status='waiting_case_id', error=NULL, updated_at=? WHERE id=?""",
        (now, int(attempt_id)),
    )
    conn.execute(
        """UPDATE reapplication_campaigns
           SET status='waiting_case_id', current_route_index=?,
               stop_reason=NULL, updated_at=? WHERE id=?""",
        (int(attempt["route_index"]), now, int(campaign_id)),
    )
    conn.commit()
    conn.close()
    return {
        "status": "waiting_case_id",
        "campaign_id": int(campaign_id),
        "attempt_id": int(attempt_id),
        "recovery_id": int(recovery_id),
    }


def handle_case_outcome(
    settings: Mapping[str, Any],
    task: Mapping[str, Any],
    result: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Apply one linked Case result to the finite route, idempotently."""

    campaign_id = task.get("reapplication_campaign_id")
    attempt_id = task.get("reapplication_attempt_id")
    if campaign_id is None or attempt_id is None:
        return {"status": "not_linked"}
    outcome = str(result.get("result") or "answered_unknown").strip().lower()
    reason = _safe_reason(result.get("decision_reason"))
    transition_time = now or datetime.now()
    timestamp = _db_datetime(transition_time)
    db_path = _db_path(settings)
    init_db(db_path)
    conn = get_conn(db_path)
    conn.execute("BEGIN IMMEDIATE")
    row = conn.execute(
        """SELECT a.*, c.account_id, c.brand_name, c.region, c.route_json,
                  c.status AS campaign_status, c.decline_delay_hours
           FROM reapplication_attempts a
           JOIN reapplication_campaigns c ON c.id=a.campaign_id
           WHERE a.id=? AND c.id=?""",
        (int(attempt_id), int(campaign_id)),
    ).fetchone()
    if not row:
        conn.rollback()
        conn.close()
        return {"status": "link_not_found"}
    if (
        str(row["account_id"]) != str(task.get("account_id"))
        or str(row["brand_name"]).casefold() != str(task.get("brand_name") or "").casefold()
        or str(row["site"]).upper() != str(task.get("marketplace") or "").upper()
    ):
        conn.execute(
            """UPDATE reapplication_campaigns
               SET status='blocked', stop_reason='case_link_mismatch', updated_at=?
               WHERE id=?""",
            (timestamp, int(campaign_id)),
        )
        conn.commit()
        conn.close()
        return {"status": "blocked", "reason": "case_link_mismatch"}
    if row["final_result"]:
        current = conn.execute(
            "SELECT status, stop_reason FROM reapplication_campaigns WHERE id=?",
            (int(campaign_id),),
        ).fetchone()
        conn.commit()
        conn.close()
        return {
            "status": "already_handled",
            "outcome": str(row["final_result"]),
            "campaign_status": str(current["status"]),
            "reason": str(current["stop_reason"] or ""),
        }

    if outcome in WAIT_RESULTS:
        if result.get("automatic_followup_exhausted"):
            pause_reason = "case_followup_attempt_limit_reached:" + outcome
            conn.execute(
                """UPDATE reapplication_attempts
                   SET status='manual_review', final_result=?,
                       decision_reason=?, completed_at=?, updated_at=?
                   WHERE id=?""",
                (outcome, reason, timestamp, timestamp, int(attempt_id)),
            )
            conn.execute(
                """UPDATE reapplication_campaigns
                   SET status='paused', stop_reason=?,
                       completed_at=NULL, updated_at=?
                   WHERE id=?""",
                (pause_reason, timestamp, int(campaign_id)),
            )
            conn.commit()
            conn.close()
            return {
                "status": "paused",
                "site": str(row["site"]),
                "outcome": outcome,
                "reason": pause_reason,
            }
        conn.execute(
            """UPDATE reapplication_attempts
               SET status='waiting_case', final_result=NULL,
                   decision_reason=?, completed_at=NULL, updated_at=?
               WHERE id=?""",
            (reason, timestamp, int(attempt_id)),
        )
        conn.execute(
            """UPDATE reapplication_campaigns
               SET status='waiting_case', stop_reason=NULL,
                   completed_at=NULL, updated_at=?
               WHERE id=?""",
            (timestamp, int(campaign_id)),
        )
        conn.commit()
        conn.close()
        return {"status": "waiting_case", "site": str(row["site"]), "outcome": outcome}

    attempt_status = "completed" if outcome in PASS_RESULTS | ADVANCE_RESULTS else "manual_review"
    conn.execute(
        """UPDATE reapplication_attempts
           SET status=?, final_result=?, decision_reason=?, completed_at=?, updated_at=?
           WHERE id=?""",
        (attempt_status, outcome, reason, timestamp, timestamp, int(attempt_id)),
    )
    route = tuple(str(site).upper() for site in json.loads(row["route_json"]))
    route_index = int(row["route_index"])
    if outcome in PASS_RESULTS:
        conn.execute(
            """UPDATE reapplication_campaigns
               SET status='passed', stop_reason='approved', completed_at=?, updated_at=?
               WHERE id=?""",
            (timestamp, timestamp, int(campaign_id)),
        )
        conn.commit()
        conn.close()
        return {"status": "passed", "site": str(row["site"]), "outcome": outcome}

    if outcome in ADVANCE_RESULTS:
        next_index = route_index + 1
        if next_index >= len(route):
            conn.execute(
                """UPDATE reapplication_campaigns
                   SET status='route_exhausted', stop_reason='route_exhausted',
                       completed_at=?, updated_at=?
                   WHERE id=?""",
                (timestamp, timestamp, int(campaign_id)),
            )
            conn.commit()
            conn.close()
            return {
                "status": "route_exhausted",
                "site": str(row["site"]),
                "outcome": outcome,
            }
        delay_hours = max(0.0, float(row["decline_delay_hours"] or 2.0))
        next_due = _db_datetime(transition_time + timedelta(hours=delay_hours))
        next_site = route[next_index]
        cur = conn.execute(
            """INSERT OR IGNORE INTO reapplication_attempts(
                   campaign_id, route_index, site, status, scheduled_at,
                   created_at, updated_at
               ) VALUES (?, ?, ?, 'scheduled', ?, ?, ?)""",
            (int(campaign_id), next_index, next_site, next_due, timestamp, timestamp),
        )
        next_row = conn.execute(
            """SELECT id, status, scheduled_at FROM reapplication_attempts
               WHERE campaign_id=? AND route_index=?""",
            (int(campaign_id), next_index),
        ).fetchone()
        conn.execute(
            """UPDATE reapplication_campaigns
               SET status='next_scheduled', current_route_index=?,
                   stop_reason=NULL, updated_at=?
               WHERE id=?""",
            (next_index, timestamp, int(campaign_id)),
        )
        conn.commit()
        conn.close()
        return {
            "status": "next_scheduled",
            "outcome": outcome,
            "next_site": next_site,
            "scheduled_at": str(next_row["scheduled_at"]),
            "attempt_id": int(next_row["id"]),
            "created": cur.rowcount > 0,
        }

    pause_reason = "manual_case_result:" + outcome
    conn.execute(
        """UPDATE reapplication_campaigns
           SET status='paused', stop_reason=?, updated_at=? WHERE id=?""",
        (pause_reason, timestamp, int(campaign_id)),
    )
    conn.commit()
    conn.close()
    return {"status": "paused", "outcome": outcome, "reason": pause_reason}


def claim_due_attempt(
    settings: Mapping[str, Any],
    *,
    due_at: str | None = None,
) -> dict[str, Any] | None:
    """Claim one authorized due attempt; campaigns execute sequentially."""

    db_path = _db_path(settings)
    init_db(db_path)
    due = due_at or now_str()
    conn = get_conn(db_path)
    conn.execute("BEGIN IMMEDIATE")
    row = conn.execute(
        """SELECT a.*, c.account_id, c.brand_name, c.region, c.route_json,
                  c.submit_authorized, c.status AS campaign_status
           FROM reapplication_attempts a
           JOIN reapplication_campaigns c ON c.id=a.campaign_id
           WHERE a.status='scheduled' AND a.scheduled_at <= ?
             AND c.status IN ('scheduled', 'next_scheduled')
             AND c.submit_authorized=1
           ORDER BY a.scheduled_at, a.id LIMIT 1""",
        (due,),
    ).fetchone()
    if not row:
        conn.commit()
        conn.close()
        return None
    changed = conn.execute(
        """UPDATE reapplication_attempts
           SET status='running', started_at=?, updated_at=?, error=NULL
           WHERE id=? AND status='scheduled'""",
        (due, due, int(row["id"])),
    ).rowcount
    if not changed:
        conn.rollback()
        conn.close()
        return None
    conn.execute(
        "UPDATE reapplication_campaigns SET status='running', updated_at=? WHERE id=?",
        (due, int(row["campaign_id"])),
    )
    conn.commit()
    conn.close()
    payload = dict(row)
    payload["status"] = "running"
    payload["started_at"] = due
    payload["route"] = json.loads(payload.pop("route_json"))
    return payload


def reschedule_running_attempt(
    settings: Mapping[str, Any],
    attempt_id: int,
    scheduled_at: str,
    reason: str,
) -> None:
    db_path = _db_path(settings)
    now = now_str()
    conn = get_conn(db_path)
    conn.execute("BEGIN IMMEDIATE")
    row = conn.execute(
        "SELECT campaign_id FROM reapplication_attempts WHERE id=?",
        (int(attempt_id),),
    ).fetchone()
    if row:
        conn.execute(
            """UPDATE reapplication_attempts
               SET status='scheduled', scheduled_at=?, error=?, pid=NULL, updated_at=?
               WHERE id=?""",
            (scheduled_at, _safe_reason(reason), now, int(attempt_id)),
        )
        conn.execute(
            """UPDATE reapplication_campaigns
               SET status='next_scheduled', stop_reason=NULL, updated_at=? WHERE id=?""",
            (now, int(row["campaign_id"])),
        )
    conn.commit()
    conn.close()


def set_attempt_process_metadata(
    settings: Mapping[str, Any],
    attempt_id: int,
    *,
    pid: int,
    state_path: str,
    stdout_path: str,
    stderr_path: str,
) -> None:
    conn = get_conn(_db_path(settings))
    conn.execute(
        """UPDATE reapplication_attempts
           SET pid=?, state_path=?, stdout_path=?, stderr_path=?, updated_at=?
           WHERE id=?""",
        (int(pid), state_path, stdout_path, stderr_path, now_str(), int(attempt_id)),
    )
    conn.commit()
    conn.close()


def pause_attempt(
    settings: Mapping[str, Any],
    attempt_id: int,
    reason: str,
    *,
    status: str = "failed",
) -> dict[str, Any]:
    db_path = _db_path(settings)
    now = now_str()
    safe_reason = _safe_reason(reason)
    conn = get_conn(db_path)
    conn.execute("BEGIN IMMEDIATE")
    row = conn.execute(
        "SELECT campaign_id FROM reapplication_attempts WHERE id=?",
        (int(attempt_id),),
    ).fetchone()
    if not row:
        conn.rollback()
        conn.close()
        return {"status": "not_found"}
    current = conn.execute(
        "SELECT status, case_followup_id FROM reapplication_attempts WHERE id=?",
        (int(attempt_id),),
    ).fetchone()
    if current["status"] == "waiting_case" and current["case_followup_id"]:
        conn.commit()
        conn.close()
        return {"status": "waiting_case", "case_followup_id": int(current["case_followup_id"])}
    if current["status"] == "waiting_case_id":
        conn.commit()
        conn.close()
        return {"status": "waiting_case_id"}
    conn.execute(
        """UPDATE reapplication_attempts
           SET status=?, error=?, completed_at=?, updated_at=? WHERE id=?""",
        (status, safe_reason, now, now, int(attempt_id)),
    )
    conn.execute(
        """UPDATE reapplication_campaigns
           SET status='paused', stop_reason=?, updated_at=? WHERE id=?""",
        (safe_reason, now, int(row["campaign_id"])),
    )
    conn.commit()
    conn.close()
    return {"status": "paused", "reason": safe_reason}


def pause_missing_case_id_attempt(
    settings: Mapping[str, Any],
    campaign_id: int | None,
    attempt_id: int | None,
    reason: str,
    *,
    blocked: bool = False,
) -> dict[str, Any]:
    """Pause a campaign when Case-ID recovery needs human intervention."""

    if campaign_id is None or attempt_id is None:
        return {"status": "not_linked"}
    db_path = _db_path(settings)
    now = now_str()
    safe_reason = _safe_reason(reason)
    conn = get_conn(db_path)
    conn.execute("BEGIN IMMEDIATE")
    row = conn.execute(
        """SELECT id FROM reapplication_attempts
           WHERE id=? AND campaign_id=?""",
        (int(attempt_id), int(campaign_id)),
    ).fetchone()
    if not row:
        conn.rollback()
        conn.close()
        return {"status": "not_found"}
    attempt_status = "blocked" if blocked else "manual_review"
    campaign_status = "blocked" if blocked else "paused"
    conn.execute(
        """UPDATE reapplication_attempts
           SET status=?, error=?, completed_at=?, updated_at=? WHERE id=?""",
        (attempt_status, safe_reason, now, now, int(attempt_id)),
    )
    conn.execute(
        """UPDATE reapplication_campaigns
           SET status=?, stop_reason=?, updated_at=? WHERE id=?""",
        (campaign_status, safe_reason, now, int(campaign_id)),
    )
    conn.commit()
    conn.close()
    return {"status": campaign_status, "reason": safe_reason}


def get_attempt(settings: Mapping[str, Any], attempt_id: int) -> dict[str, Any] | None:
    conn = get_conn(_db_path(settings))
    row = conn.execute(
        """SELECT a.*, c.status AS campaign_status, c.account_id, c.brand_name,
                  c.region, c.submit_authorized
           FROM reapplication_attempts a
           JOIN reapplication_campaigns c ON c.id=a.campaign_id
           WHERE a.id=?""",
        (int(attempt_id),),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def list_campaigns(
    settings: Mapping[str, Any],
    statuses: set[str] | None = None,
) -> list[dict[str, Any]]:
    db_path = _db_path(settings)
    init_db(db_path)
    conn = get_conn(db_path)
    sql = "SELECT * FROM reapplication_campaigns"
    params: list[Any] = []
    if statuses:
        sql += " WHERE status IN (" + ",".join("?" for _ in statuses) + ")"
        params.extend(sorted(statuses))
    sql += " ORDER BY id"
    rows = []
    for row in conn.execute(sql, params).fetchall():
        payload = dict(row)
        payload["route"] = json.loads(payload.pop("route_json"))
        rows.append(payload)
    conn.close()
    return rows


def get_next_attempt_due(settings: Mapping[str, Any]) -> str | None:
    db_path = _db_path(settings)
    init_db(db_path)
    conn = get_conn(db_path)
    row = conn.execute(
        """SELECT MIN(a.scheduled_at) AS scheduled_at
           FROM reapplication_attempts a
           JOIN reapplication_campaigns c ON c.id=a.campaign_id
           WHERE a.status='scheduled'
             AND c.status IN ('scheduled', 'next_scheduled')
             AND c.submit_authorized=1"""
    ).fetchone()
    conn.close()
    return str(row["scheduled_at"]) if row and row["scheduled_at"] else None


def seconds_until_next_attempt(settings: Mapping[str, Any]) -> float | None:
    value = get_next_attempt_due(settings)
    if not value:
        return None
    return max(0.0, (_parse_datetime(value) - datetime.now()).total_seconds())


def build_attempt_paths(attempt: Mapping[str, Any]) -> dict[str, Path]:
    campaign_id = int(attempt["campaign_id"])
    attempt_id = int(attempt["id"])
    site = str(attempt["site"]).upper()
    root = PROJECT_ROOT / "runtime" / "state" / "reapplications" / f"campaign_{campaign_id}"
    logs = PROJECT_ROOT / "runtime" / "logs" / "reapplications" / f"campaign_{campaign_id}"
    return {
        "state": root / f"attempt_{attempt_id}_{site}.json",
        "stdout": logs / f"attempt_{attempt_id}_{site}.out.log",
        "stderr": logs / f"attempt_{attempt_id}_{site}.err.log",
    }


def build_attempt_command(
    settings: Mapping[str, Any],
    attempt: Mapping[str, Any],
    *,
    python_executable: str | None = None,
) -> tuple[list[str], dict[str, Path]]:
    """Build the explicit one-account/one-brand real batch command."""

    if not int(attempt.get("submit_authorized") or 0):
        raise ValueError("Campaign does not authorize real submission")
    paths = build_attempt_paths(attempt)
    delay = float((settings.get("case_followup") or {}).get("delay_hours", 2.0))
    command = [
        python_executable or sys.executable,
        str(PROJECT_ROOT / "scripts" / "run_full_5461_batch.py"),
        "--accounts",
        str(attempt["account_id"]),
        "--brands",
        str(attempt["brand_name"]),
        "--site",
        str(attempt["site"]).upper(),
        "--state-file",
        str(paths["state"].relative_to(PROJECT_ROOT)),
        "--case-followup-delay-hours",
        str(delay),
        "--reapplication-campaign-id",
        str(attempt["campaign_id"]),
        "--reapplication-attempt-id",
        str(attempt["id"]),
        "--yes",
        "--no-confirm",
    ]
    return command, paths


def launch_reapplication_worker(
    settings: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    settings = settings or load_yaml(str(PROJECT_ROOT / "config" / "settings.yaml"))
    config = get_reapplication_config(settings)
    if not config["enabled"] or not config["auto_start_worker"]:
        return {"started": False, "reason": "disabled"}
    if get_next_attempt_due(settings) is None:
        return {"started": False, "reason": "no_scheduled_attempts"}
    state_dir = PROJECT_ROOT / "runtime" / "state"
    logs_dir = PROJECT_ROOT / "runtime" / "logs"
    state_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    meta_path = state_dir / "reapplication_worker.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            pid = int(meta.get("pid") or 0)
            if pid > 0:
                os.kill(pid, 0)
                return {"started": False, "reason": "already_running", **meta}
        except Exception:
            pass
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stdout_path = logs_dir / f"reapplication_worker_{stamp}.out.log"
    stderr_path = logs_dir / f"reapplication_worker_{stamp}.err.log"
    command = [sys.executable, str(PROJECT_ROOT / "scripts" / "run_reapplications.py"), "--watch"]
    popen_kwargs: dict[str, Any] = {
        "cwd": str(PROJECT_ROOT),
        "stdin": subprocess.DEVNULL,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    else:
        popen_kwargs["start_new_session"] = True
    with stdout_path.open("a", encoding="utf-8") as stdout_file, stderr_path.open(
        "a", encoding="utf-8"
    ) as stderr_file:
        proc = subprocess.Popen(
            command,
            stdout=stdout_file,
            stderr=stderr_file,
            **popen_kwargs,
        )
    meta = {
        "pid": proc.pid,
        "started_at": datetime.now().isoformat(),
        "stdout_log": str(stdout_path),
        "stderr_log": str(stderr_path),
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"started": True, **meta}
