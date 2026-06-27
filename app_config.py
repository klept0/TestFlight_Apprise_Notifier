"""
Per-app configuration for monitored TestFlight IDs.

Holds optional per-ID settings (enabled, friendly name, check-interval override,
and per-status notification toggles) in a JSON config file, separate from both
the simple `.env` ID list (backward compatible) and the runtime state file.
An ID with no entry uses defaults that preserve the original behavior.
"""

import json
import logging
import os
import tempfile
import threading

APP_CONFIG_FILE = os.getenv("APP_CONFIG_FILE", "data/app_config.json")
CONFIG_VERSION = 1

# Defaults chosen to preserve the original behavior: every app enabled, no
# friendly name, global interval, OPEN/FULL notifications on, CLOSED off.
DEFAULTS = {
    "enabled": True,
    "friendly_name": None,
    "check_interval_seconds": None,
    "notify_on_open": True,
    "notify_on_full": True,
    "notify_on_closed": False,
}

_settings = {}  # tf_id -> partial settings dict (validated)
_lock = threading.Lock()


def get(tf_id: str) -> dict:
    """Return the effective settings for an ID (defaults merged with overrides)."""
    with _lock:
        stored = dict(_settings.get(tf_id, {}))
    merged = dict(DEFAULTS)
    merged.update(stored)
    return merged


def get_all() -> dict:
    """Return all stored per-app overrides (no defaults), for export."""
    with _lock:
        return {k: dict(v) for k, v in _settings.items()}


def validate(partial) -> tuple:
    """Validate a partial settings dict. Returns (normalized, None) or (None, err)."""
    if not isinstance(partial, dict):
        return None, "settings must be an object"
    unknown = set(partial) - set(DEFAULTS)
    if unknown:
        return None, f"Unknown setting(s): {', '.join(sorted(unknown))}"

    norm = {}
    for key in ("enabled", "notify_on_open", "notify_on_full", "notify_on_closed"):
        if key in partial:
            if not isinstance(partial[key], bool):
                return None, f"{key} must be a boolean"
            norm[key] = partial[key]
    if "friendly_name" in partial:
        v = partial["friendly_name"]
        if v is not None and (not isinstance(v, str) or len(v) > 100):
            return None, "friendly_name must be a string (<=100 chars) or null"
        norm["friendly_name"] = v.strip() if isinstance(v, str) else v
    if "check_interval_seconds" in partial:
        v = partial["check_interval_seconds"]
        if v is not None and (not isinstance(v, int) or isinstance(v, bool) or v < 1):
            return None, "check_interval_seconds must be a positive integer or null"
        norm["check_interval_seconds"] = v
    return norm, None


def update(tf_id: str, partial) -> tuple:
    """Validate and merge new settings for an ID, then persist. Returns (ok, msg)."""
    norm, error = validate(partial)
    if error:
        return False, error
    with _lock:
        current = dict(_settings.get(tf_id, {}))
        current.update(norm)
        _settings[tf_id] = current
    _save()
    return True, "Settings updated"


def replace_all(mapping) -> tuple:
    """Validate and replace per-app settings for the given IDs (used by import).

    Validates every entry before applying any, so an invalid mapping is rejected
    in full. Returns (ok, error). Only the provided IDs are updated.
    """
    if not isinstance(mapping, dict):
        return False, "app_settings must be an object"
    validated = {}
    for tf_id, partial in mapping.items():
        norm, error = validate(partial)
        if error:
            return False, f"{tf_id}: {error}"
        validated[tf_id] = norm
    with _lock:
        for tf_id, norm in validated.items():
            _settings[tf_id] = norm
    _save()
    return True, "ok"


def load_from_disk() -> None:
    """Load per-app config from disk. Tolerates a missing or corrupt file."""
    global _settings
    try:
        if not os.path.exists(APP_CONFIG_FILE):
            return
        with open(APP_CONFIG_FILE, "r") as f:
            data = json.load(f)
        apps = data.get("apps", {}) if isinstance(data, dict) else {}
        loaded = {}
        for tf_id, partial in (apps or {}).items():
            norm, error = validate(partial)
            if error:
                logging.warning("Ignoring invalid per-app config for %s: %s", tf_id, error)
                continue
            loaded[tf_id] = norm
        with _lock:
            _settings = loaded
    except (json.JSONDecodeError, ValueError, OSError) as e:
        logging.warning("Could not load per-app config (%s); using defaults", e)


def _save() -> None:
    """Atomically write the per-app config to disk (best effort)."""
    with _lock:
        data = {"version": CONFIG_VERSION, "apps": {k: dict(v) for k, v in _settings.items()}}
    try:
        directory = os.path.dirname(os.path.abspath(APP_CONFIG_FILE)) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=".app_config.", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, APP_CONFIG_FILE)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
    except OSError as e:
        logging.error("Failed to save per-app config: %s", e)
