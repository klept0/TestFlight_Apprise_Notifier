"""
Startup validation of runtime paths.

Before the app starts serving, checks that the paths it needs to read/write are
accessible, so container users get clear warnings (and a clear failure for a
truly required path) instead of silent feature breakage later. The checks are
non-destructive: they only test access, never create or modify files, and they
log paths only — never file contents — so no secrets are exposed.
"""

import logging
import os


def _readable(path: str) -> bool:
    return os.access(path, os.R_OK)


def _writable(path: str) -> bool:
    return os.access(path, os.W_OK)


def _dir_writable_or_creatable(path: str) -> bool:
    """True if the file's directory exists and is writable, or can be created.

    Walks up to the nearest existing ancestor and checks it is a writable
    directory. Does not create anything.
    """
    directory = os.path.dirname(os.path.abspath(path)) or "."
    d = directory
    while not os.path.isdir(d):
        parent = os.path.dirname(d)
        if not parent or parent == d:
            return False
        d = parent
    return _writable(d)


def validate_runtime_paths(env_path: str, state_file: str, app_config_file: str):
    """Return a list of ``(level, message)`` issues. ``level`` is 'error' or
    'warning'. Pure: no logging, no filesystem modification."""
    results = []

    # .env — the configuration source. A present-but-unreadable config file is a
    # truly broken setup (error); a read-only one only disables dashboard edits.
    if os.path.exists(env_path):
        if not _readable(env_path):
            results.append(
                ("error", f"Config file '{env_path}' exists but is not readable")
            )
        elif not _writable(env_path):
            results.append(
                (
                    "warning",
                    f"Config file '{env_path}' is not writable; dashboard config "
                    f"edits and add/remove of IDs/URLs will not persist",
                )
            )

    # Runtime state and per-app config files — optional (the app degrades
    # gracefully), so only warn when they can't be written.
    for path, what in ((state_file, "runtime state"), (app_config_file, "per-app config")):
        if os.path.exists(path):
            if not _writable(path):
                results.append(
                    (
                        "warning",
                        f"{what.capitalize()} file '{path}' is not writable; "
                        f"{what} will not be saved",
                    )
                )
        elif not _dir_writable_or_creatable(path):
            directory = os.path.dirname(os.path.abspath(path)) or "."
            results.append(
                (
                    "warning",
                    f"Directory '{directory}' for {what} is not writable; {what} "
                    f"will not be saved. If running as a non-root container, chown "
                    f"it to the container user (uid 10001).",
                )
            )

    return results


def run_startup_validation(env_path: str, state_file: str, app_config_file: str) -> bool:
    """Validate runtime paths, logging warnings/errors. Returns True if OK to
    proceed (no error-level / truly-required failures)."""
    results = validate_runtime_paths(env_path, state_file, app_config_file)
    has_error = False
    for level, message in results:
        if level == "error":
            logging.error("Startup path check: %s", message)
            has_error = True
        else:
            logging.warning("Startup path check: %s", message)
    if not results:
        logging.info("Startup path checks passed (runtime paths are accessible)")
    return not has_error
