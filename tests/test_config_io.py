"""Tests for configuration import/export (Runbook 2 — Task 9)."""

import json
import os

os.environ.setdefault("APPRISE_URL", "json://localhost/")
os.environ.setdefault("ENABLE_UPDATE_CHECKER", "false")
os.environ.pop("WEB_USERNAME", None)
os.environ.pop("WEB_PASSWORD", None)

import pytest
from fastapi.testclient import TestClient

import config_io  # noqa: E402
import main  # noqa: E402
import state  # noqa: E402


@pytest.fixture
def client():
    c = TestClient(main.app)
    c.get("/")  # obtain a CSRF cookie
    token = c.cookies.get("csrf_token")
    if token:
        c.headers.update({"X-CSRF-Token": token})
    return c


# ── Export ──────────────────────────────────────────────────────
def test_export_excludes_secrets_by_default(client):
    saved = list(state.current_apprise_urls)
    try:
        state.current_apprise_urls[:] = ["discord://id999/SECRETTOKEN123"]
        body = client.get("/api/config/export").json()
        blob = json.dumps(body)
        assert "apprise_urls" not in body  # raw URLs not exported
        assert "SECRETTOKEN123" not in blob  # no token anywhere
        assert "discord://" not in blob
        # Non-secret service metadata is fine.
        assert body["notifications"]["services"] == ["Discord"]
    finally:
        state.current_apprise_urls[:] = saved


def test_export_includes_secrets_only_when_requested(client):
    saved = list(state.current_apprise_urls)
    try:
        state.current_apprise_urls[:] = ["discord://id999/SECRETTOKEN123"]
        body = client.get("/api/config/export?include_secrets=true").json()
        assert body["apprise_urls"] == ["discord://id999/SECRETTOKEN123"]
        assert "_warning" in body  # user is warned
    finally:
        state.current_apprise_urls[:] = saved


def test_export_excludes_runtime_state(client):
    body = client.get("/api/config/export").json()
    blob = json.dumps(body)
    for runtime_key in (
        "failure_count",
        "next_check_ts",
        "last_success_ts",
        "last_failure_ts",
        "backoff_delay",
        "notified_open",
        "icon_url",
    ):
        assert runtime_key not in blob
    assert set(body) <= {
        "version",
        "testflight_ids",
        "settings",
        "app_settings",
        "notifications",
    }


# ── Import validation (no writes) ───────────────────────────────
def test_validate_rejects_unknown_fields():
    normalized, error = config_io.validate_import({"evil_field": 1})
    assert normalized is None and "Unknown field" in error


def test_validate_rejects_bad_id():
    normalized, error = config_io.validate_import({"testflight_ids": ["!bad!"]})
    assert normalized is None and "Invalid TestFlight ID" in error


def test_validate_rejects_unknown_setting():
    normalized, error = config_io.validate_import({"settings": {"rm_rf": True}})
    assert normalized is None and "Unknown setting" in error


# ── Import via endpoint ─────────────────────────────────────────
def test_import_malformed_json_rejected(client):
    r = client.post(
        "/api/config/import",
        content="{ this is not json ",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 400


def test_import_unsafe_fields_rejected(client):
    r = client.post("/api/config/import", json={"settings": {"danger": 1}})
    assert r.status_code == 400


def test_import_valid_returns_ok(client, monkeypatch):
    # Stub the write so the test doesn't touch the repo's .env.
    monkeypatch.setattr(config_io, "apply_import", lambda n: True)
    r = client.post(
        "/api/config/import",
        json={"version": 1, "testflight_ids": ["abcd1234"], "settings": {"ui_theme": "light"}},
    )
    assert r.status_code == 200, r.text
    assert r.json()["success"] is True


def test_import_requires_csrf():
    c = TestClient(main.app)  # fresh client, no CSRF token
    assert c.post("/api/config/import", json={"version": 1}).status_code == 403


# ── Apply (atomic .env write) ───────────────────────────────────
def test_apply_import_writes_atomically_and_preserves_secret(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    env = tmp_path / ".env"
    env.write_text(
        "# my config\n"
        "ID_LIST=old111,\n"
        "APPRISE_URL=discord://keep/THISSECRET,\n"
        "UI_THEME=dark\n"
    )
    data = {
        "version": 1,
        "testflight_ids": ["newid001", "newid002"],
        "settings": {"ui_theme": "light", "always_notify_open": True},
    }
    normalized, error = config_io.validate_import(data)
    assert error is None
    assert config_io.apply_import(normalized) is True

    content = env.read_text()
    assert "newid001" in content and "newid002" in content
    assert "old111" not in content
    assert "discord://keep/THISSECRET" in content  # secret preserved (not in import)
    assert "UI_THEME=light" in content
    assert "ALWAYS_NOTIFY_OPEN=true" in content
    assert "# my config" in content  # comments preserved
    assert (tmp_path / ".env.backup").exists()  # hardened backup
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]


def test_invalid_import_does_not_apply(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    env = tmp_path / ".env"
    original = "ID_LIST=keepme12,\nAPPRISE_URL=discord://a/b,\n"
    env.write_text(original)

    # Valid IDs but an invalid setting -> the whole import is rejected.
    normalized, error = config_io.validate_import(
        {"testflight_ids": ["validid1"], "settings": {"interval_check_ms": 1}}
    )
    assert normalized is None and error  # rejected
    # The caller never applies, so the file is untouched (no partial apply).
    assert env.read_text() == original
