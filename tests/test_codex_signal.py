import json

from src.codex_signal import mark_signal_handled, read_signal, write_pending_signal


def test_signal_round_trip(tmp_path):
    path = tmp_path / "runtime" / "codex_signal.json"
    created = write_pending_signal(
        reason="stuck_at_form_loading",
        brand_data={"account_id": "test_account", "brand_name": "TEST", "site": "US"},
        evidence={"url": "https://example.test/page", "title": "Example", "screenshot_path": "shot.png"},
        step=7,
        signal_path=path,
    )

    assert created["status"] == "pending"
    assert read_signal(path) == created
    assert json.loads(path.read_text(encoding="utf-8"))["reason"] == "stuck_at_form_loading"

    handled = mark_signal_handled(note="diagnosed", signal_path=path)
    assert handled is not None
    assert handled["status"] == "handled"
    assert handled["note"] == "diagnosed"
    assert "handled_at" in handled


def test_missing_signal_is_none(tmp_path):
    path = tmp_path / "missing.json"
    assert read_signal(path) is None
    assert mark_signal_handled(signal_path=path) is None
