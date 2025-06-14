"""Unit tests for rate limiting coordinator."""

import time
import threading
from unittest.mock import patch, MagicMock
import pytest

from hacktivity.core.rate_limiter import RateLimitCoordinator, get_rate_limit_coordinator
from hacktivity.core.config import GitHubConfig


class TestRateLimitCoordinator:
    """Test the RateLimitCoordinator class."""
    
    def test_singleton_pattern(self):
        """Test that RateLimitCoordinator follows singleton pattern."""
        # Reset singleton for clean test
        RateLimitCoordinator._instance = None
        
        with patch("hacktivity.core.rate_limiter.get_config") as mock_config:
            mock_config.return_value.github = GitHubConfig()
            
            coordinator1 = RateLimitCoordinator()
            coordinator2 = RateLimitCoordinator()
            
            assert coordinator1 is coordinator2
    
    def test_initialization_with_config(self):
        """Test initialization uses configuration values."""
        # Reset singleton for clean test
        RateLimitCoordinator._instance = None
        
        with patch("hacktivity.core.rate_limiter.get_config") as mock_config:
            mock_config.return_value.github = GitHubConfig(rate_limit_buffer=200)
            
            coordinator = RateLimitCoordinator()
            
            # Capacity should be 5000 - buffer
            expected_capacity = 5000 - 200
            assert coordinator._capacity == expected_capacity
            assert coordinator._tokens == float(expected_capacity)
    
    def test_acquire_token_success(self):
        """Test successful token acquisition."""
        # Reset singleton for clean test
        RateLimitCoordinator._instance = None
        
        with patch("hacktivity.core.rate_limiter.get_config") as mock_config:
            mock_config.return_value.github = GitHubConfig()
            
            coordinator = RateLimitCoordinator()
            initial_tokens = coordinator._tokens
            
            # Should successfully acquire token
            coordinator.acquire()
            
            assert coordinator._tokens == initial_tokens - 1.0
    
    def test_acquire_blocks_when_no_tokens(self):
        """Test that acquire blocks when no tokens available."""
        # Reset singleton for clean test
        RateLimitCoordinator._instance = None
        
        with patch("hacktivity.core.rate_limiter.get_config") as mock_config:
            mock_config.return_value.github = GitHubConfig()
            
            coordinator = RateLimitCoordinator()
            
            # Exhaust all tokens
            coordinator._tokens = 0.0
            
            acquired = False
            def acquire_with_timeout():
                nonlocal acquired
                # This should block for a bit
                start_time = time.time()
                coordinator.acquire()
                end_time = time.time()
                acquired = True
                # Should have taken some time due to blocking
                return end_time - start_time
            
            # Manually add a token after a short delay
            def add_token():
                time.sleep(0.2)
                with coordinator._lock:
                    coordinator._tokens = 1.0
            
            # Start both threads
            acquire_thread = threading.Thread(target=acquire_with_timeout)
            add_token_thread = threading.Thread(target=add_token)
            
            acquire_thread.start()
            add_token_thread.start()
            
            acquire_thread.join(timeout=1.0)
            add_token_thread.join()
            
            assert acquired
            assert coordinator._tokens == 0.0  # Token was consumed
    
    def test_refill_daemon_behavior(self):
        """Test that the refill daemon properly refills tokens."""
        # Reset singleton for clean test
        RateLimitCoordinator._instance = None
        
        with patch("hacktivity.core.rate_limiter.get_config") as mock_config:
            mock_config.return_value.github = GitHubConfig(rate_limit_buffer=100)
            
            coordinator = RateLimitCoordinator()
            capacity = coordinator._capacity
            
            # Exhaust tokens
            coordinator._tokens = 0.0
            
            # Wait for refill (daemon refills every second)
            time.sleep(1.1)
            
            # Should have been refilled by capacity/3600 tokens
            expected_refill = capacity / 3600.0
            assert coordinator._tokens >= expected_refill * 0.9  # Allow some tolerance
            assert coordinator._tokens <= capacity  # Should not exceed capacity
    
    def test_token_capacity_limit(self):
        """Test that tokens don't exceed capacity."""
        # Reset singleton for clean test
        RateLimitCoordinator._instance = None
        
        with patch("hacktivity.core.rate_limiter.get_config") as mock_config:
            mock_config.return_value.github = GitHubConfig()
            
            coordinator = RateLimitCoordinator()
            capacity = coordinator._capacity
            
            # Manually overfill tokens
            with coordinator._lock:
                coordinator._tokens = capacity + 1000
            
            # Wait for refill cycle
            time.sleep(1.1)
            
            # Should be capped at capacity
            assert coordinator._tokens <= capacity
    
    def test_concurrent_token_acquisition(self):
        """Test thread safety of concurrent token acquisition."""
        # Reset singleton for clean test
        RateLimitCoordinator._instance = None
        
        with patch("hacktivity.core.rate_limiter.get_config") as mock_config:
            mock_config.return_value.github = GitHubConfig()
            
            coordinator = RateLimitCoordinator()
            
            # Set specific number of tokens
            with coordinator._lock:
                coordinator._tokens = 10.0
            
            acquired_count = 0
            acquisition_lock = threading.Lock()
            
            def acquire_token():
                nonlocal acquired_count
                try:
                    coordinator.acquire()
                    with acquisition_lock:
                        acquired_count += 1
                except:
                    pass
            
            # Start multiple threads trying to acquire tokens
            threads = []
            for _ in range(15):  # More threads than available tokens
                thread = threading.Thread(target=acquire_token)
                threads.append(thread)
                thread.start()
            
            # Wait for all threads
            for thread in threads:
                thread.join(timeout=2.0)
            
            # Should have acquired exactly 10 tokens (the amount we set)
            assert acquired_count == 10
            assert coordinator._tokens == 0.0


class TestRateLimitCoordinatorFactory:
    """Test the rate limiter factory function."""
    
    def test_get_rate_limit_coordinator_singleton(self):
        """Test that factory returns singleton instance."""
        # Reset singleton for clean test
        RateLimitCoordinator._instance = None
        
        with patch("hacktivity.core.rate_limiter.get_config") as mock_config:
            mock_config.return_value.github = GitHubConfig()
            
            coordinator1 = get_rate_limit_coordinator()
            coordinator2 = get_rate_limit_coordinator()
            
            assert coordinator1 is coordinator2
            assert isinstance(coordinator1, RateLimitCoordinator)


class TestRateLimitIntegration:
    """Test integration of rate limiter with API calls."""
    
    def test_rate_limiter_integration_concept(self):
        """Test the concept of integrating rate limiter with API calls."""
        # This test verifies the integration pattern without actually making API calls
        
        # Reset singleton for clean test
        RateLimitCoordinator._instance = None
        
        with patch("hacktivity.core.rate_limiter.get_config") as mock_config:
            mock_config.return_value.github = GitHubConfig()
            
            coordinator = get_rate_limit_coordinator()
            initial_tokens = coordinator._tokens
            
            # Simulate API call pattern: acquire token, then make call
            def simulated_api_call():
                coordinator.acquire()
                # Simulate actual API call here
                return "api_result"
            
            result = simulated_api_call()
            
            assert result == "api_result"
            assert coordinator._tokens == initial_tokens - 1.0
    
    def test_multiple_api_calls_rate_limiting(self):
        """Test that multiple API calls properly consume tokens."""
        # Reset singleton for clean test
        RateLimitCoordinator._instance = None
        
        with patch("hacktivity.core.rate_limiter.get_config") as mock_config:
            mock_config.return_value.github = GitHubConfig()
            
            coordinator = get_rate_limit_coordinator()
            
            # Set specific token count
            with coordinator._lock:
                coordinator._tokens = 5.0
            
            # Make multiple API calls
            for i in range(3):
                coordinator.acquire()
            
            # Should have consumed 3 tokens
            assert coordinator._tokens == 2.0
    
    def test_rate_limiter_with_circuit_breaker_integration(self):
        """Test conceptual integration with circuit breaker pattern."""
        # This tests that rate limiter can work with existing circuit breaker
        
        # Reset singleton for clean test
        RateLimitCoordinator._instance = None
        
        with patch("hacktivity.core.rate_limiter.get_config") as mock_config:
            mock_config.return_value.github = GitHubConfig()
            
            coordinator = get_rate_limit_coordinator()
            
            def api_call_with_rate_limit_and_circuit_breaker():
                # Step 1: Acquire rate limit token
                coordinator.acquire()
                
                # Step 2: Make protected call (circuit breaker would go here)
                # For test, just simulate successful call
                return "protected_api_result"
            
            result = api_call_with_rate_limit_and_circuit_breaker()
            assert result == "protected_api_result"