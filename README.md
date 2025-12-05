# TestFlight Apprise Notifier

A Python-based monitoring tool that continuously checks Apple TestFlight beta availability and sends notifications through [Apprise](https://github.com/caronc/apprise) when slots become available.

## Features

- 🔔 **Real-time Notifications** – Get notified instantly when a TestFlight beta opens up
- 📱 **80+ Notification Services** – Supports Discord, Slack, Telegram, Pushover, Email, and many more via Apprise
- 🌐 **Web Dashboard** – Built-in FastAPI web interface to monitor status and manage TestFlight IDs
- 🐳 **Docker Support** – Easy deployment with Docker and Docker Compose
- ⚡ **High Performance** – Async HTTP requests with connection pooling and caching
- 🔄 **Status Tracking** – Detects status changes (Open, Full, Closed) and notifies accordingly
- 💓 **Heartbeat Notifications** – Optional periodic notifications to confirm the service is running
- 🎨 **Dark Mode UI** – Modern, responsive web dashboard with dark/light theme toggle
- 📝 **Code Editor** - Basic code editor to live modify your .env files and restart services through web dashboard.

## Status Detection

The notifier detects the following TestFlight statuses:

| Status | Description |
|--------|-------------|
| **OPEN** | Beta is accepting new testers – notification sent! |
| **FULL** | Beta is full, not accepting new testers |
| **CLOSED** | Beta is no longer available |
| **UNKNOWN** | Unable to determine status |

## Requirements

- Python 3.11+
- pip (Python package manager)

## Installation

### Option 1: Local Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/klept0/TestFlight_Apprise_Notifier.git
   cd TestFlight_Apprise_Notifier
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Create your configuration file:**
   ```bash
   cp .env.example .env
   ```

4. **Edit `.env` with your settings** (see [Configuration](#configuration) below)

5. **Run the application:**
   ```bash
   python main.py
   ```

### Option 2: Docker (Recommended)

1. **Clone the repository:**
   ```bash
   git clone https://github.com/klept0/TestFlight_Apprise_Notifier.git
   cd TestFlight_Apprise_Notifier
   ```

2. **Create your configuration file:**
   ```bash
   cp .env.example .env
   ```

3. **Edit `.env` with your settings**

4. **Start with Docker Compose:**
   ```bash
   docker-compose up -d
   ```

5. **View logs:**
   ```bash
   docker-compose logs -f
   ```

For detailed Docker instructions, see [DOCKER.md](DOCKER.md).

## Configuration

All configuration is done through environment variables in the `.env` file.

### Required Settings

| Variable | Description | Example |
|----------|-------------|---------|
| `ID_LIST` | Comma-separated TestFlight IDs to monitor | `abc123,def456,ghi789` |
| `APPRISE_URL` | Apprise notification URL(s), comma-separated | `discord://webhook_id/token` |

### Optional Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `INTERVAL_CHECK` | `10000` | Check interval in milliseconds |
| `HEARTBEAT_INTERVAL` | `6` | Hours between heartbeat notifications (0 to disable) |
| `ALWAYS_NOTIFY_OPEN` | `false` | Send notification on every poll when status is OPEN |
| `FASTAPI_HOST` | `0.0.0.0` | Web dashboard bind address |
| `FASTAPI_PORT` | `8080` | Web dashboard port |

### Example `.env` File

```ini
# TestFlight IDs to monitor (comma-separated)
ID_LIST=abc12345,xyz98765,test1234

# Apprise notification URLs (comma-separated for multiple)
APPRISE_URL=discord://webhook_id/webhook_token,tgram://bot_token/chat_id

# Check every 10 seconds (10000ms)
INTERVAL_CHECK=10000

# Send heartbeat every 6 hours
HEARTBEAT_INTERVAL=6

# Only notify once when beta opens (not on every check)
ALWAYS_NOTIFY_OPEN=false

# Web dashboard settings
FASTAPI_HOST=0.0.0.0
FASTAPI_PORT=8080
```

### Finding TestFlight IDs

The TestFlight ID is the alphanumeric code at the end of a TestFlight URL:

```
https://testflight.apple.com/join/abc12345
                                   ^^^^^^^^
                                   This is the ID
```

### Apprise URL Examples

Apprise supports 80+ notification services. Here are some common examples:

| Service | URL Format |
|---------|------------|
| Discord | `discord://webhook_id/webhook_token` |
| Telegram | `tgram://bot_token/chat_id` |
| Slack | `slack://token_a/token_b/token_c` |
| Pushover | `pover://user_key@app_token` |
| Email | `mailto://user:password@gmail.com` |
| Gotify | `gotify://hostname/token` |
| ntfy | `ntfy://topic` |

For the full list, see the [Apprise documentation](https://github.com/caronc/apprise/wiki).

## Web Dashboard

Once running, access the web dashboard at `http://localhost:8080` (or your configured host/port).

### Dashboard Features

- **Live Status** – View current status of all monitored TestFlight betas
- **Add/Remove IDs** – Dynamically manage TestFlight IDs without restarting
- **Manage Notifications** – Add or remove Apprise notification URLs
- **View Logs** – See recent application logs
- **Metrics** – View check statistics and uptime
- **Dark Mode** – Toggle between light and dark themes

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Web dashboard |
| `/api/health` | GET | Health check endpoint |
| `/api/status` | GET | Current status of all TestFlight IDs |
| `/api/metrics` | GET | Application metrics |
| `/api/logs` | GET | Recent log entries |
| `/add` | POST | Add a TestFlight ID |
| `/remove` | POST | Remove a TestFlight ID |

## Dependencies

```
aiohttp>=3.9.0,<4.0.0
apprise>=1.8.0,<2.0.0
beautifulsoup4>=4.12.0,<5.0.0
fastapi>=0.100.0,<1.0.0
python-dotenv>=1.0.0,<2.0.0
uvicorn[standard]>=0.23.0,<1.0.0
colorama>=0.4.0,<1.0.0
```

## Troubleshooting

### No notifications received

1. Verify your `APPRISE_URL` is correctly formatted
2. Check the logs for any error messages
3. Ensure your notification service (Discord, Telegram, etc.) is properly configured

### TestFlight ID not found

1. Verify the ID is correct (8-12 alphanumeric characters)
2. Check that the TestFlight link is still valid
3. Some TestFlight betas may be region-locked

### High CPU/Memory usage

1. Increase `INTERVAL_CHECK` to reduce polling frequency
2. Reduce the number of monitored TestFlight IDs
3. Consider using Docker with resource limits

## License

This project is open source. See the [LICENSE](LICENSE) file for details.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## Acknowledgments

- [Apprise](https://github.com/caronc/apprise) – The powerful notification library that makes this possible
- [FastAPI](https://fastapi.tiangolo.com/) – The modern web framework powering the dashboard

