"""Unit tests for circuit breaker functionality."""

import tempfile
import time
import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from hacktivity.core.circuit_breaker import (
    CircuitBreaker, CircuitState, CircuitOpenError, _PersistentStore,
    get_circuit, protected_call
)
from hacktivity.core.config import GitHubConfig


class TestPersistentStore:
    """Test the SQLite-based persistent store."""
    
    def test_store_creation(self):
        """Test creating a new persistent store."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "test.db"
            store = _PersistentStore(db_path)
            
            # Verify database file was created
            assert db_path.exists()
            
            # Verify table was created
            conn = sqlite3.connect(str(db_path))
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]
            assert "circuits" in tables
            conn.close()
    
    def test_load_default_values(self):
        """Test loading default values for non-existent endpoint."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "test.db"
            store = _PersistentStore(db_path)
            
            state, failures, opened_at = store.load("non-existent")
            
            assert state == CircuitState.CLOSED
            assert failures == 0
            assert opened_at == 0.0
    
    def test_save_and_load(self):
        """Test saving and loading circuit state."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "test.db"
            store = _PersistentStore(db_path)
            
            # Save state
            endpoint = "test/endpoint"
            test_time = time.time()
            store.save(endpoint, CircuitState.OPEN, 5, test_time)
            
            # Load state
            state, failures, opened_at = store.load(endpoint)
            
            assert state == CircuitState.OPEN
            assert failures == 5
            assert opened_at == test_time
    
    def test_save_overwrites_existing(self):
        """Test that saving overwrites existing data."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "test.db"
            store = _PersistentStore(db_path)
            
            endpoint = "test/endpoint"
            
            # Save initial state
            store.save(endpoint, CircuitState.CLOSED, 0, 0.0)
            
            # Overwrite with new state
            test_time = time.time()
            store.save(endpoint, CircuitState.OPEN, 3, test_time)
            
            # Verify new state
            state, failures, opened_at = store.load(endpoint)
            assert state == CircuitState.OPEN
            assert failures == 3
            assert opened_at == test_time


class TestCircuitBreaker:
    """Test the CircuitBreaker class."""
    
    def setup_method(self):
        """Set up test environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / "test.db"
        self.store = _PersistentStore(self.db_path)
        
        # Mock configuration
        self.config = GitHubConfig(
            cb_failure_threshold=3,
            cb_cooldown_sec=60
        )
    
    def test_circuit_breaker_creation(self):
        """Test creating a CircuitBreaker."""
        breaker = CircuitBreaker("test/endpoint", self.config, self.store)
        
        assert breaker.endpoint == "test/endpoint"
        assert breaker._state == CircuitState.CLOSED
        assert breaker._failures == 0
        assert breaker._opened_at == 0.0
    
    def test_successful_call(self):
        """Test successful function call through circuit breaker."""
        breaker = CircuitBreaker("test/endpoint", self.config, self.store)
        
        # Mock function that succeeds
        mock_func = MagicMock(return_value="success")
        
        result = breaker.call(mock_func)
        
        assert result == "success"
        assert breaker._state == CircuitState.CLOSED
        assert breaker._failures == 0
        mock_func.assert_called_once()
    
    def test_failed_call(self):
        """Test failed function call through circuit breaker."""
        breaker = CircuitBreaker("test/endpoint", self.config, self.store)
        
        # Mock function that fails
        mock_func = MagicMock(side_effect=RuntimeError("API error"))
        
        with pytest.raises(RuntimeError, match="API error"):
            breaker.call(mock_func)
        
        assert breaker._state == CircuitState.CLOSED  # Still closed after 1 failure
        assert breaker._failures == 1
        mock_func.assert_called_once()
    
    def test_circuit_opens_after_threshold_failures(self):
        """Test circuit opens after threshold failures."""
        breaker = CircuitBreaker("test/endpoint", self.config, self.store)
        
        # Mock function that always fails
        mock_func = MagicMock(side_effect=RuntimeError("API error"))
        
        # Fail up to threshold (3 times)
        for i in range(self.config.cb_failure_threshold):
            with pytest.raises(RuntimeError):
                breaker.call(mock_func)
            
            if i < self.config.cb_failure_threshold - 1:
                assert breaker._state == CircuitState.CLOSED
            else:
                assert breaker._state == CircuitState.OPEN
        
        assert breaker._failures == self.config.cb_failure_threshold
        assert breaker._opened_at > 0
    
    def test_circuit_open_rejects_calls(self):
        """Test that open circuit rejects calls."""
        breaker = CircuitBreaker("test/endpoint", self.config, self.store)
        
        # Force circuit open
        breaker._state = CircuitState.OPEN
        breaker._opened_at = time.time()
        breaker._failures = self.config.cb_failure_threshold
        
        mock_func = MagicMock()
        
        with pytest.raises(CircuitOpenError) as exc_info:
            breaker.call(mock_func)
        
        assert exc_info.value.endpoint == "test/endpoint"
        mock_func.assert_not_called()
    
    def test_circuit_transitions_to_half_open(self):
        """Test circuit transitions from OPEN to HALF_OPEN after cooldown."""
        breaker = CircuitBreaker("test/endpoint", self.config, self.store)
        
        # Force circuit open in the past
        past_time = time.time() - self.config.cb_cooldown_sec - 1
        breaker._state = CircuitState.OPEN
        breaker._opened_at = past_time
        breaker._failures = self.config.cb_failure_threshold
        
        # Mock successful function
        mock_func = MagicMock(return_value="success")
        
        result = breaker.call(mock_func)
        
        assert result == "success"
        assert breaker._state == CircuitState.CLOSED  # Success transitions to CLOSED
        assert breaker._failures == 0
        mock_func.assert_called_once()
    
    def test_half_open_success_closes_circuit(self):
        """Test that success in HALF_OPEN state closes the circuit."""
        breaker = CircuitBreaker("test/endpoint", self.config, self.store)
        
        # Force circuit to HALF_OPEN state
        breaker._state = CircuitState.HALF_OPEN
        breaker._failures = 0
        
        mock_func = MagicMock(return_value="success")
        
        result = breaker.call(mock_func)
        
        assert result == "success"
        assert breaker._state == CircuitState.CLOSED
        assert breaker._failures == 0
        assert breaker._opened_at == 0.0
    
    def test_half_open_failure_opens_circuit(self):
        """Test that failure in HALF_OPEN state opens the circuit."""
        breaker = CircuitBreaker("test/endpoint", self.config, self.store)
        
        # Force circuit to HALF_OPEN state
        breaker._state = CircuitState.HALF_OPEN
        breaker._failures = 0
        
        mock_func = MagicMock(side_effect=RuntimeError("Still failing"))
        
        with pytest.raises(RuntimeError):
            breaker.call(mock_func)
        
        assert breaker._state == CircuitState.OPEN
        assert breaker._failures == 1
        assert breaker._opened_at > 0
    
    def test_state_persistence(self):
        """Test that circuit breaker state is persisted."""
        endpoint = "test/endpoint"
        
        # Create first breaker and trigger failure
        breaker1 = CircuitBreaker(endpoint, self.config, self.store)
        mock_func = MagicMock(side_effect=RuntimeError("Error"))
        
        with pytest.raises(RuntimeError):
            breaker1.call(mock_func)
        
        assert breaker1._failures == 1
        
        # Create new breaker instance for same endpoint
        breaker2 = CircuitBreaker(endpoint, self.config, self.store)
        
        # Should load previous state
        assert breaker2._failures == 1
        assert breaker2._state == CircuitState.CLOSED


class TestCircuitBreakerFactory:
    """Test the global circuit breaker factory functions."""
    
    def test_get_circuit_creates_singleton(self):
        """Test that get_circuit returns singleton instances."""
        with patch("hacktivity.core.circuit_breaker.get_config") as mock_config:
            mock_config.return_value.cache.directory = None
            mock_config.return_value.github = GitHubConfig()
            
            endpoint = "test/endpoint"
            
            breaker1 = get_circuit(endpoint)
            breaker2 = get_circuit(endpoint)
            
            assert breaker1 is breaker2
            assert breaker1.endpoint == endpoint
    
    def test_protected_call_wrapper(self):
        """Test the protected_call wrapper function."""
        with patch("hacktivity.core.circuit_breaker.get_config") as mock_config:
            mock_config.return_value.cache.directory = None
            mock_config.return_value.github = GitHubConfig()
            
            endpoint = "test/endpoint"
            mock_func = MagicMock(return_value="protected_result")
            
            result = protected_call(endpoint, mock_func)
            
            assert result == "protected_result"
            mock_func.assert_called_once()
    
    def test_protected_call_circuit_open_error(self):
        """Test protected_call raises CircuitOpenError when circuit is open."""
        with patch("hacktivity.core.circuit_breaker.get_config") as mock_config:
            mock_config.return_value.cache.directory = None
            mock_config.return_value.github = GitHubConfig()
            
            endpoint = "test/endpoint"
            
            # Get circuit and force it open
            breaker = get_circuit(endpoint)
            breaker._state = CircuitState.OPEN
            breaker._opened_at = time.time()
            
            mock_func = MagicMock()
            
            with pytest.raises(CircuitOpenError):
                protected_call(endpoint, mock_func)
            
            mock_func.assert_not_called()


class TestCircuitBreakerIntegration:
    """Test circuit breaker integration scenarios."""
    
    def test_circuit_breaker_with_retries(self):
        """Test circuit breaker works correctly with retry logic."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "test.db"
            store = _PersistentStore(db_path)
            config = GitHubConfig(cb_failure_threshold=2, cb_cooldown_sec=30)
            
            breaker = CircuitBreaker("test/endpoint", config, store)
            
            # Mock function that fails once then succeeds
            call_count = 0
            def mock_func_with_retry():
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise RuntimeError("Temporary failure")
                return "success"
            
            # First call should fail
            with pytest.raises(RuntimeError):
                breaker.call(mock_func_with_retry)
            
            assert breaker._failures == 1
            assert breaker._state == CircuitState.CLOSED
            
            # Second call should succeed 
            call_count = 0  # Reset for clean test
            result = breaker.call(lambda: "success")
            
            assert result == "success"
            assert breaker._failures == 0
            assert breaker._state == CircuitState.CLOSED
    
    def test_configuration_integration(self):
        """Test that circuit breaker uses configuration values."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "test.db"
            store = _PersistentStore(db_path)
            
            # Custom config with different threshold
            config = GitHubConfig(cb_failure_threshold=1, cb_cooldown_sec=30)
            breaker = CircuitBreaker("test/endpoint", config, store)
            
            mock_func = MagicMock(side_effect=RuntimeError("Error"))
            
            # Should open after just 1 failure with threshold=1
            with pytest.raises(RuntimeError):
                breaker.call(mock_func)
            
            assert breaker._state == CircuitState.OPEN
            assert breaker._failures == 1