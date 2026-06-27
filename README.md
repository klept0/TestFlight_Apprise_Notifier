# TestFlight Apprise Notifier

A Python-based monitoring tool that continuously checks Apple TestFlight beta availability and sends notifications through [Apprise](https://github.com/caronc/apprise) when slots become available.

## Features

- 🔔 **Real-time Notifications** – Get notified instantly when a TestFlight beta opens up
- 📱 **80+ Notification Services** – Supports Discord, Slack, Telegram, Pushover, Email, and many more via Apprise
- 🌐 **Web Dashboard** – Modern full-page FastAPI web interface with sidebar navigation
- 🔒 **Optional Authentication** – Protect the dashboard/API with HTTP Basic auth; binds to localhost by default
- 📲 **Installable (PWA)** – Responsive, mobile-friendly dashboard you can add to your home screen
- 📖 **Interactive API Docs** – Auto-generated OpenAPI docs at `/docs`
- 🐳 **Docker Support** – Easy deployment with Docker and Docker Compose
- ⚡ **High Performance** – Async HTTP requests with connection pooling and caching
- 🔄 **Status Tracking** – Detects status changes (Open, Full, Closed) and notifies accordingly
- 💓 **Heartbeat Notifications** – Optional periodic notifications to confirm the service is running
- 🧪 **Test Notification** – Send a one-off test message to your configured destinations from the dashboard
- 🎨 **Dark/Light Theme** – Responsive web dashboard with persistent theme preference
- 📝 **Live Config Editor** – Edit your `.env` file and restart the service directly from the dashboard
- 🔍 **Update Checker** – Optional GitHub version checks with toggle in the dashboard

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

All configuration is done through environment variables in the `.env` file. You can also edit settings live through the web dashboard's Settings section.

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
| `ENABLE_UPDATE_CHECKER` | `true` | Check GitHub for new version releases |
| `GITHUB_CHECK_INTERVAL_HOURS` | `24` | How often to check for updates (hours) |
| `GITHUB_REPO` | `klept0/TestFlight_Apprise_Notifier` | GitHub repo to check for updates |
| `GITHUB_BRANCH` | `main` | Branch to compare against for update checks |
| `UI_THEME` | `dark` | Default web dashboard theme (`dark` or `light`) |
| `FASTAPI_HOST` | `127.0.0.1` | Web dashboard bind address (set `0.0.0.0` to expose it) |
| `FASTAPI_PORT` | random `8000–9000` | Web dashboard port (set explicitly to keep it stable) |
| `WEB_USERNAME` | _(unset)_ | Username for optional HTTP Basic auth on the dashboard/API |
| `WEB_PASSWORD` | _(unset)_ | Password for optional HTTP Basic auth (auth is enabled only when both are set) |

> **Note:** `UI_THEME` sets the server-side default for new visitors. Each browser can override it independently via the theme toggle in the dashboard — the preference is saved in `localStorage`.

> **🔒 Security:** The dashboard can read/write your `.env` (which contains your notification secrets) and stop/restart the process. It binds to `127.0.0.1` by default for that reason. If you expose it on a non-loopback address (`FASTAPI_HOST=0.0.0.0`, e.g. in Docker), **authentication is required**: `WEB_USERNAME` and `WEB_PASSWORD` must both be set, otherwise the app refuses to start and exits with code `1`. On `127.0.0.1`/`localhost` auth is optional. `/api/health` stays open for health checks.

### Example `.env` File

```ini
# ==============================================================
# TestFlight Apprise Notifier - Environment Configuration
# ==============================================================

# --- Required -------------------------------------------------

# Comma-separated list of TestFlight beta IDs to monitor
ID_LIST=abc12345,xyz98765,def00001

# Apprise notification URL(s) - comma-separated if using multiple services
APPRISE_URL=discord://webhook_id/webhook_token,tgram://bot_token/chat_id

# --- Monitoring -----------------------------------------------

# How often to check each TestFlight ID (milliseconds). Default: 10000 (10s)
INTERVAL_CHECK=10000

# --- Notifications --------------------------------------------

# Send a notification on every OPEN status check, not just when status changes
ALWAYS_NOTIFY_OPEN=false

# Periodic "I'm alive" heartbeat notification interval (hours). 0 to disable.
HEARTBEAT_INTERVAL=6

# --- Update Checker -------------------------------------------

# Check GitHub for new versions of the notifier
ENABLE_UPDATE_CHECKER=true

# How often to check for updates (hours)
GITHUB_CHECK_INTERVAL_HOURS=24

# --- Web Dashboard --------------------------------------------

# Bind to localhost by default. Only set 0.0.0.0 if you also set the
# WEB_USERNAME / WEB_PASSWORD below to require a login.
FASTAPI_HOST=127.0.0.1
FASTAPI_PORT=8080

# Optional HTTP Basic auth (both must be set to enable it)
WEB_USERNAME=
WEB_PASSWORD=

# Default UI theme for new visitors (dark / light)
UI_THEME=dark
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

### Dashboard Sections

- **Dashboard** – Live status of all monitored betas, uptime, metrics, and service controls (start/stop/restart)
- **TestFlight IDs** – Add or remove IDs to monitor without restarting
- **Apprise URLs** – Add or remove notification endpoints on the fly, and send a test notification to confirm they work
- **Settings** – Toggle options (update checker, always-notify), change the default theme, and edit the `.env` file directly with the built-in editor
- **Logs** – Filterable live log viewer with level and line-count controls

### API Endpoints

Interactive OpenAPI docs are available at `/docs`.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Web dashboard |
| `/docs` | GET | Interactive API documentation (Swagger UI) |
| `/api/health` | GET | Health check (always reachable, even with auth enabled) |
| `/api/metrics` | GET | Application metrics (uptime, check counts, etc.) |
| `/api/logs` | GET | Recent log entries (`?limit=` 1–1000) |
| `/api/updates` | GET | Check GitHub for a newer version (`?force=true` to bypass cache) |
| `/api/testflight-ids` | GET / POST | List or add a monitored TestFlight ID |
| `/api/testflight-ids/details` | GET | Monitored IDs with app names and icons |
| `/api/testflight-ids/{id}` | DELETE | Remove a TestFlight ID |
| `/api/testflight-ids/batch` | POST | Add/remove multiple IDs at once |
| `/api/apprise-urls` | GET / POST | List or add an Apprise notification URL |
| `/api/apprise-urls/{url}` | DELETE | Remove an Apprise URL |
| `/api/config` | GET / POST | Read or write the `.env` configuration file |
| `/api/control/stop` | POST | Stop the service |
| `/api/control/restart` | POST | Restart the service (re-executes in place) |
| `/api/test-notification` | POST | Send a test notification to the configured Apprise destinations |

## Dependencies

```
aiohttp>=3.13.5,<4.0.0
apprise>=1.9.9,<2.0.0
beautifulsoup4>=4.14.3,<5.0.0
fastapi>=0.136.0,<1.0.0
python-dotenv>=1.2.2,<2.0.0
uvicorn[standard]>=0.45.0,<1.0.0
colorama>=0.4.6,<1.0.0
python-multipart
```

## Project Structure

The application is organized into focused modules:

| File | Responsibility |
|------|----------------|
| `main.py` | App wiring: FastAPI app, auth dependency, lifespan, the monitor loop, and the entrypoint |
| `config.py` | Environment/configuration parsing (loads `.env`, exposes settings and constants) |
| `state.py` | Shared runtime state and business logic: Apprise object, HTTP session, ID/URL management, validation, GitHub update checker, `.env` persistence |
| `routes.py` | The web/API layer as a FastAPI `APIRouter` (all dashboard and API endpoints) |
| `utils/` | Helpers: `testflight.py` (status detection), `formatting.py`, `notifications.py`, `metrics.py`, `service_icons.py`, `web_logging.py`, `colors.py` |
| `templates/`, `static/` | Dashboard HTML template and the CSS/JS/PWA assets |
| `tests/` | Pytest suite (`test_testflight.py` for status detection, `test_api.py` for the API layer) |

## Troubleshooting

### No notifications received

1. Verify your `APPRISE_URL` is correctly formatted
2. Check the Logs section of the dashboard for error messages
3. Ensure your notification service (Discord, Telegram, etc.) is properly configured

### TestFlight ID not found

1. Verify the ID is correct (8–12 alphanumeric characters)
2. Check that the TestFlight link is still valid
3. Some TestFlight betas may be region-locked

### High CPU/Memory usage

1. Increase `INTERVAL_CHECK` to reduce polling frequency
2. Reduce the number of monitored TestFlight IDs
3. Consider using Docker with resource limits

### Settings not reflecting `.env` values

Configuration is loaded once at startup (`config.py` calls `load_dotenv()`). If you edit the `.env` file — directly or through the dashboard's Settings editor — use the **Restart** button on the Dashboard to apply the changes.

## License

This project is open source. See the [LICENSE](LICENSE) file for details.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## Acknowledgments

- [Apprise](https://github.com/caronc/apprise) – The powerful notification library that makes this possible
- [FastAPI](https://fastapi.tiangolo.com/) – The modern web framework powering the dashboard
