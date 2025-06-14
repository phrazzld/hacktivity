"""Integration tests for circuit breaker with API modules."""

import tempfile
import time
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock, Mock
import pytest

from hacktivity.core.circuit_breaker import CircuitBreaker, CircuitState, CircuitOpenError, get_circuit
from hacktivity.core.config import GitHubConfig
from hacktivity.core import cache


class TestCircuitBreakerWithRetry:
    """Test circuit breaker integration with tenacity retry logic."""
    
    def test_circuit_breaker_with_tenacity_retries(self):
        """Test that circuit breaker works correctly with tenacity retry mechanism."""
        # Mock configuration for faster testing
        with patch("hacktivity.core.circuit_breaker.get_config") as mock_config:
            mock_config.return_value.cache.directory = None
            mock_config.return_value.github = GitHubConfig(
                cb_failure_threshold=2,
                cb_cooldown_sec=30,
                retry_attempts=3
            )
            
            # Reset global circuit breaker state
            import hacktivity.core.circuit_breaker as cb_module
            cb_module._STORE = None
            cb_module._BREAKERS = {}
            
            # Mock subprocess.run to fail a specific number of times
            call_count = 0
            def mock_subprocess_run(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count <= 1:
                    # First call fails (timeout)
                    raise subprocess.TimeoutExpired("gh", 30)
                else:
                    # Subsequent calls succeed
                    mock_result = Mock()
                    mock_result.stdout = '[]'
                    return mock_result
            
            with patch('subprocess.run', side_effect=mock_subprocess_run):
                with patch('hacktivity.core.commits._get_config', return_value=mock_config.return_value):
                    with patch('hacktivity.core.cache.get', return_value=None):
                        with patch('hacktivity.core.cache.set'):
                            from hacktivity.core.commits import _fetch_commits_with_api
                            
                            # Should succeed after retries, circuit should remain closed
                            result = _fetch_commits_with_api("repos/test/repo/commits", {})
                            
                            assert result == []
                            assert call_count == 2  # Failed once, succeeded on retry
                            
                            # Circuit should still be closed
                            breaker = get_circuit("repos/test/repo/commits")
                            assert breaker._state == CircuitState.CLOSED
                            assert breaker._failures == 0  # Success resets failures
    
    def test_circuit_breaker_opens_after_retry_exhaustion(self):
        """Test that circuit opens when retries are exhausted."""
        with patch("hacktivity.core.circuit_breaker.get_config") as mock_config:
            mock_config.return_value.cache.directory = None
            mock_config.return_value.github = GitHubConfig(
                cb_failure_threshold=2,
                cb_cooldown_sec=30,
                retry_attempts=2
            )
            
            # Reset global circuit breaker state
            import hacktivity.core.circuit_breaker as cb_module
            cb_module._STORE = None
            cb_module._BREAKERS = {}
            
            # Mock subprocess.run to always fail
            def mock_subprocess_run(*args, **kwargs):
                raise subprocess.TimeoutExpired("gh", 30)
            
            with patch('subprocess.run', side_effect=mock_subprocess_run):
                with patch('hacktivity.core.commits._get_config', return_value=mock_config.return_value):
                    with patch('hacktivity.core.cache.get', return_value=None):
                        with patch('hacktivity.core.cache.set'):
                            from hacktivity.core.commits import _fetch_commits_with_api
                            
                            # First call - should fail after retries, circuit stays closed
                            with pytest.raises(subprocess.TimeoutExpired):
                                _fetch_commits_with_api("repos/test/repo/commits", {})
                            
                            breaker = get_circuit("repos/test/repo/commits")
                            assert breaker._failures == 1
                            assert breaker._state == CircuitState.CLOSED
                            
                            # Second call - should fail and open circuit
                            with pytest.raises(subprocess.TimeoutExpired):
                                _fetch_commits_with_api("repos/test/repo/commits", {})
                            
                            assert breaker._failures == 2
                            assert breaker._state == CircuitState.OPEN


class TestCircuitBreakerWithCacheFallback:
    """Test circuit breaker integration with cache fallback mechanisms."""
    
    def test_cache_fallback_on_circuit_open(self):
        """Test that cache fallback is triggered when circuit is open."""
        with patch("hacktivity.core.config.get_config") as mock_config:
            mock_config.return_value.cache.directory = None
            mock_config.return_value.github = GitHubConfig(
                cb_failure_threshold=1,
                cb_cooldown_sec=30
            )
            
            # Reset global circuit breaker state
            import hacktivity.core.circuit_breaker as cb_module
            cb_module._STORE = None
            cb_module._BREAKERS = {}
            
            # Mock cache to return stale data on extended TTL request
            stale_commits = [{'sha': 'abc123', 'message': 'old commit'}]
            
            with patch('hacktivity.core.cache.get') as mock_cache_get:
                # Normal cache miss, extended TTL cache hit
                mock_cache_get.side_effect = lambda key, max_age_hours=None: (
                    stale_commits if max_age_hours == 168 else None
                )
                
                with patch('subprocess.run', side_effect=subprocess.TimeoutExpired("gh", 30)):
                    with patch('hacktivity.core.commits._get_config', return_value=mock_config.return_value):
                        from hacktivity.core.commits import fetch_repo_commits
                        
                        # First call - should fail and open circuit
                        with pytest.raises(subprocess.TimeoutExpired):
                            fetch_repo_commits("test/repo", "2024-01-01", "2024-01-31")
                        
                        # Verify circuit is open
                        breaker = get_circuit("repos/test/repo/commits")
                        assert breaker._state == CircuitState.OPEN
                        
                        # Second call - should trigger circuit open and cache fallback
                        result = fetch_repo_commits("test/repo", "2024-01-01", "2024-01-31")
                        
                        assert result == stale_commits
                        
                        # Verify extended TTL cache was called
                        mock_cache_get.assert_any_call(
                            'commits:test/repo:2024-01-01:2024-01-31:all',
                            max_age_hours=168
                        )
    
    def test_no_cache_fallback_raises_error(self):
        """Test that CircuitOpenError is raised when no cache fallback is available."""
        with patch("hacktivity.core.config.get_config") as mock_config:
            mock_config.return_value.cache.directory = None
            mock_config.return_value.github = GitHubConfig(
                cb_failure_threshold=1,
                cb_cooldown_sec=30
            )
            
            # Reset global circuit breaker state
            import hacktivity.core.circuit_breaker as cb_module
            cb_module._STORE = None
            cb_module._BREAKERS = {}
            
            # Mock cache to always return None (no cached data)
            with patch('hacktivity.core.cache.get', return_value=None):
                with patch('subprocess.run', side_effect=subprocess.TimeoutExpired("gh", 30)):
                    with patch('hacktivity.core.commits._get_config', return_value=mock_config.return_value):
                        from hacktivity.core.commits import fetch_repo_commits
                        
                        # First call - should fail and open circuit
                        with pytest.raises(subprocess.TimeoutExpired):
                            fetch_repo_commits("test/repo", "2024-01-01", "2024-01-31")
                        
                        # Second call - should trigger circuit open, no cache available
                        with pytest.raises(CircuitOpenError):
                            fetch_repo_commits("test/repo", "2024-01-01", "2024-01-31")


class TestCircuitBreakerPerEndpoint:
    """Test that circuit breakers are isolated per endpoint."""
    
    def test_endpoint_isolation(self):
        """Test that different endpoints have independent circuit breakers."""
        with patch("hacktivity.core.circuit_breaker.get_config") as mock_config:
            mock_config.return_value.cache.directory = None
            mock_config.return_value.github = GitHubConfig(
                cb_failure_threshold=1,
                cb_cooldown_sec=30
            )
            
            # Reset global circuit breaker state
            import hacktivity.core.circuit_breaker as cb_module
            cb_module._STORE = None
            cb_module._BREAKERS = {}
            
            # Get breakers for different endpoints
            breaker1 = get_circuit("repos/owner1/repo1/commits")
            breaker2 = get_circuit("repos/owner2/repo2/commits")
            breaker3 = get_circuit("user/repos")
            
            # Verify they are different instances
            assert breaker1 is not breaker2
            assert breaker2 is not breaker3
            assert breaker1 is not breaker3
            
            # Fail one breaker
            def failing_call():
                raise RuntimeError("API Error")
            
            with pytest.raises(RuntimeError):
                breaker1.call(failing_call)
            
            # Verify only the first breaker opened
            assert breaker1._state == CircuitState.OPEN
            assert breaker2._state == CircuitState.CLOSED
            assert breaker3._state == CircuitState.CLOSED
            
            # Other breakers should still work
            result = breaker2.call(lambda: "success")
            assert result == "success"
            
            result = breaker3.call(lambda: "success")
            assert result == "success"
    
    def test_same_endpoint_shares_circuit(self):
        """Test that the same endpoint shares the same circuit breaker instance."""
        with patch("hacktivity.core.circuit_breaker.get_config") as mock_config:
            mock_config.return_value.cache.directory = None
            mock_config.return_value.github = GitHubConfig()
            
            # Reset global circuit breaker state
            import hacktivity.core.circuit_breaker as cb_module
            cb_module._STORE = None
            cb_module._BREAKERS = {}
            
            endpoint = "repos/test/repo/commits"
            
            # Get the same endpoint multiple times
            breaker1 = get_circuit(endpoint)
            breaker2 = get_circuit(endpoint)
            breaker3 = get_circuit(endpoint)
            
            # Should be the same instance
            assert breaker1 is breaker2
            assert breaker2 is breaker3
            assert breaker1.endpoint == endpoint


class TestCircuitBreakerRecovery:
    """Test circuit breaker recovery mechanisms."""
    
    def test_half_open_recovery_success(self):
        """Test successful recovery from HALF_OPEN state."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "test.db"
            
            from hacktivity.core.circuit_breaker import _PersistentStore
            store = _PersistentStore(db_path)
            
            config = GitHubConfig(
                cb_failure_threshold=1,
                cb_cooldown_sec=1  # Short cooldown for testing
            )
            
            breaker = CircuitBreaker("test/endpoint", config, store)
            
            # Force circuit open
            with pytest.raises(RuntimeError):
                breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("fail")))
            
            assert breaker._state == CircuitState.OPEN
            
            # Wait for cooldown
            time.sleep(1.1)
            
            # Successful call should transition to CLOSED
            result = breaker.call(lambda: "success")
            assert result == "success"
            assert breaker._state == CircuitState.CLOSED
            assert breaker._failures == 0
    
    def test_half_open_recovery_failure(self):
        """Test failed recovery from HALF_OPEN state."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "test.db"
            
            from hacktivity.core.circuit_breaker import _PersistentStore
            store = _PersistentStore(db_path)
            
            config = GitHubConfig(
                cb_failure_threshold=1,
                cb_cooldown_sec=1  # Short cooldown for testing
            )
            
            breaker = CircuitBreaker("test/endpoint", config, store)
            
            # Force circuit open
            with pytest.raises(RuntimeError):
                breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("fail")))
            
            assert breaker._state == CircuitState.OPEN
            
            # Wait for cooldown
            time.sleep(1.1)
            
            # Failed call should go back to OPEN
            with pytest.raises(RuntimeError):
                breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("still failing")))
            
            assert breaker._state == CircuitState.OPEN
            assert breaker._failures >= 1