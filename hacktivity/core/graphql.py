"""
Core module for executing GitHub GraphQL queries.

This module provides a client for running GraphQL queries that is fully integrated
with the application's rate limiting, circuit breaking, and retry mechanisms.
It also includes a one-time availability check to avoid repeated failures.
"""

from __future__ import annotations

import json
import subprocess
import threading
from textwrap import dedent
from typing import Any, Dict

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .circuit_breaker import protected_call, CircuitOpenError
from .logging import get_logger
from .rate_limiter import get_rate_limit_coordinator

logger = get_logger(__name__)

# Logical circuit-breaker key for all GraphQL calls
_GRAPHQL_ENDPOINT = "graphql"


class GraphQLError(RuntimeError):
    """Raised when GitHub returns an `errors` array in the GraphQL response."""
    def __init__(self, errors: Any):
        super().__init__(f"GraphQL query failed with errors: {errors}")
        self.errors = errors


class GraphQLClient:
    """A client for executing GitHub GraphQL queries with integrated fault tolerance."""
    _availability_lock = threading.Lock()
    _is_available: bool | None = None

    def __init__(self):
        # Lazy import config to avoid circular imports
        from .config import get_config
        self.config = get_config().github

    @classmethod
    def is_available(cls) -> bool:
        """
        Checks if the GraphQL API is available by running a simple probe query.
        The result is cached for the lifetime of the application process to avoid
        repeated checks.
        """
        with cls._availability_lock:
            if cls._is_available is not None:
                return cls._is_available

            # Lazy import config to avoid circular imports
            from .config import get_config
            config = get_config().github
            
            if not config.graphql_enabled:
                logger.info("GraphQL is disabled in the configuration.")
                cls._is_available = False
                return False

            try:
                probe_query = "query { viewer { login } }"
                GraphQLClient().run_query(probe_query, {})
                logger.info("GraphQL availability probe successful.")
                cls._is_available = True
            except Exception as e:
                logger.warning(
                    "GraphQL availability probe failed: %s. "
                    "GraphQL will be disabled for this session.", e
                )
                cls._is_available = False

            return cls._is_available

    def _build_cli_command(self, query: str, variables: Dict[str, Any]) -> list[str]:
        """Builds the `gh api graphql` command with variables passed safely."""
        # Using -F for variables ensures proper JSON serialization by gh CLI
        cmd = ["gh", "api", "graphql", "-f", f"query={dedent(query).strip()}"]
        for key, value in variables.items():
            if value is not None:  # Skip None values
                cmd.extend(["-F", f"{key}={json.dumps(value)}"])
        return cmd

    def run_query(self, query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
        """
        Executes a GraphQL query with retries, circuit breaking, and rate limiting.

        Returns:
            The 'data' field from the GraphQL JSON response.

        Raises:
            GraphQLError: If the API response contains logical errors.
            CircuitOpenError: If the circuit breaker is open.
            subprocess.TimeoutExpired: If the command times out.
            subprocess.CalledProcessError: For other command execution errors.
        """
        @retry(
            stop=stop_after_attempt(self.config.retry_attempts),
            wait=wait_exponential(
                multiplier=1, min=self.config.retry_min_wait, max=self.config.retry_max_wait
            ),
            retry=retry_if_exception_type(subprocess.TimeoutExpired),
            reraise=True,
        )
        def _runner() -> Dict[str, Any]:
            command = self._build_cli_command(query, variables)
            logger.debug("Executing GraphQL command: %s", " ".join(command))

            def _subprocess_call():
                get_rate_limit_coordinator().acquire()
                return subprocess.run(
                    command,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=self.config.graphql_timeout_seconds,
                )

            result = protected_call(_GRAPHQL_ENDPOINT, _subprocess_call)
            payload: Dict[str, Any] = json.loads(result.stdout)

            if "errors" in payload and payload["errors"]:
                raise GraphQLError(payload["errors"])

            return payload.get("data", {})

        return _runner()