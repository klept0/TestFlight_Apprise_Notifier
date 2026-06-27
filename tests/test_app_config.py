"""Tests for per-app configuration (Runbook 2 — Task 10)."""

import asyncio
import os

os.environ.setdefault("APPRISE_URL", "json://localhost/")
os.environ.setdefault("ENABLE_UPDATE_CHECKER", "false")
os.environ.pop("WEB_USERNAME", None)
os.environ.pop("WEB_PASSWORD", None)

import pytest
from fastapi.testclient import TestClient

import app_config  # noqa: E402
import config_io  # noqa: E402
import main  # noqa: E402
import routes  # noqa: E402
import state  # noqa: E402
from utils.testflight import TestFlightStatus  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_app_config(tmp_path, monkeypatch):
    # Each test gets its own per-app config file and a clean in-memory store.
    monkeypatch.setattr(app_config, "APP_CONFIG_FILE", str(tmp_path / "app_config.json"))
    app_config._settings.clear()
    yield
    app_config._settings.clear()


@pytest.fixture
def client():
    c = TestClient(main.app)
    c.get("/")
    token = c.cookies.get("csrf_token")
    if token:
        c.headers.update({"X-CSRF-Token": token})
    return c


def _clear_runtime():
    for d in (main._previous_status, main._open_notified, main._last_check_ts):
        d.clear()


def _patch_fetch(monkeypatch, status, sent, names):
    async def fake_check(session, url):
        return {"status": status, "status_text": status.value, "app_name": "Detected"}

    async def fake_name(b, t):
        return "Detected"

    async def fake_icon(b, t):
        return "http://i/x.png"

    async def fake_link(b, t, name_override=None):
        names.append(name_override)
        return "msg"

    async def fake_send(*a, **k):
        sent.append(a)

    monkeypatch.setattr(main, "check_testflight_status", fake_check)
    monkeypatch.setattr(main, "get_app_name", fake_name)
    monkeypatch.setattr(main, "get_app_icon", fake_icon)
    monkeypatch.setattr(main, "format_notification_link", fake_link)
    monkeypatch.setattr(main, "send_notification_async", fake_send)
    monkeypatch.setattr(main, "ALWAYS_NOTIFY_OPEN", False)


# ── Backward compatibility / defaults ───────────────────────────
def test_unconfigured_id_uses_defaults():
    s = app_config.get("brandnew1")
    assert s == app_config.DEFAULTS
    assert s["enabled"] is True
    assert s["notify_on_open"] is True and s["notify_on_full"] is True
    assert s["notify_on_closed"] is False


# ── Enabled / disabled ──────────────────────────────────────────
def test_disabled_app_is_skipped(monkeypatch):
    _clear_runtime()
    app_config.update("disabled1", {"enabled": False})

    checked = []

    async def fake_fetch(session, tf_id):
        checked.append(tf_id)

    async def fake_session():
        return object()

    monkeypatch.setattr(main, "get_current_id_list", lambda: ["disabled1", "enabled01"])
    monkeypatch.setattr(main, "get_http_session", fake_session)
    monkeypatch.setattr(main, "fetch_testflight_status", fake_fetch)

    asyncio.run(main.watch())
    assert "enabled01" in checked  # other apps keep checking
    assert "disabled1" not in checked  # disabled app is not checked


# ── Friendly name ───────────────────────────────────────────────
def test_friendly_name_overrides_in_details(client, monkeypatch):
    async def fake_name(b, t):
        return "Detected"

    async def fake_icon(b, t):
        return "ic"

    monkeypatch.setattr(routes, "get_app_name", fake_name)
    monkeypatch.setattr(routes, "get_app_icon", fake_icon)
    saved = list(state.current_id_list)
    try:
        state.current_id_list[:] = ["friendly1"]
        app_config.update("friendly1", {"friendly_name": "My Cool App"})
        item = client.get("/api/testflight-ids/details").json()["testflight_ids"][0]
        assert item["display_name"] == "My Cool App"
        assert item["settings"]["friendly_name"] == "My Cool App"
    finally:
        state.current_id_list[:] = saved


def test_friendly_name_used_in_notification(monkeypatch):
    _clear_runtime()
    app_config.update("friendly2", {"friendly_name": "Friendly!"})
    sent, names = [], []
    _patch_fetch(monkeypatch, TestFlightStatus.OPEN, sent, names)
    asyncio.run(main.fetch_testflight_status(None, "friendly2"))
    assert len(sent) == 1
    assert names == ["Friendly!"]  # passed to the message builder


def test_no_friendly_name_uses_detected(monkeypatch):
    _clear_runtime()
    sent, names = [], []
    _patch_fetch(monkeypatch, TestFlightStatus.OPEN, sent, names)
    asyncio.run(main.fetch_testflight_status(None, "plainid01"))
    assert len(sent) == 1
    assert names == [None]  # no override -> detected name used (default behavior)


# ── Notification toggles ────────────────────────────────────────
def test_default_open_notifies(monkeypatch):
    _clear_runtime()
    sent, names = [], []
    _patch_fetch(monkeypatch, TestFlightStatus.OPEN, sent, names)
    asyncio.run(main.fetch_testflight_status(None, "defopen01"))
    assert len(sent) == 1  # default OPEN behavior preserved


def test_open_notification_can_be_disabled(monkeypatch):
    _clear_runtime()
    app_config.update("noopen01", {"notify_on_open": False})
    sent, names = [], []
    _patch_fetch(monkeypatch, TestFlightStatus.OPEN, sent, names)
    asyncio.run(main.fetch_testflight_status(None, "noopen01"))
    assert sent == []  # OPEN notification suppressed


def test_closed_off_by_default(monkeypatch):
    _clear_runtime()
    main._previous_status["closeflt1"] = TestFlightStatus.OPEN  # OPEN -> CLOSED transition
    sent, names = [], []
    _patch_fetch(monkeypatch, TestFlightStatus.CLOSED, sent, names)
    asyncio.run(main.fetch_testflight_status(None, "closeflt1"))
    assert sent == []  # closed notifications off by default (preserves behavior)


def test_closed_notifies_when_enabled(monkeypatch):
    _clear_runtime()
    app_config.update("closeon01", {"notify_on_closed": True})
    main._previous_status["closeon01"] = TestFlightStatus.OPEN
    sent, names = [], []
    _patch_fetch(monkeypatch, TestFlightStatus.CLOSED, sent, names)
    asyncio.run(main.fetch_testflight_status(None, "closeon01"))
    assert len(sent) == 1  # opt-in closed notification fires on transition


# ── Import / export ─────────────────────────────────────────────
def test_export_includes_app_settings(client):
    app_config.update("exp00001", {"enabled": False, "friendly_name": "X"})
    body = client.get("/api/config/export").json()
    assert "app_settings" in body
    assert body["app_settings"]["exp00001"]["enabled"] is False


def test_import_accepts_app_settings(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("ID_LIST=x,\nAPPRISE_URL=discord://a/b,\n")
    data = {
        "version": 1,
        "app_settings": {"impid001": {"enabled": False, "notify_on_full": False}},
    }
    normalized, error = config_io.validate_import(data)
    assert error is None
    assert config_io.apply_import(normalized) is True
    assert app_config.get("impid001")["enabled"] is False
    assert app_config.get("impid001")["notify_on_full"] is False


def test_import_rejects_invalid_app_settings():
    normalized, error = config_io.validate_import(
        {"app_settings": {"bad1": {"enabled": "yes"}}}
    )
    assert normalized is None and "enabled" in error


# ── Validation / endpoint ───────────────────────────────────────
def test_invalid_settings_rejected():
    ok, err = app_config.update("badid001", {"enabled": "yes"})
    assert ok is False and "boolean" in err
    ok, err = app_config.update("badid002", {"check_interval_seconds": 0})
    assert ok is False
    ok, err = app_config.update("badid003", {"unknown_key": 1})
    assert ok is False and "Unknown" in err


def test_settings_endpoint_valid(client):
    saved = list(state.current_id_list)
    try:
        state.current_id_list[:] = ["okid0001"]
        r = client.post(
            "/api/testflight-ids/okid0001/settings",
            json={"friendly_name": "Nice", "enabled": False},
        )
        assert r.status_code == 200, r.text
        assert r.json()["settings"]["friendly_name"] == "Nice"
        assert app_config.get("okid0001")["enabled"] is False
    finally:
        state.current_id_list[:] = saved


def test_settings_endpoint_rejects_invalid(client):
    saved = list(state.current_id_list)
    try:
        state.current_id_list[:] = ["epid0001"]
        r = client.post("/api/testflight-ids/epid0001/settings", json={"enabled": "nope"})
        assert r.status_code == 400
    finally:
        state.current_id_list[:] = saved


def test_settings_endpoint_unknown_id(client):
    assert client.post(
        "/api/testflight-ids/nosuchid/settings", json={"enabled": True}
    ).status_code == 404


def test_settings_endpoint_requires_csrf():
    c = TestClient(main.app)  # fresh client, no CSRF token
    saved = list(state.current_id_list)
    try:
        state.current_id_list[:] = ["csrfid01"]
        assert c.post(
            "/api/testflight-ids/csrfid01/settings", json={"enabled": True}
        ).status_code == 403
    finally:
        state.current_id_list[:] = saved
