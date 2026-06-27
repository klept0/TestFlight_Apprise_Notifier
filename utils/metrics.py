"""Metrics collection for TestFlight status checks."""

import threading
import time

from utils.testflight import TestFlightStatus


class MetricsCollector:
    """
    Collect and track metrics for TestFlight status checks.

    Tracks total checks, successes, failures, and status counts.
    """

    def __init__(self):
        """Initialize metrics collector."""
        self.total_checks = 0
        self.successful_checks = 0
        self.failed_checks = 0
        self.status_counts = {
            "open": 0,
            "full": 0,
            "closed": 0,
            "unknown": 0,
            "error": 0,
        }
        self.start_time = time.time()
        self._lock = threading.Lock()

    def record_check(self, status: TestFlightStatus, success: bool = True):
        """
        Record a status check.

        Args:
            status: The TestFlightStatus result
            success: Whether the check was successful
        """
        with self._lock:
            self.total_checks += 1
            if success:
                self.successful_checks += 1
                status_key = status.value.lower()
                if status_key in self.status_counts:
                    self.status_counts[status_key] += 1
            else:
                self.failed_checks += 1
                self.status_counts["error"] += 1

    def get_stats(self):
        """
        Get current statistics.

        Returns:
            dict: Statistics dictionary with all metrics
        """
        with self._lock:
            uptime = time.time() - self.start_time
            return {
                "total_checks": self.total_checks,
                "successful_checks": self.successful_checks,
                "failed_checks": self.failed_checks,
                "status_counts": self.status_counts.copy(),
                "uptime_seconds": uptime,
                "checks_per_minute": (
                    (self.total_checks / uptime * 60) if uptime > 0 else 0
                ),
            }

    def reset(self):
        """Reset all metrics."""
        with self._lock:
            self.total_checks = 0
            self.successful_checks = 0
            self.failed_checks = 0
            self.status_counts = {
                "open": 0,
                "full": 0,
                "closed": 0,
                "unknown": 0,
                "error": 0,
            }
            self.start_time = time.time()
