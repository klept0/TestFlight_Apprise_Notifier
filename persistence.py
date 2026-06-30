"""
Runtime state persistence.

Stores per-TestFlight-ID monitor state (last known status, last notification /
last successful check timestamps, failure count) plus the cached app name and
icon URL to a JSON file, so the app can resume cleanly after a restart without
re-sending duplicate notifications.

Reads tolerate a missing or corrupt file (returning an empty state so startup
can proceed). Writes are atomic (temp file + fsync + os.replace).
"""

import json
import logging
import os
import tempfile

# Location of the state file (overridable for tests / custom deployments).
STATE_FILE = os.getenv("STATE_FILE", "data/state.json")
STATE_VERSION = 1


def read_json(path: str, default=None):
    """Load a JSON value from ``path``; return ``default`` on missing/corrupt.

    Generic, reusable counterpart to :func:`load_state` for other JSON stores
    (e.g. the Library). Never raises on a bad or missing file.
    """
    fallback = {} if default is None else default
    try:
        if not os.path.exists(path):
            return fallback
        with open(path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, ValueError, OSError) as e:
        logging.warning("Could not read JSON file '%s' (%s); using default", path, e)
        return fallback


def atomic_write_json(path: str, data) -> bool:
    """Atomically write ``data`` as JSON to ``path`` (temp + fsync + os.replace).

    Generic, reusable counterpart to :func:`save_state`. Returns True on
    success; logs and returns False on failure (never raises).
    """
    try:
        directory = os.path.dirname(os.path.abspath(path)) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=".json.", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w") as tmp:
                json.dump(data, tmp, indent=2)
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(tmp_path, path)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        return True
    except OSError as e:
        logging.error("Failed to write JSON file '%s': %s", path, e)
        return False


def load_state() -> dict:
    """Load persisted runtime state.

    Returns an empty dict if the file is missing, unreadable, or corrupt — a
    bad state file must never prevent startup.
    """
    try:
        if not os.path.exists(STATE_FILE):
            return {}
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            logging.warning("Runtime state file is not an object; ignoring it")
            return {}
        return data
    except (json.JSONDecodeError, ValueError, OSError) as e:
        logging.warning("Could not load runtime state (%s); starting fresh", e)
        return {}


def save_state(state: dict) -> bool:
    """Atomically persist runtime state to disk. Returns True on success.

    Failures are logged but never raised, so a write problem can't crash the
    monitor loop or shutdown.
    """
    try:
        directory = os.path.dirname(os.path.abspath(STATE_FILE)) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=".state.", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w") as tmp:
                json.dump(state, tmp, indent=2)
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(tmp_path, STATE_FILE)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        return True
    except OSError as e:
        logging.error("Failed to persist runtime state: %s", e)
        return False
