"""Integration tests for parallel repository processing."""

import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock, Mock
import pytest

from hacktivity.core.config import GitHubConfig
from hacktivity.core.parallel import fetch_commits_parallel


class TestParallelConfigurationIntegration:
    """Test parallel processing configuration integration."""
    
    def test_config_extension_fields(self):
        """Test that GitHubConfig has parallel processing fields."""
        config = GitHubConfig()
        
        # Should have default values
        assert hasattr(config, 'max_workers')
        assert hasattr(config, 'rate_limit_buffer') 
        assert hasattr(config, 'parallel_enabled')
        
        # Test default values
        assert config.max_workers == 5
        assert config.rate_limit_buffer == 100
        assert config.parallel_enabled == True
    
    def test_config_validation(self):
        """Test configuration validation."""
        # Valid configuration
        config = GitHubConfig(
            max_workers=3,
            rate_limit_buffer=150,
            parallel_enabled=False
        )
        assert config.max_workers == 3
        assert config.rate_limit_buffer == 150
        assert config.parallel_enabled == False
        
        # Test limits
        with pytest.raises(ValueError):
            GitHubConfig(max_workers=0)  # Below minimum
            
        with pytest.raises(ValueError):
            GitHubConfig(max_workers=11)  # Above maximum
            
        with pytest.raises(ValueError):
            GitHubConfig(rate_limit_buffer=40)  # Below minimum
            
        with pytest.raises(ValueError):
            GitHubConfig(rate_limit_buffer=600)  # Above maximum


class TestRateLimiterIntegration:
    """Test rate limiter integration with parallel processing."""
    
    def test_rate_limiter_with_parallel_processing(self):
        """Test that rate limiter works with parallel processing."""
        with patch('hacktivity.core.parallel.get_config') as mock_config:
            mock_config.return_value.github = GitHubConfig(
                parallel_enabled=True,
                max_workers=3,
                rate_limit_buffer=200
            )
            
            # Mock rate limiter
            with patch('hacktivity.core.rate_limiter.get_rate_limit_coordinator') as mock_rlc:
                mock_coordinator = MagicMock()
                mock_rlc.return_value = mock_coordinator
                
                # Mock worker execution
                def mock_worker(repo_name, operation_id, since, until, author_filter, max_days, progress):
                    # Each worker should acquire rate limit token
                    mock_coordinator.acquire()
                    progress.mark_done(success=True)
                    return repo_name, [{'sha': 'commit'}]
                
                with patch('hacktivity.core.parallel._worker', side_effect=mock_worker):
                    with patch('rich.progress.Progress'):  # Mock rich
                        result = fetch_commits_parallel(
                            operation_id="op123",
                            repositories=["test/repo1", "test/repo2", "test/repo3"],
                            since="2024-01-01",
                            until="2024-01-31"
                        )
                
                # Verify rate limiter was called for each repository
                assert mock_coordinator.acquire.call_count == 3
    
    def test_rate_limiter_configuration_applied(self):
        """Test that rate limiter uses correct configuration."""
        with patch('hacktivity.core.rate_limiter.get_config') as mock_config:
            mock_config.return_value.github = GitHubConfig(rate_limit_buffer=300)
            
            # Reset singleton to pick up new config
            from hacktivity.core.rate_limiter import RateLimitCoordinator
            RateLimitCoordinator._instance = None
            
            coordinator = RateLimitCoordinator()
            
            # Should use 5000 - 300 = 4700 capacity
            expected_capacity = 5000 - 300
            assert coordinator._capacity == expected_capacity


class TestStateManagementIntegration:
    """Test integration with state management system."""
    
    def test_parallel_processing_with_state_tracking(self):
        """Test that parallel processing integrates with state management."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "test_state.db"
            
            with patch('hacktivity.core.parallel.get_config') as mock_config:
                mock_config.return_value.github = GitHubConfig(parallel_enabled=True)
                
                # Mock state manager
                with patch('hacktivity.core.parallel.get_state_manager') as mock_state_mgr:
                    mock_manager = MagicMock()
                    mock_state_mgr.return_value = mock_manager
                    
                    # Mock fetch_repo_commits_chunked to verify state integration
                    with patch('hacktivity.core.parallel.fetch_repo_commits_chunked') as mock_fetch:
                        mock_fetch.return_value = [{'sha': 'commit'}]
                        
                        result = fetch_commits_parallel(
                            operation_id="op123",
                            repositories=["test/repo"],
                            since="2024-01-01",
                            until="2024-01-31"
                        )
                        
                        # Verify fetch_repo_commits_chunked was called with operation_id
                        mock_fetch.assert_called_once_with(
                            "test/repo", "2024-01-01", "2024-01-31", None, 7, "op123"
                        )
    
    def test_parallel_processing_state_consistency(self):
        """Test that state remains consistent across parallel workers."""
        with patch('hacktivity.core.parallel.get_config') as mock_config:
            mock_config.return_value.github = GitHubConfig(
                parallel_enabled=True, 
                max_workers=3
            )
            
            # Track state operations
            state_operations = []
            
            def track_fetch_call(repo_name, since, until, author_filter, max_days, operation_id):
                state_operations.append({
                    'repo': repo_name,
                    'operation_id': operation_id,
                    'thread_id': threading.current_thread().ident
                })
                return [{'sha': f'{repo_name}_commit'}]
            
            import threading
            with patch('hacktivity.core.parallel.fetch_repo_commits_chunked', side_effect=track_fetch_call):
                with patch('hacktivity.core.parallel.get_state_manager'):
                    with patch('rich.progress.Progress'):
                        result = fetch_commits_parallel(
                            operation_id="op123",
                            repositories=["test/repo1", "test/repo2", "test/repo3"],
                            since="2024-01-01",
                            until="2024-01-31"
                        )
            
            # Verify all operations used the same operation_id
            assert len(state_operations) == 3
            for op in state_operations:
                assert op['operation_id'] == "op123"
            
            # Verify different threads were used (parallel execution)
            thread_ids = [op['thread_id'] for op in state_operations]
            # With 3 workers and 3 repos, we should see multiple threads
            assert len(set(thread_ids)) >= 2  # At least 2 different threads


class TestCircuitBreakerIntegration:
    """Test integration with circuit breaker system."""
    
    def test_parallel_processing_with_circuit_breaker(self):
        """Test that parallel processing works with circuit breakers."""
        with patch('hacktivity.core.parallel.get_config') as mock_config:
            mock_config.return_value.github = GitHubConfig(parallel_enabled=True)
            
            # Mock circuit breaker being used in fetch_repo_commits_chunked
            circuit_breaker_calls = []
            
            def mock_fetch_with_circuit_breaker(repo_name, since, until, author_filter, max_days, operation_id):
                # Simulate circuit breaker integration
                circuit_breaker_calls.append(repo_name)
                return [{'sha': f'{repo_name}_commit'}]
            
            with patch('hacktivity.core.parallel.fetch_repo_commits_chunked', side_effect=mock_fetch_with_circuit_breaker):
                with patch('hacktivity.core.parallel.get_state_manager'):
                    with patch('rich.progress.Progress'):
                        result = fetch_commits_parallel(
                            operation_id="op123",
                            repositories=["test/repo1", "test/repo2"],
                            since="2024-01-01",
                            until="2024-01-31"
                        )
            
            # Verify all repositories were processed through circuit breaker
            assert len(circuit_breaker_calls) == 2
            assert "test/repo1" in circuit_breaker_calls
            assert "test/repo2" in circuit_breaker_calls
    
    def test_parallel_processing_with_circuit_breaker_failures(self):
        """Test parallel processing when circuit breakers fail."""
        with patch('hacktivity.core.parallel.get_config') as mock_config:
            mock_config.return_value.github = GitHubConfig(parallel_enabled=True)
            
            # Mock circuit breaker failures
            def mock_fetch_with_failures(repo_name, since, until, author_filter, max_days, operation_id):
                if repo_name == "test/repo2":
                    from hacktivity.core.circuit_breaker import CircuitOpenError
                    raise CircuitOpenError("test/endpoint")
                return [{'sha': f'{repo_name}_commit'}]
            
            with patch('hacktivity.core.parallel.fetch_repo_commits_chunked', side_effect=mock_fetch_with_failures):
                with patch('hacktivity.core.parallel.get_state_manager'):
                    with patch('rich.progress.Progress'):
                        result = fetch_commits_parallel(
                            operation_id="op123",
                            repositories=["test/repo1", "test/repo2", "test/repo3"],
                            since="2024-01-01",
                            until="2024-01-31"
                        )
            
            # Should handle circuit breaker failures gracefully
            assert "test/repo1" in result
            assert "test/repo2" in result
            assert "test/repo3" in result
            
            # Failed repository should have empty results
            assert len(result["test/repo1"]) == 1
            assert len(result["test/repo2"]) == 0  # Failed due to circuit breaker
            assert len(result["test/repo3"]) == 1


class TestEndToEndParallelWorkflow:
    """Test complete end-to-end parallel processing workflow."""
    
    def test_complete_parallel_workflow(self):
        """Test complete parallel processing workflow from configuration to results."""
        with patch('hacktivity.core.parallel.get_config') as mock_config:
            mock_config.return_value.github = GitHubConfig(
                parallel_enabled=True,
                max_workers=2,
                rate_limit_buffer=150
            )
            
            # Mock all dependencies
            with patch('hacktivity.core.parallel.get_state_manager') as mock_state:
                with patch('hacktivity.core.parallel.fetch_repo_commits_chunked') as mock_fetch:
                    with patch('rich.progress.Progress') as mock_progress:
                        
                        # Configure mock responses
                        def mock_fetch_response(repo_name, since, until, author_filter, max_days, operation_id):
                            return [
                                {'sha': f'{repo_name}_commit1', 'message': f'Commit 1 from {repo_name}'},
                                {'sha': f'{repo_name}_commit2', 'message': f'Commit 2 from {repo_name}'}
                            ]
                        mock_fetch.side_effect = mock_fetch_response
                        
                        # Execute parallel processing
                        result = fetch_commits_parallel(
                            operation_id="integration_test_op",
                            repositories=["user/repo1", "user/repo2", "user/repo3"],
                            since="2024-01-01",
                            until="2024-01-31",
                            author_filter="testuser"
                        )
                        
                        # Verify results
                        assert len(result) == 3
                        for repo in ["user/repo1", "user/repo2", "user/repo3"]:
                            assert repo in result
                            assert len(result[repo]) == 2
                            assert all('sha' in commit for commit in result[repo])
                            assert all('message' in commit for commit in result[repo])
                        
                        # Verify all repositories were processed with correct parameters
                        assert mock_fetch.call_count == 3
                        for call in mock_fetch.call_args_list:
                            args, kwargs = call
                            assert args[1] == "2024-01-01"  # since
                            assert args[2] == "2024-01-31"  # until
                            assert args[3] == "testuser"   # author_filter
                            assert args[4] == 7            # max_days
                            assert args[5] == "integration_test_op"  # operation_id
    
    def test_performance_characteristics(self):
        """Test that parallel processing provides expected performance characteristics."""
        import time
        
        with patch('hacktivity.core.parallel.get_config') as mock_config:
            mock_config.return_value.github = GitHubConfig(
                parallel_enabled=True,
                max_workers=3
            )
            
            # Mock with simulated processing time
            def mock_slow_fetch(repo_name, since, until, author_filter, max_days, operation_id):
                time.sleep(0.1)  # Simulate processing time
                return [{'sha': f'{repo_name}_commit'}]
            
            with patch('hacktivity.core.parallel.fetch_repo_commits_chunked', side_effect=mock_slow_fetch):
                with patch('hacktivity.core.parallel.get_state_manager'):
                    with patch('rich.progress.Progress'):
                        
                        start_time = time.time()
                        result = fetch_commits_parallel(
                            operation_id="perf_test",
                            repositories=["repo1", "repo2", "repo3", "repo4", "repo5"],
                            since="2024-01-01",
                            until="2024-01-31"
                        )
                        end_time = time.time()
                        
                        # With 3 workers and 5 repos, should complete in roughly 2 rounds
                        # Each round takes ~0.1s, so total should be < 0.5s with overhead
                        execution_time = end_time - start_time
                        assert execution_time < 0.5  # Should be significantly faster than sequential (5 * 0.1 = 0.5s)
                        
                        # Verify all repositories were processed
                        assert len(result) == 5
                        for i in range(1, 6):
                            assert f"repo{i}" in result


class TestParallelProcessingFallbacks:
    """Test fallback mechanisms in parallel processing."""
    
    def test_fallback_to_sequential_on_low_repo_count(self):
        """Test that single repositories fall back to sequential processing."""
        with patch('hacktivity.core.parallel.get_config') as mock_config:
            mock_config.return_value.github = GitHubConfig(parallel_enabled=True)
            
            with patch('hacktivity.core.parallel.process_repositories_with_operation_state') as mock_sequential:
                mock_sequential.return_value = {"test/repo": [{'sha': 'commit'}]}
                
                result = fetch_commits_parallel(
                    operation_id="fallback_test",
                    repositories=["test/repo"],
                    since="2024-01-01",
                    until="2024-01-31"
                )
                
                # Should use sequential processing
                mock_sequential.assert_called_once_with(
                    "fallback_test", ["test/repo"], "2024-01-01", "2024-01-31", None
                )
                assert result == {"test/repo": [{'sha': 'commit'}]}
    
    def test_fallback_when_parallel_disabled(self):
        """Test fallback when parallel processing is disabled."""
        with patch('hacktivity.core.parallel.get_config') as mock_config:
            mock_config.return_value.github = GitHubConfig(parallel_enabled=False)
            
            with patch('hacktivity.core.parallel.process_repositories_with_operation_state') as mock_sequential:
                mock_sequential.return_value = {
                    "test/repo1": [{'sha': 'commit1'}],
                    "test/repo2": [{'sha': 'commit2'}]
                }
                
                result = fetch_commits_parallel(
                    operation_id="disabled_test", 
                    repositories=["test/repo1", "test/repo2"],
                    since="2024-01-01",
                    until="2024-01-31"
                )
                
                # Should use sequential even with multiple repos
                mock_sequential.assert_called_once()
                assert len(result) == 2