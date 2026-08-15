"""Shared fixtures for automation-job tests.

Everything runs against temporary directories: the real
``runtime/private/accounts.json``, the real ``ledger.db`` and the real
AdsPower service are never touched.  Child processes are fake
``python -c`` scripts launched through an injectable process factory.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.db import init_db  # noqa: E402
from src.web.config import WebSettings  # noqa: E402

TEST_PASSWORD = "test-only-password-123"
TEST_SESSION_SECRET = "0123456789abcdef" * 4

ACTIVE_ACCOUNT = "us_store_999"
PAUSED_ACCOUNT = "uk_store_998"
PROFILE_ID = "fixture-profile-id-xyz"


@pytest.fixture()
def job_env(tmp_path):
    """Temporary WebSettings + initialized DB + account/brand/site catalogs."""
    runtime = tmp_path / "runtime"
    db_path = runtime / "state" / "ledger.db"
    init_db(str(db_path))

    private = runtime / "private"
    private.mkdir(parents=True)
    accounts_path = private / "accounts.json"
    accounts_path.write_text(json.dumps({
        "accounts": [
            {
                "account_id": ACTIVE_ACCOUNT,
                "marketplace": "US",
                "status": "active",
                "username": "fixture-secret-user@example.com",
                "password": "fixture-account-password",
                "adspower_profile_id": PROFILE_ID,
            },
            {
                "account_id": PAUSED_ACCOUNT,
                "marketplace": "UK",
                "status": "paused",
                "adspower_profile_id": "fixture-profile-id-2",
            },
        ]
    }, ensure_ascii=False), encoding="utf-8")

    marketplaces_dir = tmp_path / "marketplaces"
    marketplaces_dir.mkdir()
    (marketplaces_dir / "us.yaml").write_text('marketplace: "US"\n', encoding="utf-8")
    (marketplaces_dir / "uk.yaml").write_text('marketplace: "UK"\n', encoding="utf-8")

    brand_packs_root = tmp_path / "brand_packs"
    (brand_packs_root / "TESTBRAND").mkdir(parents=True)
    (brand_packs_root / "TESTBRAND2").mkdir(parents=True)

    settings = WebSettings(
        db_path=db_path,
        accounts_path=accounts_path,
        marketplaces_dir=marketplaces_dir,
        brand_packs_root=brand_packs_root,
        evidence_root=runtime / "evidence",
        logs_root=runtime / "logs",
        state_root=runtime / "state",
        data_root=tmp_path / "data",
        frontend_dist=tmp_path / "no_frontend_dist",
        codex_signal_path=runtime / "codex_signal.json",
        session_secret=TEST_SESSION_SECRET,
        sse_poll_seconds=0.05,
        job_tick_seconds=0.05,
        # Never let a test app probe or spawn a real Codex CLI.
        codex_enabled=False,
    )
    return settings


def make_fake_process_factory(
    *,
    delay: float = 0.2,
    exit_code: int = 0,
    stdout_text: str = "fake worker line",
    batch_state: dict | None = None,
):
    """Return a process factory launching a real (fake) ``python -c`` child.

    The child sleeps ``delay`` seconds, optionally writes ``batch_state`` to
    the ``--state-file`` argument found in the command, prints to stdout and
    exits with ``exit_code`` — exercising real PID/poll semantics.
    """

    def factory(cmd, *, stdout_path, stderr_path, env, cwd=None):
        state_file = ""
        if "--state-file" in cmd:
            state_file = cmd[cmd.index("--state-file") + 1]
        script = (
            "import sys, time, json, os\n"
            f"time.sleep({delay!r})\n"
            f"_sf = {state_file!r}\n"
            "_bs = " + repr(batch_state) + "\n"
            "if _sf and _bs:\n"
            "    open(_sf, 'w', encoding='utf-8').write(json.dumps(_bs))\n"
            f"print({stdout_text!r}, flush=True)\n"
            f"sys.exit({exit_code!r})\n"
        )
        stdout_handle = open(stdout_path, "ab", buffering=0)
        stderr_handle = open(stderr_path, "ab", buffering=0)
        try:
            proc = subprocess.Popen(
                [sys.executable, "-c", script],
                stdout=stdout_handle,
                stderr=stderr_handle,
                stdin=subprocess.DEVNULL,
                env=env,
            )
        finally:
            stdout_handle.close()
            stderr_handle.close()
        return proc

    return factory


def dead_pid() -> int:
    """A PID that was real but is guaranteed gone by the time it is used."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def wait_for(predicate, timeout: float = 10.0, interval: float = 0.05) -> bool:
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()
