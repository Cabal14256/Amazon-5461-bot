"""Stage-6 auto-triage: scan open repair incidents and trigger read-only triage.

Runs inside the web process as a background asyncio loop (same pattern as
the stage-3 job dispatcher).  An incident is a candidate when it is ``open``,
classified as a fix candidate by the detector (human-handling classes such as
CAPTCHA/2FA/rate-limit never auto-trigger), at or above
``codex.auto_triage_min_confidence``, and has not been triaged since its last
occurrence — a failed triage is retried only after a recurrence or a manual
trigger.  Auto-trigger failures only produce audit rows; nothing is fed back
into the detector and the main automation is never affected.
"""

from __future__ import annotations

import asyncio
import logging

from src.codex_client.availability import check_availability
from src.codex_client.triage import run_triage
from src.db import (
    count_triage_jobs_today,
    get_latest_triage_job,
    list_auto_triage_candidates,
)

logger = logging.getLogger(__name__)


def run_auto_triage_scan(settings, *, availability=None) -> list[dict]:
    """One serial scan pass; returns the run_triage outcomes (for tests)."""
    if not bool(getattr(settings, "codex_enabled", True)):
        return []
    if not bool(getattr(settings, "incidents_enabled", True)):
        return []
    db_path = str(settings.db_path)

    probe = availability or check_availability(settings.codex_command)
    if not probe.available:
        return []

    outcomes: list[dict] = []
    candidates = list_auto_triage_candidates(
        db_path, float(settings.codex_auto_triage_min_confidence)
    )
    for incident in candidates:
        if count_triage_jobs_today(db_path) >= int(settings.codex_daily_call_limit):
            break
        latest = get_latest_triage_job(db_path, int(incident["id"]))
        if latest is not None:
            # Already triaged (or attempted) since the last occurrence; a
            # recurrence refreshes last_seen_at and re-arms auto-triage once.
            if str(latest["status"]) == "running":
                continue
            if str(latest["created_at"] or "") >= str(incident["last_seen_at"] or ""):
                continue
        try:
            outcomes.append(run_triage(settings, int(incident["id"]), trigger="auto"))
        except Exception:  # noqa: BLE001 — auto triage must never break the loop
            logger.exception("[codex] 自动判因失败 incident=%s", incident["id"])
    return outcomes


class AutoTriageRunner:
    """Asyncio background loop mirroring the stage-3 JobManager lifecycle."""

    def __init__(self, settings, tick_seconds: float | None = None) -> None:
        self.settings = settings
        self.tick_seconds = float(
            tick_seconds or getattr(settings, "codex_auto_triage_poll_seconds", 15.0)
        )
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run_loop(self) -> None:
        while True:
            try:
                await asyncio.to_thread(run_auto_triage_scan, self.settings)
            except Exception:  # keep the loop alive; next tick retries
                logger.exception("[codex] 自动判因扫描失败")
            await asyncio.sleep(self.tick_seconds)
