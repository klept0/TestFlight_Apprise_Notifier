"""Apprise service icon/name lookup for the web dashboard."""

from typing import Dict


def get_apprise_service_icon(url: str) -> Dict[str, str]:
    """
    Get service icon URL and name for an Apprise URL.

    Returns a dict with 'icon_url', 'service_name', and 'fallback_emoji'.
    """
    # Service icon mappings (using public CDN URLs for logos)
    service_map = {
        "discord": {
            "icon_url": "https://cdn.simpleicons.org/discord/5865F2",
            "service_name": "Discord",
            "emoji": "💬",
        },
        "slack": {
            "icon_url": "https://cdn.simpleicons.org/slack/4A154B",
            "service_name": "Slack",
            "emoji": "💼",
        },
        "telegram": {
            "icon_url": "https://cdn.simpleicons.org/telegram/26A5E4",
            "service_name": "Telegram",
            "emoji": "✈️",
        },
        "tgram": {
            "icon_url": "https://cdn.simpleicons.org/telegram/26A5E4",
            "service_name": "Telegram",
            "emoji": "✈️",
        },
        "pushover": {
            "icon_url": "https://cdn.simpleicons.org/pushover/249DF1",
            "service_name": "Pushover",
            "emoji": "📱",
        },
        "pover": {
            "icon_url": "https://cdn.simpleicons.org/pushover/249DF1",
            "service_name": "Pushover",
            "emoji": "📱",
        },
        "gotify": {
            "icon_url": "https://cdn.simpleicons.org/gotify/00A4D8",
            "service_name": "Gotify",
            "emoji": "🔔",
        },
        "mailto": {
            "icon_url": "https://cdn.simpleicons.org/gmail/EA4335",
            "service_name": "Email",
            "emoji": "📧",
        },
        "mailtos": {
            "icon_url": "https://cdn.simpleicons.org/gmail/EA4335",
            "service_name": "Email",
            "emoji": "📧",
        },
        "ntfy": {
            "icon_url": "https://cdn.simpleicons.org/ntfy/3A9EEA",
            "service_name": "ntfy",
            "emoji": "🔔",
        },
        "matrix": {
            "icon_url": "https://cdn.simpleicons.org/matrix/000000",
            "service_name": "Matrix",
            "emoji": "💬",
        },
        "mattermost": {
            "icon_url": "https://cdn.simpleicons.org/mattermost/0058CC",
            "service_name": "Mattermost",
            "emoji": "💬",
        },
        "rocketchat": {
            "icon_url": "https://cdn.simpleicons.org/rocketdotchat/F5455C",
            "service_name": "Rocket.Chat",
            "emoji": "🚀",
        },
        "teams": {
            "icon_url": "https://cdn.simpleicons.org/microsoftteams/6264A7",
            "service_name": "Microsoft Teams",
            "emoji": "👥",
        },
        "webhook": {
            "icon_url": "https://cdn.simpleicons.org/webhooks/2088FF",
            "service_name": "Webhook",
            "emoji": "🌐",
        },
        "json": {
            "icon_url": "https://cdn.simpleicons.org/json/000000",
            "service_name": "JSON",
            "emoji": "🌐",
        },
        "xml": {
            "icon_url": "https://cdn.simpleicons.org/xml/005FAD",
            "service_name": "XML",
            "emoji": "🌐",
        },
        "prowl": {
            "icon_url": "https://cdn.simpleicons.org/apple/000000",
            "service_name": "Prowl",
            "emoji": "🍎",
        },
        "pushbullet": {
            "icon_url": "https://cdn.simpleicons.org/pushbullet/4AB367",
            "service_name": "Pushbullet",
            "emoji": "📱",
        },
        "signal": {
            "icon_url": "https://cdn.simpleicons.org/signal/3A76F0",
            "service_name": "Signal",
            "emoji": "💬",
        },
        "twilio": {
            "icon_url": "https://cdn.simpleicons.org/twilio/F22F46",
            "service_name": "Twilio",
            "emoji": "📱",
        },
        "twitter": {
            "icon_url": "https://cdn.simpleicons.org/x/000000",
            "service_name": "Twitter/X",
            "emoji": "🐦",
        },
        "mastodon": {
            "icon_url": "https://cdn.simpleicons.org/mastodon/6364FF",
            "service_name": "Mastodon",
            "emoji": "🐘",
        },
        "reddit": {
            "icon_url": "https://cdn.simpleicons.org/reddit/FF4500",
            "service_name": "Reddit",
            "emoji": "👽",
        },
        "ifttt": {
            "icon_url": "https://cdn.simpleicons.org/ifttt/000000",
            "service_name": "IFTTT",
            "emoji": "⚡",
        },
        "join": {
            "icon_url": "https://cdn.simpleicons.org/android/3DDC84",
            "service_name": "Join",
            "emoji": "📱",
        },
        "kodi": {
            "icon_url": "https://cdn.simpleicons.org/kodi/17B2E7",
            "service_name": "Kodi",
            "emoji": "📺",
        },
        "xbmc": {
            "icon_url": "https://cdn.simpleicons.org/kodi/17B2E7",
            "service_name": "XBMC",
            "emoji": "📺",
        },
        "synology": {
            "icon_url": "https://cdn.simpleicons.org/synology/B5B5B6",
            "service_name": "Synology",
            "emoji": "💾",
        },
        "webex": {
            "icon_url": "https://cdn.simpleicons.org/webex/000000",
            "service_name": "Webex",
            "emoji": "👥",
        },
        "zulip": {
            "icon_url": "https://cdn.simpleicons.org/zulip/52C2AF",
            "service_name": "Zulip",
            "emoji": "💬",
        },
        "homeassistant": {
            "icon_url": "https://cdn.simpleicons.org/homeassistant/18BCF2",
            "service_name": "Home Assistant",
            "emoji": "🏠",
        },
        "gitter": {
            "icon_url": "https://cdn.simpleicons.org/gitter/ED1965",
            "service_name": "Gitter",
            "emoji": "💬",
        },
        "notica": {
            "icon_url": "https://cdn.simpleicons.org/notifications/FF6B6B",
            "service_name": "Notica",
            "emoji": "🔔",
        },
        "notifico": {
            "icon_url": "https://cdn.simpleicons.org/notifications/FF6B6B",
            "service_name": "Notifico",
            "emoji": "🔔",
        },
        "opsgenie": {
            "icon_url": "https://cdn.simpleicons.org/opsgenie/172B4D",
            "service_name": "Opsgenie",
            "emoji": "🚨",
        },
        "pagerduty": {
            "icon_url": "https://cdn.simpleicons.org/pagerduty/06AC38",
            "service_name": "PagerDuty",
            "emoji": "🚨",
        },
    }

    # Extract service type from URL
    url_lower = url.lower()
    for service_key, service_info in service_map.items():
        if url_lower.startswith(f"{service_key}://") or url_lower.startswith(
            f"{service_key}s://"
        ):
            return service_info

    # Default fallback for unknown services
    if url_lower.startswith("http://") or url_lower.startswith("https://"):
        return {
            "icon_url": "https://cdn.simpleicons.org/webhooks/2088FF",
            "service_name": "Webhook",
            "emoji": "🌐",
        }

    # Generic fallback
    return {"icon_url": "", "service_name": "Unknown Service", "emoji": "📢"}
