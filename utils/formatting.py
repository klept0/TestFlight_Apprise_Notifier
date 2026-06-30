from __future__ import annotations
import re
import aiohttp

from datetime import datetime

try:
    from bs4 import BeautifulSoup
except Exception:
    BeautifulSoup = None

# Cache for app names and icons with size limits
from collections import OrderedDict


class LRUCache:
    def __init__(self, max_size=200):
        self.cache = OrderedDict()
        self.max_size = max_size

    def get(self, key):
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        return None

    def put(self, key, value):
        if key in self.cache:
            self.cache.move_to_end(key)
        self.cache[key] = value
        if len(self.cache) > self.max_size:
            self.cache.popitem(last=False)


app_name_cache = LRUCache(100)  # Cache up to 100 app names
app_icon_cache = LRUCache(100)  # Cache up to 100 app icons

DEFAULT_TIMEOUT = 10
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Expired/invalid link detection phrases
EXPIRED_PHRASES = [
    "This beta isn't accepting any new testers",
    "no longer accepting testers",
    "Unable to Accept Invite",
]


def _safe_join(base_url: str, tf_id: str) -> str:
    return f"{base_url.rstrip('/')}/{tf_id.lstrip('/')}"


def _extract_title_html(html: str) -> str | None:
    if BeautifulSoup is not None:
        soup = BeautifulSoup(html, "html.parser")
        if soup.title and soup.title.string:
            return soup.title.string.strip()
        return None
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()
    return None


def _extract_app_name_from_html(html: str) -> str:
    """Extract app name from HTML using multiple strategies."""
    if BeautifulSoup is not None:
        soup = BeautifulSoup(html, "html.parser")

        # Try grabbing from <title> with better cleaning
        title = soup.find("title")
        if title and title.text:
            clean_title = (
                title.text.strip().replace(" - TestFlight", "").replace(" - Apple", "")
            )
            invalid_titles = ["testflight", "apple"]
            if clean_title and clean_title.lower() not in invalid_titles:
                return clean_title

        # Try grabbing from meta tags
        meta_title = soup.find("meta", property="og:title")
        if meta_title and meta_title.get("content"):
            return meta_title["content"].strip()

    # Fallback to existing logic
    raw_title = _extract_title_html(html)
    return _normalize_app_name(raw_title)


def _normalize_app_name(raw_title: str | None) -> str:
    if not raw_title:
        return "UnknownApp"

    # Remove "Step X" patterns
    raw_title = re.sub(r"Step \d+", "", raw_title, flags=re.IGNORECASE).strip()

    # Match "Join the (AppName) beta - TestFlight - Apple"
    m = re.search(r"Join the (.+) beta - TestFlight - Apple", raw_title)
    if m:
        app_name = m.group(1).strip()
        # Don't return generic TestFlight titles
        invalid_names = ["testflight", "testflight - apple", "apple"]
        if app_name.lower() not in invalid_names:
            return app_name

    # Fallback: strip " on TestFlight"
    name = re.sub(r"\s+on\s+TestFlight\s*$", "", raw_title, flags=re.IGNORECASE).strip()

    # Check if the result is a generic TestFlight title
    invalid_names = ["testflight", "testflight - apple", "apple", ""]
    if name.lower() in invalid_names:
        return "UnknownApp"

    return name or "UnknownApp"


async def get_app_name(base_url: str, tf_id: str) -> str:
    cache_key = f"{base_url}:{tf_id}"
    cached_result = app_name_cache.get(cache_key)
    if cached_result is not None:
        return cached_result

    url = _safe_join(base_url, tf_id)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers=HEADERS,
                timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT),
            ) as resp:
                resp.raise_for_status()
                html = await resp.text()

                # Check for expired/invalid links first
                if any(phrase in html for phrase in EXPIRED_PHRASES):
                    app_name = "Expired or Invalid Link"
                    app_name_cache.put(cache_key, app_name)
                    return app_name

                # Try multiple sources for app name
                app_name = _extract_app_name_from_html(html)
                app_name_cache.put(cache_key, app_name)
                return app_name
    except Exception:
        app_name = "UnknownApp"
        app_name_cache.put(cache_key, app_name)
        return app_name


async def get_app_icon(base_url: str, tf_id: str) -> str:
    cache_key = f"{base_url}:{tf_id}"
    cached_result = app_icon_cache.get(cache_key)
    if cached_result is not None:
        return cached_result

    url = _safe_join(base_url, tf_id)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers=HEADERS,
                timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT),
            ) as resp:
                resp.raise_for_status()
                html = await resp.text()
                if BeautifulSoup is not None:
                    soup = BeautifulSoup(html, "html.parser")
                    # Assume the app icon is the first img tag or with class 'app-icon'
                    img = soup.find("img", class_="app-icon") or soup.find("img")
                    if img and img.get("src"):
                        icon_url = img["src"]
                        # Make absolute if relative
                        if icon_url.startswith("/"):
                            icon_url = f"https://testflight.apple.com{icon_url}"
                        app_icon_cache.put(cache_key, icon_url)
                        return icon_url
    except Exception:
        pass
    # Default icon or empty
    icon_url = ""
    app_icon_cache.put(cache_key, icon_url)
    return icon_url


def format_link(base_url: str, tf_id: str) -> str:
    return f"{base_url.rstrip('/')}/{tf_id.lstrip('/')}"


async def format_notification_link(
    base_url: str, tf_id: str, name_override: str = None
) -> str:
    app_name = name_override or await get_app_name(base_url, tf_id)
    return f"Slots available for {app_name}: {format_link(base_url, tf_id)}"


def format_datetime(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")
