"""FastAPI application factory for the web console.

Stage-2 boundary: business reads require at least the ``viewer`` role; web
user management requires ``admin``.

Stage-3 boundary: the console may create and manage *diagnose* and
*dry_run* automation jobs only (operator+), stop them safely (operator+) and
force-terminate them (admin).

Stage-4 boundary: real submissions additionally exist, but only behind the
``submit_enabled`` master switch and the explicit single-step entry —
``POST /api/jobs/submit`` (reviewer+) validates, preflights and creates the
job directly in ``queued``.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from src.codex_client.auto_triage import AutoTriageRunner
from src.db import init_db
from src.jobs.manager import JobManager
from src.repair.runner import RepairWorkflowRunner
from src.web.api import (
    applications,
    auth,
    case_followups,
    case_id_recoveries,
    catalog,
    evidence,
    health,
    incidents,
    jobs,
    overview,
    reapplications,
    repair_jobs,
    submit,
    users,
)
from src.web.auth import LoginRateLimiter
from src.web.config import WebSettings, load_settings
from src.web.deps import require_role
from src.web.loop_noise import install_loop_noise_filter


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'",
        )
        return response


def create_app(
    settings: WebSettings | None = None,
    job_manager: JobManager | None = None,
    repair_runner: RepairWorkflowRunner | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    # Idempotent migration: adds web/job tables on existing DBs.
    init_db(str(settings.db_path))

    manager = job_manager or JobManager(settings, tick_seconds=settings.job_tick_seconds)
    triage_runner = AutoTriageRunner(settings)
    workflow_runner = repair_runner or RepairWorkflowRunner(
        settings, tick_seconds=settings.codex_workflow_poll_seconds
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Windows Proactor transports can raise ConnectionResetError while
        # cleaning up an HTTPS socket that the browser already closed. Keep
        # those expected teardown callbacks out of stderr without hiding any
        # other event-loop error.
        install_loop_noise_filter(asyncio.get_running_loop())
        # Reconcile DB state with process liveness *before* dispatching.
        manager.recover()
        manager.start()
        # Stage-6 auto-triage scanner: fully degraded no-op while disabled.
        if settings.codex_enabled and settings.incidents_enabled:
            triage_runner.start()
        if settings.codex_workflow_enabled and settings.incidents_enabled:
            workflow_runner.recover()
            workflow_runner.start()
        yield
        await workflow_runner.stop()
        await triage_runner.stop()
        # Cancel the dispatch loop only; running child processes keep going
        # and will be reconciled by recover() on the next startup.
        await manager.stop()

    app = FastAPI(title="Amazon 5461 Web Console", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.settings = settings
    app.state.rate_limiter = LoginRateLimiter()
    app.state.job_manager = manager
    app.state.triage_runner = triage_runner
    app.state.repair_runner = workflow_runner
    app.add_middleware(SecurityHeadersMiddleware)

    viewer = [Depends(require_role("viewer"))]
    admin = [Depends(require_role("admin"))]

    app.include_router(auth.router, prefix="/api")
    app.include_router(health.router, prefix="/api", dependencies=viewer)
    app.include_router(catalog.router, prefix="/api", dependencies=viewer)
    app.include_router(jobs.router, prefix="/api", dependencies=viewer)
    app.include_router(submit.router, prefix="/api", dependencies=viewer)
    app.include_router(applications.router, prefix="/api", dependencies=viewer)
    app.include_router(case_followups.router, prefix="/api", dependencies=viewer)
    app.include_router(case_id_recoveries.router, prefix="/api", dependencies=viewer)
    app.include_router(reapplications.router, prefix="/api", dependencies=viewer)
    app.include_router(overview.router, prefix="/api", dependencies=viewer)
    app.include_router(evidence.router, prefix="/api", dependencies=viewer)
    app.include_router(incidents.router, prefix="/api", dependencies=viewer)
    app.include_router(repair_jobs.router, prefix="/api", dependencies=viewer)
    app.include_router(users.router, prefix="/api", dependencies=admin)

    # Static frontend last so /api routes always win; skip silently when the
    # frontend bundle has not been built yet.
    if settings.frontend_dist.is_dir():
        index_html = settings.frontend_dist / "index.html"
        app.mount("/assets", StaticFiles(directory=str(settings.frontend_dist / "assets")), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str):
            # Client-side routes (/jobs, /login, ...) have no file on disk;
            # serve index.html and let the Vue router resolve them.
            candidate = (settings.frontend_dist / full_path).resolve()
            if full_path and candidate.is_file() and candidate.is_relative_to(settings.frontend_dist.resolve()):
                return FileResponse(str(candidate))
            return FileResponse(str(index_html))

    return app
