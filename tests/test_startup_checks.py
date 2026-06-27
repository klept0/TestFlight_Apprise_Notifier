"""Tests for startup runtime-path validation (Runbook 3 — Task 11)."""

import logging

import startup_checks


def _levels(results):
    return [level for level, _ in results]


def test_writable_env_and_creatable_paths_no_issues(tmp_path):
    env = tmp_path / ".env"
    env.write_text("APPRISE_URL=json://localhost/\n")
    # state/app_config don't exist yet but the dir is writable -> creatable.
    results = startup_checks.validate_runtime_paths(
        str(env), str(tmp_path / "state.json"), str(tmp_path / "app_config.json")
    )
    assert results == []


def test_non_writable_env_warns_but_does_not_fail(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("APPRISE_URL=json://localhost/\n")
    monkeypatch.setattr(
        startup_checks, "_writable", lambda p: False if str(p) == str(env) else True
    )
    results = startup_checks.validate_runtime_paths(
        str(env), str(tmp_path / "state.json"), str(tmp_path / "app_config.json")
    )
    assert any(level == "warning" and ".env" in msg for level, msg in results)
    assert "error" not in _levels(results)  # not fatal


def test_unreadable_env_is_fatal(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("APPRISE_URL=json://localhost/\n")
    monkeypatch.setattr(startup_checks, "_readable", lambda p: False)
    results = startup_checks.validate_runtime_paths(
        str(env), str(tmp_path / "state.json"), str(tmp_path / "app_config.json")
    )
    assert any(level == "error" and ".env" in msg for level, msg in results)
    # run_startup_validation must report not-ok (caller fails startup).
    assert startup_checks.run_startup_validation(
        str(env), str(tmp_path / "state.json"), str(tmp_path / "app_config.json")
    ) is False


def test_missing_creatable_state_and_config_no_issues(tmp_path):
    env = tmp_path / ".env"
    env.write_text("APPRISE_URL=json://localhost/\n")
    results = startup_checks.validate_runtime_paths(
        str(env), str(tmp_path / "data" / "state.json"), str(tmp_path / "app_config.json")
    )
    # data/ doesn't exist but tmp_path is writable -> creatable.
    assert results == []


def test_non_writable_data_directory_warns(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("APPRISE_URL=json://localhost/\n")
    state = tmp_path / "data" / "state.json"  # data/ does not exist
    appc = tmp_path / "data" / "app_config.json"
    # Simulate a non-writable filesystem (e.g. unmounted/unowned data dir).
    monkeypatch.setattr(startup_checks, "_writable", lambda p: False)
    results = startup_checks.validate_runtime_paths(str(env), str(state), str(appc))
    assert any("not writable" in msg for _, msg in results)
    # state + per-app config dirs both warn; none are fatal.
    assert "error" not in _levels(results)


def test_validation_does_not_leak_secrets(tmp_path, monkeypatch, caplog):
    env = tmp_path / ".env"
    env.write_text(
        "APPRISE_URL=discord://id/SUPERSECRETTOKEN123\nWEB_PASSWORD=hunter2pw\n"
    )
    # Force warnings so path messages are emitted, then confirm no secret leaks.
    monkeypatch.setattr(startup_checks, "_writable", lambda p: False)
    with caplog.at_level(logging.INFO):
        startup_checks.run_startup_validation(
            str(env), str(tmp_path / "state.json"), str(tmp_path / "app_config.json")
        )
    blob = " ".join(r.getMessage() for r in caplog.records)
    assert "SUPERSECRETTOKEN123" not in blob
    assert "hunter2pw" not in blob
