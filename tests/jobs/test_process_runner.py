"""Windows process-window suppression for web-console automation jobs."""

from src.jobs import process_runner


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
