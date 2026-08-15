"""GET /api/overview — aggregated dashboard payload."""

from __future__ import annotations

from fastapi import APIRouter, Request

from src.db import count_open_incidents, count_running_automation_jobs, get_conn
from src.state_files import read_json_tolerant
from src.web.deps import get_settings

router = APIRouter(prefix="/overview", tags=["overview"])


def _scalar(conn, sql: str, params: tuple = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0] or 0)


@router.get("")
def get_overview(request: Request):
    settings = get_settings(request)
    db_path = str(settings.db_path)

    status_counts: dict[str, int] = {}
    pending_case_followups = 0
    pending_recoveries = 0
    manual_review_followups = 0
    active_campaigns = 0
    campaigns_by_status: dict[str, int] = {}
    try:
        conn = get_conn(db_path)
        for row in conn.execute(
            "SELECT current_status, COUNT(*) FROM brand_status_snapshot GROUP BY current_status"
        ).fetchall():
            status_counts[str(row[0])] = int(row[1])
        pending_case_followups = _scalar(
            conn, "SELECT COUNT(*) FROM case_followups WHERE status IN ('pending','retry','running')"
        )
        manual_review_followups = _scalar(
            conn, "SELECT COUNT(*) FROM case_followups WHERE status IN ('manual_review','blocked')"
        )
        pending_recoveries = _scalar(
            conn, "SELECT COUNT(*) FROM case_id_recoveries WHERE status IN ('pending','retry','running')"
        )
        for row in conn.execute(
            "SELECT status, COUNT(*) FROM reapplication_campaigns GROUP BY status"
        ).fetchall():
            campaigns_by_status[str(row[0])] = int(row[1])
        active_campaigns = _scalar(
            conn,
            """SELECT COUNT(*) FROM reapplication_campaigns
               WHERE status IN ('scheduled','next_scheduled','running','waiting_case_id',
                                'waiting_case','paused','blocked')""",
        )
        conn.close()
    except Exception:
        pass

    try:
        running_jobs = count_running_automation_jobs(db_path)
    except Exception:
        running_jobs = 0

    try:
        incidents_open = count_open_incidents(db_path)
    except Exception:
        incidents_open = 0

    signal = read_json_tolerant(settings.codex_signal_path)
    codex_signal_pending = bool(isinstance(signal, dict) and signal.get("status") == "pending")

    return {
        "brand_status_counts": status_counts,
        "pending_case_followups": pending_case_followups,
        "manual_review_followups": manual_review_followups,
        "pending_case_id_recoveries": pending_recoveries,
        "active_reapplication_campaigns": active_campaigns,
        "reapplication_campaigns_by_status": campaigns_by_status,
        "running_jobs": running_jobs,
        "codex_signal_pending": codex_signal_pending,
        "incidents_open": incidents_open,
    }
