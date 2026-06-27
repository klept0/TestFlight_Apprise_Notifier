"""
Shared runtime state and business logic for TestFlight Apprise Notifier.

Holds the live ID/URL lists, the Apprise notifier, the HTTP session, metrics,
the GitHub update checker, .env persistence, and the validation / add / remove
helpers. Imported by both the web routes and the monitor loop; it must not
import them back (no FastAPI here).
"""

import os
import asyncio
import threading
import time
import logging
from datetime import datetime
from typing import Optional, Dict, Any

import aiohttp
import apprise
from bs4 import BeautifulSoup

from config import (
    APPRISE_URLS,
    GITHUB_BRANCH,
    GITHUB_CHECK_INTERVAL,
    GITHUB_REPO,
    ID_LIST,
    TESTFLIGHT_URL,
)
from utils.formatting import format_datetime, format_link
from utils.masking import mask_secret
from utils.metrics import MetricsCollector
from utils.notifications import send_notification, send_notification_async

# Global HTTP session and lock
_http_session = None
_session_lock = threading.Lock()

# GitHub update check tracking
_last_update_check: Optional[Dict[str, Any]] = None
_update_check_lock = threading.Lock()

# Global metrics collector
_metrics = MetricsCollector()

# Initialize Apprise notifier
apobj = apprise.Apprise()
for url in APPRISE_URLS:
    if url:
        apobj.add(url)

# Global variables for dynamic ID management
id_list_lock = threading.Lock()
current_id_list = ID_LIST.copy()  # Thread-safe copy for monitoring

# Global variables for dynamic Apprise URL management
apprise_urls_lock = threading.Lock()
current_apprise_urls = APPRISE_URLS.copy()  # Thread-safe copy for monitoring

# Graceful shutdown (cross-platform compatibility)
shutdown_event = asyncio.Event()

# Timestamp used for uptime reporting in the dashboard.
app_start_time = datetime.now()


def http_session_active() -> bool:
    """Return True if the shared HTTP session is open."""
    return _http_session is not None and not _http_session.closed


async def get_http_session() -> aiohttp.ClientSession:
    """Get or create a shared HTTP session with connection pooling."""
    global _http_session
    if _http_session is None or _http_session.closed:
        with _session_lock:
            if _http_session is None or _http_session.closed:
                connector = aiohttp.TCPConnector(
                    limit=20,  # Connection pool size
                    limit_per_host=5,  # Connections per host
                    ttl_dns_cache=300,  # DNS cache TTL
                    use_dns_cache=True,
                    keepalive_timeout=60,
                    enable_cleanup_closed=True,
                )
                timeout = aiohttp.ClientTimeout(
                    total=30,  # Total timeout
                    connect=10,  # Connection timeout
                    sock_read=10,  # Socket read timeout
                )
                _http_session = aiohttp.ClientSession(
                    connector=connector,
                    timeout=timeout,
                    headers={
                        "User-Agent": "TestFlight-Notifier/1.0.9",
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": "en-US,en;q=0.9",
                        "Accept-Encoding": "gzip, deflate, br",
                        "Connection": "keep-alive",
                    },
                )
    return _http_session


async def cleanup_http_session():
    """Clean up the shared HTTP session."""
    global _http_session
    if _http_session and not _http_session.closed:
        await _http_session.close()
        _http_session = None


def get_current_id_list():
    """Thread-safe function to get current ID list."""
    with id_list_lock:
        return current_id_list.copy()


def get_current_apprise_urls():
    """Thread-safe function to get current Apprise URLs list."""
    with apprise_urls_lock:
        return current_apprise_urls.copy()


def apprise_url_id(url: str) -> str:
    """Return a stable, non-secret identifier for an Apprise URL.

    Lets the API/dashboard reference a URL (e.g. for removal) without exposing
    the secret-bearing value itself.
    """
    import hashlib

    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def find_apprise_url_by_id(url_id: str):
    """Return the current Apprise URL whose id matches, or None."""
    with apprise_urls_lock:
        for u in current_apprise_urls:
            if apprise_url_id(u) == url_id:
                return u
    return None


def update_env_file(key: str, new_values: list[str]):
    """Safely update the .env file with new values for a given key.

    Validates inputs first, preserves all other lines (comments and ordering),
    writes to a temporary file in the same directory, backs up the previous
    version to ``.env.bak``, and atomically replaces the original. Returns
    False without modifying the file if validation fails.
    """
    import re
    import shutil
    import tempfile

    env_path = ".env"
    backup_path = ".env.bak"

    # Validate before writing; abort (without touching the file) on failure.
    if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
        logging.error("Refusing to update .env: invalid key %r", key)
        return False
    if not isinstance(new_values, list) or any(
        not isinstance(v, str) or "\n" in v or "\r" in v for v in new_values
    ):
        logging.error("Refusing to update .env: values must be newline-free strings")
        return False

    try:
        # Read current .env content
        if not os.path.exists(env_path):
            logging.error("Cannot update .env file: file does not exist")
            return False

        with open(env_path, "r") as f:
            lines = f.readlines()

        # Find and update the key line, removing old continuation lines
        updated = False
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith(f"{key}="):
                # Found the key, remove this line and all continuation lines
                lines.pop(i)

                # Remove continuation lines (lines that don't start with a key)
                while i < len(lines):
                    next_line = lines[i].strip()
                    # Stop if we hit another key or empty line
                    if not next_line or next_line.startswith("#") or "=" in next_line:
                        break
                    # This is a continuation line, remove it
                    lines.pop(i)

                # Insert new values at this position
                if new_values:
                    # Write first value on the key line
                    lines.insert(i, f"{key}={new_values[0]},\n")
                    # Write remaining values as continuation lines
                    for j, value in enumerate(new_values[1:], 1):
                        lines.insert(i + j, f"{value},\n")
                else:
                    # Empty value
                    lines.insert(i, f"{key}=\n")

                updated = True
                break
            i += 1

        if not updated:
            # Add the key if it doesn't exist
            if new_values:
                lines.append(f"{key}={new_values[0]},\n")
                # Add additional lines
                for value in new_values[1:]:
                    lines.append(f"{value},\n")
            else:
                lines.append(f"{key}=\n")

        # Write atomically: temp file in the same directory, fsync, back up the
        # previous version, then os.replace() onto the original.
        env_dir = os.path.dirname(os.path.abspath(env_path)) or "."
        fd, tmp_path = tempfile.mkstemp(prefix=".env.", suffix=".tmp", dir=env_dir)
        try:
            with os.fdopen(fd, "w") as tmp:
                tmp.writelines(lines)
                tmp.flush()
                os.fsync(tmp.fileno())
            shutil.copy2(env_path, backup_path)
            os.replace(tmp_path, env_path)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

        logging.info(f"Updated .env file with {len(new_values)} {key} values")
        return True
    except Exception as e:
        logging.error(f"Failed to update .env file: {e}")
        return False


def validate_testflight_id_format(tf_id):
    """
    Validate TestFlight ID format.

    Args:
        tf_id: The TestFlight ID to validate

    Returns:
        tuple: (is_valid, message)
    """
    if not tf_id or not tf_id.strip():
        return False, "TestFlight ID cannot be empty"

    tf_id = tf_id.strip()

    # TestFlight IDs are typically 8-12 alphanumeric characters
    import re

    if not re.match(r"^[a-zA-Z0-9]{8,12}$", tf_id):
        return False, (
            "Invalid TestFlight ID format. " "ID must be 8-12 alphanumeric characters"
        )

    return True, "Valid format"


async def validate_testflight_id(tf_id):
    """Validate if a TestFlight ID exists and is accessible."""
    # First check format
    is_valid_format, format_message = validate_testflight_id_format(tf_id)
    if not is_valid_format:
        return False, format_message

    tf_id = tf_id.strip()
    testflight_url = format_link(TESTFLIGHT_URL, tf_id)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                testflight_url, headers={"Accept-Language": "en-us"}
            ) as response:
                if response.status == 404:
                    return False, "TestFlight ID not found (404)"
                elif response.status != 200:
                    return False, f"HTTP {response.status} error"

                text = await response.text()
                soup = BeautifulSoup(text, "html.parser")

                # Check if it's a valid TestFlight page
                title = soup.find("title")
                if not title or "TestFlight" not in title.text:
                    return False, "Not a valid TestFlight page"

                return True, "Valid TestFlight ID"
    except Exception as e:
        return False, f"Error validating ID: {str(e)}"


def add_testflight_id(tf_id):
    """Add a TestFlight ID to the list and update .env file."""
    with id_list_lock:
        if tf_id in current_id_list:
            return False, "TestFlight ID already exists"

        new_list = current_id_list + [tf_id]
        if update_env_file("ID_LIST", new_list):
            current_id_list.append(tf_id)
            logging.info(f"Added TestFlight ID: {tf_id}")
            # Send notification about the addition
            total_ids = len(current_id_list)
            msg = f"TestFlight ID Added: {tf_id} (Total: {total_ids} IDs)"
            send_notification(msg, apobj)
            return True, "TestFlight ID added successfully"
        else:
            return False, "Failed to update .env file"


def remove_testflight_id(tf_id):
    """Remove a TestFlight ID from the list and update .env file."""
    with id_list_lock:
        if tf_id not in current_id_list:
            return False, "TestFlight ID not found"

        new_list = [id for id in current_id_list if id != tf_id]
        if update_env_file("ID_LIST", new_list):
            current_id_list.remove(tf_id)
            logging.info(f"Removed TestFlight ID: {tf_id}")
            # Send notification about the removal
            total_ids = len(current_id_list)
            msg = f"TestFlight ID Removed: {tf_id} (Total: {total_ids} IDs)"
            send_notification(msg, apobj)
            return True, "TestFlight ID removed successfully"
        else:
            return False, "Failed to update .env file"


def validate_apprise_url(url: str) -> tuple[bool, str]:
    """Validate if an Apprise URL is properly formatted and supported."""
    if not url or not url.strip():
        return False, "Apprise URL cannot be empty"

    url = url.strip()

    # Use Apprise library to validate the URL format
    try:
        # Create a temporary Apprise object to test URL validity
        test_apprise = apprise.Apprise()
        result = test_apprise.add(url)

        if result:
            # URL was successfully added, get service information
            urls = test_apprise.urls()
            if urls:
                service_info = urls[0]
                service_name = service_info.get("service_name", "Unknown Service")
                return True, f"Valid {service_name} URL"
            else:
                return True, "Valid Apprise URL"
        else:
            # Try to provide more specific error information
            msg = (
                "Invalid Apprise URL format. Please check "
                "the URL syntax and ensure the service is supported."
            )
            return False, msg

    except Exception as e:
        # Fallback to basic validation if Apprise validation fails
        logging.warning(f"Apprise validation error for URL {mask_secret(url)}: {e}")

        # Basic URL validation - should start with a protocol
        supported_protocols = [
            # Productivity Based Notifications
            "apprise://",
            "apprises://",
            "ses://",
            "bark://",
            "barks://",
            "bluesky://",
            "chantify://",
            "discord://",
            "emby://",
            "embys://",
            "enigma2://",
            "enigma2s://",
            "fcm://",
            "feishu://",
            "flock://",
            "gchat://",
            "gotify://",
            "gotifys://",
            "growl://",
            "guilded://",
            "hassio://",
            "hassios://",
            "ifttt://",
            "join://",
            "kodi://",
            "kodis://",
            "kumulos://",
            "lametric://",
            "lark://",
            "line://",
            "mailgun://",
            "mastodon://",
            "mastodons://",
            "matrix://",
            "matrixs://",
            "mmost://",
            "mmosts://",
            "workflows://",
            "msteams://",
            "misskey://",
            "misskeys://",
            "mqtt://",
            "mqtts://",
            "ncloud://",
            "nclouds://",
            "nctalk://",
            "nctalks://",
            "notica://",
            "notifiarr://",
            "notifico://",
            "ntfy://",
            "o365://",
            "onesignal://",
            "opsgenie://",
            "pagerduty://",
            "pagertree://",
            "parsep://",
            "parseps://",
            "popcorn://",
            "prowl://",
            "pbul://",
            "pjet://",
            "pjets://",
            "push://",
            "pushed://",
            "pushme://",
            "pushover://",
            "pover://",
            "pushplus://",
            "psafer://",
            "psafers://",
            "pushy://",
            "pushdeer://",
            "pushdeers://",
            "qq://",
            "reddit://",
            "resend://",
            "revolt://",
            "rocket://",
            "rockets://",
            "rsyslog://",
            "ryver://",
            "sendgrid://",
            "sendpulse://",
            "schan://",
            "signal://",
            "signals://",
            "signl4://",
            "simplepush://",
            "slack://",
            "smtp2go://",
            "sparkpost://",
            "spike://",
            "splunk://",
            "victorops://",
            "spugpush://",
            "strmlabs://",
            "synology://",
            "synologys://",
            "syslog://",
            "tgram://",
            "twitter://",
            "twist://",
            "vapid://",
            "wxteams://",
            "wecombot://",
            "whatsapp://",
            "wxpusher://",
            "xbmc://",
            "xbmcs://",
            "zulip://",
            # SMS Notifications
            "atalk://",
            "aprs://",
            "sns://",
            "bulksms://",
            "bulkvs://",
            "burstsms://",
            "clickatell://",
            "clicksend://",
            "dapnet://",
            "d7sms://",
            "dingtalk://",
            "freemobile://",
            "httpsms://",
            "kavenegar://",
            "msgbird://",
            "msg91://",
            "plivo://",
            "seven://",
            "sfr://",
            "smpp://",
            "smpps://",
            "smseagle://",
            "smseagles://",
            "smsmgr://",
            "threema://",
            "twilio://",
            "voipms://",
            "nexmo://",
            # Desktop Notifications
            "dbus://",
            "qt://",
            "glib://",
            "kde://",
            "gnome://",
            "macosx://",
            "windows://",
            # Email Notifications
            "mailto://",
            "mailtos://",
            # Custom Notifications
            "form://",
            "forms://",
            "json://",
            "jsons://",
            "xml://",
            "xmls://",
            # Backward compatibility
            "telegram://",
        ]

        if not any(url.startswith(protocol) for protocol in supported_protocols):
            supported_services = [
                "HTTP/HTTPS (http://, https://)",
                "Email (mailto:)",
                "Slack (slack://)",
                "Discord (discord://)",
                "Telegram (tgram://)",
                "Pushover (pushover://)",
                "Gotify (gotify://)",
                "Zulip (zulip://)",
                "Matrix (matrix://)",
                "Rocket.Chat (rocketchat://)",
                "Mattermost (mattermost://)",
                "Microsoft Teams (teams://)",
                "Webex (webex://)",
                "Zoom (zoom://)",
                "Webhooks (webhook://)",
                "Generic (generic://)",
            ]
            msg = (
                "Invalid URL format. Must start with a supported protocol. "
                f"Examples: {', '.join(supported_services[:8])}..."
            )
            return False, msg

        return True, "Valid Apprise URL (basic validation)"


def add_apprise_url(url: str) -> tuple[bool, str]:
    """Add an Apprise URL to the list and update .env file."""
    with apprise_urls_lock:
        if url in current_apprise_urls:
            return False, "Apprise URL already exists"

        # Validate the URL
        is_valid, message = validate_apprise_url(url)
        if not is_valid:
            return False, message

        new_list = current_apprise_urls + [url]
        if update_env_file("APPRISE_URL", new_list):
            current_apprise_urls.append(url)
            # Add to the live Apprise object
            apobj.add(url)
            logging.info(f"Added Apprise URL: {mask_secret(url)}")
            # Send notification about the addition
            total_urls = len(current_apprise_urls)
            msg = f"Apprise URL Added: {mask_secret(url)} (Total: {total_urls} URLs)"
            send_notification(msg, apobj)
            return True, "Apprise URL added successfully"
        else:
            return False, "Failed to update .env file"


def remove_apprise_url(url: str) -> tuple[bool, str]:
    """Remove an Apprise URL from the list and update .env file."""
    with apprise_urls_lock:
        if url not in current_apprise_urls:
            return False, "Apprise URL not found"

        new_list = [u for u in current_apprise_urls if u != url]
        if update_env_file("APPRISE_URL", new_list):
            current_apprise_urls.remove(url)
            # Remove from the live Apprise object by recreating it
            apobj.clear()
            for remaining_url in current_apprise_urls:
                apobj.add(remaining_url)
            logging.info(f"Removed Apprise URL: {mask_secret(url)}")
            # Send notification about the removal
            total_urls = len(current_apprise_urls)
            msg = f"Apprise URL Removed: {mask_secret(url)} (Total: {total_urls} URLs)"
            send_notification(msg, apobj)
            return True, "Apprise URL removed successfully"
        else:
            return False, "Failed to update .env file"


async def check_github_updates(force: bool = False) -> Dict[str, Any]:
    """
    Check for updates from GitHub repository.

    Args:
        force: If True, bypass the interval check and force an update check

    Returns:
        Dictionary containing update information and status
    """
    global _last_update_check

    with _update_check_lock:
        # Check if we should skip based on interval (unless forced)
        if not force and _last_update_check is not None:
            time_since_check = time.time() - _last_update_check["timestamp"]
            if time_since_check < GITHUB_CHECK_INTERVAL * 3600:  # Convert hours
                return {
                    "status": "cached",
                    "message": "Using cached update check result",
                    "last_check": _last_update_check["checked_at"],
                    "next_check_in_hours": round(
                        (GITHUB_CHECK_INTERVAL * 3600 - time_since_check) / 3600, 2
                    ),
                    **{
                        k: v
                        for k, v in _last_update_check.items()
                        if k not in ["timestamp", "checked_at"]
                    },
                }

    try:
        session = await get_http_session()

        # Get latest commit from GitHub API
        api_url = f"https://api.github.com/repos/{GITHUB_REPO}/commits/{GITHUB_BRANCH}"
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "TestFlight-Apprise-Notifier",
        }

        async with session.get(api_url, headers=headers, timeout=10) as response:
            if response.status != 200:
                error_msg = f"GitHub API returned status {response.status}"
                logging.warning(f"Update check failed: {error_msg}")
                return {
                    "status": "error",
                    "message": error_msg,
                    "checked_at": format_datetime(datetime.now()),
                }

            data = await response.json()

            # Extract commit information
            latest_commit = data.get("sha", "unknown")[:7]  # Short SHA
            commit_date = data.get("commit", {}).get("author", {}).get("date", "")
            commit_message = data.get("commit", {}).get("message", "").split("\n")[0]
            commit_url = data.get("html_url", "")

            # Try to read current version from a VERSION file or use git
            current_version = "unknown"
            try:
                if os.path.exists("VERSION"):
                    with open("VERSION", "r") as f:
                        current_version = f.read().strip()
                else:
                    # Try to get git commit if available
                    import subprocess

                    result = subprocess.run(
                        ["git", "rev-parse", "--short", "HEAD"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    if result.returncode == 0:
                        current_version = result.stdout.strip()
            except Exception as e:
                logging.debug(f"Could not determine current version: {e}")

            update_available = (
                current_version != "unknown" and latest_commit != current_version
            )

            result = {
                "status": "success",
                "update_available": update_available,
                "current_version": current_version,
                "latest_version": latest_commit,
                "latest_commit_date": commit_date,
                "latest_commit_message": commit_message,
                "commit_url": commit_url,
                "repository": GITHUB_REPO,
                "branch": GITHUB_BRANCH,
                "checked_at": format_datetime(datetime.now()),
                "timestamp": time.time(),
            }

            # Cache the result
            with _update_check_lock:
                _last_update_check = result.copy()

            if update_available:
                logging.info(f"Update available: {current_version} -> {latest_commit}")
                # Send notification about update
                msg = (
                    f"🔔 TestFlight Notifier Update Available!\n"
                    f"Current: {current_version}\n"
                    f"Latest: {latest_commit}\n"
                    f"Message: {commit_message}"
                )
                await send_notification_async(msg, apobj)
            else:
                logging.info("No updates available - running latest version")

            return result

    except asyncio.TimeoutError:
        error_msg = "GitHub API request timed out"
        logging.warning(f"Update check failed: {error_msg}")
        return {
            "status": "error",
            "message": error_msg,
            "checked_at": format_datetime(datetime.now()),
        }
    except Exception as e:
        error_msg = f"Update check failed: {str(e)}"
        logging.error(error_msg)
        return {
            "status": "error",
            "message": error_msg,
            "checked_at": format_datetime(datetime.now()),
        }


async def periodic_update_check():
    """Background task to periodically check for GitHub updates."""
    logging.info(
        f"Starting periodic update checker "
        f"(interval: {GITHUB_CHECK_INTERVAL} hours)"
    )

    while not shutdown_event.is_set():
        try:
            await check_github_updates(force=False)
        except Exception as e:
            logging.error(f"Error in periodic update check: {e}")

        # Wait for the interval or until shutdown
        try:
            await asyncio.wait_for(
                shutdown_event.wait(), timeout=GITHUB_CHECK_INTERVAL * 3600
            )
            break  # Shutdown event was set
        except asyncio.TimeoutError:
            continue  # Timeout reached, do another check


def handle_shutdown_signal():
    logging.info("Shutdown signal received. Cleaning up...")
    shutdown_event.set()


def _perform_restart():
    """Replace the current process image with a fresh instance.

    Using os.execv (rather than spawning a child via subprocess) keeps the
    same PID, so it works both on bare metal and inside a container where the
    app is PID 1 - a spawned child would die when the original process exits
    and take the container down with it.
    """
    import sys

    python_executable = sys.executable
    script_path = os.path.abspath(sys.argv[0])
    logging.info("Re-executing application for restart...")
    os.execv(python_executable, [python_executable, script_path])
