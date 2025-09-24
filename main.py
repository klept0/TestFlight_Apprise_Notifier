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
from collections import deque
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from datetime import datetime
from utils.notifications import send_notification, send_notification_async
from utils.formatting import (
    format_datetime,
    format_link,
    format_notification_link,
    get_app_icon,
)
from utils.colors import print_green

# Version
__version__ = "1.0.4"

# Load environment variables
load_dotenv()

# Constants
TESTFLIGHT_URL = "https://testflight.apple.com/join/"
FULL_TEXT = "This beta is full."
NOT_OPEN_TEXT = "This beta isn't accepting any new testers right now."
id_list_raw = os.getenv("ID_LIST", "").split(",")
SLEEP_TIME = int(os.getenv("INTERVAL_CHECK", "10000"))  # in ms
TITLE_REGEX = re.compile(r"Join the (.+) beta - TestFlight - Apple")
apprise_urls_raw = os.getenv("APPRISE_URL", "").split(",")
HEARTBEAT_INTERVAL = 6 * 60 * 60  # 6 hours in seconds

# Configure logging
format_str = f"%(asctime)s - %(levelname)s - %(message)s [v{__version__}]"
logging.basicConfig(level=logging.INFO, format=format_str)

# Web logging handler to capture logs for web interface
log_entries = deque(maxlen=100)  # Keep last 100 log entries
app_start_time = datetime.now()

class WebLogHandler(logging.Handler):
    def emit(self, record):
        log_entry = {
            'timestamp': datetime.fromtimestamp(record.created).strftime('%Y-%m-%d %H:%M:%S'),
            'level': record.levelname,
            'message': record.getMessage()
        }
        log_entries.append(log_entry)

# Add the web log handler to the root logger
web_handler = WebLogHandler()
logging.getLogger().addHandler(web_handler)

# Validate environment variables
ID_LIST = [tf_id.strip() for tf_id in id_list_raw if tf_id.strip()]
APPRISE_URLS = [url.strip() for url in apprise_urls_raw if url.strip()]

if not ID_LIST:
    logging.error(
        "Environment variable 'ID_LIST' is not set or contains only empty values."
    )
    raise ValueError("Environment variable 'ID_LIST' is required.")
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

# Graceful shutdown (cross-platform compatibility)
shutdown_event = asyncio.Event()


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

# FastAPI server
app = FastAPI()


@app.get("/", response_class=HTMLResponse)
async def home():
    uptime = datetime.now() - app_start_time
    uptime_str = str(uptime).split('.')[0]  # Remove microseconds
    
    # Get recent logs
    recent_logs = list(log_entries)[-20:]  # Show last 20 entries
    
    # Build log HTML
    log_html = ""
    for entry in reversed(recent_logs):  # Show newest first
        level_color = {
            'INFO': '#28a745',
            'WARNING': '#ffc107', 
            'ERROR': '#dc3545',
            'DEBUG': '#6c757d'
        }.get(entry['level'], '#000000')
        
        log_html += f"""
        <div style="margin: 5px 0; padding: 5px; border-left: 3px solid {level_color}; background-color: #f8f9fa;">
            <span style="color: #6c757d; font-size: 0.9em;">{entry['timestamp']}</span>
            <span style="color: {level_color}; font-weight: bold; margin-left: 10px;">[{entry['level']}]</span>
            <span style="margin-left: 10px;">{entry['message']}</span>
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
            body {{ 
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; 
                margin: 20px; 
                background-color: #f5f5f5;
            }}
            .container {{ 
                max-width: 1200px; 
                margin: 0 auto; 
                background-color: white; 
                padding: 20px; 
                border-radius: 8px; 
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            }}
            .header {{ 
                text-align: center; 
                color: #333; 
                border-bottom: 2px solid #007bff; 
                padding-bottom: 10px; 
                margin-bottom: 20px;
            }}
            .status-grid {{ 
                display: grid; 
                grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); 
                gap: 20px; 
                margin-bottom: 30px;
            }}
            .status-card {{ 
                background-color: #f8f9fa; 
                padding: 15px; 
                border-radius: 6px; 
                border-left: 4px solid #28a745;
            }}
            .status-card h3 {{ 
                margin: 0 0 10px 0; 
                color: #333; 
                font-size: 1.1em;
            }}
            .status-card p {{ 
                margin: 5px 0; 
                color: #666;
            }}
            .logs-section {{ 
                margin-top: 30px;
            }}
            .logs-header {{ 
                background-color: #343a40; 
                color: white; 
                padding: 10px 15px; 
                margin: 0; 
                border-radius: 6px 6px 0 0;
            }}
            .logs-container {{ 
                max-height: 400px; 
                overflow-y: auto; 
                border: 1px solid #dee2e6; 
                border-top: none; 
                border-radius: 0 0 6px 6px; 
                padding: 10px;
                background-color: #ffffff;
            }}
            .refresh-info {{ 
                text-align: center; 
                color: #6c757d; 
                font-size: 0.9em; 
                margin-top: 20px;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1 class="header">🚀 TestFlight Apprise Notifier</h1>
            
            <div class="status-grid">
                <div class="status-card">
                    <h3>🟢 Bot Status</h3>
                    <p><strong>Status:</strong> Running</p>
                    <p><strong>Version:</strong> v{__version__}</p>
                    <p><strong>Uptime:</strong> {uptime_str}</p>
                </div>
                
                <div class="status-card">
                    <h3>📱 Monitoring</h3>
                    <p><strong>TestFlight IDs:</strong> {len(ID_LIST)}</p>
                    <p><strong>Check Interval:</strong> {SLEEP_TIME/1000:.1f}s</p>
                    <p><strong>Notification URLs:</strong> {len(APPRISE_URLS)}</p>
                </div>
                
                <div class="status-card">
                    <h3>💓 Heartbeat</h3>
                    <p><strong>Interval:</strong> {HEARTBEAT_INTERVAL//3600}h</p>
                    <p><strong>Last Check:</strong> {format_datetime(datetime.now())}</p>
                </div>
            </div>
            
            <div class="logs-section">
                <h3 class="logs-header">📜 Recent Activity (Last 20 entries)</h3>
                <div class="logs-container">
                    {log_html if log_html else '<div style="text-align: center; color: #6c757d; padding: 20px;">No log entries yet...</div>'}
                </div>
            </div>
            
            <div class="refresh-info">
                🔄 Page auto-refreshes every 30 seconds | Last updated: {format_datetime(datetime.now())}
            </div>
        </div>
    </body>
    </html>
    """
    return html_content


@app.get("/api/status")
async def api_status():
    """API endpoint for status information in JSON format"""
    uptime = datetime.now() - app_start_time
    return {
        "status": "running",
        "version": __version__,
        "uptime_seconds": int(uptime.total_seconds()),
        "uptime_human": str(uptime).split('.')[0],
        "monitored_ids": len(ID_LIST),
        "check_interval_ms": SLEEP_TIME,
        "notification_urls": len(APPRISE_URLS),
        "heartbeat_interval_hours": HEARTBEAT_INTERVAL // 3600,
        "log_entries_count": len(log_entries),
        "last_updated": format_datetime(datetime.now())
    }


@app.get("/api/logs")
async def api_logs(limit: int = 50):
    """API endpoint for recent logs in JSON format"""
    recent_logs = list(log_entries)[-limit:]
    return {
        "logs": list(reversed(recent_logs)),
        "total_entries": len(log_entries),
        "limit": limit
    }


async def fetch_testflight_status(session, tf_id):
    """Fetch and check TestFlight status."""
    testflight_url = format_link(TESTFLIGHT_URL, tf_id)
    try:
        async with session.get(
            testflight_url, headers={"Accept-Language": "en-us"}
        ) as response:
            if response.status != 200:
                logging.warning(
                    "%s - %s - Not Found. URL: %s",
                    response.status,
                    tf_id,
                    testflight_url,
                )
                return

            text = await response.text()
            soup = BeautifulSoup(text, "html.parser")
            status_text = soup.select_one(".beta-status span")
            status_text = status_text.text.strip() if status_text else ""

            if status_text in [NOT_OPEN_TEXT, FULL_TEXT]:
                logging.info(f"{response.status} - {tf_id} - {status_text}")
                return

            title_tag = soup.find("title")
            title = title_tag.text if title_tag else "Unknown"
            title_match = TITLE_REGEX.search(title)
            notify_msg = await format_notification_link(TESTFLIGHT_URL, tf_id)
            icon_url = await get_app_icon(TESTFLIGHT_URL, tf_id)
            await send_notification_async(notify_msg, apobj, icon_url)
            logging.info(
                f"{response.status} - {tf_id} - {title_match.group(1) if title_match else 'Unknown'} - {status_text}"
            )
    except aiohttp.ClientResponseError as e:
        logging.error(f"HTTP error fetching {tf_id} (URL: {testflight_url}): {e}")
    except aiohttp.ClientError as e:
        logging.error(f"Network error fetching {tf_id} (URL: {testflight_url}): {e}")
    except Exception as e:
        logging.error(f"Unexpected error fetching {tf_id} (URL: {testflight_url}): {e}")


async def watch():
    """Check all TestFlight links."""
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_testflight_status(session, tf_id) for tf_id in ID_LIST]
        await asyncio.gather(*tasks)


async def heartbeat():
    """Send periodic heartbeat notifications."""
    while True:
        current_time = format_datetime(datetime.now())
        message = f"Heartbeat - {current_time}"
        send_notification(message, apobj)
        print_green(message)
        await asyncio.sleep(HEARTBEAT_INTERVAL)


async def start_watching():
    """Continuously check TestFlight links."""
    while not shutdown_event.is_set():
        await watch()
        await asyncio.sleep(SLEEP_TIME / 1000)  # Convert ms to seconds


def start_fastapi():
    """Start FastAPI server with default host and port."""
    default_host = os.getenv("FASTAPI_HOST", "0.0.0.0")
    default_port = int(os.getenv("FASTAPI_PORT", random.randint(8000, 9000)))

    logging.info(f"Starting FastAPI server on {default_host}:{default_port}")
    uvicorn.run(app, host=default_host, port=default_port)


def main():
    """Main function to start all tasks."""
    threading.Thread(target=start_fastapi, daemon=True).start()
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
    try:
        await asyncio.gather(start_watching(), heartbeat(), shutdown_event.wait())
    except asyncio.CancelledError:
        logging.info("Async tasks cancelled during shutdown.")
    finally:
        logging.info("Async main loop has exited.")


if __name__ == "__main__":
    main()
