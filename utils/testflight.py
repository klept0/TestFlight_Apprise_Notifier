"""
TestFlight status checking utilities.

Provides efficient async checking of TestFlight beta status with detailed
status detection, error handling, optional caching, and rate limiting.
"""

import logging
import asyncio
import time
from enum import Enum
from typing import Dict, Optional, Any, Deque
from collections import deque
from datetime import datetime, timedelta
import aiohttp
from bs4 import BeautifulSoup


class TestFlightStatus(Enum):
    """TestFlight beta status states."""

    OPEN = "open"
    FULL = "full"
    CLOSED = "closed"
    UNKNOWN = "unknown"
    ERROR = "error"


# Status text patterns for detection
# Note: These patterns are matched against lowercase page text
# Order matters - FULL and CLOSED should be checked before OPEN
STATUS_PATTERNS = {
    TestFlightStatus.FULL: [
        "this beta is full",
        "beta is full",
        "full - there aren't any more spots",
        "there are no more spots available",
    ],
    TestFlightStatus.CLOSED: [
        "no longer accepting testers",
        "not accepting any new testers",
        "this beta isn't accepting",
        "isn't accepting any new testers",
        "beta isn't open to new testers",
    ],
    TestFlightStatus.OPEN: [
        "join the beta",
        "start testing",
        "open to testers",
        "get this app from the app store",
        "get it from the app store",
        # Match button text or CTAs that appear on open beta pages
        "join the test",
        "become a tester",
        "join as a tester",
        "test this app",
        # Newly added patterns for pages like:
        # "To join the Dark Noise: Ambient Sounds beta, open the link on your iPhone, iPad, or Mac after you install TestFlight."
        # These phrases appear on OPEN pages but were previously not matched.
        "to join the",  # used in combination with app name and 'beta'
        "open the link on your iphone",  # instruction for open enrollment
        "after you install testflight",  # part of generic open instructions
    ],
}

# Regex-based patterns (evaluated if plain substring patterns did not match)
# Enables more precise matching for constructs like: "to join the <app name> beta"
STATUS_REGEX_PATTERNS = {
    TestFlightStatus.OPEN: [
        # Generic open phrasing with app name
        r"to join the [a-z0-9: _\-]+ beta",
    ],
}


# Optional status caching (disabled by default)
_status_cache: Dict[str, tuple] = {}  # url -> (result, timestamp)
_cache_ttl_seconds = 300  # 5 minutes default TTL
_cache_enabled = False


def enable_status_cache(ttl_seconds: int = 300):
    """
    Enable status caching with specified TTL.

    Args:
        ttl_seconds: Cache time-to-live in seconds (default: 300 = 5 minutes)
    """
    global _cache_enabled, _cache_ttl_seconds
    _cache_enabled = True
    _cache_ttl_seconds = ttl_seconds
    logging.info(f"TestFlight status cache enabled (TTL: {ttl_seconds}s)")


def disable_status_cache():
    """Disable status caching and clear the cache."""
    global _cache_enabled
    _cache_enabled = False
    _status_cache.clear()
    logging.info("TestFlight status cache disabled")


def clear_status_cache():
    """Clear all cached status entries."""
    _status_cache.clear()
    logging.info("TestFlight status cache cleared")


def get_cached_status(url: str) -> Optional[Dict]:
    """
    Get cached status for a URL if available and not expired.

    Args:
        url: TestFlight URL to check

    Returns:
        Cached result dict if available and valid, None otherwise
    """
    if not _cache_enabled or url not in _status_cache:
        return None

    result, timestamp = _status_cache[url]
    age = time.time() - timestamp

    if age < _cache_ttl_seconds:
        return result
    else:
        # Expired - remove from cache
        del _status_cache[url]
        return None


def cache_status(url: str, result: Dict):
    """
    Cache a status result for a URL.

    Args:
        url: TestFlight URL
        result: Result dictionary to cache
    """
    if _cache_enabled:
        _status_cache[url] = (result, time.time())


# Rate limiting (configurable)
class RateLimiter:
    """Rate limiter using sliding window algorithm."""

    def __init__(self, max_requests: int = 100, time_window: int = 60):
        """
        Initialize rate limiter.

        Args:
            max_requests: Maximum requests allowed in time window
            time_window: Time window in seconds
        """
        self.max_requests = max_requests
        self.time_window = time_window
        self.requests: Deque[datetime] = deque()
        self.lock = asyncio.Lock()

    async def acquire(self):
        """Acquire permission to make a request, waiting if necessary."""
        async with self.lock:
            now = datetime.now()

            # Remove old requests outside time window
            while self.requests and (now - self.requests[0]) > timedelta(
                seconds=self.time_window
            ):
                self.requests.popleft()

            # Check if we're at the limit
            if len(self.requests) >= self.max_requests:
                # Calculate wait time until oldest request expires
                oldest = self.requests[0]
                wait_until = oldest + timedelta(seconds=self.time_window)
                wait_time = (wait_until - now).total_seconds()

                if wait_time > 0:
                    logging.debug(f"Rate limit reached, waiting {wait_time:.2f}s")
                    await asyncio.sleep(wait_time)

                    # After waiting, remove expired requests
                    now = datetime.now()
                    while self.requests and (now - self.requests[0]) > timedelta(
                        seconds=self.time_window
                    ):
                        self.requests.popleft()

            # Add current request timestamp
            self.requests.append(datetime.now())

    def get_stats(self) -> dict:
        """Get current rate limiter statistics."""
        now = datetime.now()
        # Count requests in current window
        recent = sum(
            1
            for req in self.requests
            if (now - req) <= timedelta(seconds=self.time_window)
        )

        return {
            "max_requests": self.max_requests,
            "time_window": self.time_window,
            "current_requests": recent,
            "remaining": max(0, self.max_requests - recent),
        }


# Global rate limiter (default: 100 requests per minute)
_rate_limiter = RateLimiter(max_requests=100, time_window=60)


def configure_rate_limiter(max_requests: int = 100, time_window: int = 60):
    """
    Configure global rate limiter settings.

    Args:
        max_requests: Maximum requests per time window
        time_window: Time window in seconds
    """
    global _rate_limiter
    _rate_limiter = RateLimiter(max_requests, time_window)
    logging.info(f"Rate limiter configured: {max_requests} req/{time_window}s")


def get_rate_limiter_stats() -> dict:
    """Get current rate limiter statistics."""
    return _rate_limiter.get_stats()


async def check_testflight_status(
    session: aiohttp.ClientSession,
    url: str,
    timeout: int = 10,
    use_cache: bool = True,
    use_rate_limit: bool = True,
) -> Dict[str, Any]:
    """
    Check TestFlight beta status asynchronously.

    Args:
        session: Active aiohttp ClientSession for connection pooling
        url: Full TestFlight URL to check
        timeout: Request timeout in seconds (default: 10)
        use_cache: Whether to use cached results if available (default: True)
        use_rate_limit: Whether to apply rate limiting (default: True)

    Returns:
        Dictionary containing:
            - url: The checked URL
            - status: TestFlightStatus enum value
            - status_text: Human-readable status message
            - app_name: App name if detected (optional)
            - raw_text: Raw status text from page (optional)
            - error: Error message if status is ERROR
            - cached: True if result came from cache
            - rate_limited: True if request was rate limited
    """
    # Check cache first if enabled
    if use_cache:
        cached_result = get_cached_status(url)
        if cached_result is not None:
            cached_result["cached"] = True
            cached_result["rate_limited"] = False
            return cached_result

    # Apply rate limiting if enabled
    if use_rate_limit:
        await _rate_limiter.acquire()

    result = {
        "url": url,
        "status": TestFlightStatus.UNKNOWN,
        "status_text": "Unknown",
        "cached": False,
        "rate_limited": use_rate_limit,
    }

    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            # Handle HTTP errors
            if resp.status == 404:
                result["status"] = TestFlightStatus.ERROR
                result["status_text"] = "Not Found (404)"
                result["error"] = "TestFlight page does not exist"
                return result
            elif resp.status != 200:
                result["status"] = TestFlightStatus.ERROR
                result["status_text"] = f"HTTP {resp.status}"
                result["error"] = f"HTTP error: {resp.status}"
                return result

            # Parse HTML response
            html = await resp.text()
            soup = BeautifulSoup(html, "html.parser")

            # Extract app name from title
            title_tag = soup.find("title")
            if title_tag:
                title_text = title_tag.text.strip()
                # Extract from "Join the [App Name] beta - TestFlight"
                if " beta - TestFlight" in title_text:
                    app_name = title_text.split(" beta - TestFlight")[0]
                    app_name = app_name.replace("Join the ", "")
                    result["app_name"] = app_name

            # Get status text from the page - try multiple selectors
            status_element = soup.select_one(".beta-status span")
            if status_element:
                raw_status_text = status_element.text.strip()
            else:
                # Try alternative selectors that Apple might be using
                alt_selectors = [
                    "main p",  # Main content area paragraph
                    "[data-testid='beta-status']",  # Data attribute
                    ".status-text",  # Alternative class name
                    "div[role='main'] p",  # Semantic div
                ]
                raw_status_text = ""
                for selector in alt_selectors:
                    elem = soup.select_one(selector)
                    if elem:
                        raw_status_text = elem.text.strip()
                        break

            # Always also get full page text as fallback for pattern matching
            full_page_text = soup.get_text(separator=" ").lower()
            page_text = raw_status_text.lower() if raw_status_text else full_page_text

            # Store raw text for debugging (up to 500 chars for investigation)
            result["raw_text"] = raw_status_text or full_page_text[:300]

            # Detect status based on substring patterns first
            detected = False
            for status, patterns in STATUS_PATTERNS.items():
                for pattern in patterns:
                    if pattern in page_text:
                        result["status"] = status
                        result["status_text"] = status.value.capitalize()
                        detected = True
                        logging.debug(
                            f"Detected {status.value} via pattern '{pattern}' for {url}"
                        )
                        break
                if detected:
                    break

            # If still not detected, try regex patterns (more flexible)
            if not detected:
                for status, regex_list in STATUS_REGEX_PATTERNS.items():
                    for regex_pattern in regex_list:
                        import re

                        if re.search(regex_pattern, page_text):
                            result["status"] = status
                            result["status_text"] = status.value.capitalize()
                            detected = True
                            logging.debug(
                                f"Detected {status.value} via regex '{regex_pattern}' for {url}"
                            )
                            break
                    if detected:
                        break

            # If no pattern matched, status remains UNKNOWN
            if not detected:
                result["status"] = TestFlightStatus.UNKNOWN
                result["status_text"] = "Unknown status"

            # Cache the result before returning
            cache_status(url, result)
            return result

    except asyncio.TimeoutError:
        result["status"] = TestFlightStatus.ERROR
        result["status_text"] = "Timeout"
        result["error"] = f"Request timed out after {timeout}s"
        logging.error(f"Timeout checking {url}")
        # Don't cache errors
        return result
    except aiohttp.ClientError as e:
        result["status"] = TestFlightStatus.ERROR
        result["status_text"] = "Network Error"
        result["error"] = f"Network error: {str(e)}"
        logging.error(f"Network error checking {url}: {e}")
        # Don't cache errors
        return result
    except Exception as e:
        result["status"] = TestFlightStatus.ERROR
        result["status_text"] = "Error"
        result["error"] = f"Unexpected error: {str(e)}"
        logging.error(f"Unexpected error checking {url}: {e}")
        # Don't cache errors
        return result


async def check_testflight_status_with_retry(
    session: aiohttp.ClientSession,
    url: str,
    max_retries: int = 3,
    base_delay: float = 1.0,
    timeout: int = 10,
    use_cache: bool = True,
    use_rate_limit: bool = True,
) -> Dict[str, Any]:
    """
    Check TestFlight status with exponential backoff retry logic.

    Args:
        session: Active aiohttp ClientSession
        url: Full TestFlight URL to check
        max_retries: Maximum retry attempts (default: 3)
        base_delay: Initial delay in seconds for exponential backoff (default: 1.0)
        timeout: Request timeout in seconds (default: 10)
        use_cache: Whether to use cached results (default: True)
        use_rate_limit: Whether to apply rate limiting (default: True)

    Returns:
        Dictionary with status results (same as check_testflight_status)
    """
    last_error = None
    last_result = None

    for attempt in range(max_retries):
        try:
            result = await check_testflight_status(
                session, url, timeout, use_cache, use_rate_limit
            )

            # Success - return result
            if result["status"] != TestFlightStatus.ERROR:
                if attempt > 0:
                    logging.info(
                        f"Request succeeded on attempt {attempt + 1}/{max_retries}"
                    )
                return result

            # Error but might be transient
            last_error = result.get("error", "Unknown error")
            last_result = result

        except Exception as e:
            last_error = str(e)
            logging.warning(f"Attempt {attempt + 1}/{max_retries} failed: {last_error}")

        # Calculate exponential backoff delay
        if attempt < max_retries - 1:
            # Exponential backoff: 1s, 2s, 4s, 8s, etc.
            delay = base_delay * (2**attempt)
            # Add jitter (randomness) to prevent thundering herd
            import random

            jitter = random.uniform(0, delay * 0.1)
            total_delay = delay + jitter

            logging.debug(
                f"Retrying in {total_delay:.2f}s "
                f"(attempt {attempt + 2}/{max_retries})"
            )
            await asyncio.sleep(total_delay)

    # All retries failed
    if last_result:
        last_result["error"] = f"Failed after {max_retries} retries: {last_error}"
        return last_result
    else:
        return {
            "url": url,
            "status": TestFlightStatus.ERROR,
            "status_text": "Error",
            "error": f"Failed after {max_retries} retries: {last_error}",
            "cached": False,
            "rate_limited": False,
        }


def is_status_available(status: TestFlightStatus) -> bool:
    """
    Check if a TestFlight status indicates availability.

    Args:
        status: TestFlightStatus enum value

    Returns:
        True if beta is open/available, False otherwise
    """
    return status == TestFlightStatus.OPEN
