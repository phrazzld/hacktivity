"""
Resilient, persistent, per-endpoint circuit-breaker for external service calls.
"""
from __future__ import annotations

import enum
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from .logging import get_logger

logger = get_logger(__name__)


class CircuitState(str, enum.Enum):
    """Enumeration for the state of the circuit breaker."""
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(RuntimeError):
    """Raised when an API call is short-circuited due to an open circuit."""
    def __init__(self, endpoint: str):
        self.endpoint = endpoint
        super().__init__(f"Circuit for endpoint '{endpoint}' is open. Call rejected.")


class _PersistentStore:
    """A thread-safe SQLite helper to persist circuit breaker states across restarts."""
    _DDL = """
        CREATE TABLE IF NOT EXISTS circuits(
            endpoint TEXT PRIMARY KEY,
            state TEXT NOT NULL,
            failures INTEGER NOT NULL,
            opened_at REAL NOT NULL
        )
    """

    def __init__(self, db_path: Path):
        self._lock = threading.Lock()
        # `check_same_thread=False` is safe here because we serialize access with our own lock.
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        with self._lock, self._db:
            self._db.execute(self._DDL)

    def load(self, endpoint: str) -> Tuple[CircuitState, int, float]:
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT state, failures, opened_at FROM circuits WHERE endpoint = ?", (endpoint,)
            ).fetchone()
        return (CircuitState(row[0]), row[1], row[2]) if row else (CircuitState.CLOSED, 0, 0.0)

    def save(self, endpoint: str, state: CircuitState, failures: int, opened_at: float) -> None:
        with self._lock, self._db:
            self._db.execute(
                "REPLACE INTO circuits(endpoint, state, failures, opened_at) VALUES(?,?,?,?)",
                (endpoint, state.value, failures, opened_at),
            )
            self._db.commit()

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            self._db.close()


class CircuitBreaker:
    """
    Stateful, per-endpoint circuit breaker implementing the CLOSED, OPEN, and HALF_OPEN states.
    State is persisted to survive process restarts and is safe for concurrent use.
    """
    def __init__(self, endpoint: str, config: Any, store: _PersistentStore):
        self.endpoint = endpoint
        self._cfg = config
        self._store = store
        self._lock = threading.Lock()
        self._state, self._failures, self._opened_at = self._store.load(self.endpoint)

    def call(self, func: Callable[[], Any]) -> Any:
        """Executes the provided callable, wrapping it with circuit breaker logic."""
        with self._lock:
            self._check_and_transition_state()
            if self._state == CircuitState.OPEN:
                raise CircuitOpenError(self.endpoint)
            # In HALF_OPEN, we optimistically allow the call to proceed.
            # Its success or failure will determine the next state transition.

        try:
            result = func()
            self._on_success()
            return result
        except Exception as e:
            # Any exception from the underlying call is considered a failure.
            # This works seamlessly with tenacity, which will raise its final
            # exception after all retries are exhausted.
            self._on_failure()
            raise e

    def _check_and_transition_state(self):
        """Transitions from OPEN to HALF_OPEN if the recovery timeout has passed."""
        if self._state == CircuitState.OPEN and (time.time() - self._opened_at >= self._cfg.cb_cooldown_sec):
            logger.info("Circuit %s entering HALF_OPEN after cooldown.", self.endpoint)
            self._state = CircuitState.HALF_OPEN
            self._failures = 0
            self._persist_state()

    def _on_failure(self):
        with self._lock:
            self._failures += 1
            if self._state == CircuitState.HALF_OPEN or self._failures >= self._cfg.cb_failure_threshold:
                if self._state != CircuitState.OPEN:
                    logger.warning(
                        "Circuit %s is OPENING due to %d failures.",
                        self.endpoint, self._failures
                    )
                    self._state = CircuitState.OPEN
                    self._opened_at = time.time()
            self._persist_state()

    def _on_success(self):
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                logger.info("Circuit %s is now CLOSED after successful probe.", self.endpoint)
            self._state = CircuitState.CLOSED
            self._failures = 0
            self._opened_at = 0.0
            self._persist_state()

    def _persist_state(self):
        self._store.save(self.endpoint, self._state, self._failures, self._opened_at)


# --- Global Factory for managing circuit breaker instances ---
_STORE: Optional[_PersistentStore] = None
_BREAKERS: Dict[str, CircuitBreaker] = {}
_FACTORY_LOCK = threading.Lock()


def get_circuit(endpoint: str) -> CircuitBreaker:
    """Factory function to get a singleton CircuitBreaker instance for a given endpoint."""
    global _STORE
    with _FACTORY_LOCK:
        if _STORE is None:
            from .config import get_config
            config = get_config()
            db_dir = Path(config.cache.directory or (Path.home() / ".hacktivity" / "cache"))
            db_dir.mkdir(parents=True, exist_ok=True)
            _STORE = _PersistentStore(db_dir / "circuit_breaker.sqlite3")

        if endpoint not in _BREAKERS:
            from .config import get_config
            _BREAKERS[endpoint] = CircuitBreaker(endpoint, get_config().github, _STORE)
        return _BREAKERS[endpoint]


def protected_call(endpoint: str, func: Callable[[], Any]) -> Any:
    """Public helper to wrap any function call with a circuit breaker for the given endpoint."""
    breaker = get_circuit(endpoint)
    return breaker.call(func)