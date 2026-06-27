"""Shared test fixtures.

Isolates the on-disk JSON stores (runtime state, library, per-app config) to a
per-test temp directory so the suite never touches the repo's ``data/``
directory, and starts each test from a clean in-memory state.
"""

import pytest


@pytest.fixture(autouse=True)
def _isolate_data_files(tmp_path, monkeypatch):
    import persistence
    import library
    import app_config

    monkeypatch.setattr(persistence, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(library, "LIBRARY_FILE", str(tmp_path / "library.json"))
    monkeypatch.setattr(app_config, "APP_CONFIG_FILE", str(tmp_path / "app_config.json"))

    library._history.clear()
    library._favorites.clear()
    app_config._settings.clear()
    yield
    library._history.clear()
    library._favorites.clear()
    app_config._settings.clear()
