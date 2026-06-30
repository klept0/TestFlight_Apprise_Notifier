"""
Static configuration for TestFlight Apprise Notifier.

Loads the .env file and exposes the parsed, read-only configuration values
(versions, intervals, feature flags, constants, and the initial ID / Apprise
URL lists). Mutable runtime state (the live ID/URL lists, the Apprise object,
HTTP session, metrics, etc.) lives in main.py, not here.
"""

import os
import re

from dotenv import load_dotenv

# Load .env before any os.getenv() calls so all variables see the file values.
load_dotenv()

# Version
__version__ = "2.0.3"

# GitHub repository configuration (override via environment variables)
GITHUB_REPO = os.getenv("GITHUB_REPO", "klept0/TestFlight_Apprise_Notifier")
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")
GITHUB_CHECK_INTERVAL = int(os.getenv("GITHUB_CHECK_INTERVAL_HOURS", "24"))
ENABLE_UPDATE_CHECKER = os.getenv("ENABLE_UPDATE_CHECKER", "true").lower() in (
    "1", "true", "yes", "on"
)

# UI default theme: "dark" or "light" (users can override per-browser via the toggle)
_raw_theme = os.getenv("UI_THEME", "dark").lower().strip()
UI_THEME = "dark" if _raw_theme not in ("light", "dark") else _raw_theme

# Force notifications for every OPEN poll (default: only on change / first OPEN)
ALWAYS_NOTIFY_OPEN = os.getenv("ALWAYS_NOTIFY_OPEN", "false").lower() in (
    "1",
    "true",
    "yes",
    "on",
)


def get_multiline_env_value(key: str) -> str:
    """Get environment value that may span multiple lines in .env file."""
    try:
        env_path = ".env"
        if not os.path.exists(env_path):
            return os.getenv(key, "")

        with open(env_path, "r") as f:
            lines = f.readlines()

        # Find the key and collect all continuation lines
        value_lines = []
        in_multiline = False

        for line in lines:
            line = line.strip()
            if line.startswith(f"{key}="):
                # Start of the key
                value_part = line[len(f"{key}=") :]
                if value_part.startswith('"') and value_part.endswith('"'):
                    # Quoted value - remove quotes and unescape
                    return value_part[1:-1].replace("\\n", "\n")
                else:
                    # Multi-line value starting
                    value_lines.append(value_part.rstrip(","))
                    in_multiline = True
            elif in_multiline and line and not line.startswith("#") and "=" not in line:
                # Continuation line
                value_lines.append(line.rstrip(","))
            elif in_multiline and (
                line.startswith(("APPRISE_URL=", "ID_LIST=", "INTERVAL_CHECK="))
                or not line
            ):
                # End of multi-line value (next key or empty line)
                break

        if value_lines:
            return "\n".join(value_lines)
        else:
            return os.getenv(key, "")
    except Exception:
        return os.getenv(key, "")


# Constants
TESTFLIGHT_URL = "https://testflight.apple.com/join/"
FULL_TEXT = "This beta is full."
NOT_OPEN_TEXT = "This beta isn't accepting any new testers right now."

# Parse ID_LIST (supporting multi-line format)
_id_list_raw_value = get_multiline_env_value("ID_LIST")
ID_LIST = [
    tf_id.strip().rstrip(",")
    for tf_id in _id_list_raw_value.replace("\n", ",").split(",")
    if tf_id.strip()
]

SLEEP_TIME = int(os.getenv("INTERVAL_CHECK", "10000"))  # in ms
TITLE_REGEX = re.compile(r"Join the (.+) beta - TestFlight - Apple")

# Parse Apprise URLs (supporting multi-line format)
_apprise_url_raw = get_multiline_env_value("APPRISE_URL")
APPRISE_URLS = [
    url.strip().rstrip(",")
    for url in _apprise_url_raw.replace("\n", ",").split(",")
    if url.strip()
]

# Heartbeat interval (default: 6 hours), configured in hours via HEARTBEAT_INTERVAL.
HEARTBEAT_INTERVAL = (
    int(os.getenv("HEARTBEAT_INTERVAL", "6")) * 60 * 60
)  # Convert hours to seconds

# Per-ID retry backoff (seconds). After a failed check, an ID is skipped until
# its cooldown elapses; the delay grows exponentially up to the cap.
RETRY_BACKOFF_BASE = float(os.getenv("RETRY_BACKOFF_BASE_SECONDS", "30"))
RETRY_BACKOFF_MAX = float(os.getenv("RETRY_BACKOFF_MAX_SECONDS", "3600"))
