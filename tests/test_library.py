"""Tests for the Library archive feature."""

import os
import time

os.environ.setdefault("APPRISE_URL", "json://localhost/")
os.environ.setdefault("ENABLE_UPDATE_CHECKER", "false")
os.environ.pop("WEB_USERNAME", None)
os.environ.pop("WEB_PASSWORD", None)

import pytest
from fastapi.testclient import TestClient

import library  # noqa: E402
import main  # noqa: E402
import routes  # noqa: E402
import state  # noqa: E402


@pytest.fixture
def client():
    c = TestClient(main.app)
    c.get("/")
    token = c.cookies.get("csrf_token")
    if token:
        c.headers.update({"X-CSRF-Token": token})
    return c


# ── Library module ──────────────────────────────────────────────
def test_archive_creates_entry():
    library.archive("abc12345", app_name="Cool App", icon_url="http://i/x.png", last_status="open")
    e = library.get_entry("abc12345")
    assert e["id"] == "abc12345"
    assert e["app_name"] == "Cool App"
    assert e["icon_url"] == "http://i/x.png"
    assert e["last_status"] == "open"
    assert e["first_archived"] == e["last_archived"]


def test_archive_never_duplicates_and_updates():
    library.archive("dup12345", app_name="First", last_status="open")
    first = library.get_entry("dup12345")["first_archived"]
    time.sleep(0.01)
    library.archive("dup12345", app_name="Second", last_status="full")
    assert len(library.get_history()) == 1  # no duplicate
    e = library.get_entry("dup12345")
    assert e["app_name"] == "Second"  # updated
    assert e["last_status"] == "full"
    assert e["first_archived"] == first  # first_archived preserved
    assert e["last_archived"] >= first  # last_archived refreshed


def test_remove_and_clear():
    library.archive("rm111111")
    library.archive("rm222222")
    assert library.remove("rm111111") is True
    assert library.remove("rm111111") is False  # already gone
    assert library.get_entry("rm111111") is None
    assert library.clear() == 1  # one left
    assert library.get_history() == []


def test_search_and_sort():
    library.archive("zeb11111", app_name="Zebra")
    time.sleep(0.01)
    library.archive("app22222", app_name="Apple")
    assert [i["app_name"] for i in library.get_history(sort="name")] == ["Apple", "Zebra"]
    # Most recently archived first.
    assert library.get_history(sort="archived")[0]["id"] == "app22222"
    res = library.get_history(search="zeb")
    assert len(res) == 1 and res[0]["id"] == "zeb11111"


def test_persistence_survives_restart():
    library.archive("persist1", app_name="P", last_status="full")
    # Simulate a restart: drop in-memory state and reload from the file.
    library._history.clear()
    library._favorites.clear()
    library.load_from_disk()
    e = library.get_entry("persist1")
    assert e and e["app_name"] == "P" and e["last_status"] == "full"


# ── API: archive-on-remove + endpoints ──────────────────────────
def _allow_removal(monkeypatch):
    monkeypatch.setattr(state, "update_env_file", lambda *a, **k: True)
    monkeypatch.setattr(state, "send_notification", lambda *a, **k: None)


def test_remove_archives_to_library(client, monkeypatch):
    _allow_removal(monkeypatch)
    tf = "arch1234"
    state.current_id_list[:] = [tf]
    routes.app_name_cache.put(f"{routes.TESTFLIGHT_URL}:{tf}", "Cached Name")
    try:
        r = client.delete(f"/api/testflight-ids/{tf}")
        assert r.status_code == 200, r.text
        entry = library.get_entry(tf)
        assert entry is not None
        assert entry["app_name"] == "Cached Name"
    finally:
        state.current_id_list[:] = []


def test_get_library_endpoint(client):
    library.archive("getlib01", app_name="Lib App")
    body = client.get("/api/library").json()
    assert "history" in body and "favorites" in body
    assert any(i["id"] == "getlib01" for i in body["history"])


def test_restore_endpoint(client, monkeypatch):
    _allow_removal(monkeypatch)
    monkeypatch.setattr(state, "validate_apprise_url", lambda u: (True, "ok"))
    tf = "restore1"
    library.archive(tf, app_name="R")
    state.current_id_list[:] = []
    try:
        r = client.post(f"/api/library/{tf}/restore")
        assert r.status_code == 200, r.text
        assert tf in state.current_id_list  # back in monitoring
        assert library.get_entry(tf) is None  # removed from library
    finally:
        state.current_id_list[:] = []


def test_restore_unknown_404(client):
    assert client.post("/api/library/nosuch1/restore").status_code == 404


def test_delete_endpoint(client):
    library.archive("del11111")
    assert client.request("DELETE", "/api/library/del11111").status_code == 200
    assert library.get_entry("del11111") is None
    assert client.request("DELETE", "/api/library/del11111").status_code == 404


def test_clear_all_endpoint(client):
    library.archive("clr11111")
    library.archive("clr22222")
    r = client.request("DELETE", "/api/library")
    assert r.status_code == 200
    assert r.json()["removed"] == 2
    assert library.get_history() == []


# ── CSRF on state-changing library endpoints ────────────────────
def test_library_endpoints_require_csrf():
    c = TestClient(main.app)  # no CSRF token
    library.archive("csrf1234")
    assert c.post("/api/library/csrf1234/restore").status_code == 403
    assert c.request("DELETE", "/api/library/csrf1234").status_code == 403
    assert c.request("DELETE", "/api/library").status_code == 403
    # A safe GET is unaffected.
    assert c.get("/api/library").status_code == 200
