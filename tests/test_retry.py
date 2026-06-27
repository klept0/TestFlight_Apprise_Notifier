"""Tests for per-ID retry/backoff scheduling (Runbook 2 — Task 8)."""

import asyncio
import os
import time

os.environ.setdefault("APPRISE_URL", "json://localhost/")

import main  # noqa: E402
from utils.testflight import TestFlightStatus  # noqa: E402


def _clear():
    for d in (
        main._previous_status,
        main._open_notified,
        main._failure_count,
        main._last_success_ts,
        main._last_notification_ts,
        main._last_failure_ts,
        main._next_check_ts,
        main._backoff_delay,
    ):
        d.clear()


def test_failure_increases_backoff():
    _clear()
    d1 = main._record_failure("idfail01")
    assert main._failure_count["idfail01"] == 1
    assert d1 >= main.RETRY_BACKOFF_BASE
    assert main._next_check_ts["idfail01"] > time.time()
    assert main._last_failure_ts["idfail01"] > 0

    d2 = main._record_failure("idfail01")
    assert main._failure_count["idfail01"] == 2
    assert d2 > d1  # exponential growth


def test_backoff_is_capped_at_max():
    _clear()
    for _ in range(40):  # drive the failure count very high
        main._record_failure("idcap001")
    # Capped at MAX (plus at most the 10% jitter).
    assert main._backoff_delay["idcap001"] <= main.RETRY_BACKOFF_MAX * 1.1 + 1


def test_success_resets_backoff():
    _clear()
    main._record_failure("idok0001")
    assert main._backoff_delay["idok0001"] > 0
    assert "idok0001" in main._next_check_ts

    prev = main._record_success("idok0001", TestFlightStatus.OPEN)
    assert prev is None
    assert main._failure_count["idok0001"] == 0
    assert main._backoff_delay["idok0001"] == 0.0
    assert "idok0001" not in main._next_check_ts  # next eligible retry cleared


def test_cooldown_skips_only_failing_id(monkeypatch):
    _clear()
    now = time.time()
    main._next_check_ts["coolID01"] = now + 1000  # still cooling down
    # "okID0001" has no next_check_ts -> eligible immediately.

    checked = []

    async def fake_fetch(session, tf_id):
        checked.append(tf_id)

    async def fake_session():
        return object()

    monkeypatch.setattr(main, "get_current_id_list", lambda: ["coolID01", "okID0001"])
    monkeypatch.setattr(main, "get_http_session", fake_session)
    monkeypatch.setattr(main, "fetch_testflight_status", fake_fetch)

    asyncio.run(main.watch())

    assert "okID0001" in checked  # other IDs keep checking
    assert "coolID01" not in checked  # the cooling-down ID is skipped
    # Skipping must NOT be treated as a new failure.
    assert "coolID01" not in main._failure_count


def test_backoff_state_survives_restart():
    _clear()
    main._record_failure("idpersist")
    snap = main.snapshot_runtime_state()
    rec = snap["apps"]["idpersist"]
    assert rec["failure_count"] == 1
    assert rec["next_check_ts"] is not None
    assert rec["backoff_delay"] > 0

    _clear()
    main.restore_runtime_state(snap)
    assert main._failure_count["idpersist"] == 1
    assert main._next_check_ts["idpersist"] == rec["next_check_ts"]
    assert main._backoff_delay["idpersist"] == rec["backoff_delay"]
    # Still in cooldown immediately after restore.
    assert main._is_in_cooldown("idpersist", time.time()) is True
