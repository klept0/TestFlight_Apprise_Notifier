"""
Logging configuration and in-memory log buffer for the web dashboard.

Provides colored console logging, a bounded ring buffer of recent log entries
surfaced by the dashboard, and a uvicorn log config that reuses our handlers.
Call :func:`configure_logging` once at startup before logging anything that
should carry the version suffix.
"""

import itertools
import logging
import threading
from collections import deque
from datetime import datetime

# In-memory ring buffer of recent log entries for the web UI.
log_entries: deque = deque(maxlen=100)  # Keep last 100 log entries
log_entries_lock = threading.Lock()  # Thread safety for log access

# Set by configure_logging() so the format can include the app version.
_format_str = "%(asctime)s - %(levelname)s - %(message)s"


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


# The web log handler is attached lazily in ensure_web_handler_attached().
web_handler = WebLogHandler()
_web_handler_attached = False  # Track if web handler has been attached


def configure_logging(version: str) -> None:
    """Set up colored console logging. Call once at startup."""
    global _format_str
    _format_str = f"%(asctime)s - %(levelname)s - %(message)s [v{version}]"

    # Create console handler with colored formatter
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(ColoredFormatter(_format_str))

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
                "format": _format_str,
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
