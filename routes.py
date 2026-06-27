"""FastAPI routes for the TestFlight Apprise Notifier dashboard."""

import os
import logging
import threading
from datetime import datetime

from fastapi import APIRouter, HTTPException, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

import state
from state import (
    apobj,
    add_apprise_url,
    add_testflight_id,
    app_start_time,
    apprise_url_id,
    check_github_updates,
    find_apprise_url_by_id,
    get_current_apprise_urls,
    get_current_id_list,
    handle_shutdown_signal,
    remove_apprise_url,
    remove_testflight_id,
    validate_apprise_url,
    validate_testflight_id,
    _metrics,
    _perform_restart,
)
from config import (
    __version__,
    ALWAYS_NOTIFY_OPEN,
    APPRISE_URLS,
    ENABLE_UPDATE_CHECKER,
    HEARTBEAT_INTERVAL,
    ID_LIST,
    SLEEP_TIME,
    TESTFLIGHT_URL,
    UI_THEME,
)
from utils.formatting import (
    format_datetime,
    get_app_icon,
    get_app_name,
    app_name_cache,
    app_icon_cache,
)
from utils.notifications import send_notification_async
from utils.masking import mask_secret
from utils.service_icons import get_apprise_service_icon
from utils.web_logging import get_recent_logs, log_entries, log_entries_lock

router = APIRouter()
templates = Jinja2Templates(directory="templates")



# --- Request models ------------------------------------------------------
class TestFlightIdRequest(BaseModel):
    """Body for single TestFlight ID validate/add requests."""

    id: str = Field(..., description="TestFlight beta ID")


class AppriseUrlRequest(BaseModel):
    """Body for single Apprise URL validate/add requests."""

    url: str = Field(..., description="Apprise notification URL")


class BatchIdRequest(BaseModel):
    """Body for batch add/remove of TestFlight IDs."""

    add: list[str] = Field(default_factory=list, description="IDs to add")
    remove: list[str] = Field(default_factory=list, description="IDs to remove")


@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    uptime = datetime.now() - app_start_time
    uptime_str = str(uptime).split(".")[0]

    return templates.TemplateResponse(request, "index.html", {
        "version": __version__,
        "uptime_str": uptime_str,
        "id_count": len(ID_LIST),
        "url_count": len(APPRISE_URLS),
        "sleep_time_s": SLEEP_TIME / 1000,
        "heartbeat_hours": HEARTBEAT_INTERVAL // 3600,
        "enable_update_checker": ENABLE_UPDATE_CHECKER,
        "always_notify_open": ALWAYS_NOTIFY_OPEN,
        "ui_theme": UI_THEME,
    })


@router.get("/api/health")
async def health_check():
    """Health check endpoint for monitoring."""
    current_ids = get_current_id_list()

    return {
        "status": "healthy",
        "version": __version__,
        "uptime_seconds": int((datetime.now() - app_start_time).total_seconds()),
        "monitored_ids": len(current_ids),
        "cache_stats": {
            "app_names": len(app_name_cache.cache),
            "app_icons": len(app_icon_cache.cache),
        },
        "http_session": (
            "active" if state.http_session_active() else "inactive"
        ),
        "timestamp": format_datetime(datetime.now()),
    }


@router.get("/api/metrics")
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


@router.get("/api/logs")
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


@router.get("/api/testflight-ids")
async def get_testflight_ids():
    """Get current list of TestFlight IDs."""
    return {"testflight_ids": get_current_id_list()}


@router.get("/api/updates")
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


@router.get("/api/testflight-ids/details")
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


@router.post("/api/testflight-ids/validate")
async def validate_id(payload: TestFlightIdRequest):
    """Validate a TestFlight ID."""
    tf_id = payload.id.strip()

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


@router.post("/api/testflight-ids")
async def add_id(payload: TestFlightIdRequest):
    """Add a new TestFlight ID."""
    tf_id = payload.id.strip()

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


@router.delete("/api/testflight-ids/{tf_id}")
async def remove_id(tf_id: str):
    """Remove a TestFlight ID."""
    success, message = remove_testflight_id(tf_id)
    if not success:
        raise HTTPException(status_code=404, detail=message)

    return {"message": message, "testflight_ids": get_current_id_list()}


@router.post("/api/testflight-ids/batch")
async def batch_operations(payload: BatchIdRequest):
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
    ids_to_add = payload.add
    ids_to_remove = payload.remove

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


def _apprise_urls_payload():
    """Build the Apprise URL list for API responses.

    Each entry exposes a stable non-secret ``id`` (used for removal) and a
    masked ``display_url`` — never the raw, secret-bearing URL.
    """
    payload = []
    for url in get_current_apprise_urls():
        service_info = get_apprise_service_icon(url)
        payload.append(
            {
                "id": apprise_url_id(url),
                "display_url": mask_secret(url),
                "service_name": service_info["service_name"],
                "icon_url": service_info["icon_url"],
                "emoji": service_info["emoji"],
            }
        )
    return payload


@router.get("/api/apprise-urls")
async def get_apprise_urls():
    """Get current list of Apprise URLs with service information."""
    return {"apprise_urls": _apprise_urls_payload()}


@router.post("/api/apprise-urls/validate")
async def validate_url(payload: AppriseUrlRequest):
    """Validate an Apprise URL."""
    url = payload.url.strip()

    if not url:
        raise HTTPException(status_code=400, detail="Apprise URL is required")

    is_valid, message = validate_apprise_url(url)

    return {
        "valid": is_valid,
        "message": message,
    }


@router.post("/api/apprise-urls")
async def add_url(payload: AppriseUrlRequest):
    """Add a new Apprise URL."""
    url = payload.url.strip()

    if not url:
        raise HTTPException(status_code=400, detail="Apprise URL is required")

    # Add to list
    success, message = add_apprise_url(url)
    if not success:
        raise HTTPException(status_code=400, detail=message)

    return {"message": message, "apprise_urls": _apprise_urls_payload()}


@router.delete("/api/apprise-urls/{url_id}")
async def remove_url(url_id: str):
    """Remove an Apprise URL by its non-secret id."""
    url = find_apprise_url_by_id(url_id)
    if url is None:
        raise HTTPException(status_code=404, detail="Apprise URL not found")

    success, message = remove_apprise_url(url)
    if not success:
        raise HTTPException(status_code=404, detail=message)

    return {"message": message, "apprise_urls": _apprise_urls_payload()}


@router.post("/api/control/stop")
async def stop_application():
    """Stop the application gracefully."""
    logging.info("Stop command received via web interface")
    # Send notification about the stop
    try:
        msg = "🛑 TestFlight Apprise Notifier stopped via web interface"
        await send_notification_async(msg, apobj)
    except Exception:
        pass  # Ignore notification errors during shutdown

    # Trigger graceful shutdown
    handle_shutdown_signal()
    return {"message": "Application is shutting down..."}


@router.post("/api/control/restart")
async def restart_application():
    """Restart the application by re-executing it in place."""
    logging.info("Restart command received via web interface")

    # Send notification about the restart
    try:
        msg = "🔄 TestFlight Apprise Notifier restarting via web interface"
        await send_notification_async(msg, apobj)
    except Exception:
        pass  # Ignore notification errors during restart

    # The re-exec inherits the current environment, so an unset FASTAPI_PORT
    # means a new random port is chosen on restart and the dashboard URL changes.
    if not os.getenv("FASTAPI_PORT"):
        logging.warning(
            "FASTAPI_PORT is not set; the dashboard may come back on a "
            "different random port after restart. Set FASTAPI_PORT to keep it stable."
        )

    # Defer the re-exec briefly so this HTTP response can flush to the client
    # before the process image is replaced.
    threading.Timer(1.0, _perform_restart).start()
    return {"message": "Application is restarting..."}


@router.get("/api/config")
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


@router.post("/api/config")
async def save_config(content: str = Form(...)):
    """Save changes to the .env file (validated, backed up, atomic)."""
    import shutil
    import tempfile

    env_path = ".env"
    backup_path = f"{env_path}.backup"

    # Validate before writing; abort (without touching the file) on invalid input.
    if not isinstance(content, str) or "\x00" in content or not content.strip():
        raise HTTPException(status_code=400, detail="Invalid configuration content")

    try:
        # Back up the current file (if any) before replacing it.
        if os.path.exists(env_path):
            shutil.copy2(env_path, backup_path)
            logging.info(f"Created backup at {backup_path}")

        # Write to a temp file in the same directory, fsync, then atomically
        # replace the original. Content is preserved exactly.
        env_dir = os.path.dirname(os.path.abspath(env_path)) or "."
        fd, tmp_path = tempfile.mkstemp(prefix=".env.", suffix=".tmp", dir=env_dir)
        try:
            with os.fdopen(fd, "w") as tmp:
                tmp.write(content)
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(tmp_path, env_path)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

        logging.info("Configuration file updated via web interface")

        return {
            "success": True,
            "message": "Configuration saved. Restart to apply changes.",
            "backup_created": os.path.exists(backup_path),
        }
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Failed to save .env file: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to save config: {str(e)}"
        )


@router.post("/api/config/restore")
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


@router.get("/config")
async def config_redirect():
    """Redirect legacy /config URL to the Settings section of the dashboard."""
    return RedirectResponse(url="/#settings")
