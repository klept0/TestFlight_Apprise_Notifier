# TestFlight Apprise Notifier

This project monitors TestFlight beta links and sends notifications when a beta becomes available. It uses FastAPI for the server, Apprise for notifications, and aiohttp for asynchronous HTTP requests.

## Features

- **TestFlight Monitoring**: Continuously checks TestFlight beta links for availability.
- **Notifications**: Sends notifications using Apprise when a beta becomes available.
- **Heartbeat Notifications**: Sends periodic heartbeat messages to indicate the bot is running.
- **Logging**: Uses Python's `logging` module for better log management.
- **Graceful Shutdown**: Handles shutdown signals (`SIGINT`, `SIGTERM`) to clean up resources.
- **Environment Variable Validation**: Ensures required environment variables are set before starting.

## Setup

### Prerequisites

- Python 3.8 or higher
- Install dependencies using `pip install -r requirements.txt`

### Environment Variables

Create a `.env` file in the project root with the following variables:

- `ID_LIST`: Comma-separated list of TestFlight IDs to monitor.
- `APPRISE_URL`: Comma-separated list of Apprise notification URLs.
- `INTERVAL_CHECK`: Interval (in milliseconds) between checks for each TestFlight link.

Example `.env` file:

```env
ID_LIST=abc123,def456,ghi789
APPRISE_URL=mailto://user:password@smtp.example.com,discord://webhook_id/webhook_token
INTERVAL_CHECK=10000
```

### Running the Application

1. Start the application:
   ```bash
   python main.py
   ```

2. Access the FastAPI server at `http://localhost:8089`.

### Utility Functions

The project includes utility functions for better modularity:

- **`utils/notifications.py`**: Handles sending notifications with error handling.
- **`utils/formatting.py`**: Provides functions for formatting dates and links.
- **`utils/colors.py`**: Prints messages in green for heartbeat notifications.

### Logging

Logs are displayed in the console with timestamps and log levels. Example:

```
2023-01-01 12:00:00 - INFO - Notification sent: Heartbeat - 2023-01-01 12:00:00
2023-01-01 12:00:00 - INFO - Shutdown signal received. Cleaning up...
```

### Graceful Shutdown

The application listens for `SIGINT` and `SIGTERM` signals to clean up resources like the `aiohttp.ClientSession` before exiting.

### Heartbeat Notifications

Heartbeat messages are sent every 6 hours to indicate the bot is running. These messages are displayed in **green** in the console.

### Example Output

```plaintext
2023-01-01 12:00:00 - INFO - Notification sent: Heartbeat - 2023-01-01 12:00:00
Heartbeat - 2023-01-01 12:00:00
2023-01-01 12:01:00 - INFO - 200 - abc123 - AppName - Available
Notification sent: https://testflight.apple.com/join/abc123
```

## Contributing

Feel free to submit issues or pull requests to improve the project.

## License

This project is licensed under the MIT License.