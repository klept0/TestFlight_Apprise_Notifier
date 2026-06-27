import os
import sys
import asyncio
import uvicorn
import threading
import logging
import signal
import random
import secrets
import time
import persistence
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from datetime import datetime
from typing import Dict, Optional
from config import (
    __version__,
    ALWAYS_NOTIFY_OPEN,
    APPRISE_URLS,
    ENABLE_UPDATE_CHECKER,
    GITHUB_CHECK_INTERVAL,
    HEARTBEAT_INTERVAL,
    ID_LIST,
    SLEEP_TIME,
    TESTFLIGHT_URL,
)
from utils.notifications import send_notification_async
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
    enable_status_cache,
)
from utils.web_logging import (
    configure_logging,
    ensure_web_handler_attached,
    get_uvicorn_log_config,
)
from state import (
    apobj,
    cleanup_http_session,
    get_current_id_list,
    get_http_session,
    handle_shutdown_signal,
    periodic_update_check,
    shutdown_event,
    _metrics,
)

# Enable status caching with 5-minute TTL for improved performance
enable_status_cache(ttl_seconds=300)

# Optional HTTP Basic auth for the web dashboard/API. Enabled only when BOTH
# WEB_USERNAME and WEB_PASSWORD are set; otherwise the app relies on binding to
# localhost (the default host). Read here (not in config.py) so the values are
# re-read when the module is reloaded, e.g. in tests.
WEB_USERNAME = os.getenv("WEB_USERNAME", "").strip()
WEB_PASSWORD = os.getenv("WEB_PASSWORD", "").strip()

# Hosts considered local, where authentication is optional. Any other bind
# address is treated as publicly reachable and requires credentials.
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


def validate_auth_config():
    """Require dashboard authentication when bound to a non-loopback host.

    If FASTAPI_HOST is a public address (anything other than localhost), both
    WEB_USERNAME and WEB_PASSWORD must be set. When they are missing, log an
    error and exit with status 1 rather than starting an unprotected,
    publicly reachable dashboard.
    """
    host = os.getenv("FASTAPI_HOST", "127.0.0.1")
    if host in LOOPBACK_HOSTS:
        return
    if not (WEB_USERNAME and WEB_PASSWORD):
        logging.error(
            "Refusing to start: FASTAPI_HOST=%s exposes the dashboard publicly, "
            "but WEB_USERNAME and WEB_PASSWORD are not set. Set both to require "
            "a login, or bind to 127.0.0.1.",
            host,
        )
        sys.exit(1)


# Status tracking for change notifications
_previous_status = {}  # tf_id -> TestFlightStatus
_status_lock = threading.Lock()

# Track whether an OPEN notification has been sent per TestFlight ID
_open_notified: Dict[str, bool] = {}  # tf_id -> notification sent?
_open_notified_lock = threading.Lock()

# Additional per-ID runtime state (guarded by _status_lock).
_last_notification_ts: Dict[str, float] = {}  # tf_id -> epoch seconds
_last_success_ts: Dict[str, float] = {}  # tf_id -> epoch seconds
_failure_count: Dict[str, int] = {}  # tf_id -> consecutive failures


def snapshot_runtime_state() -> dict:
    """Build a JSON-serializable snapshot of the monitor's runtime state."""
    with _status_lock:
        prev = dict(_previous_status)
        notif = dict(_last_notification_ts)
        succ = dict(_last_success_ts)
        fail = dict(_failure_count)
    with _open_notified_lock:
        opened = dict(_open_notified)

    apps = {}
    for tf_id in set(prev) | set(notif) | set(succ) | set(fail) | set(opened):
        cache_key = f"{TESTFLIGHT_URL}:{tf_id}"
        status = prev.get(tf_id)
        apps[tf_id] = {
            "status": status.value if status else None,
            "notified_open": opened.get(tf_id, False),
            "last_notification_ts": notif.get(tf_id),
            "last_success_ts": succ.get(tf_id),
            "failure_count": fail.get(tf_id, 0),
            "app_name": app_name_cache.get(cache_key),
            "icon_url": app_icon_cache.get(cache_key),
        }
    return {"version": persistence.STATE_VERSION, "apps": apps}


def restore_runtime_state(snapshot: dict) -> None:
    """Repopulate live monitor state from a snapshot, skipping bad entries."""
    apps = (snapshot or {}).get("apps", {})
    if not isinstance(apps, dict):
        return
    restored = 0
    for tf_id, rec in apps.items():
        if not isinstance(rec, dict):
            continue
        try:
            with _status_lock:
                status_val = rec.get("status")
                if status_val:
                    try:
                        _previous_status[tf_id] = TestFlightStatus(status_val)
                    except ValueError:
                        pass
                if rec.get("last_notification_ts") is not None:
                    _last_notification_ts[tf_id] = float(rec["last_notification_ts"])
                if rec.get("last_success_ts") is not None:
                    _last_success_ts[tf_id] = float(rec["last_success_ts"])
                _failure_count[tf_id] = int(rec.get("failure_count", 0) or 0)
            with _open_notified_lock:
                _open_notified[tf_id] = bool(rec.get("notified_open", False))

            cache_key = f"{TESTFLIGHT_URL}:{tf_id}"
            if rec.get("app_name"):
                app_name_cache.put(cache_key, rec["app_name"])
            if rec.get("icon_url"):
                app_icon_cache.put(cache_key, rec["icon_url"])
            restored += 1
        except Exception as e:  # noqa: BLE001 - one bad entry must not abort restore
            logging.warning("Skipping corrupt state entry for %s: %s", tf_id, e)

    if restored:
        logging.info("Restored runtime state for %d TestFlight ID(s)", restored)


def persist_runtime_state() -> None:
    """Persist the current runtime state snapshot to disk (best effort)."""
    persistence.save_state(snapshot_runtime_state())


# Configure colored console logging (see utils/web_logging.py).
configure_logging(__version__)


# Validate environment variables
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


def install_signal_handlers():
    """Attach SIGINT/SIGTERM handlers to the running event loop.

    Must be called from within the running loop (e.g. async_main) so the
    handlers are registered on the loop that actually serves the app.
    Falls back gracefully on platforms (Windows) that don't support it.
    """
    if os.name == "nt":
        return
    loop = asyncio.get_running_loop()
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
    if ENABLE_UPDATE_CHECKER and GITHUB_CHECK_INTERVAL > 0:
        update_task = asyncio.create_task(periodic_update_check())
        logging.info("GitHub update checker started")
    elif not ENABLE_UPDATE_CHECKER:
        logging.info("GitHub update checker disabled via ENABLE_UPDATE_CHECKER")

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


# Optional HTTP Basic auth (see WEB_USERNAME / WEB_PASSWORD above).
_basic_security = HTTPBasic(auto_error=False)

# Paths that must stay reachable without credentials: the health check (used by
# Docker/orchestrators) and static assets (served by a sub-app anyway).
_AUTH_EXEMPT_PREFIXES = ("/api/health", "/static")


def require_auth(
    request: Request,
    credentials: Optional[HTTPBasicCredentials] = Depends(_basic_security),
):
    """Enforce HTTP Basic auth when credentials are configured.

    No-op when WEB_USERNAME/WEB_PASSWORD are unset (localhost-only deployments).
    """
    if not (WEB_USERNAME and WEB_PASSWORD):
        return
    if request.url.path.startswith(_AUTH_EXEMPT_PREFIXES):
        return
    valid = (
        credentials is not None
        and secrets.compare_digest(credentials.username, WEB_USERNAME)
        and secrets.compare_digest(credentials.password, WEB_PASSWORD)
    )
    if not valid:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing credentials",
            headers={"WWW-Authenticate": "Basic"},
        )


# FastAPI server
app = FastAPI(lifespan=lifespan, dependencies=[Depends(require_auth)])
app.mount("/static", StaticFiles(directory="static"), name="static")


# --- CSRF protection (double-submit cookie) ----------------------------------
CSRF_COOKIE = "csrf_token"
CSRF_HEADER = "x-csrf-token"
CSRF_SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}


def _issue_csrf_cookie(response, existing):
    """Set a CSRF cookie on the response if the client doesn't have one yet."""
    if not existing:
        response.set_cookie(
            CSRF_COOKIE,
            secrets.token_urlsafe(32),
            samesite="strict",
            httponly=False,  # readable by JS so it can echo the value in a header
            path="/",
        )


@app.middleware("http")
async def csrf_protect(request: Request, call_next):
    """Require a matching CSRF token on every state-changing request.

    Double-submit-cookie pattern: the browser holds a ``csrf_token`` cookie and
    must echo it in the ``X-CSRF-Token`` header for any non-safe method. Safe
    methods (GET/HEAD/OPTIONS) are never blocked — they only get a cookie issued.
    """
    cookie_token = request.cookies.get(CSRF_COOKIE)
    if request.method not in CSRF_SAFE_METHODS:
        header_token = request.headers.get(CSRF_HEADER, "")
        if not (
            cookie_token
            and header_token
            and secrets.compare_digest(cookie_token, header_token)
        ):
            resp = JSONResponse(
                {"detail": "CSRF token missing or invalid"}, status_code=403
            )
            _issue_csrf_cookie(resp, cookie_token)
            return resp
    response = await call_next(request)
    _issue_csrf_cookie(response, cookie_token)
    return response


# Web routes live in routes.py (APIRouter).
from routes import router  # noqa: E402  (imported after app/auth are defined)
app.include_router(router)


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
            with _status_lock:
                _failure_count[tf_id] = _failure_count.get(tf_id, 0) + 1
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
            _last_success_ts[tf_id] = time.time()
            _failure_count[tf_id] = 0

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
            with _status_lock:
                _last_notification_ts[tf_id] = time.time()
            logging.info(f"Notification sent for {app_name}")

    except Exception as e:
        with _status_lock:
            _failure_count[tf_id] = _failure_count.get(tf_id, 0) + 1
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
    # HEARTBEAT_INTERVAL <= 0 disables the heartbeat (see README / .env.example).
    # Wait for shutdown instead of returning: a sleep(0) loop would busy-spin and
    # flood notifications, while returning immediately would complete this task
    # and trip async_main's FIRST_COMPLETED wait, shutting the app down at startup.
    if HEARTBEAT_INTERVAL <= 0:
        logging.info("Heartbeat disabled (HEARTBEAT_INTERVAL=0)")
        await shutdown_event.wait()
        return
    try:
        while True:
            current_time = format_datetime(datetime.now())
            message = f"Heartbeat - {current_time}"
            await send_notification_async(message, apobj)
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
            # Persist after each cycle so state survives an unclean exit too.
            persist_runtime_state()
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
        # Default to loopback so the dashboard (which can read/write .env and
        # control the process) is not exposed on all interfaces by accident.
        # Set FASTAPI_HOST=0.0.0.0 to expose it, ideally with WEB_USERNAME/
        # WEB_PASSWORD configured.
        default_host = os.getenv("FASTAPI_HOST", "127.0.0.1")
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
    validate_auth_config()
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

        # Register signal handlers on the running loop so SIGTERM (e.g.
        # `docker stop`) triggers a graceful shutdown.
        install_signal_handlers()

        # Restore persisted runtime state before monitoring starts so we resume
        # without re-sending duplicate notifications, then (re)create the file.
        restore_runtime_state(persistence.load_state())
        persist_runtime_state()

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
        # Persist runtime state on shutdown so the next start resumes cleanly.
        persist_runtime_state()
        # Clean up HTTP session
        await cleanup_http_session()
        logging.info("HTTP session cleaned up.")
        logging.info("Async main loop has exited.")


if __name__ == "__main__":
    main()
