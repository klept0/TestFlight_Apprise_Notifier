"""
API-level tests for the FastAPI dashboard.

These drive the app via Starlette's TestClient and avoid real network calls by
monkeypatching the TestFlight validation and the .env writer / notifier. They
cover the read endpoints, input validation, the add/remove flows, and the
optional HTTP Basic auth.
"""

import asyncio
import importlib
import os
import urllib.parse

import pytest
from fastapi.testclient import TestClient

# The app reads required config at import time, so set it before importing.
os.environ.setdefault("APPRISE_URL", "json://localhost/")
os.environ.setdefault("ENABLE_UPDATE_CHECKER", "false")
# Default import: auth disabled (the auth test reloads with it enabled).
os.environ.pop("WEB_USERNAME", None)
os.environ.pop("WEB_PASSWORD", None)

import main  # noqa: E402
import state  # noqa: E402
import routes  # noqa: E402


@pytest.fixture
def client():
    # No context manager -> lifespan startup is skipped, so no background
    # update task / outbound calls are spawned during tests.
    return TestClient(main.app)


# ── Read endpoints ──────────────────────────────────────────────
def test_health_is_open_and_well_formed(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "healthy"
    assert "version" in body and "uptime_seconds" in body


def test_metrics_shape(client):
    r = client.get("/api/metrics")
    assert r.status_code == 200
    assert "total_checks" in r.json()


def test_list_ids(client):
    r = client.get("/api/testflight-ids")
    assert r.status_code == 200
    assert "testflight_ids" in r.json()


def test_apprise_urls_list(client):
    r = client.get("/api/apprise-urls")
    assert r.status_code == 200
    assert "apprise_urls" in r.json()


# ── Input validation ────────────────────────────────────────────
def test_logs_limit_validation(client):
    assert client.get("/api/logs?limit=0").status_code == 400
    assert client.get("/api/logs?limit=100000").status_code == 400
    r = client.get("/api/logs?limit=5")
    assert r.status_code == 200 and "logs" in r.json()


def test_validate_bad_format_does_not_hit_network(client):
    # An invalid format is rejected before any HTTP request is made.
    r = client.post("/api/testflight-ids/validate", json={"id": "!!bad!!"})
    assert r.status_code == 200
    assert r.json()["valid"] is False


# ── Add / remove flows (network + .env writes stubbed) ──────────
def test_add_and_remove_testflight_id(client, monkeypatch):
    async def fake_validate(tf_id):
        return True, "ok"

    monkeypatch.setattr(routes, "validate_testflight_id", fake_validate)
    monkeypatch.setattr(state, "update_env_file", lambda *a, **k: True)
    monkeypatch.setattr(state, "send_notification", lambda *a, **k: None)

    test_id = "abcd1234"
    state.current_id_list[:] = [x for x in state.current_id_list if x != test_id]

    r = client.post("/api/testflight-ids", json={"id": test_id})
    assert r.status_code == 200, r.text
    assert test_id in r.json()["testflight_ids"]

    r = client.delete(f"/api/testflight-ids/{test_id}")
    assert r.status_code == 200
    assert test_id not in r.json()["testflight_ids"]


def test_remove_unknown_id_404(client):
    r = client.delete("/api/testflight-ids/does-not-exist")
    assert r.status_code == 404


def test_add_and_remove_apprise_url(client, monkeypatch):
    monkeypatch.setattr(state, "update_env_file", lambda *a, **k: True)
    monkeypatch.setattr(state, "send_notification", lambda *a, **k: None)

    url = "discord://aaaa/bbbb"
    state.current_apprise_urls[:] = [u for u in state.current_apprise_urls if u != url]

    r = client.post("/api/apprise-urls", json={"url": url})
    assert r.status_code == 200, r.text
    assert url in r.json()["apprise_urls"]

    encoded = urllib.parse.quote(url, safe="")
    r = client.request("DELETE", f"/api/apprise-urls/{encoded}")
    assert r.status_code == 200
    assert url not in r.json()["apprise_urls"]


# ── Optional HTTP Basic auth ────────────────────────────────────
def test_auth_enforced_when_configured(monkeypatch):
    monkeypatch.setenv("WEB_USERNAME", "user")
    monkeypatch.setenv("WEB_PASSWORD", "pass")
    monkeypatch.setenv("APPRISE_URL", "json://localhost/")
    try:
        reloaded = importlib.reload(main)
        c = TestClient(reloaded.app)
        # Health stays open for the Docker healthcheck.
        assert c.get("/api/health").status_code == 200
        # A protected endpoint requires credentials.
        assert c.get("/api/config").status_code == 401
        assert c.get("/api/config", auth=("user", "pass")).status_code == 200
        assert c.get("/api/config", auth=("user", "wrong")).status_code == 401
    finally:
        # Restore the no-auth module state for any later tests.
        monkeypatch.delenv("WEB_USERNAME", raising=False)
        monkeypatch.delenv("WEB_PASSWORD", raising=False)
        importlib.reload(main)


# ── Auth required on a public bind address ──────────────────────
def test_public_host_without_credentials_exits(monkeypatch):
    """A non-loopback host with no credentials must abort startup (exit 1)."""
    monkeypatch.setenv("FASTAPI_HOST", "0.0.0.0")
    monkeypatch.setattr(main, "WEB_USERNAME", "")
    monkeypatch.setattr(main, "WEB_PASSWORD", "")
    with pytest.raises(SystemExit) as exc:
        main.validate_auth_config()
    assert exc.value.code == 1


def test_public_host_with_credentials_ok(monkeypatch):
    """A non-loopback host is allowed when both credentials are set."""
    monkeypatch.setenv("FASTAPI_HOST", "0.0.0.0")
    monkeypatch.setattr(main, "WEB_USERNAME", "user")
    monkeypatch.setattr(main, "WEB_PASSWORD", "pass")
    # Should not raise.
    main.validate_auth_config()


def test_loopback_host_without_credentials_ok(monkeypatch):
    """Auth is optional on localhost, even with no credentials."""
    monkeypatch.setattr(main, "WEB_USERNAME", "")
    monkeypatch.setattr(main, "WEB_PASSWORD", "")
    for host in ("127.0.0.1", "localhost", "::1"):
        monkeypatch.setenv("FASTAPI_HOST", host)
        main.validate_auth_config()  # should not raise


# ── Disabled heartbeat must not exit immediately ────────────────
def test_disabled_heartbeat_waits_for_shutdown(monkeypatch):
    """With HEARTBEAT_INTERVAL=0 the heartbeat task must stay alive until
    shutdown_event is set, so it doesn't trip async_main's FIRST_COMPLETED."""
    monkeypatch.setattr(main, "HEARTBEAT_INTERVAL", 0)

    async def run():
        ev = asyncio.Event()
        monkeypatch.setattr(main, "shutdown_event", ev)

        task = asyncio.create_task(main.heartbeat())
        await asyncio.sleep(0.05)
        # Must NOT have returned immediately.
        assert not task.done()

        # Setting shutdown lets it finish cleanly (no exception).
        ev.set()
        await asyncio.wait_for(task, timeout=1)
        assert task.done() and task.exception() is None

    asyncio.run(run())
