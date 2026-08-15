"""Batch worker safe-stop check (AMAZON5461_STOP_FILE sentinel).

The helpers live in ``scripts/_stop_check.py`` (side-effect free) and are
wired into the brand loop of ``scripts/run_full_5461_batch.py``.
"""

import importlib

import pytest


@pytest.fixture()
def stop_check():
    return importlib.import_module("scripts._stop_check")


def test_stop_file_requested_env(monkeypatch, tmp_path, stop_check):
    monkeypatch.delenv("AMAZON5461_STOP_FILE", raising=False)
    assert stop_check.stop_file_requested() is False

    sentinel = tmp_path / "STOP_REQUESTED"
    monkeypatch.setenv("AMAZON5461_STOP_FILE", str(sentinel))
    assert stop_check.stop_file_requested() is False
    sentinel.touch()
    assert stop_check.stop_file_requested() is True

    monkeypatch.setenv("AMAZON5461_STOP_FILE", str(tmp_path / "missing" / "STOP"))
    assert stop_check.stop_file_requested() is False


def test_apply_stop_request_marks_remaining_skipped(stop_check):
    state = {
        "summary": {"total": 3, "completed": 1, "failed": 0, "pending": 2},
    }
    items = [
        {"brand_name": "BRAND_A", "status": "completed"},
        {"brand_name": "BRAND_B", "status": "running"},
        {"brand_name": "BRAND_C", "status": "pending"},
    ]
    skipped = stop_check.apply_stop_request(state, items)
    assert skipped == 2
    assert items[0]["status"] == "completed"  # finished items untouched
    assert items[1]["status"] == "skipped"
    assert items[1]["error"] == "stop_requested"
    assert items[2]["status"] == "skipped"
    assert state["summary"]["pending"] == 0


def test_apply_stop_request_without_pending(stop_check):
    state = {"summary": {"total": 1, "completed": 1, "failed": 0, "pending": 0}}
    items = [{"brand_name": "BRAND_A", "status": "completed"}]
    assert stop_check.apply_stop_request(state, items) == 0
    assert items[0]["status"] == "completed"


def test_batch_script_wires_stop_check():
    """The brand loop of run_full_5461_batch.py checks the sentinel at its top."""
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent.parent
              / "scripts" / "run_full_5461_batch.py").read_text(encoding="utf-8")
    assert "from scripts._stop_check import" in source
    loop_pos = source.index("for item in items:")
    check_pos = source.index("stop_file_requested()")
    assert 0 < check_pos < source.index("group_key", loop_pos)
