"""
Portable configuration import/export.

Exports the non-secret, runtime-independent configuration (monitored TestFlight
IDs and user-facing settings) and validates such a structure before importing
it. Secrets (raw Apprise URLs/tokens, dashboard credentials) and runtime state
(status, failure counts, timestamps, cooldowns, cached icons) are excluded by
default; raw Apprise URLs are only included when secrets are explicitly
requested. Imports are validated in full before anything is written, and are
applied with a single atomic .env rewrite (no partial application).
"""

import logging
import os
import shutil
import tempfile

import config
import state

EXPORT_VERSION = 1

# Allowed top-level keys in an imported config (anything else is rejected).
_ALLOWED_TOP_LEVEL = {
    "version",
    "exported_at",
    "testflight_ids",
    "settings",
    "notifications",
    "apprise_urls",
    "_warning",
}
# Allowed setting keys.
_ALLOWED_SETTINGS = {
    "always_notify_open",
    "enable_update_checker",
    "ui_theme",
    "interval_check_ms",
    "heartbeat_interval_hours",
}


def build_export(include_secrets: bool = False) -> dict:
    """Build the portable export structure.

    Excludes secrets and runtime state by default. When ``include_secrets`` is
    True the raw Apprise URLs are added along with a clear warning.
    """
    urls = state.get_current_apprise_urls()
    from utils.service_icons import get_apprise_service_icon

    services = sorted({get_apprise_service_icon(u)["service_name"] for u in urls})

    data = {
        "version": EXPORT_VERSION,
        "testflight_ids": state.get_current_id_list(),
        "settings": {
            "always_notify_open": config.ALWAYS_NOTIFY_OPEN,
            "enable_update_checker": config.ENABLE_UPDATE_CHECKER,
            "ui_theme": config.UI_THEME,
            "interval_check_ms": config.SLEEP_TIME,
            "heartbeat_interval_hours": config.HEARTBEAT_INTERVAL // 3600,
        },
        # Non-secret notification metadata only (service names, never URLs/tokens).
        "notifications": {
            "configured_count": len(urls),
            "services": services,
        },
    }
    if include_secrets:
        data["_warning"] = (
            "This file contains secret Apprise URLs. Keep it private and do not "
            "share or commit it."
        )
        data["apprise_urls"] = urls
    return data


def validate_import(data) -> tuple:
    """Validate an imported config structure.

    Returns ``(normalized, None)`` on success or ``(None, error_message)`` on
    failure. Nothing is written here, so a rejected import applies nothing.
    """
    if not isinstance(data, dict):
        return None, "Config must be a JSON object"

    unknown = set(data) - _ALLOWED_TOP_LEVEL
    if unknown:
        return None, f"Unknown field(s): {', '.join(sorted(unknown))}"

    normalized = {}

    if "testflight_ids" in data:
        ids = data["testflight_ids"]
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            return None, "testflight_ids must be a list of strings"
        cleaned = []
        for tf in ids:
            tf = tf.strip()
            ok, _ = state.validate_testflight_id_format(tf)
            if not ok:
                return None, f"Invalid TestFlight ID: {tf!r}"
            cleaned.append(tf)
        normalized["testflight_ids"] = cleaned

    if "settings" in data:
        settings = data["settings"]
        if not isinstance(settings, dict):
            return None, "settings must be an object"
        unknown_s = set(settings) - _ALLOWED_SETTINGS
        if unknown_s:
            return None, f"Unknown setting(s): {', '.join(sorted(unknown_s))}"
        norm_s = {}
        for key in ("always_notify_open", "enable_update_checker"):
            if key in settings:
                if not isinstance(settings[key], bool):
                    return None, f"{key} must be a boolean"
                norm_s[key] = settings[key]
        if "ui_theme" in settings:
            if settings["ui_theme"] not in ("dark", "light"):
                return None, "ui_theme must be 'dark' or 'light'"
            norm_s["ui_theme"] = settings["ui_theme"]
        if "interval_check_ms" in settings:
            v = settings["interval_check_ms"]
            if not isinstance(v, int) or isinstance(v, bool) or v < 1000:
                return None, "interval_check_ms must be an integer >= 1000"
            norm_s["interval_check_ms"] = v
        if "heartbeat_interval_hours" in settings:
            v = settings["heartbeat_interval_hours"]
            if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                return None, "heartbeat_interval_hours must be a non-negative integer"
            norm_s["heartbeat_interval_hours"] = v
        normalized["settings"] = norm_s

    # Replacement secrets are only honored if explicitly present in the import.
    if "apprise_urls" in data:
        urls = data["apprise_urls"]
        if not isinstance(urls, list) or not all(isinstance(u, str) for u in urls):
            return None, "apprise_urls must be a list of strings"
        normalized["apprise_urls"] = [u.strip() for u in urls if u.strip()]

    return normalized, None


def _format_value(key, value):
    """Format a key's .env line(s): scalar as KEY=value, list in comma form."""
    if isinstance(value, list):
        if not value:
            return [f"{key}=\n"]
        out = [f"{key}={value[0]},\n"]
        out += [f"{v},\n" for v in value[1:]]
        return out
    return [f"{key}={value}\n"]


def _apply_env_updates(content: str, updates: dict) -> str:
    """Return new .env text with the given keys replaced; everything else kept.

    Other lines (comments, blank lines, untouched keys) are preserved in order.
    Keys not already present are appended.
    """
    lines = content.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"

    new_lines = []
    inserted = set()
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        matched = next((k for k in updates if stripped.startswith(f"{k}=")), None)
        if matched is not None:
            if matched not in inserted:
                new_lines.extend(_format_value(matched, updates[matched]))
                inserted.add(matched)
            # Skip the old key line and any continuation lines.
            i += 1
            while i < len(lines):
                nxt = lines[i].strip()
                if not nxt or nxt.startswith("#") or "=" in nxt:
                    break
                i += 1
            continue
        new_lines.append(lines[i])
        i += 1

    for key in updates:
        if key not in inserted:
            new_lines.extend(_format_value(key, updates[key]))
    return "".join(new_lines)


def apply_import(normalized: dict) -> bool:
    """Apply a validated import as a single atomic .env rewrite.

    Existing secrets are preserved unless ``apprise_urls`` is present in the
    import. Returns True on success.
    """
    updates = {}
    if "testflight_ids" in normalized:
        updates["ID_LIST"] = normalized["testflight_ids"]
    if "apprise_urls" in normalized:
        updates["APPRISE_URL"] = normalized["apprise_urls"]
    settings = normalized.get("settings", {})
    if "always_notify_open" in settings:
        updates["ALWAYS_NOTIFY_OPEN"] = "true" if settings["always_notify_open"] else "false"
    if "enable_update_checker" in settings:
        updates["ENABLE_UPDATE_CHECKER"] = (
            "true" if settings["enable_update_checker"] else "false"
        )
    if "ui_theme" in settings:
        updates["UI_THEME"] = settings["ui_theme"]
    if "interval_check_ms" in settings:
        updates["INTERVAL_CHECK"] = str(settings["interval_check_ms"])
    if "heartbeat_interval_hours" in settings:
        updates["HEARTBEAT_INTERVAL"] = str(settings["heartbeat_interval_hours"])

    if not updates:
        return True

    env_path = ".env"
    backup_path = f"{env_path}.backup"
    try:
        current = ""
        if os.path.exists(env_path):
            with open(env_path, "r") as f:
                current = f.read()
        new_content = _apply_env_updates(current, updates)

        directory = os.path.dirname(os.path.abspath(env_path)) or "."
        if os.path.exists(env_path):
            shutil.copy2(env_path, backup_path)
        fd, tmp_path = tempfile.mkstemp(prefix=".env.", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w") as tmp:
                tmp.write(new_content)
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(tmp_path, env_path)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        return True
    except OSError as e:
        logging.error("Failed to write imported config: %s", e)
        return False
