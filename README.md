# TestFlight Apprise Notifier

![TestFlight Notifier](https://img.shields.io/badge/TestFlight-Monitor-blue)
![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)
![License](https://img.shields.io/badge/License-MIT-green.svg)

This script monitors TestFlight links and sends notifications when slots become available. It uses FastAPI for a simple server, Apprise for notifications, and asyncio for efficient network requests.

## 🚀 Features
- ✅ Monitors multiple TestFlight links.
- 🔔 Sends notifications via Apprise when a slot is available.
- 🌐 Includes a FastAPI server to keep the script alive.
- ⏳ Periodic heartbeat notifications.

## 📋 Requirements
- Python 3.8+
- Required dependencies:
  - `aiohttp`
  - `apprise`
  - `bs4`
  - `fastapi`
  - `python-dotenv`
  - `uvicorn`

## 📦 Installation
1. Clone this repository:
   ```sh
   git clone https://github.com/your-repo/testflight-apprise.git
   cd testflight-apprise
   ```
2. Install dependencies:
   ```sh
   pip install -r requirements.txt
   ```

## ⚙️ Configuration
Create a `.env` file and set the following environment variables:
```ini
ID_LIST=your_testflight_ids_here (comma-separated)
INTERVAL_CHECK=10000  # Interval in milliseconds
APPRISE_URL=your_apprise_url_here
```

## ▶️ Usage
Run the script using:
```sh
python main.py
```

## 🌍 API Endpoint
The script includes a simple FastAPI server:
- **`GET /`** - Returns `{ "status": "Bot is alive" }` to confirm the script is running.

## 📦 Deployment
You can deploy this script using Docker, systemd, or a cloud provider for continuous execution.

## 📝 License
This project is licensed under the MIT License.

---
💡 *Feel free to contribute, open issues, or submit pull requests!* 🚀

