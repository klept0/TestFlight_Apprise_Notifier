import os
import re
import asyncio
import aiohttp
import uvicorn
import apprise
import threading
import logging
import signal
import random
import itertools
import time
from collections import deque
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from datetime import datetime
from typing import Optional, Dict, Any
from utils.notifications import send_notification, send_notification_async
from utils.formatting import (
    format_datetime,
    format_link,
    format_notification_link,
    get_app_icon,
    get_app_name,
    app_name_cache,
    app_icon_cache,
)
from utils.colors import print_green
from utils.testflight import (
    check_testflight_status,
    TestFlightStatus,
    is_status_available,
    enable_status_cache,
)

# Enable status caching with 5-minute TTL for improved performance
enable_status_cache(ttl_seconds=300)

# Circuit breaker for external requests
_request_failures = {}
_circuit_breaker_threshold = 5
_circuit_breaker_timeout = 300  # 5 minutes

# Global HTTP session and lock
_http_session = None
_session_lock = threading.Lock()

# GitHub repository configuration (override via environment variables)
GITHUB_REPO = os.getenv("GITHUB_REPO", "klept0/TestFlight_Apprise_Notifier")
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")
GITHUB_CHECK_INTERVAL = int(os.getenv("GITHUB_CHECK_INTERVAL_HOURS", "24"))

# Status tracking for change notifications
_previous_status = {}  # tf_id -> TestFlightStatus
_status_lock = threading.Lock()

# Track whether an OPEN notification has been sent per TestFlight ID
_open_notified: Dict[str, bool] = {}  # tf_id -> notification sent?
_open_notified_lock = threading.Lock()

# Configuration: force notifications for every OPEN poll
ALWAYS_NOTIFY_OPEN = os.getenv("ALWAYS_NOTIFY_OPEN", "false").lower() in (
    "1",
    "true",
    "yes",
    "on",
)

# GitHub update check tracking
_last_update_check: Optional[Dict[str, Any]] = None
_update_check_lock = threading.Lock()


class MetricsCollector:
    """
    Collect and track metrics for TestFlight status checks.

    Tracks total checks, successes, failures, and status counts.
    """

    def __init__(self):
        """Initialize metrics collector."""
        self.total_checks = 0
        self.successful_checks = 0
        self.failed_checks = 0
        self.status_counts = {
            "open": 0,
            "full": 0,
            "closed": 0,
            "unknown": 0,
            "error": 0,
        }
        self.start_time = time.time()
        self._lock = threading.Lock()

    def record_check(self, status: TestFlightStatus, success: bool = True):
        """
        Record a status check.

        Args:
            status: The TestFlightStatus result
            success: Whether the check was successful
        """
        with self._lock:
            self.total_checks += 1
            if success:
                self.successful_checks += 1
                status_key = status.value.lower()
                if status_key in self.status_counts:
                    self.status_counts[status_key] += 1
            else:
                self.failed_checks += 1
                self.status_counts["error"] += 1

    def get_stats(self):
        """
        Get current statistics.

        Returns:
            dict: Statistics dictionary with all metrics
        """
        with self._lock:
            uptime = time.time() - self.start_time
            return {
                "total_checks": self.total_checks,
                "successful_checks": self.successful_checks,
                "failed_checks": self.failed_checks,
                "status_counts": self.status_counts.copy(),
                "uptime_seconds": uptime,
                "checks_per_minute": (
                    (self.total_checks / uptime * 60) if uptime > 0 else 0
                ),
            }

    def reset(self):
        """Reset all metrics."""
        with self._lock:
            self.total_checks = 0
            self.successful_checks = 0
            self.failed_checks = 0
            self.status_counts = {
                "open": 0,
                "full": 0,
                "closed": 0,
                "unknown": 0,
                "error": 0,
            }
            self.start_time = time.time()


# Global metrics collector
_metrics = MetricsCollector()

_circuit_breaker_timeout = 300  # 5 minutes

# Global HTTP session and lock
_http_session = None
_session_lock = threading.Lock()


def is_circuit_breaker_open(url: str) -> bool:
    """Check if circuit breaker is open for a URL."""
    if url in _request_failures:
        failures, last_failure = _request_failures[url]
        if failures >= _circuit_breaker_threshold:
            if time.time() - last_failure < _circuit_breaker_timeout:
                return True
            else:
                # Reset circuit breaker after timeout
                del _request_failures[url]
    return False


def record_request_failure(url: str):
    """Record a request failure for circuit breaker."""
    if url not in _request_failures:
        _request_failures[url] = [0, 0]
    _request_failures[url][0] += 1
    _request_failures[url][1] = time.time()


def record_request_success(url: str):
    """Record a request success, resetting circuit breaker."""
    if url in _request_failures:
        del _request_failures[url]


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
                        "User-Agent": "TestFlight-Notifier/1.0.8",
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": "en-US,en;q=0.9",
                        "Accept-Encoding": "gzip, deflate, br",
                        "Connection": "keep-alive",
                    },
                )
    return _http_session


# Version
__version__ = "1.0.8"


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


# Load environment variables
load_dotenv()

# Constants
TESTFLIGHT_URL = "https://testflight.apple.com/join/"
FULL_TEXT = "This beta is full."
NOT_OPEN_TEXT = "This beta isn't accepting any new testers right now."

# Parse ID_LIST (supporting multi-line format)
id_list_raw_value = get_multiline_env_value("ID_LIST")
id_list_raw = [
    tf_id.strip().rstrip(",")
    for tf_id in id_list_raw_value.replace("\n", ",").split(",")
    if tf_id.strip()
]

SLEEP_TIME = int(os.getenv("INTERVAL_CHECK", "10000"))  # in ms
TITLE_REGEX = re.compile(r"Join the (.+) beta - TestFlight - Apple")

# Parse Apprise URLs (supporting multi-line format)
apprise_url_raw = get_multiline_env_value("APPRISE_URL")
apprise_urls_raw = [
    url.strip().rstrip(",")
    for url in apprise_url_raw.replace("\n", ",").split(",")
    if url.strip()
]

# Heartbeat interval (default: 6 hours)
# Can be configured via HEARTBEAT_INTERVAL environment variable (in hours)
HEARTBEAT_INTERVAL = (
    int(os.getenv("HEARTBEAT_INTERVAL", "6")) * 60 * 60
)  # Convert hours to seconds



# Configure logging with colored output
class ColoredFormatter(logging.Formatter):
    """Custom formatter that adds color to log levels in console."""

    # ANSI color codes
    COLORS = {
        'DEBUG': '\033[36m',      # Cyan
        'INFO': '\033[90m',       # Gray
        'WARNING': '\033[93m',    # Yellow
        'ERROR': '\033[91m',      # Red
        'CRITICAL': '\033[91m',   # Red (same as ERROR)
    }
    RESET = '\033[0m'            # Reset color

    def format(self, record):
        # Get the log level color
        levelname = record.levelname
        color = self.COLORS.get(levelname, self.RESET)

        # Color the level name
        record.levelname = f"{color}{levelname}{self.RESET}"

        # Format the message
        return super().format(record)


format_str = f"%(asctime)s - %(levelname)s - %(message)s [v{__version__}]"

# Create console handler with colored formatter
console_handler = logging.StreamHandler()
console_handler.setFormatter(ColoredFormatter(format_str))

# Configure logging - use force=True to avoid duplicate basicConfig issues
# Don't pass handlers here, we'll configure manually
logging.basicConfig(level=logging.INFO, force=True)

# Clear any default handlers and add only our custom one
root_logger = logging.getLogger()
root_logger.handlers.clear()
root_logger.addHandler(console_handler)
root_logger.setLevel(logging.INFO)

# Prevent propagation in child loggers that might have their own handlers
logging.getLogger("uvicorn").propagate = True
logging.getLogger("uvicorn.error").propagate = True
logging.getLogger("uvicorn.access").propagate = True

# Web logging handler to capture logs for web interface
log_entries: deque = deque(maxlen=100)  # Keep last 100 log entries
log_entries_lock = threading.Lock()  # Thread safety for log access
app_start_time = datetime.now()


class WebLogHandler(logging.Handler):
    def emit(self, record):
        log_entry = {
            "timestamp": datetime.fromtimestamp(record.created).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        with log_entries_lock:
            log_entries.append(log_entry)


def get_recent_logs(limit: int = 20) -> list:
    """Thread-safe function to get recent log entries."""
    with log_entries_lock:
        total_entries = len(log_entries)
        if total_entries == 0:
            return []

        # Use efficient slicing for better performance
        start_idx = max(0, total_entries - limit)
        return list(itertools.islice(log_entries, start_idx, total_entries))


# Add the web log handler to the root logger (will be attached in ensure_web_handler_attached)
web_handler = WebLogHandler()
_web_handler_attached = False  # Track if web handler has been attached


def ensure_web_handler_attached():
    """
    Ensure WebLogHandler is attached to all relevant loggers.
    This is called after uvicorn initializes to make sure our handler
    captures all logs including uvicorn logs.
    """
    global _web_handler_attached
    
    if _web_handler_attached:
        return  # Already attached, don't add again
    
    # Only attach to root logger - handlers propagate to child loggers
    root_logger = logging.getLogger()
    
    # Double-check no duplicate WebLogHandlers exist
    for handler in root_logger.handlers:
        if isinstance(handler, WebLogHandler):
            _web_handler_attached = True
            logging.debug("WebLogHandler already present on root logger")
            return
    
    # Add web handler
    root_logger.addHandler(web_handler)
    _web_handler_attached = True
    logging.debug("WebLogHandler attached to root logger")


# Custom uvicorn log config that preserves our formatting
def get_uvicorn_log_config():
    """
    Create a uvicorn log config that uses our existing logging setup.

    This prevents uvicorn from reconfiguring logging while still allowing
    it to log properly through our configured handlers.
    """
    return {
        "version": 1,
        "disable_existing_loggers": False,  # Keep our handlers!
        "formatters": {
            "default": {
                "format": format_str,
            },
        },
        "handlers": {},  # Don't create any new handlers - use root logger
        "loggers": {
            "uvicorn": {"level": "INFO", "propagate": True},
            "uvicorn.error": {"level": "INFO", "propagate": True},
            "uvicorn.access": {"level": "INFO", "propagate": True},
        },
        "root": {
            "level": "INFO",
        },
    }


# Validate environment variables
ID_LIST = [tf_id.strip() for tf_id in id_list_raw if tf_id.strip()]
APPRISE_URLS = [url.strip() for url in apprise_urls_raw if url.strip()]

# Allow empty ID_LIST - user can add IDs via web interface
if not ID_LIST:
    logging.warning(
        "Environment variable 'ID_LIST' is empty. "
        "No TestFlight IDs will be monitored until added via web interface."
    )

# Apprise URLs are still required for notifications to work
if not APPRISE_URLS:
    logging.error(
        "Environment variable 'APPRISE_URL' is not set or contains only empty values."
    )
    raise ValueError("Environment variable 'APPRISE_URL' is required.")

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


def get_current_id_list():
    """Thread-safe function to get current ID list."""
    with id_list_lock:
        return current_id_list.copy()


def get_current_apprise_urls():
    """Thread-safe function to get current Apprise URLs list."""
    with apprise_urls_lock:
        return current_apprise_urls.copy()


def get_apprise_service_icon(url: str) -> Dict[str, str]:
    """
    Get service icon URL and name for an Apprise URL.

    Returns a dict with 'icon_url', 'service_name', and 'fallback_emoji'.
    """
    # Service icon mappings (using public CDN URLs for logos)
    service_map = {
        "discord": {
            "icon_url": "https://cdn.simpleicons.org/discord/5865F2",
            "service_name": "Discord",
            "emoji": "💬",
        },
        "slack": {
            "icon_url": "https://cdn.simpleicons.org/slack/4A154B",
            "service_name": "Slack",
            "emoji": "💼",
        },
        "telegram": {
            "icon_url": "https://cdn.simpleicons.org/telegram/26A5E4",
            "service_name": "Telegram",
            "emoji": "✈️",
        },
        "tgram": {
            "icon_url": "https://cdn.simpleicons.org/telegram/26A5E4",
            "service_name": "Telegram",
            "emoji": "✈️",
        },
        "pushover": {
            "icon_url": "https://cdn.simpleicons.org/pushover/249DF1",
            "service_name": "Pushover",
            "emoji": "📱",
        },
        "pover": {
            "icon_url": "https://cdn.simpleicons.org/pushover/249DF1",
            "service_name": "Pushover",
            "emoji": "📱",
        },
        "gotify": {
            "icon_url": "https://cdn.simpleicons.org/gotify/00A4D8",
            "service_name": "Gotify",
            "emoji": "🔔",
        },
        "mailto": {
            "icon_url": "https://cdn.simpleicons.org/gmail/EA4335",
            "service_name": "Email",
            "emoji": "📧",
        },
        "mailtos": {
            "icon_url": "https://cdn.simpleicons.org/gmail/EA4335",
            "service_name": "Email",
            "emoji": "📧",
        },
        "ntfy": {
            "icon_url": "https://cdn.simpleicons.org/ntfy/3A9EEA",
            "service_name": "ntfy",
            "emoji": "🔔",
        },
        "matrix": {
            "icon_url": "https://cdn.simpleicons.org/matrix/000000",
            "service_name": "Matrix",
            "emoji": "💬",
        },
        "mattermost": {
            "icon_url": "https://cdn.simpleicons.org/mattermost/0058CC",
            "service_name": "Mattermost",
            "emoji": "💬",
        },
        "rocketchat": {
            "icon_url": "https://cdn.simpleicons.org/rocketdotchat/F5455C",
            "service_name": "Rocket.Chat",
            "emoji": "🚀",
        },
        "teams": {
            "icon_url": "https://cdn.simpleicons.org/microsoftteams/6264A7",
            "service_name": "Microsoft Teams",
            "emoji": "👥",
        },
        "webhook": {
            "icon_url": "https://cdn.simpleicons.org/webhooks/2088FF",
            "service_name": "Webhook",
            "emoji": "🌐",
        },
        "json": {
            "icon_url": "https://cdn.simpleicons.org/json/000000",
            "service_name": "JSON",
            "emoji": "🌐",
        },
        "xml": {
            "icon_url": "https://cdn.simpleicons.org/xml/005FAD",
            "service_name": "XML",
            "emoji": "🌐",
        },
        "prowl": {
            "icon_url": "https://cdn.simpleicons.org/apple/000000",
            "service_name": "Prowl",
            "emoji": "🍎",
        },
        "pushbullet": {
            "icon_url": "https://cdn.simpleicons.org/pushbullet/4AB367",
            "service_name": "Pushbullet",
            "emoji": "📱",
        },
        "signal": {
            "icon_url": "https://cdn.simpleicons.org/signal/3A76F0",
            "service_name": "Signal",
            "emoji": "💬",
        },
        "twilio": {
            "icon_url": "https://cdn.simpleicons.org/twilio/F22F46",
            "service_name": "Twilio",
            "emoji": "📱",
        },
        "twitter": {
            "icon_url": "https://cdn.simpleicons.org/x/000000",
            "service_name": "Twitter/X",
            "emoji": "🐦",
        },
        "mastodon": {
            "icon_url": "https://cdn.simpleicons.org/mastodon/6364FF",
            "service_name": "Mastodon",
            "emoji": "🐘",
        },
        "reddit": {
            "icon_url": "https://cdn.simpleicons.org/reddit/FF4500",
            "service_name": "Reddit",
            "emoji": "👽",
        },
        "ifttt": {
            "icon_url": "https://cdn.simpleicons.org/ifttt/000000",
            "service_name": "IFTTT",
            "emoji": "⚡",
        },
        "join": {
            "icon_url": "https://cdn.simpleicons.org/android/3DDC84",
            "service_name": "Join",
            "emoji": "📱",
        },
        "kodi": {
            "icon_url": "https://cdn.simpleicons.org/kodi/17B2E7",
            "service_name": "Kodi",
            "emoji": "📺",
        },
        "xbmc": {
            "icon_url": "https://cdn.simpleicons.org/kodi/17B2E7",
            "service_name": "XBMC",
            "emoji": "📺",
        },
        "synology": {
            "icon_url": "https://cdn.simpleicons.org/synology/B5B5B6",
            "service_name": "Synology",
            "emoji": "💾",
        },
        "webex": {
            "icon_url": "https://cdn.simpleicons.org/webex/000000",
            "service_name": "Webex",
            "emoji": "👥",
        },
        "zulip": {
            "icon_url": "https://cdn.simpleicons.org/zulip/52C2AF",
            "service_name": "Zulip",
            "emoji": "💬",
        },
        "homeassistant": {
            "icon_url": "https://cdn.simpleicons.org/homeassistant/18BCF2",
            "service_name": "Home Assistant",
            "emoji": "🏠",
        },
        "gitter": {
            "icon_url": "https://cdn.simpleicons.org/gitter/ED1965",
            "service_name": "Gitter",
            "emoji": "💬",
        },
        "notica": {
            "icon_url": "https://cdn.simpleicons.org/notifications/FF6B6B",
            "service_name": "Notica",
            "emoji": "🔔",
        },
        "notifico": {
            "icon_url": "https://cdn.simpleicons.org/notifications/FF6B6B",
            "service_name": "Notifico",
            "emoji": "🔔",
        },
        "opsgenie": {
            "icon_url": "https://cdn.simpleicons.org/opsgenie/172B4D",
            "service_name": "Opsgenie",
            "emoji": "🚨",
        },
        "pagerduty": {
            "icon_url": "https://cdn.simpleicons.org/pagerduty/06AC38",
            "service_name": "PagerDuty",
            "emoji": "🚨",
        },
    }

    # Extract service type from URL
    url_lower = url.lower()
    for service_key, service_info in service_map.items():
        if url_lower.startswith(f"{service_key}://") or url_lower.startswith(
            f"{service_key}s://"
        ):
            return service_info

    # Default fallback for unknown services
    if url_lower.startswith("http://") or url_lower.startswith("https://"):
        return {
            "icon_url": "https://cdn.simpleicons.org/webhooks/2088FF",
            "service_name": "Webhook",
            "emoji": "🌐",
        }

    # Generic fallback
    return {"icon_url": "", "service_name": "Unknown Service", "emoji": "📢"}


def update_env_file(key: str, new_values: list[str]):
    """Safely update the .env file with new values for a given key."""
    try:
        # Read current .env content
        env_path = ".env"
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

        # Write back to file atomically
        with open(env_path, "w") as f:
            f.writelines(lines)

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
        logging.warning(f"Apprise validation error for URL {url}: {e}")

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
            logging.info(f"Added Apprise URL: {url}")
            # Send notification about the addition
            total_urls = len(current_apprise_urls)
            msg = f"Apprise URL Added: {url} (Total: {total_urls} URLs)"
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
            logging.info(f"Removed Apprise URL: {url}")
            # Send notification about the removal
            total_urls = len(current_apprise_urls)
            msg = f"Apprise URL Removed: {url} (Total: {total_urls} URLs)"
            send_notification(msg, apobj)
            return True, "Apprise URL removed successfully"
        else:
            return False, "Failed to update .env file"


# Graceful shutdown (cross-platform compatibility)
shutdown_event = asyncio.Event()


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


async def cleanup_http_session():
    """Clean up the shared HTTP session."""
    global _http_session
    if _http_session and not _http_session.closed:
        await _http_session.close()
        _http_session = None


def handle_shutdown_signal():
    logging.info("Shutdown signal received. Cleaning up...")
    shutdown_event.set()


# Only attach signal handlers on non-Windows
if os.name != "nt":
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_shutdown_signal)
        except NotImplementedError:
            logging.warning(
                f"Signal handling is not implemented for {sig} on this platform."
            )


# FastAPI lifespan context manager for proper startup/shutdown handling
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manage FastAPI application lifespan.

    Handles startup and shutdown events gracefully, including proper
    exception handling for cancellation during shutdown/restart.
    """
    # Startup
    logging.info("FastAPI application starting up...")
    # Ensure web handler is attached after any uvicorn initialization
    ensure_web_handler_attached()

    # Start periodic GitHub update checker if enabled
    update_task = None
    if GITHUB_CHECK_INTERVAL > 0:
        update_task = asyncio.create_task(periodic_update_check())
        logging.info("GitHub update checker started")

    try:
        yield
    except asyncio.CancelledError:
        # This is expected during graceful shutdown/restart
        logging.info("FastAPI lifespan cancelled during shutdown/restart")
    finally:
        # Shutdown
        logging.info("FastAPI application shutting down...")
        if update_task and not update_task.done():
            update_task.cancel()
            try:
                await update_task
            except asyncio.CancelledError:
                pass
        await cleanup_http_session()


# FastAPI server
app = FastAPI(lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def home():
    uptime = datetime.now() - app_start_time
    uptime_str = str(uptime).split(".")[0]  # Remove microseconds

    # Get recent logs using thread-safe function
    recent_logs = get_recent_logs(20)  # Show last 20 entries

    # Build log HTML
    log_html = ""
    for entry in reversed(recent_logs):  # Show newest first
        level_color = {
            "INFO": "#28a745",
            "WARNING": "#ffc107",
            "ERROR": "#dc3545",
            "DEBUG": "#6c757d",
        }.get(entry["level"], "#000000")

        log_html += f"""
        <div style="margin: 5px 0; padding: 5px; border-left: 3px solid {level_color}; background-color: var(--card-bg);">
            <span style="color: var(--text-secondary); font-size: 0.9em;">{entry['timestamp']}</span>
            <span style="color: {level_color}; font-weight: bold; margin-left: 10px;">[{entry['level']}]</span>
            <span style="margin-left: 10px; color: var(--text-color);">{entry['message']}</span>
        </div>
        """

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>TestFlight Apprise Notifier - Status</title>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <meta http-equiv="refresh" content="30">
        <style>
            :root {{
                --bg-color: #f5f5f5;
                --container-bg: white;
                --card-bg: #f8f9fa;
                --text-color: #333;
                --text-secondary: #666;
                --border-color: #dee2e6;
                --header-border: #007bff;
                --success-color: #28a745;
                --danger-color: #dc3545;
                --warning-color: #ffc107;
                --info-color: #17a2b8;
                --shadow: rgba(0,0,0,0.1);
            }}
            
            body.dark-mode {{
                --bg-color: #1a1a1a;
                --container-bg: #2d2d2d;
                --card-bg: #3a3a3a;
                --text-color: #e0e0e0;
                --text-secondary: #b0b0b0;
                --border-color: #404040;
                --header-border: #4a9eff;
                --success-color: #28a745;
                --danger-color: #dc3545;
                --warning-color: #ffc107;
                --info-color: #17a2b8;
                --shadow: rgba(0,0,0,0.3);
            }}
            
            body {{ 
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; 
                margin: 10px; 
                background-color: var(--bg-color);
                color: var(--text-color);
                transition: background-color 0.3s, color 0.3s;
            }}
            .container {{ 
                max-width: 1200px; 
                margin: 0 auto; 
                background-color: var(--container-bg); 
                padding: 15px; 
                border-radius: 8px; 
                box-shadow: 0 2px 4px var(--shadow);
                transition: background-color 0.3s, box-shadow 0.3s;
            }}
            .header {{ 
                text-align: center; 
                color: var(--text-color); 
                border-bottom: 2px solid var(--header-border); 
                padding-bottom: 10px; 
                margin-bottom: 20px;
                position: relative;
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 15px;
            }}
            .theme-toggle {{
                position: absolute;
                right: 0;
                background-color: var(--card-bg);
                border: 1px solid var(--border-color);
                border-radius: 50%;
                width: 40px;
                height: 40px;
                cursor: pointer;
                display: flex;
                align-items: center;
                justify-content: center;
                font-size: 1.3em;
                transition: background-color 0.3s, transform 0.2s;
            }}
            .theme-toggle:hover {{
                transform: scale(1.1);
                background-color: var(--border-color);
            }}
            .theme-toggle.dark {{
                filter: brightness(1.2);
            }}
            .status-grid {{ 
                display: grid; 
                grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); 
                gap: 20px; 
                margin-bottom: 30px;
            }}
            .status-card {{ 
                background-color: var(--card-bg); 
                padding: 15px; 
                border-radius: 6px; 
                border-left: 4px solid var(--success-color);
                transition: background-color 0.3s;
            }}
            .status-card h3 {{ 
                margin: 0 0 10px 0; 
                color: var(--text-color); 
                font-size: 1.1em;
            }}
            .status-card p {{
                margin: 5px 0;
                color: var(--text-secondary);
            }}
            .control-section {{
                margin: 30px 0;
                padding: 20px;
                background-color: var(--card-bg);
                border-radius: 8px;
                text-align: center;
                transition: background-color 0.3s;
            }}
            .control-section h2 {{
                margin: 0 0 20px 0;
                color: var(--text-color);
                font-size: 1.3em;
                font-weight: 600;
            }}
            .control-buttons {{
                display: flex;
                justify-content: center;
                gap: 20px;
                flex-wrap: wrap;
            }}
            .control-btn {{
                padding: 8px 16px;
                background-color: var(--success-color);
                color: white;
                border: none;
                border-radius: 4px;
                cursor: pointer;
                font-size: 1em;
                font-weight: 600;
                margin: 0 5px;
                min-width: 140px;
                transition: background-color 0.2s;
            }}
            .control-btn:hover {{
                opacity: 0.9;
            }}
            .control-btn:disabled {{
                opacity: 0.6;
                cursor: not-allowed;
            }}
            .stop-btn {{
                background-color: var(--danger-color);
            }}
            .restart-btn {{
                background-color: var(--warning-color);
                color: #212529;
            }}
            .btn-icon {{
                font-size: 1.2em;
                display: inline-block;
            }}
            .btn-text {{
                font-weight: 600;
            }}
            .logs-section {{ 
                margin-top: 30px;
            }}
            .logs-header {{ 
                background-color: var(--card-bg); 
                color: var(--text-color);
                padding: 10px 15px; 
                margin: 0; 
                border-radius: 6px 6px 0 0;
            }}
            .logs-container {{ 
                max-height: 400px; 
                overflow-y: auto; 
                border: 1px solid var(--border-color); 
                border-top: none; 
                border-radius: 0 0 6px 6px; 
                padding: 10px;
                background-color: var(--container-bg);
            }}
            .refresh-info {{ 
                text-align: center; 
                color: var(--text-secondary); 
                font-size: 0.9em; 
                margin-top: 20px;
            }}
            .management-section {{ 
                margin: 30px 0; 
                padding: 20px; 
                background-color: var(--card-bg); 
                border-radius: 8px;
                transition: background-color 0.3s;
            }}
            .management-grid {{
                display: grid;
                grid-template-columns: 1fr;
                gap: 20px;
            }}
            .management-card {{ 
                background-color: var(--container-bg); 
                padding: 15px; 
                border-radius: 6px; 
                border: 1px solid var(--border-color);
                transition: background-color 0.3s, border-color 0.3s;
            }}
            .management-card h3 {{
                margin: 0 0 15px 0;
                color: var(--text-color);
                font-size: 1.1em;
            }}
            .collapsible {{
                cursor: pointer;
                user-select: none;
                position: relative;
                padding-left: 20px;
            }}
            .collapsible:hover {{
                background-color: var(--border-color);
            }}
            .collapsible::before {{
                content: "+";
                position: absolute;
                left: 0;
                top: 50%;
                transform: translateY(-50%);
                color: var(--text-secondary);
                font-weight: bold;
                font-size: 1.2em;
                transition: transform 0.2s ease;
                margin-right: 5px;
            }}
            .collapsible.expanded::before {{
                content: "−";
                transform: translateY(-50%);
            }}
            .collapsible-content {{
                display: none;
                padding-left: 20px;
            }}
            .collapsible-content.expanded {{
                display: block;
            }}
            input[type="text"] {{
                background-color: var(--container-bg);
                color: var(--text-color);
                border: 1px solid var(--border-color) !important;
                transition: background-color 0.3s, color 0.3s, border-color 0.3s;
            }}
            input[type="text"]:focus {{
                outline: none;
                border-color: var(--header-border) !important;
                box-shadow: 0 0 0 3px rgba(74, 158, 255, 0.1);
            }}
            input[type="text"]::placeholder {{
                color: var(--text-secondary);
            }}
            @media (max-width: 768px) {{
                body {{
                    margin: 5px;
                }}
                .container {{
                    padding: 10px;
                }}
                .status-grid {{
                    grid-template-columns: 1fr;
                    gap: 15px;
                }}
                .management-section {{
                    margin: 20px 0;
                    padding: 15px;
                }}
                .logs-container {{
                    max-height: 300px;
                }}
                .control-section {{
                    padding: 15px;
                    margin: 20px 0;
                }}
                .control-buttons {{
                    gap: 10px;
                }}
                .control-btn {{
                    min-width: 120px;
                    padding: 8px 12px;
                    font-size: 0.9em;
                }}
            }}
            @media (max-width: 480px) {{
                .header {{
                    font-size: 1.5em;
                }}
                .status-card {{
                    padding: 12px;
                }}
                .management-card {{
                    padding: 12px;
                }}
                .collapsible {{
                    padding-left: 25px;
                    font-size: 1em;
                }}
                .control-section {{
                    padding: 15px;
                    margin: 15px 0;
                }}
                .control-section h2 {{
                    font-size: 1.1em;
                    margin-bottom: 10px;
                }}
                .control-buttons {{
                    flex-direction: column;
                    gap: 8px;
                }}
                .control-btn {{
                    min-width: 100%;
                    padding: 10px 16px;
                    font-size: 0.95em;
                }}
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1 style="margin: 0;">🚀 TestFlight Apprise Notifier</h1>
                <button class="theme-toggle" id="theme-toggle" title="Toggle dark mode" onclick="toggleTheme()">🌙</button>
            </div>
            
            <div class="status-grid">
                <div class="status-card">
                    <h3>🟢 Bot Status</h3>
                    <p><strong>Status:</strong> Running</p>
                    <p><strong>Version:</strong> v{__version__}</p>
                    <p><strong>Uptime:</strong> {uptime_str}</p>
                </div>
                
                <div class="status-card">
                    <h3>📱 Monitoring</h3>
                    <p><strong>TestFlight IDs:</strong> <span id="id-count">{len(ID_LIST)}</span></p>
                    <p><strong>Apprise URLs:</strong> <span id="url-count">{len(APPRISE_URLS)}</span></p>
                    <p><strong>Check Interval:</strong> {SLEEP_TIME/1000:.1f}s</p>
                </div>
                
                <div class="status-card">
                    <h3>💓 Heartbeat</h3>
                    <p><strong>Interval:</strong> {HEARTBEAT_INTERVAL//3600}h</p>
                    <p><strong>Last Check:</strong> {format_datetime(datetime.now())}</p>
                </div>
            </div>
            
            <div class="control-section">
                <h2 style="color: var(--text-color); margin-bottom: 20px; text-align: center;">
                    ⚙️ Application & Notification Settings
                </h2>
                <div class="control-buttons">
                    <button class="control-btn stop-btn" onclick="stopApplication()">
                        🛑 Stop Application
                    </button>
                    <button class="control-btn restart-btn" onclick="restartApplication()">
                        🔄 Restart Application
                    </button>
                </div>
                
                <div class="management-grid" style="margin-top: 30px;">
                    <div class="management-card">
                        <h3 class="collapsible" onclick="toggleCard(this)">Add Apprise URL</h3>
                        <div class="collapsible-content">
                            <div style="margin-bottom: 10px;">
                                <input type="text" id="new-apprise-url" 
                                       placeholder="Enter Apprise URL (e.g., discord://...)" 
                                       style="padding: 8px; width: 100%; max-width: 300px; border-radius: 4px; box-sizing: border-box;">
                                <button onclick="validateAndAddUrl()" 
                                        style="padding: 8px 16px; background-color: #28a745; color: white; border: none; border-radius: 4px; cursor: pointer; margin-top: 8px; width: 100%; max-width: 150px;">
                                    Validate & Add
                                </button>
                            </div>
                            <div id="add-url-status" style="margin-top: 10px; min-height: 20px;"></div>
                        </div>
                    </div>
                    
                    <div class="management-card">
                        <h3 class="collapsible" onclick="toggleCard(this)">Current Apprise URLs</h3>
                        <div class="collapsible-content">
                            <div id="current-urls" style="max-height: 200px; overflow-y: auto; padding: 10px; border-radius: 4px; background-color: var(--card-bg); border: 1px solid var(--border-color);">
                                Loading...
                            </div>
                            <button onclick="refreshUrls()" 
                                    style="margin-top: 10px; padding: 6px 12px; background-color: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer;">
                                🔄 Refresh
                            </button>
                        </div>
                    </div>
                </div>
            </div>
            
            <h2 style="color: var(--text-color); margin-bottom: 20px;">
                🔧 TestFlight ID Management
            </h2>
                
                <div class="management-grid">
                    <div class="management-card">
                        <h3 class="collapsible" onclick="toggleCard(this)">Add TestFlight ID</h3>
                        <div class="collapsible-content">
                            <div style="margin-bottom: 10px;">
                                <input type="text" id="new-tf-id" placeholder="Enter TestFlight ID" 
                                       style="padding: 8px; width: 100%; max-width: 250px; border-radius: 4px; box-sizing: border-box;">
                                <button onclick="validateAndAddId()" 
                                        style="padding: 8px 16px; background-color: #28a745; color: white; border: none; border-radius: 4px; cursor: pointer; margin-top: 8px; width: 100%; max-width: 150px;">
                                    Validate & Add
                                </button>
                            </div>
                            <div id="add-status" style="margin-top: 10px; min-height: 20px;"></div>
                        </div>
                    </div>
                    
                    <div class="management-card">
                        <h3 class="collapsible" onclick="toggleCard(this)">Current TestFlight IDs</h3>
                        <div class="collapsible-content">
                            <div id="current-ids" style="max-height: 200px; overflow-y: auto; padding: 10px; border-radius: 4px; background-color: var(--card-bg); border: 1px solid var(--border-color);">
                                Loading...
                            </div>
                            <button onclick="refreshIds()" 
                                    style="margin-top: 10px; padding: 6px 12px; background-color: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer;">
                                🔄 Refresh
                            </button>
                        </div>
                    </div>
                </div>
            </div>
            
            <h2 style="color: var(--text-color); margin: 30px 0 20px 0;">
                ⚙️ Configuration Editor
            </h2>
            
            <div class="management-grid">
                <div class="management-card">
                    <h3 class="collapsible" onclick="toggleCard(this)">Edit .env Configuration</h3>
                    <div class="collapsible-content">
                        <p style="color: var(--text-secondary); font-size: 0.9em; margin-bottom: 10px;">
                            Edit your environment configuration. Changes require a restart to take effect.
                        </p>
                        <textarea id="config-editor" 
                                  style="width: 100%; min-height: 200px; font-family: monospace; font-size: 0.9em; 
                                         padding: 10px; border-radius: 4px; border: 1px solid var(--border-color); 
                                         background-color: var(--container-bg); color: var(--text-color); 
                                         resize: vertical; box-sizing: border-box;"
                                  placeholder="Loading configuration..."></textarea>
                        <div style="margin-top: 10px; display: flex; gap: 10px; flex-wrap: wrap;">
                            <button onclick="loadConfig()" 
                                    style="padding: 8px 16px; background-color: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer;">
                                🔄 Reload
                            </button>
                            <button onclick="saveConfig()" 
                                    style="padding: 8px 16px; background-color: #28a745; color: white; border: none; border-radius: 4px; cursor: pointer;">
                                💾 Save Configuration
                            </button>
                            <button onclick="saveAndRestart()" 
                                    style="padding: 8px 16px; background-color: #fd7e14; color: white; border: none; border-radius: 4px; cursor: pointer;">
                                💾🔄 Save & Restart
                            </button>
                        </div>
                        <div id="config-status" style="margin-top: 10px; min-height: 20px;"></div>
                    </div>
                </div>
            </div>
            
            <div class="logs-section">
                <h3 class="logs-header">📜 Recent Activity (Last 20 entries)</h3>
                <div class="logs-container">
                    {log_html if log_html else '<div style="text-align: center; color: var(--text-secondary); padding: 20px;">No log entries yet...</div>'}
                </div>
            </div>
            
            <div class="refresh-info">
                🔄 Page auto-refreshes every 30 seconds | Last updated: {format_datetime(datetime.now())}
            </div>
        </div>
        
        <script>
            function toggleCard(header) {{
                const content = header.nextElementSibling;
                const isExpanded = content.classList.contains('expanded');
                
                // Toggle the content visibility
                content.classList.toggle('expanded');
                
                // Toggle the arrow rotation
                header.classList.toggle('expanded');
                
                // Optionally collapse other cards when one is expanded
                if (!isExpanded) {{
                    // Close other cards
                    document.querySelectorAll('.collapsible-content.expanded').forEach(otherContent => {{
                        if (otherContent !== content) {{
                            otherContent.classList.remove('expanded');
                            otherContent.previousElementSibling.classList.remove('expanded');
                        }}
                    }});
                }}
            }}
            
            async function refreshIds() {{
                try {{
                    const response = await fetch('/api/testflight-ids/details');
                    const data = await response.json();
                    displayIds(data.testflight_ids);
                    document.getElementById('id-count').textContent = data.testflight_ids.length;
                }} catch (error) {{
                    console.error('Error refreshing IDs:', error);
                }}
            }}
            
            function displayIds(ids) {{
                const container = document.getElementById('current-ids');
                if (ids.length === 0) {{
                    container.innerHTML = '<em>No TestFlight IDs configured</em>';
                    return;
                }}
                
                container.innerHTML = ids.map(item => {{
                    const displayName = item.display_name || item.id;
                    const isAppName = item.app_name && item.app_name !== item.id;
                    const iconHtml = item.icon_url ? 
                        `<img src="${{item.icon_url}}" style="width: 20px; height: 20px; border-radius: 4px; margin-right: 8px; vertical-align: middle;" alt="App Icon">` : 
                        '📱 ';
                    
                    return `<div style="display: flex; justify-content: space-between; align-items: center; padding: 8px 0; border-bottom: 1px solid var(--border-color);">
                        <div style="display: flex; align-items: center; flex-grow: 1;">
                            ${{iconHtml}}
                            <div>
                                <div style="font-weight: ${{isAppName ? 'bold' : 'normal'}}; color: var(--text-color);">${{displayName}}</div>
                                ${{isAppName ? `<div style="font-size: 0.8em; color: var(--text-secondary);">${{item.id}}</div>` : ''}}
                            </div>
                        </div>
                        <button onclick="removeId('${{item.id}}')" 
                                style="padding: 8px 16px; background-color: #dc3545; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 0.9em;">
                            ❌ Remove
                        </button>
                    </div>`;
                }}).join('');
            }}
            
            async function validateAndAddId() {{
                const tfId = document.getElementById('new-tf-id').value.trim();
                const statusDiv = document.getElementById('add-status');
                
                if (!tfId) {{
                    statusDiv.innerHTML = '<span style="color: #dc3545;">Please enter a TestFlight ID</span>';
                    return;
                }}
                
                statusDiv.innerHTML = '<span style="color: #007bff;">Validating...</span>';
                
                try {{
                    // First validate
                    const validateResponse = await fetch('/api/testflight-ids/validate', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify({{ id: tfId }})
                    }});
                    
                    const validateData = await validateResponse.json();
                    
                    if (!validateData.valid) {{
                        statusDiv.innerHTML = `<span style="color: #dc3545;">${{validateData.message}}</span>`;
                        return;
                    }}
                    
                    // Show validation success with app name if available
                    let validationMessage = 'Valid TestFlight ID';
                    if (validateData.app_name && validateData.app_name !== tfId) {{
                        const iconHtml = validateData.icon_url ? 
                            `<img src="${{validateData.icon_url}}" style="width: 16px; height: 16px; border-radius: 3px; margin-right: 5px; vertical-align: middle;" alt="App Icon">` : 
                            '📱 ';
                        validationMessage = `${{iconHtml}}Found: <strong>${{validateData.app_name}}</strong>`;
                    }}
                    statusDiv.innerHTML = `<span style="color: #28a745;">${{validationMessage}}</span>`;
                    
                    // Add small delay to show the validation result before adding
                    await new Promise(resolve => setTimeout(resolve, 1000));
                    
                    // If valid, add it
                    statusDiv.innerHTML = '<span style="color: #007bff;">Adding...</span>';
                    
                    const addResponse = await fetch('/api/testflight-ids', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify({{ id: tfId }})
                    }});
                    
                    const addData = await addResponse.json();
                    
                    if (addResponse.ok) {{
                        statusDiv.innerHTML = `<span style="color: #28a745;">${{addData.message}}</span>`;
                        document.getElementById('new-tf-id').value = '';
                        displayIds(addData.testflight_ids);
                        document.getElementById('id-count').textContent = addData.testflight_ids.length;
                    }} else {{
                        statusDiv.innerHTML = `<span style="color: #dc3545;">${{addData.detail || 'Failed to add ID'}}</span>`;
                    }}
                }} catch (error) {{
                    statusDiv.innerHTML = `<span style="color: #dc3545;">Error: ${{error.message}}</span>`;
                }}
            }}
            
            async function removeId(tfId) {{
                if (!confirm(`Are you sure you want to remove TestFlight ID "${{tfId}}"?`)) {{
                    return;
                }}
                
                try {{
                    const response = await fetch(`/api/testflight-ids/${{tfId}}`, {{
                        method: 'DELETE'
                    }});
                    
                    const data = await response.json();
                    
                    if (response.ok) {{
                        displayIds(data.testflight_ids);
                        document.getElementById('id-count').textContent = data.testflight_ids.length;
                        alert(data.message);
                    }} else {{
                        alert(`Failed to remove ID: ${{data.detail || 'Unknown error'}}`);
                    }}
                }} catch (error) {{
                    alert(`Error removing ID: ${{error.message}}`);
                }}
            }}
            
            async function refreshUrls() {{
                try {{
                    const response = await fetch('/api/apprise-urls');
                    const data = await response.json();
                    displayUrls(data.apprise_urls);
                }} catch (error) {{
                    console.error('Error refreshing URLs:', error);
                }}
            }}
            
            function displayUrls(urls) {{
                const container = document.getElementById('current-urls');
                if (urls.length === 0) {{
                    container.innerHTML = '<em>No Apprise URLs configured</em>';
                    return;
                }}
                
                container.innerHTML = urls.map(urlInfo => {{
                    const url = urlInfo.url;
                    const displayUrl = urlInfo.display_url;
                    const serviceName = urlInfo.service_name;
                    const iconUrl = urlInfo.icon_url;
                    const emoji = urlInfo.emoji;
                    
                    // Use icon if available, otherwise use emoji
                    let iconHtml;
                    if (iconUrl) {{
                        iconHtml = `<img src="${{iconUrl}}" alt="${{serviceName}}" 
                                        style="width: 24px; height: 24px; margin-right: 10px; vertical-align: middle;"
                                        onerror="this.style.display='none'; this.nextElementSibling.style.display='inline';">
                                    <span style="font-size: 1.3em; margin-right: 10px; display: none;">${{emoji}}</span>`;
                    }} else {{
                        iconHtml = `<span style="font-size: 1.3em; margin-right: 10px;">${{emoji}}</span>`;
                    }}
                    
                    return `<div style="display: flex; justify-content: space-between; align-items: center; padding: 10px; border-bottom: 1px solid var(--border-color);">
                        <div style="display: flex; align-items: center; flex-grow: 1; min-width: 0;">
                            <div style="flex-shrink: 0;">
                                ${{iconHtml}}
                            </div>
                            <div style="min-width: 0; flex-grow: 1;">
                                <div style="font-weight: 500; color: var(--text-color); margin-bottom: 2px;">${{serviceName}}</div>
                                <div style="font-family: monospace; font-size: 0.85em; color: var(--text-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${{displayUrl}}">${{displayUrl}}</div>
                            </div>
                        </div>
                        <button onclick="removeUrl('${{encodeURIComponent(url)}}')" 
                                style="padding: 6px 12px; background-color: #dc3545; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 0.85em; margin-left: 10px; flex-shrink: 0; white-space: nowrap;">
                            ❌ Remove
                        </button>
                    </div>`;
                }}).join('');
            }}
            
            async function validateAndAddUrl() {{
                const url = document.getElementById('new-apprise-url').value.trim();
                const statusDiv = document.getElementById('add-url-status');
                
                if (!url) {{
                    statusDiv.innerHTML = '<span style="color: #dc3545;">Please enter an Apprise URL</span>';
                    return;
                }}
                
                statusDiv.innerHTML = '<span style="color: #007bff;">Validating...</span>';
                
                try {{
                    // First validate
                    const validateResponse = await fetch('/api/apprise-urls/validate', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify({{ url: url }})
                    }});
                    
                    const validateData = await validateResponse.json();
                    
                    if (!validateData.valid) {{
                        statusDiv.innerHTML = `<span style="color: #dc3545;">${{validateData.message}}</span>`;
                        return;
                    }}
                    
                    // Show validation success
                    statusDiv.innerHTML = `<span style="color: #28a745;">${{validateData.message}}</span>`;
                    
                    // Add small delay to show the validation result before adding
                    await new Promise(resolve => setTimeout(resolve, 1000));
                    
                    // If valid, add it
                    statusDiv.innerHTML = '<span style="color: #007bff;">Adding...</span>';
                    
                    const addResponse = await fetch('/api/apprise-urls', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify({{ url: url }})
                    }});
                    
                    const addData = await addResponse.json();
                    
                    if (addResponse.ok) {{
                        statusDiv.innerHTML = `<span style="color: #28a745;">${{addData.message}}</span>`;
                        document.getElementById('new-apprise-url').value = '';
                        displayUrls(addData.apprise_urls);
                        document.getElementById('url-count').textContent = addData.apprise_urls.length;
                    }} else {{
                        statusDiv.innerHTML = `<span style="color: #dc3545;">${{addData.detail || 'Failed to add URL'}}</span>`;
                    }}
                }} catch (error) {{
                    statusDiv.innerHTML = `<span style="color: #dc3545;">Error: ${{error.message}}</span>`;
                }}
            }}
            
            async function removeUrl(encodedUrl) {{
                const url = decodeURIComponent(encodedUrl);
                if (!confirm(`Are you sure you want to remove this Apprise URL?`)) {{
                    return;
                }}
                
                try {{
                    const response = await fetch(`/api/apprise-urls/${{encodedUrl}}`, {{
                        method: 'DELETE'
                    }});
                    
                    const data = await response.json();
                    
                    if (response.ok) {{
                        displayUrls(data.apprise_urls);
                        document.getElementById('url-count').textContent = data.apprise_urls.length;
                        alert(data.message);
                    }} else {{
                        alert(`Failed to remove URL: ${{data.detail || 'Unknown error'}}`);
                    }}
                }} catch (error) {{
                    alert(`Error removing URL: ${{error.message}}`);
                }}
            }}
            
            // Theme management
            function initializeTheme() {{
                const savedTheme = localStorage.getItem('theme-preference');
                const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
                const theme = savedTheme || (prefersDark ? 'dark' : 'light');
                
                applyTheme(theme);
            }}
            
            function toggleTheme() {{
                const body = document.body;
                const isDark = body.classList.contains('dark-mode');
                const newTheme = isDark ? 'light' : 'dark';
                
                applyTheme(newTheme);
                localStorage.setItem('theme-preference', newTheme);
            }}
            
            function applyTheme(theme) {{
                const body = document.body;
                const toggle = document.getElementById('theme-toggle');
                
                if (theme === 'dark') {{
                    body.classList.add('dark-mode');
                    toggle.textContent = '☀️';
                    toggle.classList.add('dark');
                }} else {{
                    body.classList.remove('dark-mode');
                    toggle.textContent = '🌙';
                    toggle.classList.remove('dark');
                }}
            }}
            
            // Load IDs on page load
            document.addEventListener('DOMContentLoaded', function() {{
                initializeTheme();
                refreshIds();
                refreshUrls();
                loadConfig();
                // Initialize collapsible sections - expand by default
                document.querySelectorAll('.collapsible-content').forEach(content => {{
                    content.classList.add('expanded');
                }});
                document.querySelectorAll('.collapsible').forEach(header => {{
                    header.classList.add('expanded');
                }});
            }});
            
            // Allow Enter key to submit
            document.getElementById('new-tf-id').addEventListener('keypress', function(e) {{
                if (e.key === 'Enter') {{
                    validateAndAddId();
                }}
            }});
            
            async function stopApplication() {{
                if (!confirm('Are you sure you want to stop the TestFlight Apprise Notifier? This will shut down the application.')) {{
                    return;
                }}
                
                try {{
                    const response = await fetch('/api/control/stop', {{
                        method: 'POST'
                    }});
                    
                    if (response.ok) {{
                        alert('Application is shutting down...');
                        // Disable buttons
                        document.querySelector('.stop-btn').disabled = true;
                        document.querySelector('.restart-btn').disabled = true;
                        // Show shutdown message
                        document.querySelector('.container').innerHTML = `
                            <div style="text-align: center; padding: 50px;">
                                <h2>🛑 Application Stopped</h2>
                                <p>The TestFlight Apprise Notifier has been shut down.</p>
                                <p>To restart, run the application again from the command line.</p>
                            </div>
                        `;
                    }} else {{
                        alert('Failed to stop application');
                    }}
                }} catch (error) {{
                    alert('Error stopping application: ' + error.message);
                }}
            }}
            
            async function restartApplication() {{
                if (!confirm('Are you sure you want to restart the TestFlight Apprise Notifier? This will reload the application with any code changes.')) {{
                    return;
                }}
                
                try {{
                    const response = await fetch('/api/control/restart', {{
                        method: 'POST'
                    }});
                    
                    if (response.ok) {{
                        alert('Application is restarting...');
                        // Disable buttons
                        document.querySelector('.stop-btn').disabled = true;
                        document.querySelector('.restart-btn').disabled = true;
                        // Show restart message
                        document.querySelector('.container').innerHTML = `
                            <div style="text-align: center; padding: 50px;">
                                <h2>🔄 Application Restarting</h2>
                                <p>The TestFlight Apprise Notifier is restarting...</p>
                                <p>Please wait a moment and refresh the page.</p>
                            </div>
                        `;
                    }} else {{
                        alert('Failed to restart application');
                    }}
                }} catch (error) {{
                    alert('Error restarting application: ' + error.message);
                }}
            }}
            
            // Config Editor functions
            async function loadConfig() {{
                const editor = document.getElementById('config-editor');
                const statusDiv = document.getElementById('config-status');
                
                try {{
                    statusDiv.innerHTML = '<span style="color: #007bff;">Loading...</span>';
                    const response = await fetch('/api/config');
                    const data = await response.json();
                    
                    if (response.ok) {{
                        editor.value = data.content;
                        statusDiv.innerHTML = '<span style="color: #28a745;">Configuration loaded</span>';
                    }} else {{
                        statusDiv.innerHTML = `<span style="color: #dc3545;">${{data.detail || 'Failed to load config'}}</span>`;
                    }}
                }} catch (error) {{
                    statusDiv.innerHTML = `<span style="color: #dc3545;">Error: ${{error.message}}</span>`;
                }}
            }}
            
            async function saveConfig() {{
                const editor = document.getElementById('config-editor');
                const statusDiv = document.getElementById('config-status');
                const content = editor.value;
                
                try {{
                    statusDiv.innerHTML = '<span style="color: #007bff;">Saving...</span>';
                    
                    const formData = new FormData();
                    formData.append('content', content);
                    
                    const response = await fetch('/api/config', {{
                        method: 'POST',
                        body: formData
                    }});
                    
                    const data = await response.json();
                    
                    if (response.ok) {{
                        statusDiv.innerHTML = '<span style="color: #28a745;">✅ Configuration saved! Restart to apply changes.</span>';
                    }} else {{
                        statusDiv.innerHTML = `<span style="color: #dc3545;">${{data.detail || 'Failed to save config'}}</span>`;
                    }}
                }} catch (error) {{
                    statusDiv.innerHTML = `<span style="color: #dc3545;">Error: ${{error.message}}</span>`;
                }}
            }}
            
            async function saveAndRestart() {{
                const editor = document.getElementById('config-editor');
                const statusDiv = document.getElementById('config-status');
                const content = editor.value;
                
                if (!confirm('Save configuration and restart the application?')) {{
                    return;
                }}
                
                try {{
                    statusDiv.innerHTML = '<span style="color: #007bff;">Saving configuration...</span>';
                    
                    const formData = new FormData();
                    formData.append('content', content);
                    
                    const response = await fetch('/api/config', {{
                        method: 'POST',
                        body: formData
                    }});
                    
                    const data = await response.json();
                    
                    if (response.ok) {{
                        statusDiv.innerHTML = '<span style="color: #28a745;">Configuration saved! Restarting...</span>';
                        
                        // Trigger restart
                        await fetch('/api/control/restart', {{ method: 'POST' }});
                        
                        document.querySelector('.container').innerHTML = `
                            <div style="text-align: center; padding: 50px;">
                                <h2>🔄 Application Restarting</h2>
                                <p>Configuration saved. The application is restarting...</p>
                                <p>Please wait a moment and refresh the page.</p>
                            </div>
                        `;
                    }} else {{
                        statusDiv.innerHTML = `<span style="color: #dc3545;">${{data.detail || 'Failed to save config'}}</span>`;
                    }}
                }} catch (error) {{
                    statusDiv.innerHTML = `<span style="color: #dc3545;">Error: ${{error.message}}</span>`;
                }}
            }}
        </script>
    </body>
    </html>
    """
    return html_content


@app.get("/api/health")
async def health_check():
    """Health check endpoint for monitoring."""
    current_ids = get_current_id_list()
    circuit_breaker_status = {
        url: failures for url, (failures, _) in _request_failures.items()
    }

    return {
        "status": "healthy",
        "version": __version__,
        "uptime_seconds": int((datetime.now() - app_start_time).total_seconds()),
        "monitored_ids": len(current_ids),
        "cache_stats": {
            "app_names": len(app_name_cache.cache),
            "app_icons": len(app_icon_cache.cache),
        },
        "circuit_breaker": {
            "open_circuits": len(
                [
                    url
                    for url in circuit_breaker_status.keys()
                    if is_circuit_breaker_open(url)
                ]
            ),
            "total_tracked": len(circuit_breaker_status),
        },
        "http_session": (
            "active" if _http_session and not _http_session.closed else "inactive"
        ),
        "timestamp": format_datetime(datetime.now()),
    }


@app.get("/api/metrics")
async def get_metrics():
    """Get metrics and statistics for TestFlight checks."""
    stats = _metrics.get_stats()
    return {
        "total_checks": stats["total_checks"],
        "successful_checks": stats["successful_checks"],
        "failed_checks": stats["failed_checks"],
        "status_counts": stats["status_counts"],
        "uptime_seconds": stats["uptime_seconds"],
        "checks_per_minute": round(stats["checks_per_minute"], 2),
        "timestamp": format_datetime(datetime.now()),
    }


@app.get("/api/logs")
async def api_logs(limit: int = 50):
    """API endpoint for recent logs in JSON format with input validation"""
    # Input validation for limit parameter
    if limit < 1:
        raise HTTPException(status_code=400, detail="Limit must be at least 1")
    if limit > 1000:
        raise HTTPException(
            status_code=400, detail="Limit cannot exceed 1000 for performance reasons"
        )

    # Get logs using thread-safe function with efficient slicing
    recent_logs = get_recent_logs(limit)

    with log_entries_lock:
        total_entries = len(log_entries)

    return {
        "logs": list(reversed(recent_logs)),
        "total_entries": total_entries,
        "limit": limit,
    }


@app.get("/api/testflight-ids")
async def get_testflight_ids():
    """Get current list of TestFlight IDs."""
    return {"testflight_ids": get_current_id_list()}


@app.get("/api/updates")
async def api_check_updates(force: bool = False):
    """
    Check for GitHub updates via API.

    Query parameters:
        force (bool): If true, bypass cache and force a new check

    Example usage:
        curl http://localhost:8080/api/updates
        curl http://localhost:8080/api/updates?force=true
    """
    result = await check_github_updates(force=force)
    return result


@app.get("/api/testflight-ids/details")
async def get_testflight_ids_details():
    """Get detailed information for all TestFlight IDs."""
    current_ids = get_current_id_list()
    details = []

    for tf_id in current_ids:
        try:
            # Get app name (with caching)
            app_name = await get_app_name(TESTFLIGHT_URL, tf_id)

            # Get app icon URL (with caching)
            icon_url = await get_app_icon(TESTFLIGHT_URL, tf_id)

            details.append(
                {
                    "id": tf_id,
                    "app_name": app_name if app_name != tf_id else None,
                    "display_name": app_name,
                    "icon_url": icon_url,
                }
            )
        except Exception as e:
            logging.warning(f"Failed to get details for TestFlight ID {tf_id}: {e}")
            # Fallback to just the ID
            details.append(
                {"id": tf_id, "app_name": None, "display_name": tf_id, "icon_url": None}
            )

    return {"testflight_ids": details}


@app.post("/api/testflight-ids/validate")
async def validate_id(request: Request):
    """Validate a TestFlight ID."""
    data = await request.json()
    tf_id = data.get("id", "").strip()

    if not tf_id:
        raise HTTPException(status_code=400, detail="TestFlight ID is required")

    is_valid, message = await validate_testflight_id(tf_id)

    # If valid, also get the app name and icon for display
    app_name = None
    icon_url = None
    if is_valid:
        try:
            app_name = await get_app_name(TESTFLIGHT_URL, tf_id)
            icon_url = await get_app_icon(TESTFLIGHT_URL, tf_id)
        except Exception as e:
            logging.warning(
                f"Failed to get app details during validation for {tf_id}: {e}"
            )

    return {
        "valid": is_valid,
        "message": message,
        "app_name": app_name,
        "icon_url": icon_url,
    }


@app.post("/api/testflight-ids")
async def add_id(request: Request):
    """Add a new TestFlight ID."""
    data = await request.json()
    tf_id = data.get("id", "").strip()

    if not tf_id:
        raise HTTPException(status_code=400, detail="TestFlight ID is required")

    # Validate first
    is_valid, message = await validate_testflight_id(tf_id)
    if not is_valid:
        raise HTTPException(status_code=400, detail=message)

    # Add to list
    success, message = add_testflight_id(tf_id)
    if not success:
        raise HTTPException(status_code=400, detail=message)

    return {"message": message, "testflight_ids": get_current_id_list()}


@app.delete("/api/testflight-ids/{tf_id}")
async def remove_id(tf_id: str):
    """Remove a TestFlight ID."""
    success, message = remove_testflight_id(tf_id)
    if not success:
        raise HTTPException(status_code=404, detail=message)

    return {"message": message, "testflight_ids": get_current_id_list()}


@app.post("/api/testflight-ids/batch")
async def batch_operations(request: Request):
    """
    Perform batch operations on TestFlight IDs.

    Accepts JSON with:
    {
        "add": ["id1", "id2", ...],
        "remove": ["id3", "id4", ...]
    }

    Returns:
    {
        "added": {"successful": [...], "failed": [...]},
        "removed": {"successful": [...], "failed": [...]},
        "testflight_ids": [...]
    }
    """
    data = await request.json()
    ids_to_add = data.get("add", [])
    ids_to_remove = data.get("remove", [])

    result = {
        "added": {"successful": [], "failed": []},
        "removed": {"successful": [], "failed": []},
    }

    # Process additions
    for tf_id in ids_to_add:
        tf_id = tf_id.strip()
        if not tf_id:
            result["added"]["failed"].append(
                {"id": tf_id, "error": "ID cannot be empty"}
            )
            continue

        try:
            # Validate ID
            is_valid, message = await validate_testflight_id(tf_id)
            if not is_valid:
                result["added"]["failed"].append({"id": tf_id, "error": message})
                continue

            # Add to list
            success, message = add_testflight_id(tf_id)
            if success:
                result["added"]["successful"].append(tf_id)
            else:
                result["added"]["failed"].append({"id": tf_id, "error": message})
        except Exception as e:
            result["added"]["failed"].append({"id": tf_id, "error": str(e)})

    # Process removals
    for tf_id in ids_to_remove:
        tf_id = tf_id.strip()
        if not tf_id:
            result["removed"]["failed"].append(
                {"id": tf_id, "error": "ID cannot be empty"}
            )
            continue

        try:
            success, message = remove_testflight_id(tf_id)
            if success:
                result["removed"]["successful"].append(tf_id)
            else:
                result["removed"]["failed"].append({"id": tf_id, "error": message})
        except Exception as e:
            result["removed"]["failed"].append({"id": tf_id, "error": str(e)})

    result["testflight_ids"] = get_current_id_list()
    return result


@app.get("/api/apprise-urls")
async def get_apprise_urls():
    """Get current list of Apprise URLs with service information."""
    urls = get_current_apprise_urls()

    # Add service information for each URL
    urls_with_info = []
    for url in urls:
        service_info = get_apprise_service_icon(url)

        # Mask sensitive parts of URL for display
        display_url = url
        try:
            # Try to parse as URL to mask credentials
            if "://" in url:
                parts = url.split("://", 1)
                if len(parts) == 2:
                    scheme = parts[0]
                    rest = parts[1]
                    # Look for @ symbol indicating credentials
                    if "@" in rest:
                        # Mask everything before @
                        after_at = rest.split("@", 1)[1]
                        display_url = f"{scheme}://***@{after_at}"
                    # Hide query parameters for security
                    if "?" in display_url:
                        display_url = display_url.split("?")[0] + "?***"
        except Exception:
            # If parsing fails, show abbreviated version
            display_url = url[:30] + "..." if len(url) > 30 else url

        urls_with_info.append(
            {
                "url": url,  # Full URL for removal operations
                "display_url": display_url,
                "service_name": service_info["service_name"],
                "icon_url": service_info["icon_url"],
                "emoji": service_info["emoji"],
            }
        )

    return {"apprise_urls": urls_with_info}


@app.post("/api/apprise-urls/validate")
async def validate_url(request: Request):
    """Validate an Apprise URL."""
    data = await request.json()
    url = data.get("url", "").strip()

    if not url:
        raise HTTPException(status_code=400, detail="Apprise URL is required")

    is_valid, message = validate_apprise_url(url)

    return {
        "valid": is_valid,
        "message": message,
    }


@app.post("/api/apprise-urls")
async def add_url(request: Request):
    """Add a new Apprise URL."""
    data = await request.json()
    url = data.get("url", "").strip()

    if not url:
        raise HTTPException(status_code=400, detail="Apprise URL is required")

    # Add to list
    success, message = add_apprise_url(url)
    if not success:
        raise HTTPException(status_code=400, detail=message)

    return {"message": message, "apprise_urls": get_current_apprise_urls()}


@app.delete("/api/apprise-urls/{url:path}")
async def remove_url(url: str):
    """Remove an Apprise URL."""
    # URL decode the path parameter since it might contain special characters
    import urllib.parse

    decoded_url = urllib.parse.unquote(url)

    success, message = remove_apprise_url(decoded_url)
    if not success:
        raise HTTPException(status_code=404, detail=message)

    return {"message": message, "apprise_urls": get_current_apprise_urls()}


@app.post("/api/control/stop")
async def stop_application():
    """Stop the application gracefully."""
    logging.info("Stop command received via web interface")
    # Send notification about the stop
    try:
        msg = "🛑 TestFlight Apprise Notifier stopped via web interface"
        send_notification(msg, apobj)
    except Exception:
        pass  # Ignore notification errors during shutdown

    # Trigger graceful shutdown
    handle_shutdown_signal()
    return {"message": "Application is shutting down..."}


@app.post("/api/control/restart")
async def restart_application():
    """Restart the application."""
    logging.info("Restart command received via web interface")

    # Send notification about the restart
    try:
        msg = "🔄 TestFlight Apprise Notifier restarting via web interface"
        send_notification(msg, apobj)
    except Exception:
        pass  # Ignore notification errors during restart

    # Get the current Python executable and script path
    import sys
    import subprocess
    import os

    python_executable = sys.executable
    script_path = os.path.abspath(sys.argv[0])

    try:
        # Start a new instance of the application
        subprocess.Popen(
            [python_executable, script_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )

        logging.info("New application instance started, shutting down current instance")

        # Trigger graceful shutdown of current instance
        handle_shutdown_signal()

        return {"message": "Application is restarting..."}

    except Exception as e:
        logging.error(f"Failed to restart application: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to restart: {str(e)}")


@app.get("/api/config")
async def get_config():
    """Get the current .env file contents."""
    try:
        env_path = ".env"
        if not os.path.exists(env_path):
            return {
                "exists": False,
                "content": "",
                "message": ".env file does not exist"
            }
        
        with open(env_path, "r") as f:
            content = f.read()
        
        return {
            "exists": True,
            "content": content,
            "path": os.path.abspath(env_path)
        }
    except Exception as e:
        logging.error(f"Failed to read .env file: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to read config: {str(e)}")


@app.post("/api/config")
async def save_config(content: str = Form(...)):
    """Save changes to the .env file."""
    try:
        if not isinstance(content, str):
            raise HTTPException(status_code=400, detail="Content must be a string")
        
        env_path = ".env"
        
        # Create backup before saving
        if os.path.exists(env_path):
            backup_path = f"{env_path}.backup"
            with open(env_path, "r") as f:
                backup_content = f.read()
            with open(backup_path, "w") as f:
                f.write(backup_content)
            logging.info(f"Created backup at {backup_path}")
        
        # Write new content
        with open(env_path, "w") as f:
            f.write(content)
        
        logging.info("Configuration file updated via web interface")
        
        return {
            "success": True,
            "message": "Configuration saved. Restart to apply changes.",
            "backup_created": True
        }
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Failed to save .env file: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to save config: {str(e)}"
        )


@app.post("/api/config/restore")
async def restore_config():
    """Restore .env file from backup."""
    try:
        env_path = ".env"
        backup_path = f"{env_path}.backup"
        
        if not os.path.exists(backup_path):
            raise HTTPException(status_code=404, detail="No backup file found")
        
        with open(backup_path, "r") as f:
            backup_content = f.read()
        
        with open(env_path, "w") as f:
            f.write(backup_content)
        
        logging.info("Configuration restored from backup via web interface")
        
        return {
            "success": True,
            "message": "Configuration restored from backup. Restart the application for changes to take effect.",
            "content": backup_content
        }
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Failed to restore .env file: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to restore config: {str(e)}")


@app.get("/config", response_class=HTMLResponse)
async def config_editor_page():
    """Serve the configuration editor page."""
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>TestFlight Apprise Notifier - Config Editor</title>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            :root {
                --bg-color: #f5f5f5;
                --container-bg: white;
                --card-bg: #f8f9fa;
                --text-color: #333;
                --text-secondary: #666;
                --border-color: #dee2e6;
                --header-border: #007bff;
                --success-color: #28a745;
                --danger-color: #dc3545;
                --warning-color: #ffc107;
                --info-color: #17a2b8;
                --shadow: rgba(0,0,0,0.1);
                --editor-bg: #1e1e1e;
                --editor-text: #d4d4d4;
                --editor-line-numbers: #858585;
                --editor-highlight: #264f78;
            }
            
            body.dark-mode {
                --bg-color: #1a1a1a;
                --container-bg: #2d2d2d;
                --card-bg: #3a3a3a;
                --text-color: #e0e0e0;
                --text-secondary: #b0b0b0;
                --border-color: #404040;
                --header-border: #4a9eff;
                --editor-bg: #1e1e1e;
                --editor-text: #d4d4d4;
            }
            
            body { 
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; 
                margin: 0;
                padding: 10px;
                background-color: var(--bg-color);
                color: var(--text-color);
                transition: background-color 0.3s, color 0.3s;
            }
            
            .container { 
                max-width: 1200px; 
                margin: 0 auto; 
                background-color: var(--container-bg); 
                padding: 20px; 
                border-radius: 10px; 
                box-shadow: 0 2px 10px var(--shadow);
            }
            
            .header {
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 20px;
                padding-bottom: 15px;
                border-bottom: 3px solid var(--header-border);
            }
            
            .header h1 {
                margin: 0;
                font-size: 1.5em;
            }
            
            .header-buttons {
                display: flex;
                gap: 10px;
                align-items: center;
            }
            
            .back-btn {
                padding: 8px 16px;
                border: none;
                border-radius: 5px;
                cursor: pointer;
                font-size: 14px;
                background-color: var(--info-color);
                color: white;
                text-decoration: none;
            }
            
            .back-btn:hover {
                opacity: 0.9;
            }
            
            .theme-toggle {
                padding: 8px 12px;
                border: 1px solid var(--border-color);
                border-radius: 5px;
                background: var(--card-bg);
                cursor: pointer;
                font-size: 18px;
                transition: all 0.3s;
            }
            
            .editor-container {
                margin-bottom: 20px;
            }
            
            .editor-toolbar {
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 10px;
                padding: 10px;
                background-color: var(--card-bg);
                border-radius: 5px 5px 0 0;
            }
            
            .editor-info {
                font-size: 0.9em;
                color: var(--text-secondary);
            }
            
            .editor-actions {
                display: flex;
                gap: 10px;
            }
            
            .btn {
                padding: 8px 16px;
                border: none;
                border-radius: 5px;
                cursor: pointer;
                font-size: 14px;
                font-weight: 500;
                transition: all 0.2s;
            }
            
            .btn-primary {
                background-color: var(--success-color);
                color: white;
            }
            
            .btn-primary:hover {
                opacity: 0.9;
            }
            
            .btn-secondary {
                background-color: var(--card-bg);
                color: var(--text-color);
                border: 1px solid var(--border-color);
            }
            
            .btn-secondary:hover {
                background-color: var(--border-color);
            }
            
            .btn-danger {
                background-color: var(--danger-color);
                color: white;
            }
            
            .btn-danger:hover {
                opacity: 0.9;
            }
            
            .btn-warning {
                background-color: var(--warning-color);
                color: #333;
            }
            
            .btn-warning:hover {
                opacity: 0.9;
            }
            
            .btn:disabled {
                opacity: 0.5;
                cursor: not-allowed;
            }
            
            .editor {
                width: 100%;
                min-height: 500px;
                padding: 15px;
                font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
                font-size: 14px;
                line-height: 1.5;
                background-color: var(--editor-bg);
                color: var(--editor-text);
                border: 1px solid var(--border-color);
                border-radius: 0 0 5px 5px;
                resize: vertical;
                box-sizing: border-box;
                tab-size: 2;
            }
            
            .editor:focus {
                outline: 2px solid var(--header-border);
                outline-offset: -2px;
            }
            
            .status-message {
                padding: 15px;
                border-radius: 5px;
                margin-bottom: 20px;
                display: none;
            }
            
            .status-message.success {
                background-color: rgba(40, 167, 69, 0.1);
                border: 1px solid var(--success-color);
                color: var(--success-color);
                display: block;
            }
            
            .status-message.error {
                background-color: rgba(220, 53, 69, 0.1);
                border: 1px solid var(--danger-color);
                color: var(--danger-color);
                display: block;
            }
            
            .status-message.warning {
                background-color: rgba(255, 193, 7, 0.1);
                border: 1px solid var(--warning-color);
                color: #856404;
                display: block;
            }
            
            .help-section {
                margin-top: 20px;
                padding: 15px;
                background-color: var(--card-bg);
                border-radius: 5px;
                border-left: 4px solid var(--info-color);
            }
            
            .help-section h3 {
                margin-top: 0;
                color: var(--info-color);
            }
            
            .help-section code {
                background-color: var(--editor-bg);
                color: var(--editor-text);
                padding: 2px 6px;
                border-radius: 3px;
                font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
            }
            
            .help-section ul {
                margin: 10px 0;
                padding-left: 20px;
            }
            
            .help-section li {
                margin: 5px 0;
            }
            
            .unsaved-indicator {
                display: none;
                color: var(--warning-color);
                font-weight: bold;
            }
            
            .unsaved-indicator.visible {
                display: inline;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>⚙️ Configuration Editor</h1>
                <div class="header-buttons">
                    <span class="unsaved-indicator" id="unsaved-indicator">● Unsaved changes</span>
                    <a href="/" class="back-btn">← Back to Dashboard</a>
                    <button class="theme-toggle" id="theme-toggle" onclick="toggleTheme()" title="Toggle dark/light mode">🌙</button>
                </div>
            </div>
            
            <div id="status-message" class="status-message"></div>
            
            <div class="editor-container">
                <div class="editor-toolbar">
                    <div class="editor-info">
                        <strong>File:</strong> <span id="file-path">.env</span>
                    </div>
                    <div class="editor-actions">
                        <button class="btn btn-secondary" onclick="reloadConfig()" title="Reload from disk">🔄 Reload</button>
                        <button class="btn btn-warning" onclick="restoreBackup()" title="Restore from backup">⏪ Restore Backup</button>
                        <button class="btn btn-primary" onclick="saveConfig()" id="save-btn">💾 Save Changes</button>
                    </div>
                </div>
                <textarea class="editor" id="config-editor" spellcheck="false" placeholder="Loading configuration..."></textarea>
            </div>
            
            <div class="help-section">
                <h3>📖 Configuration Reference</h3>
                <ul>
                    <li><code>ID_LIST</code> - Comma-separated TestFlight IDs to monitor (e.g., <code>abc123,xyz789</code>)</li>
                    <li><code>APPRISE_URL</code> - Apprise notification URL(s), comma-separated for multiple services</li>
                    <li><code>INTERVAL_CHECK</code> - Check interval in milliseconds (default: <code>10000</code>)</li>
                    <li><code>HEARTBEAT_INTERVAL</code> - Hours between heartbeat notifications (default: <code>6</code>, set to <code>0</code> to disable)</li>
                    <li><code>ALWAYS_NOTIFY_OPEN</code> - Notify on every check when OPEN (<code>true</code>/<code>false</code>)</li>
                    <li><code>FASTAPI_HOST</code> - Web dashboard bind address (default: <code>0.0.0.0</code>)</li>
                    <li><code>FASTAPI_PORT</code> - Web dashboard port (default: <code>8080</code>)</li>
                </ul>
                <p><strong>⚠️ Note:</strong> After saving changes, you must <strong>restart the application</strong> for them to take effect.</p>
            </div>
        </div>
        
        <script>
            let originalContent = '';
            let hasUnsavedChanges = false;
            
            // Theme management
            function initializeTheme() {
                const savedTheme = localStorage.getItem('theme') || 'light';
                applyTheme(savedTheme);
            }
            
            function toggleTheme() {
                const body = document.body;
                const newTheme = body.classList.contains('dark-mode') ? 'light' : 'dark';
                localStorage.setItem('theme', newTheme);
                applyTheme(newTheme);
            }
            
            function applyTheme(theme) {
                const body = document.body;
                const toggle = document.getElementById('theme-toggle');
                
                if (theme === 'dark') {
                    body.classList.add('dark-mode');
                    toggle.textContent = '☀️';
                } else {
                    body.classList.remove('dark-mode');
                    toggle.textContent = '🌙';
                }
            }
            
            function showStatus(message, type) {
                const statusEl = document.getElementById('status-message');
                statusEl.textContent = message;
                statusEl.className = 'status-message ' + type;
                
                if (type === 'success') {
                    setTimeout(() => {
                        statusEl.className = 'status-message';
                    }, 5000);
                }
            }
            
            function updateUnsavedIndicator() {
                const editor = document.getElementById('config-editor');
                const indicator = document.getElementById('unsaved-indicator');
                hasUnsavedChanges = editor.value !== originalContent;
                
                if (hasUnsavedChanges) {
                    indicator.classList.add('visible');
                } else {
                    indicator.classList.remove('visible');
                }
            }
            
            async function loadConfig() {
                try {
                    const response = await fetch('/api/config');
                    const data = await response.json();
                    
                    const editor = document.getElementById('config-editor');
                    
                    if (data.exists) {
                        editor.value = data.content;
                        originalContent = data.content;
                        document.getElementById('file-path').textContent = data.path || '.env';
                    } else {
                        editor.value = '# .env file not found\n# Create your configuration here\n\nID_LIST=\nAPPRISE_URL=\nINTERVAL_CHECK=10000\nHEARTBEAT_INTERVAL=6\n';
                        originalContent = editor.value;
                        showStatus('.env file does not exist. A new one will be created when you save.', 'warning');
                    }
                    
                    updateUnsavedIndicator();
                } catch (error) {
                    showStatus('Failed to load configuration: ' + error.message, 'error');
                }
            }
            
            async function reloadConfig() {
                if (hasUnsavedChanges) {
                    if (!confirm('You have unsaved changes. Are you sure you want to reload?')) {
                        return;
                    }
                }
                await loadConfig();
                showStatus('Configuration reloaded from disk.', 'success');
            }
            
            async function saveConfig() {
                const editor = document.getElementById('config-editor');
                const content = editor.value;
                
                try {
                    const response = await fetch('/api/config', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify({ content: content })
                    });
                    
                    const data = await response.json();
                    
                    if (response.ok) {
                        originalContent = content;
                        updateUnsavedIndicator();
                        showStatus(data.message, 'success');
                    } else {
                        showStatus('Failed to save: ' + data.detail, 'error');
                    }
                } catch (error) {
                    showStatus('Failed to save configuration: ' + error.message, 'error');
                }
            }
            
            async function restoreBackup() {
                if (!confirm('Are you sure you want to restore the configuration from backup? This will overwrite the current .env file.')) {
                    return;
                }
                
                try {
                    const response = await fetch('/api/config/restore', {
                        method: 'POST'
                    });
                    
                    const data = await response.json();
                    
                    if (response.ok) {
                        document.getElementById('config-editor').value = data.content;
                        originalContent = data.content;
                        updateUnsavedIndicator();
                        showStatus(data.message, 'success');
                    } else {
                        showStatus('Failed to restore: ' + data.detail, 'error');
                    }
                } catch (error) {
                    showStatus('Failed to restore backup: ' + error.message, 'error');
                }
            }
            
            // Warn before leaving with unsaved changes
            window.addEventListener('beforeunload', function(e) {
                if (hasUnsavedChanges) {
                    e.preventDefault();
                    e.returnValue = '';
                }
            });
            
            // Keyboard shortcuts
            document.addEventListener('keydown', function(e) {
                // Ctrl+S or Cmd+S to save
                if ((e.ctrlKey || e.metaKey) && e.key === 's') {
                    e.preventDefault();
                    saveConfig();
                }
            });
            
            // Initialize
            document.addEventListener('DOMContentLoaded', function() {
                initializeTheme();
                loadConfig();
                
                // Track changes in editor
                document.getElementById('config-editor').addEventListener('input', updateUnsavedIndicator);
            });
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


async def fetch_testflight_status(session, tf_id):
    """Fetch and check TestFlight status using enhanced utility."""
    testflight_url = format_link(TESTFLIGHT_URL, tf_id)

    try:
        # Use the enhanced status checker utility
        result = await check_testflight_status(session, testflight_url)

        # Record metrics
        _metrics.record_check(result["status"], success=True)

        # Handle errors
        if result["status"] == TestFlightStatus.ERROR:
            logging.warning(
                "%s - %s - Error: %s",
                result.get("status_text", "Unknown"),
                tf_id,
                result.get("error", "Unknown error"),
            )
            return

        # Get app name from result or use cached function
        app_name = result.get("app_name")
        if not app_name:
            app_name = await get_app_name(TESTFLIGHT_URL, tf_id)

        # Get current and previous status
        current_status = result["status"]
        with _status_lock:
            previous_status = _previous_status.get(tf_id)
            _previous_status[tf_id] = current_status

        # Determine if we should notify
        should_notify = False
        status_changed = previous_status != current_status

        # Handle different status types
        if result["status"] == TestFlightStatus.FULL:
            logging.info(f"200 - {app_name} - Beta is full")
            # Only notify on status change to FULL (optional behavior)
            if status_changed and previous_status == TestFlightStatus.OPEN:
                should_notify = True

        elif result["status"] == TestFlightStatus.CLOSED:
            logging.info(f"200 - {app_name} - Beta is closed")
            # Don't notify for closed status

        elif result["status"] == TestFlightStatus.OPEN:
            # Decide notification policy for OPEN status
            with _open_notified_lock:
                already_notified = _open_notified.get(tf_id, False)

                if ALWAYS_NOTIFY_OPEN:
                    should_notify = True
                    logging.info(f"200 - {app_name} - Beta is OPEN (forced notification mode)")
                elif not already_notified:
                    # First ever OPEN notification for this TestFlight ID
                    should_notify = True
                    logging.info(
                        f"200 - {app_name} - Beta is OPEN! "
                        f"(changed from {previous_status.value if previous_status else 'unknown'})"
                    )
                elif previous_status is None or previous_status != TestFlightStatus.OPEN:
                    # Status transitioned into OPEN from another state
                    should_notify = True
                    logging.info(
                        f"200 - {app_name} - Beta is OPEN! (status change from {previous_status.value if previous_status else 'unknown'})"
                    )
                else:
                    logging.info(f"200 - {app_name} - Beta is still open (no notification)")

                if should_notify:
                    _open_notified[tf_id] = True

        else:
            # Unknown status - log for investigation with more details
            raw_text = result.get("raw_text", "N/A")
            logging.warning(
                f"200 - {app_name} - UNKNOWN status detected. "
                f"Full raw text (first 200 chars): '{raw_text[:200]}' - "
                f"Please check the TestFlight page and report this pattern "
                f"so we can add it to STATUS_PATTERNS for proper detection."
            )

        # Send notification if status changed to something noteworthy
        if should_notify:
            notify_msg = await format_notification_link(TESTFLIGHT_URL, tf_id)
            icon_url = await get_app_icon(TESTFLIGHT_URL, tf_id)
            # Use stock TestFlight icon if app icon is unavailable
            if not icon_url or icon_url == tf_id:
                base_url = "https://developer.apple.com/assets/elements/icons"
                icon_url = f"{base_url}/testflight/testflight-64x64_2x.png"
            await send_notification_async(notify_msg, apobj, icon_url)
            logging.info(f"Notification sent for {app_name}")

    except Exception as e:
        _metrics.record_check(TestFlightStatus.ERROR, success=False)
        logging.error(f"Unexpected error fetching {tf_id}: {e}")


async def watch():
    """Check all TestFlight links."""
    try:
        current_ids = get_current_id_list()
        session = await get_http_session()
        tasks = [fetch_testflight_status(session, tf_id) for tf_id in current_ids]
        await asyncio.gather(*tasks, return_exceptions=True)
    except asyncio.CancelledError:
        logging.debug("Watch cycle cancelled during shutdown")
        raise
    except Exception as e:
        logging.error(f"Error in watch cycle: {e}")


async def heartbeat():
    """Send periodic heartbeat notifications."""
    try:
        while True:
            current_time = format_datetime(datetime.now())
            message = f"Heartbeat - {current_time}"
            send_notification(message, apobj)
            print_green(message)
            await asyncio.sleep(HEARTBEAT_INTERVAL)
    except asyncio.CancelledError:
        logging.info("Heartbeat task cancelled during shutdown")
        raise  # Re-raise to signal proper cancellation
    except Exception as e:
        logging.error(f"Error in heartbeat task: {e}")
        raise


async def start_watching():
    """Continuously check TestFlight links."""
    try:
        # Add small delay to ensure server starts first
        await asyncio.sleep(2)
        while not shutdown_event.is_set():
            await watch()
            await asyncio.sleep(SLEEP_TIME / 1000)  # Convert ms to seconds
    except asyncio.CancelledError:
        logging.info("Watching task cancelled during shutdown")
        raise  # Re-raise to signal proper cancellation
    except Exception as e:
        logging.error(f"Error in watching task: {e}")
        raise


async def start_fastapi():
    """Start FastAPI server as an async task with graceful shutdown handling."""
    server = None
    try:
        default_host = os.getenv("FASTAPI_HOST", "0.0.0.0")
        default_port = int(os.getenv("FASTAPI_PORT", random.randint(8000, 9000)))

        logging.info(f"Starting FastAPI server on {default_host}:{default_port}")

        config = uvicorn.Config(
            app,
            host=default_host,
            port=default_port,
            log_level="info",
            access_log=False,  # Disable access logs to prevent console spam
            log_config=get_uvicorn_log_config(),  # Use custom config to preserve formatting
        )
        server = uvicorn.Server(config)

        # Install a custom signal handler to suppress CancelledError traceback
        async def serve_with_cancellation_handling():
            try:
                await server.serve()
            except asyncio.CancelledError:
                # Expected during shutdown/restart - don't log as error
                logging.debug("Server serve() cancelled - shutting down gracefully")
                raise

        await serve_with_cancellation_handling()

    except asyncio.CancelledError:
        logging.info("FastAPI server cancelled during shutdown/restart")
        # Initiate graceful shutdown if server was created
        if server:
            logging.debug("Initiating uvicorn server shutdown...")
            server.should_exit = True
        raise  # Re-raise to signal proper cancellation
    except Exception as e:
        logging.error(f"Failed to start FastAPI server: {e}")
        raise


def main():
    """Main function to start all tasks."""
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        logging.info("Shutdown initiated by user (CTRL+C).")
        shutdown_event.set()
    except Exception as e:
        logging.error(f"Unexpected error during shutdown: {e}")
    finally:
        logging.info("Application has stopped.")


async def async_main():
    """Run async tasks in the main event loop."""
    tasks = []
    try:
        logging.info("Starting TestFlight Apprise Notifier v%s", __version__)
        logging.info("All services starting...")

        # Create tasks
        watching_task = asyncio.create_task(start_watching())
        heartbeat_task = asyncio.create_task(heartbeat())
        fastapi_task = asyncio.create_task(start_fastapi())
        shutdown_task = asyncio.create_task(shutdown_event.wait())

        tasks = [watching_task, heartbeat_task, fastapi_task, shutdown_task]

        # Wait for any task to complete (shutdown event or error)
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

        logging.info("Shutdown initiated, cancelling tasks...")

        # Cancel all pending tasks
        for task in pending:
            if not task.done():
                task.cancel()

        # Wait for all tasks to complete and handle exceptions
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Log any exceptions that occurred during shutdown
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                task_name = (
                    tasks[i].get_name()
                    if hasattr(tasks[i], "get_name")
                    else f"Task-{i+1}"
                )
                if isinstance(result, asyncio.CancelledError):
                    logging.debug(f"{task_name} was cancelled during shutdown")
                elif isinstance(result, SystemExit) and result.code == 1:
                    logging.debug(
                        f"{task_name} (uvicorn) exited normally during shutdown"
                    )
                else:
                    logging.warning(f"{task_name} finished with exception: {result}")

    except asyncio.CancelledError:
        logging.info("Async tasks cancelled during shutdown.")
    except Exception as e:
        logging.error(f"Error in async main: {e}")
    finally:
        # Clean up HTTP session
        await cleanup_http_session()
        logging.info("HTTP session cleaned up.")
        logging.info("Async main loop has exited.")


if __name__ == "__main__":
    main()
