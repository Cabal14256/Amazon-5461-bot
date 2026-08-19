"""Windows process-window suppression for web-console automation jobs."""

from src.jobs import process_runner
from src.jobs.paths import job_paths


class _FakeProcess:
    pid = 4321


def test_spawn_hidden_process_forwards_hidden_startup_settings(monkeypatch, tmp_path):
    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured.update(kwargs)
        return _FakeProcess()

    hidden_startup = object()
    monkeypatch.setattr(
        process_runner,
        "no_window_kwargs",
        lambda: {"creationflags": 0x08000000, "startupinfo": hidden_startup},
    )
    monkeypatch.setattr(process_runner.subprocess, "Popen", fake_popen)

    proc = process_runner.spawn_hidden_process(
        ["python.exe", "-c", "pass"],
        stdout_path=tmp_path / "job.out.log",
        stderr_path=tmp_path / "job.err.log",
        env={"PYTHONUTF8": "1"},
        cwd=tmp_path,
    )

    assert proc.pid == 4321
    assert captured["creationflags"] & 0x08000000
    assert captured["startupinfo"] is hidden_startup
    assert captured["stdin"] is process_runner.subprocess.DEVNULL
    assert captured["cwd"] == str(tmp_path)


def test_submit_command_maps_only_allowlisted_options(tmp_path):
    paths = job_paths(tmp_path / "state", tmp_path / "logs", tmp_path / "evidence", "job-options")
    cmd = process_runner.build_job_command(
        {
            "job_type": "submit",
            "account_id": "fixture-account",
            "marketplace": "US",
            "brands": ["FIXTURE"],
            "options": {
                "case_followup_delay_hours": 3.5,
                "case_followup_enabled": False,
            },
        },
        paths,
    )
    assert cmd[cmd.index("--case-followup-delay-hours") + 1] == "3.5"
    assert "--disable-case-followup" in cmd


def test_command_rejects_options_for_non_submit_job(tmp_path):
    paths = job_paths(tmp_path / "state", tmp_path / "logs", tmp_path / "evidence", "job-invalid")
    try:
        process_runner.build_job_command(
            {
                "job_type": "dry_run",
                "account_id": "fixture-account",
                "brands": ["FIXTURE"],
                "options": {"case_followup_enabled": False},
            },
            paths,
        )
    except ValueError as exc:
        assert str(exc) == "job_options_not_supported"
    else:
        raise AssertionError("non-submit options should be rejected")
