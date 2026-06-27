import logging
import asyncio

TEST_NOTIFICATION_TITLE = "TestFlight Apprise Notifier"
TEST_NOTIFICATION_BODY = (
    "✅ Test notification from TestFlight Apprise Notifier. "
    "If you received this, your notifications are working."
)


def _send_test_sync(urls):
    """Send a test notification to each Apprise URL independently.

    Returns a (sent, failed) count tuple. Sends to each destination on its own
    so a partial outcome ("at least one sent") can be reported. Never logs or
    returns the URLs/secrets themselves.
    """
    import apprise

    sent = 0
    failed = 0
    for url in urls:
        try:
            target = apprise.Apprise()
            if target.add(url) and target.notify(
                body=TEST_NOTIFICATION_BODY, title=TEST_NOTIFICATION_TITLE
            ):
                sent += 1
            else:
                failed += 1
        except Exception:
            failed += 1
    return sent, failed


async def send_test_notification(urls):
    """Async wrapper: send a one-off test notification to each configured URL."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _send_test_sync, list(urls))


def send_notification(message: str, apobj, icon_url: str = ""):
    """Send notification using Apprise with error handling.

    Apprise does not provide a generic `attach()` method on the core object.
    To include an icon, most notification services rely on their own plugin
    parameters or simply render links in the body. For broad compatibility,
    we append the icon URL (if provided) to the message body.
    """
    try:
        body = message
        if icon_url:
            # Append icon URL on new line for visibility without breaking services
            body = f"{message}\nIcon: {icon_url}"
        apobj.notify(body=body, title="TestFlight Alert")
        logging.info(f"Notification sent: {message}")
    except Exception as e:
        logging.error(f"Error sending notification: {e}")


async def send_notification_async(message: str, apobj, icon_url: str = ""):
    """Send notification asynchronously."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, send_notification, message, apobj, icon_url)
