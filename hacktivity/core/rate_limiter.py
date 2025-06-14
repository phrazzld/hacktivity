"""
Thread-safe, process-wide GitHub rate-limit coordinator.

A token-bucket implementation backed by a background thread. All API-issuing
code can `acquire()` a token before calling GitHub, ensuring the application
never exceeds the global 5,000 req/hour limit even with many worker threads.
"""
import threading
import time
from typing import Optional

from .config import get_config
from .logging import get_logger

logger = get_logger(__name__)


class RateLimitCoordinator:
    """Manages global GitHub API request rate using a token bucket algorithm."""
    _instance: Optional["RateLimitCoordinator"] = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        # Ensure __init__ is called only once for the singleton
        if hasattr(self, '_initialized'):
            return
        self._initialized = True

        cfg = get_config().github
        self._capacity: int = 5000 - cfg.rate_limit_buffer
        self._tokens: float = float(self._capacity)
        self._lock = threading.Lock()

        # Start a daemon thread to continuously refill the token bucket
        refill_thread = threading.Thread(
            target=self._refill_daemon, daemon=True, name="RateLimitRefill"
        )
        refill_thread.start()
        logger.info(
            "RateLimitCoordinator started with capacity=%d, buffer=%d",
            self._capacity, cfg.rate_limit_buffer
        )

    def acquire(self) -> None:
        """Acquire one token, blocking if necessary until a token is available."""
        while True:
            with self._lock:
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    logger.debug("Rate limit token acquired. Tokens remaining: %.2f", self._tokens)
                    return
            # If no token, wait a bit before trying again to avoid busy-waiting
            time.sleep(0.1)

    def _refill_daemon(self) -> None:
        """Background task to refill the token bucket."""
        # GitHub's limit is per hour, so we calculate the per-second refill rate.
        refill_rate_per_sec: float = self._capacity / 3600.0
        while True:
            time.sleep(1.0)
            with self._lock:
                self._tokens = min(self._capacity, self._tokens + refill_rate_per_sec)


# Singleton accessor function
def get_rate_limit_coordinator() -> RateLimitCoordinator:
    """Get the global singleton instance of the RateLimitCoordinator."""
    return RateLimitCoordinator()