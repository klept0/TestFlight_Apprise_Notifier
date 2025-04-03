import os
import re
import asyncio
import aiohttp
import uvicorn
import apprise
import threading
from fastapi import FastAPI
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from datetime import datetime

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

# Initialize Apprise notifier
apobj = apprise.Apprise()
for url in APPRISE_URLS:
    if url:
        apobj.add(url)

# FastAPI server
app = FastAPI()

@app.get("/")
async def home():
    return {"status": "Bot is alive"}

def send_notification(message: str):
    """Send notification using Apprise with error handling."""
    try:
        apobj.notify(body=message, title="TestFlight Alert")
        print(f"Notification sent: {message}")
    except Exception as e:
        print(f"Error sending notification: {e}")

async def fetch_testflight_status(session, tf_id):
    """Fetch and check TestFlight status."""
    url = f"{TESTFLIGHT_URL}{tf_id}"
    try:
        async with session.get(url, headers={'Accept-Language': 'en-us'}) as response:
            if response.status != 200:
                print(f"{response.status} - {tf_id} - Not Found.")
                return
            
            text = await response.text()
            soup = BeautifulSoup(text, 'html.parser')
            status_text = soup.select_one('.beta-status span')
            status_text = status_text.text.strip() if status_text else ''

            if status_text in [NOT_OPEN_TEXT, FULL_TEXT]:
                print(f"{response.status} - {tf_id} - {status_text}")
                return
            
            title_tag = soup.find('title')
            title = title_tag.text if title_tag else 'Unknown'
            title_match = TITLE_REGEX.search(title)
            tf_link = url
            send_notification(tf_link)
            print(f"{response.status} - {tf_id} - {title_match.group(1) if title_match else 'Unknown'} - {status_text}")
    except Exception as e:
        print(f"Error fetching {tf_id}: {e}")

async def watch():
    """Check all TestFlight links."""
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_testflight_status(session, tf_id) for tf_id in ID_LIST]
        await asyncio.gather(*tasks)

async def heartbeat():
    """Send periodic heartbeat notifications."""
    while True:
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        send_notification(f"Heartbeat - {current_time}")
        print(f"Heartbeat - {current_time}")
        await asyncio.sleep(6 * 60 * 60)  # Every 6 hours

async def start_watching():
    """Continuously check TestFlight links."""
    while True:
        await watch()
        await asyncio.sleep(SLEEP_TIME / 1000)  # Convert ms to seconds

def start_fastapi():
    """Start FastAPI server."""
    uvicorn.run(app, host="0.0.0.0", port=8089)

def main():
    """Main function to start all tasks."""
    threading.Thread(target=start_fastapi, daemon=True).start()
    asyncio.run(async_main())

async def async_main():
    """Run async tasks in the main event loop."""
    await asyncio.gather(start_watching(), heartbeat())

if __name__ == "__main__":
    main()
