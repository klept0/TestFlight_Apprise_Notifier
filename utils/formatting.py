from datetime import datetime

def format_datetime(dt: datetime) -> str:
    """Format datetime in 24-hour time for compatibility in any locale."""
    return dt.strftime("%Y-%m-%d %H:%M:%S")

def format_link(base_url: str, tf_id: str) -> str:
    """Format TestFlight link."""
    return f"{base_url}{tf_id}"
