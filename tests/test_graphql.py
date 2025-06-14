"""Unit tests for GraphQL module."""

import json
import subprocess
import threading
from unittest.mock import patch, MagicMock
import pytest

# Mock dependencies - this needs to be at module level to properly mock imports
with patch.dict('sys.modules', {
    'tenacity': MagicMock(
        retry=lambda **kwargs: lambda func: func,
        stop_after_attempt=MagicMock(),
        wait_exponential=MagicMock(),
        retry_if_exception_type=MagicMock()
    )
}):
    from hacktivity.core.graphql import GraphQLClient, GraphQLError


class TestGraphQLClient:
    """Test GraphQL client functionality."""

    def setup_method(self):
        """Reset the availability cache before each test."""
        GraphQLClient._is_available = None

    @patch('hacktivity.core.config.get_config')
    @patch('subprocess.run')
    @patch('hacktivity.core.graphql.protected_call')
    @patch('hacktivity.core.graphql.get_rate_limit_coordinator')
    def test_run_query_success(self, mock_rate_limiter, mock_protected_call, mock_run, mock_config):
        """Test successful GraphQL query execution."""
        # Setup config
        mock_config.return_value = MagicMock(
            github=MagicMock(
                retry_attempts=3, retry_min_wait=1, retry_max_wait=10,
                graphql_timeout_seconds=120
            )
        )
        
        # Setup rate limiter
        mock_coordinator = MagicMock()
        mock_rate_limiter.return_value = mock_coordinator
        
        # Setup subprocess response
        mock_result = MagicMock(
            stdout='{"data": {"viewer": {"login": "testuser"}}}'
        )
        mock_protected_call.return_value = mock_result
        
        client = GraphQLClient()
        query = "query { viewer { login } }"
        variables = {}
        
        result = client.run_query(query, variables)
        
        assert result == {"viewer": {"login": "testuser"}}
        mock_coordinator.acquire.assert_called_once()
        mock_protected_call.assert_called_once()

    @patch('hacktivity.core.config.get_config')
    @patch('subprocess.run')
    @patch('hacktivity.core.graphql.protected_call')
    @patch('hacktivity.core.graphql.get_rate_limit_coordinator')
    def test_run_query_graphql_error(self, mock_rate_limiter, mock_protected_call, mock_run, mock_config):
        """Test GraphQL query with errors in response."""
        # Setup config
        mock_config.return_value = MagicMock(
            github=MagicMock(
                retry_attempts=3, retry_min_wait=1, retry_max_wait=10,
                graphql_timeout_seconds=120
            )
        )
        
        # Setup rate limiter
        mock_coordinator = MagicMock()
        mock_rate_limiter.return_value = mock_coordinator
        
        # Setup subprocess response with GraphQL errors
        mock_result = MagicMock(
            stdout='{"errors": [{"message": "Field \'invalid\' does not exist"}], "data": null}'
        )
        mock_protected_call.return_value = mock_result
        
        client = GraphQLClient()
        query = "query { invalid }"
        variables = {}
        
        with pytest.raises(GraphQLError) as exc_info:
            client.run_query(query, variables)
        
        assert "Field 'invalid' does not exist" in str(exc_info.value)

    @patch('hacktivity.core.config.get_config')
    def test_build_cli_command(self, mock_config):
        """Test CLI command building with variables."""
        mock_config.return_value = MagicMock(github=MagicMock())
        
        client = GraphQLClient()
        query = "query($login: String!) { user(login: $login) { id } }"
        variables = {"login": "testuser", "first": 10}
        
        command = client._build_cli_command(query, variables)
        
        expected_start = ["gh", "api", "graphql", "-f"]
        assert command[:4] == expected_start
        assert f"query={query}" in command[4]
        assert "-F" in command
        assert "login=\"testuser\"" in " ".join(command)
        assert "first=10" in " ".join(command)

    @patch('hacktivity.core.config.get_config')
    def test_build_cli_command_skip_none_values(self, mock_config):
        """Test CLI command building skips None values."""
        mock_config.return_value = MagicMock(github=MagicMock())
        
        client = GraphQLClient()
        query = "query { viewer { login } }"
        variables = {"login": "testuser", "after": None}
        
        command = client._build_cli_command(query, variables)
        
        # Should not include the None value
        assert "after" not in " ".join(command)
        assert "login=\"testuser\"" in " ".join(command)

    @patch('hacktivity.core.config.get_config')
    @patch('hacktivity.core.graphql.GraphQLClient.run_query')
    def test_is_available_probe_success(self, mock_run_query, mock_config):
        """Test GraphQL availability probe succeeds."""
        mock_config.return_value = MagicMock(
            github=MagicMock(graphql_enabled=True)
        )
        mock_run_query.return_value = {"viewer": {"login": "testuser"}}
        
        # First call should probe
        assert GraphQLClient.is_available() is True
        mock_run_query.assert_called_once_with("query { viewer { login } }", {})
        
        # Second call should use cached result
        mock_run_query.reset_mock()
        assert GraphQLClient.is_available() is True
        mock_run_query.assert_not_called()

    @patch('hacktivity.core.config.get_config')
    @patch('hacktivity.core.graphql.GraphQLClient.run_query')
    def test_is_available_probe_failure(self, mock_run_query, mock_config):
        """Test GraphQL availability probe fails."""
        mock_config.return_value = MagicMock(
            github=MagicMock(graphql_enabled=True)
        )
        mock_run_query.side_effect = subprocess.CalledProcessError(1, ['gh'])
        
        assert GraphQLClient.is_available() is False
        mock_run_query.assert_called_once()

    @patch('hacktivity.core.config.get_config')
    def test_is_available_disabled_in_config(self, mock_config):
        """Test GraphQL availability when disabled in config."""
        mock_config.return_value = MagicMock(
            github=MagicMock(graphql_enabled=False)
        )
        
        assert GraphQLClient.is_available() is False

    def test_graphql_error_class(self):
        """Test GraphQLError exception class."""
        errors = [{"message": "Test error", "path": ["user"]}]
        error = GraphQLError(errors)
        
        assert str(error) == f"GraphQL query failed with errors: {errors}"
        assert error.errors == errors

    @patch('hacktivity.core.config.get_config')
    def test_availability_thread_safety(self, mock_config):
        """Test that availability checking is thread-safe."""
        mock_config.return_value = MagicMock(
            github=MagicMock(graphql_enabled=False)
        )
        
        results = []
        
        def check_availability():
            results.append(GraphQLClient.is_available())
        
        # Create multiple threads
        threads = [threading.Thread(target=check_availability) for _ in range(5)]
        
        # Start all threads
        for thread in threads:
            thread.start()
        
        # Wait for all threads
        for thread in threads:
            thread.join()
        
        # All should return the same result
        assert len(set(results)) == 1
        assert all(result is False for result in results)