"""Shared test fixtures.

Isolates the on-disk JSON stores (runtime state, library, per-app config) to a
per-test temp directory so the suite never touches the repo's ``data/``
directory, and starts each test from a clean in-memory state.
"""

import enum

import pytest


def pytest_pycollect_makeitem(collector, name, obj):
    """Skip collecting imported Enum classes as test classes.

    ``utils.testflight.TestFlightStatus`` is an Enum imported into several test
    modules; its name matches pytest's default ``Test*`` class pattern, which
    triggers a ``PytestCollectionWarning``. Enums are never test classes, so
    tell pytest to collect nothing for them.
    """
    if isinstance(obj, type) and issubclass(obj, enum.Enum):
        return []
    return None


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
