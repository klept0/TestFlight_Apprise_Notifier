"""
Unit tests for TestFlight Apprise Notifier utilities.

Run with: pytest tests/test_testflight.py -v
"""

import pytest
from unittest.mock import Mock, AsyncMock
from utils.testflight import (
    TestFlightStatus,
    check_testflight_status,
    check_testflight_status_with_retry,
    is_status_available,
    enable_status_cache,
    disable_status_cache,
    configure_rate_limiter,
    RateLimiter,
)


class TestTestFlightStatus:
    """Tests for TestFlightStatus enum."""

    def test_status_values(self):
        """Test that all status values are defined."""
        assert TestFlightStatus.OPEN.value == "open"
        assert TestFlightStatus.FULL.value == "full"
        assert TestFlightStatus.CLOSED.value == "closed"
        assert TestFlightStatus.UNKNOWN.value == "unknown"
        assert TestFlightStatus.ERROR.value == "error"


class TestIsStatusAvailable:
    """Tests for is_status_available function."""

    def test_open_status_is_available(self):
        """Test that OPEN status returns True."""
        assert is_status_available(TestFlightStatus.OPEN) is True

    def test_other_statuses_not_available(self):
        """Test that non-OPEN statuses return False."""
        assert is_status_available(TestFlightStatus.FULL) is False
        assert is_status_available(TestFlightStatus.CLOSED) is False
        assert is_status_available(TestFlightStatus.UNKNOWN) is False
        assert is_status_available(TestFlightStatus.ERROR) is False


class TestRateLimiter:
    """Tests for RateLimiter class."""

    @pytest.mark.asyncio
    async def test_rate_limiter_allows_within_limit(self):
        """Test that requests within limit are allowed immediately."""
        limiter = RateLimiter(max_requests=5, time_window=60)

        # Make 5 requests (at limit)
        for i in range(5):
            await limiter.acquire()

        # Should complete quickly
        assert len(limiter.requests) == 5

    @pytest.mark.asyncio
    async def test_rate_limiter_stats(self):
        """Test rate limiter statistics."""
        limiter = RateLimiter(max_requests=10, time_window=60)

        await limiter.acquire()
        await limiter.acquire()

        stats = limiter.get_stats()
        assert stats["max_requests"] == 10
        assert stats["time_window"] == 60
        assert stats["current_requests"] == 2
        assert stats["remaining"] == 8


class TestCaching:
    """Tests for caching functionality."""

    def teardown_method(self):
        """Clean up after each test."""
        disable_status_cache()

    def test_enable_cache(self):
        """Test enabling cache."""
        enable_status_cache(ttl_seconds=300)
        # Cache should now be enabled
        # This is more of an integration test

    def test_disable_cache(self):
        """Test disabling cache."""
        enable_status_cache(ttl_seconds=300)
        disable_status_cache()
        # Cache should be disabled and cleared


@pytest.mark.asyncio
class TestCheckTestFlightStatus:
    """Tests for check_testflight_status function."""

    async def test_check_status_open(self):
        """Test checking status for open beta."""
        mock_session = Mock()
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.text = AsyncMock(
            return_value="<html><title>Join the My App beta - TestFlight - Apple</title><body>join the beta</body></html>"
        )

        mock_session.get = Mock(
            return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_response))
        )

        result = await check_testflight_status(
            mock_session,
            "https://testflight.apple.com/join/abc123",
            use_cache=False,
            use_rate_limit=False,
        )

        assert result["status"] == TestFlightStatus.OPEN
        assert result["url"] == "https://testflight.apple.com/join/abc123"

    async def test_check_status_full(self):
        """Test checking status for full beta."""
        mock_session = Mock()
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.text = AsyncMock(
            return_value='<html><body><span class="beta-status"><span>This beta is full.</span></span></body></html>'
        )

        mock_session.get = Mock(
            return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_response))
        )

        result = await check_testflight_status(
            mock_session,
            "https://testflight.apple.com/join/abc123",
            use_cache=False,
            use_rate_limit=False,
        )

        assert result["status"] == TestFlightStatus.FULL

    async def test_check_status_404(self):
        """Test checking status for non-existent beta."""
        mock_session = Mock()
        mock_response = AsyncMock()
        mock_response.status = 404

        mock_session.get = Mock(
            return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_response))
        )

        result = await check_testflight_status(
            mock_session,
            "https://testflight.apple.com/join/invalid",
            use_cache=False,
            use_rate_limit=False,
        )

        assert result["status"] == TestFlightStatus.ERROR
        assert "404" in result["status_text"]


@pytest.mark.asyncio
class TestCheckTestFlightStatusWithRetry:
    """Tests for check_testflight_status_with_retry function."""

    async def test_retry_success_on_first_attempt(self):
        """Test that successful request on first attempt doesn't retry."""
        mock_session = Mock()
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.text = AsyncMock(
            return_value="<html><body>join the beta</body></html>"
        )

        mock_session.get = Mock(
            return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_response))
        )

        result = await check_testflight_status_with_retry(
            mock_session,
            "https://testflight.apple.com/join/abc123",
            max_retries=3,
            use_cache=False,
            use_rate_limit=False,
        )

        assert result["status"] != TestFlightStatus.ERROR

    async def test_retry_fails_after_max_attempts(self):
        """Test that retry gives up after max attempts."""
        mock_session = Mock()

        # Mock a failing response
        mock_response = AsyncMock()
        mock_response.status = 500

        mock_session.get = Mock(
            return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_response))
        )

        result = await check_testflight_status_with_retry(
            mock_session,
            "https://testflight.apple.com/join/abc123",
            max_retries=2,
            base_delay=0.01,
            use_cache=False,
            use_rate_limit=False,
        )

        assert result["status"] == TestFlightStatus.ERROR
        assert "Failed after 2 retries" in result["error"]


class TestConfiguration:
    """Tests for configuration functions."""

    def test_configure_rate_limiter(self):
        """Test configuring rate limiter."""
        configure_rate_limiter(max_requests=50, time_window=30)
        # Should not raise any errors

    def test_enable_cache_with_custom_ttl(self):
        """Test enabling cache with custom TTL."""
        enable_status_cache(ttl_seconds=600)
        # Should not raise any errors
        disable_status_cache()  # Clean up


# Integration test (requires network, optional)
@pytest.mark.integration
@pytest.mark.asyncio
class TestIntegration:
    """Integration tests that require network access."""

    async def test_real_testflight_check(self):
        """Test checking a real TestFlight URL (requires network)."""
        import aiohttp

        async with aiohttp.ClientSession() as session:
            # Note: This is a placeholder URL and will likely return ERROR or 404
            result = await check_testflight_status(
                session,
                "https://testflight.apple.com/join/abc123",
                use_cache=False,
                use_rate_limit=False,
            )

            # Should return some result (even if ERROR)
            assert "status" in result
            assert "url" in result


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v"])
