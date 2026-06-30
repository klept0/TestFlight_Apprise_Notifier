"""Tests for runtime state persistence (persistence.py + main integration)."""

import asyncio
import os

os.environ.setdefault("APPRISE_URL", "json://localhost/")

import persistence  # noqa: E402
import main  # noqa: E402
from utils.testflight import TestFlightStatus  # noqa: E402


def _clear_live_state():
    main._previous_status.clear()
    main._open_notified.clear()
    main._failure_count.clear()
    main._last_success_ts.clear()
    main._last_notification_ts.clear()
    main.app_name_cache.cache.clear()
    main.app_icon_cache.cache.clear()


# ── persistence.py: file handling ───────────────────────────────
def test_initial_state_creation(tmp_path, monkeypatch):
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(persistence, "STATE_FILE", str(state_file))
    assert not state_file.exists()
    assert persistence.save_state({"version": 1, "apps": {}}) is True
    assert state_file.exists()
    # A missing file loads as empty (no error) so startup can proceed.
    state_file.unlink()
    assert persistence.load_state() == {}


def test_state_persistence_roundtrip(tmp_path, monkeypatch):
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(persistence, "STATE_FILE", str(state_file))
    data = {"version": 1, "apps": {"abc12345": {"status": "open", "failure_count": 2}}}
    assert persistence.save_state(data) is True
    assert persistence.load_state() == data
    # Atomic write leaves no temp files behind.
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]


def test_corrupt_state_recovery(tmp_path, monkeypatch):
    state_file = tmp_path / "state.json"
    state_file.write_text("{ not valid json :::")
    monkeypatch.setattr(persistence, "STATE_FILE", str(state_file))
    # Must not raise; returns empty so the app can still start.
    assert persistence.load_state() == {}
    # A non-object payload is also ignored.
    state_file.write_text("[1, 2, 3]")
    assert persistence.load_state() == {}


# ── main.py: snapshot / restore ─────────────────────────────────
def test_snapshot_and_restore_roundtrip():
    _clear_live_state()
    main._previous_status["t1abcdef"] = TestFlightStatus.FULL
    main._open_notified["t1abcdef"] = True
    main._failure_count["t1abcdef"] = 4
    main._last_success_ts["t1abcdef"] = 222.0
    main._last_notification_ts["t1abcdef"] = 111.0
    main.app_name_cache.put(f"{main.TESTFLIGHT_URL}:t1abcdef", "My App")
    main.app_icon_cache.put(f"{main.TESTFLIGHT_URL}:t1abcdef", "http://i/x.png")

    snap = main.snapshot_runtime_state()
    rec = snap["apps"]["t1abcdef"]
    assert rec["status"] == "full"
    assert rec["notified_open"] is True
    assert rec["failure_count"] == 4
    assert rec["app_name"] == "My App"
    assert rec["icon_url"] == "http://i/x.png"

    _clear_live_state()
    main.restore_runtime_state(snap)
    assert main._previous_status["t1abcdef"] == TestFlightStatus.FULL
    assert main._open_notified["t1abcdef"] is True
    assert main._failure_count["t1abcdef"] == 4
    assert main._last_notification_ts["t1abcdef"] == 111.0
    key = f"{main.TESTFLIGHT_URL}:t1abcdef"
    assert main.app_name_cache.get(key) == "My App"
    assert main.app_icon_cache.get(key) == "http://i/x.png"


def test_restore_skips_corrupt_entries():
    _clear_live_state()
    snap = {
        "apps": {
            "good1234": {"status": "open", "notified_open": True},
            "bad12345": {"status": "not-a-real-status", "failure_count": "oops"},
            "weird999": "not-a-dict",
        }
    }
    # Must not raise; the good entry is restored.
    main.restore_runtime_state(snap)
    assert main._open_notified.get("good1234") is True


# ── Duplicate-notification prevention after restart ─────────────
def _patch_fetch_deps(monkeypatch, sent, status=TestFlightStatus.OPEN):
    async def fake_check(session, url):
        return {"status": status, "status_text": status.value, "app_name": "App"}

    async def fake_name(base, tf):
        return "App"

    async def fake_icon(base, tf):
        return "http://i/x.png"

    async def fake_link(base, tf, name_override=None):
        return "Beta is OPEN"

    async def fake_send(*a, **k):
        sent.append(a)

    monkeypatch.setattr(main, "check_testflight_status", fake_check)
    monkeypatch.setattr(main, "get_app_name", fake_name)
    monkeypatch.setattr(main, "get_app_icon", fake_icon)
    monkeypatch.setattr(main, "format_notification_link", fake_link)
    monkeypatch.setattr(main, "send_notification_async", fake_send)
    monkeypatch.setattr(main, "ALWAYS_NOTIFY_OPEN", False)


def test_no_duplicate_notification_after_restart(monkeypatch):
    _clear_live_state()
    # Simulate restored state: this ID was already notified as OPEN.
    main.restore_runtime_state(
        {"apps": {"dup12345": {"status": "open", "notified_open": True}}}
    )

    sent = []
    _patch_fetch_deps(monkeypatch, sent)
    asyncio.run(main.fetch_testflight_status(session=None, tf_id="dup12345"))
    assert sent == []  # no duplicate notification after restart

    # A fresh ID (never notified) still notifies on its first OPEN.
    asyncio.run(main.fetch_testflight_status(session=None, tf_id="fresh999"))
    assert len(sent) == 1
