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
from fastapi import FastAPI
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from datetime import datetime
from utils.notifications import send_notification
from utils.formatting import format_datetime, format_link
from utils.colors import print_cyan, print_green

# Version
__version__ = "1.0.0"

# Load environment variables
load_dotenv()

# Constants
TESTFLIGHT_URL = 'https://testflight.apple.com/join/'
FULL_TEXT = 'This beta is full.'
NOT_OPEN_TEXT = "This beta isn't accepting any new testers right now."
ID_LIST = os.getenv('ID_LIST', '').split(',')
SLEEP_TIME = int(os.getenv('INTERVAL_CHECK', 10000))  # in ms
TITLE_REGEX = re.compile(r'Join the (.+) beta - TestFlight - Apple')
APPRISE_URLS = os.getenv('APPRISE_URL', '').split(',')

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s [v%s]" % __version__)

# Validate environment variables
if not ID_LIST or not any(ID_LIST):
    logging.error("Environment variable 'ID_LIST' is not set or empty.")
    raise ValueError("Environment variable 'ID_LIST' is required.")
if not APPRISE_URLS or not any(APPRISE_URLS):
    logging.error("Environment variable 'APPRISE_URL' is not set or empty.")
    raise ValueError("Environment variable 'APPRISE_URL' is required.")

# Initialize Apprise notifier
apobj = apprise.Apprise()
for url in APPRISE_URLS:
    if url:
        apobj.add(url)

# Graceful shutdown
shutdown_event = asyncio.Event()

def handle_shutdown_signal():
    logging.info("Shutdown signal received. Cleaning up...")
    shutdown_event.set()

signal.signal(signal.SIGINT, lambda s, f: handle_shutdown_signal())
signal.signal(signal.SIGTERM, lambda s, f: handle_shutdown_signal())

# FastAPI server
app = FastAPI()

@app.get("/")
async def home():
    return {"status": "Bot is alive"}

async def fetch_testflight_status(session, tf_id):
    """Fetch and check TestFlight status."""
    url = format_link(TESTFLIGHT_URL, tf_id)
    try:
        async with session.get(url, headers={'Accept-Language': 'en-us'}) as response:
            if response.status != 200:
                logging.warning(f"{response.status} - {tf_id} - Not Found.")
                return
            
            text = await response.text()
            soup = BeautifulSoup(text, 'html.parser')
            status_text = soup.select_one('.beta-status span')
            status_text = status_text.text.strip() if status_text else ''

            if status_text in [NOT_OPEN_TEXT, FULL_TEXT]:
                logging.info(f"{response.status} - {tf_id} - {status_text}")
                return
            
            title_tag = soup.find('title')
            title = title_tag.text if title_tag else 'Unknown'
            title_match = TITLE_REGEX.search(title)
            send_notification(url, apobj)
            logging.info(f"{response.status} - {tf_id} - {title_match.group(1) if title_match else 'Unknown'} - {status_text}")
    except aiohttp.ClientError as e:
        logging.error(f"Network error fetching {tf_id}: {e}")
    except Exception as e:
        logging.error(f"Error fetching {tf_id}: {e}")

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
        await asyncio.sleep(6 * 60 * 60)  # Every 6 hours

async def start_watching():
    """Continuously check TestFlight links."""
    while not shutdown_event.is_set():
        await watch()
        await asyncio.sleep(SLEEP_TIME / 1000)  # Convert ms to seconds

def start_fastapi():
    """Start FastAPI server with a randomized port and optional IP binding."""
    default_host = "0.0.0.0"
    default_port = random.randint(8000, 9000)  # Randomize port between 8000 and 9000

    # Allow user to specify host and port via input
    host = input(f"Enter the host IP to bind (default: {default_host}): ").strip() or default_host
    try:
        port = int(input(f"Enter the port to bind (default: {default_port}): ").strip() or default_port)
    except ValueError:
        logging.warning(f"Invalid port entered. Using default port: {default_port}")
        port = default_port

    logging.info(f"Starting FastAPI server on {host}:{port}")
    uvicorn.run(app, host=host, port=port)

def main():
    """Main function to start all tasks."""
    threading.Thread(target=start_fastapi, daemon=True).start()
    asyncio.run(async_main())

async def async_main():
    """Run async tasks in the main event loop."""
    await asyncio.gather(start_watching(), heartbeat(), shutdown_event.wait())

if __name__ == "__main__":
    main()
