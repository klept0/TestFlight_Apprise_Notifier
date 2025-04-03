import logging

def send_notification(message: str, apobj):
    """Send notification using Apprise with error handling."""
    try:
        apobj.notify(body=message, title="TestFlight Alert")
        logging.info(f"Notification sent: {message}")
    except Exception as e:
        logging.error(f"Error sending notification: {e}")
