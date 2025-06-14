"""Unit tests for parallel repository processing."""

import concurrent.futures
import threading
import time
from unittest.mock import patch, MagicMock, Mock
import pytest

from hacktivity.core.parallel import (
    ProgressAggregator, fetch_commits_parallel, _worker
)
from hacktivity.core.config import GitHubConfig


class TestProgressAggregator:
    """Test the ProgressAggregator class."""
    
    def test_progress_aggregator_initialization(self):
        """Test ProgressAggregator initialization."""
        progress = ProgressAggregator(total_repos=10)
        
        assert progress.total == 10
        assert progress.completed == 0
        assert progress.failed == 0
        assert progress.processed_count == 0
    
    def test_mark_done_success(self):
        """Test marking successful completion."""
        progress = ProgressAggregator(total_repos=5)
        
        progress.mark_done(success=True)
        progress.mark_done(success=True)
        
        assert progress.completed == 2
        assert progress.failed == 0
        assert progress.processed_count == 2
    
    def test_mark_done_failure(self):
        """Test marking failed completion."""
        progress = ProgressAggregator(total_repos=5)
        
        progress.mark_done(success=False)
        progress.mark_done(success=False)
        
        assert progress.completed == 0
        assert progress.failed == 2
        assert progress.processed_count == 2
    
    def test_mark_done_mixed(self):
        """Test marking mixed success and failure."""
        progress = ProgressAggregator(total_repos=5)
        
        progress.mark_done(success=True)
        progress.mark_done(success=False)
        progress.mark_done(success=True)
        
        assert progress.completed == 2
        assert progress.failed == 1
        assert progress.processed_count == 3
    
    def test_thread_safety(self):
        """Test that ProgressAggregator is thread-safe."""
        progress = ProgressAggregator(total_repos=100)
        
        def worker_thread(success: bool, count: int):
            for _ in range(count):
                progress.mark_done(success=success)
        
        # Start multiple threads marking progress
        threads = []
        threads.append(threading.Thread(target=worker_thread, args=(True, 25)))
        threads.append(threading.Thread(target=worker_thread, args=(False, 15)))
        threads.append(threading.Thread(target=worker_thread, args=(True, 30)))
        threads.append(threading.Thread(target=worker_thread, args=(False, 10)))
        
        for thread in threads:
            thread.start()
        
        for thread in threads:
            thread.join()
        
        assert progress.completed == 55  # 25 + 30
        assert progress.failed == 25     # 15 + 10
        assert progress.processed_count == 80


class TestWorkerFunction:
    """Test the _worker function."""
    
    def test_worker_success(self):
        """Test successful worker execution."""
        with patch('hacktivity.core.parallel.get_state_manager') as mock_state:
            with patch('hacktivity.core.parallel.fetch_repo_commits_chunked') as mock_fetch:
                mock_fetch.return_value = [
                    {'sha': 'abc123', 'message': 'test commit'}
                ]
                
                progress = ProgressAggregator(total_repos=1)
                
                repo_name, commits = _worker(
                    repo_name="test/repo",
                    operation_id="op123",
                    since="2024-01-01",
                    until="2024-01-31",
                    author_filter="testuser",
                    max_days=7,
                    progress=progress
                )
                
                assert repo_name == "test/repo"
                assert len(commits) == 1
                assert commits[0]['sha'] == 'abc123'
                assert progress.completed == 1
                assert progress.failed == 0
                
                mock_fetch.assert_called_once_with(
                    "test/repo", "2024-01-01", "2024-01-31", "testuser", 7, "op123"
                )
    
    def test_worker_failure(self):
        """Test worker execution with failure."""
        with patch('hacktivity.core.parallel.get_state_manager') as mock_state:
            with patch('hacktivity.core.parallel.fetch_repo_commits_chunked') as mock_fetch:
                mock_fetch.side_effect = Exception("API Error")
                
                progress = ProgressAggregator(total_repos=1)
                
                repo_name, commits = _worker(
                    repo_name="test/repo",
                    operation_id="op123",
                    since="2024-01-01",
                    until="2024-01-31",
                    author_filter="testuser",
                    max_days=7,
                    progress=progress
                )
                
                assert repo_name == "test/repo"
                assert commits == []  # Empty list on failure
                assert progress.completed == 0
                assert progress.failed == 1


class TestFetchCommitsParallel:
    """Test the fetch_commits_parallel function."""
    
    def test_parallel_disabled_fallback(self):
        """Test fallback to sequential when parallel is disabled."""
        with patch('hacktivity.core.parallel.get_config') as mock_config:
            mock_config.return_value.github = GitHubConfig(parallel_enabled=False)
            
            with patch('hacktivity.core.parallel.process_repositories_with_operation_state') as mock_sequential:
                mock_sequential.return_value = {
                    "test/repo": [{'sha': 'abc123'}]
                }
                
                result = fetch_commits_parallel(
                    operation_id="op123",
                    repositories=["test/repo"],
                    since="2024-01-01",
                    until="2024-01-31",
                    author_filter="testuser"
                )
                
                assert result == {"test/repo": [{'sha': 'abc123'}]}
                mock_sequential.assert_called_once_with(
                    "op123", ["test/repo"], "2024-01-01", "2024-01-31", "testuser"
                )
    
    def test_single_repo_fallback(self):
        """Test fallback to sequential for single repository."""
        with patch('hacktivity.core.parallel.get_config') as mock_config:
            mock_config.return_value.github = GitHubConfig(parallel_enabled=True)
            
            with patch('hacktivity.core.parallel.process_repositories_with_operation_state') as mock_sequential:
                mock_sequential.return_value = {
                    "test/repo": [{'sha': 'abc123'}]
                }
                
                result = fetch_commits_parallel(
                    operation_id="op123",
                    repositories=["test/repo"],
                    since="2024-01-01",
                    until="2024-01-31",
                    author_filter="testuser"
                )
                
                assert result == {"test/repo": [{'sha': 'abc123'}]}
                mock_sequential.assert_called_once()
    
    def test_parallel_processing_enabled(self):
        """Test actual parallel processing with multiple repositories."""
        with patch('hacktivity.core.parallel.get_config') as mock_config:
            mock_config.return_value.github = GitHubConfig(
                parallel_enabled=True, 
                max_workers=2
            )
            
            repositories = ["test/repo1", "test/repo2", "test/repo3"]
            
            # Mock the worker function to return predictable results
            def mock_worker(repo_name, operation_id, since, until, author_filter, max_days, progress):
                progress.mark_done(success=True)
                return repo_name, [{'sha': f'{repo_name}_commit', 'message': f'commit from {repo_name}'}]
            
            with patch('hacktivity.core.parallel._worker', side_effect=mock_worker):
                result = fetch_commits_parallel(
                    operation_id="op123",
                    repositories=repositories,
                    since="2024-01-01",
                    until="2024-01-31",
                    author_filter="testuser"
                )
                
                assert len(result) == 3
                assert "test/repo1" in result
                assert "test/repo2" in result
                assert "test/repo3" in result
                
                # Verify each repository has its expected commits
                for repo in repositories:
                    assert len(result[repo]) == 1
                    assert result[repo][0]['sha'] == f'{repo}_commit'
    
    def test_parallel_processing_with_failures(self):
        """Test parallel processing handling partial failures."""
        with patch('hacktivity.core.parallel.get_config') as mock_config:
            mock_config.return_value.github = GitHubConfig(
                parallel_enabled=True, 
                max_workers=2
            )
            
            repositories = ["test/repo1", "test/repo2", "test/repo3"]
            
            # Mock worker to fail for repo2
            def mock_worker(repo_name, operation_id, since, until, author_filter, max_days, progress):
                if repo_name == "test/repo2":
                    progress.mark_done(success=False)
                    return repo_name, []  # Empty on failure
                else:
                    progress.mark_done(success=True)
                    return repo_name, [{'sha': f'{repo_name}_commit'}]
            
            with patch('hacktivity.core.parallel._worker', side_effect=mock_worker):
                result = fetch_commits_parallel(
                    operation_id="op123",
                    repositories=repositories,
                    since="2024-01-01",
                    until="2024-01-31",
                    author_filter="testuser"
                )
                
                assert len(result) == 3
                assert len(result["test/repo1"]) == 1
                assert len(result["test/repo2"]) == 0  # Failed
                assert len(result["test/repo3"]) == 1
    
    def test_progress_tracking_integration(self):
        """Test that progress tracking works correctly."""
        with patch('hacktivity.core.parallel.get_config') as mock_config:
            mock_config.return_value.github = GitHubConfig(
                parallel_enabled=True, 
                max_workers=2
            )
            
            repositories = ["test/repo1", "test/repo2"]
            
            # Track progress updates
            progress_updates = []
            
            def mock_worker(repo_name, operation_id, since, until, author_filter, max_days, progress):
                progress.mark_done(success=True)
                progress_updates.append(progress.processed_count)
                return repo_name, [{'sha': f'{repo_name}_commit'}]
            
            with patch('hacktivity.core.parallel._worker', side_effect=mock_worker):
                # Mock rich Progress to avoid UI dependencies
                with patch('rich.progress.Progress') as mock_progress_class:
                    mock_progress = MagicMock()
                    mock_progress_class.return_value.__enter__.return_value = mock_progress
                    
                    result = fetch_commits_parallel(
                        operation_id="op123",
                        repositories=repositories,
                        since="2024-01-01",
                        until="2024-01-31",
                        author_filter="testuser"
                    )
                    
                    # Verify progress was tracked
                    assert len(progress_updates) == 2
                    assert set(progress_updates) == {1, 2}  # Should have 1 and 2 processed
    
    def test_thread_pool_executor_usage(self):
        """Test that ThreadPoolExecutor is used correctly."""
        with patch('hacktivity.core.parallel.get_config') as mock_config:
            mock_config.return_value.github = GitHubConfig(
                parallel_enabled=True, 
                max_workers=3
            )
            
            repositories = ["test/repo1", "test/repo2"]
            
            with patch('concurrent.futures.ThreadPoolExecutor') as mock_executor_class:
                mock_executor = MagicMock()
                mock_executor_class.return_value.__enter__.return_value = mock_executor
                
                # Mock future results
                mock_future1 = MagicMock()
                mock_future1.result.return_value = ("test/repo1", [{'sha': 'commit1'}])
                mock_future2 = MagicMock()
                mock_future2.result.return_value = ("test/repo2", [{'sha': 'commit2'}])
                
                mock_executor.submit.side_effect = [mock_future1, mock_future2]
                
                with patch('concurrent.futures.as_completed', return_value=[mock_future1, mock_future2]):
                    with patch('rich.progress.Progress'):  # Mock rich for UI
                        result = fetch_commits_parallel(
                            operation_id="op123",
                            repositories=repositories,
                            since="2024-01-01",
                            until="2024-01-31",
                            author_filter="testuser"
                        )
                
                # Verify ThreadPoolExecutor was created with correct max_workers
                mock_executor_class.assert_called_once_with(max_workers=3)
                
                # Verify submit was called for each repository
                assert mock_executor.submit.call_count == 2


class TestParallelConfigurationIntegration:
    """Test integration with configuration system."""
    
    def test_configuration_integration(self):
        """Test that parallel processing respects configuration."""
        with patch('hacktivity.core.parallel.get_config') as mock_config:
            # Test with custom configuration
            mock_config.return_value.github = GitHubConfig(
                parallel_enabled=True,
                max_workers=8
            )
            
            with patch('concurrent.futures.ThreadPoolExecutor') as mock_executor_class:
                mock_executor = MagicMock()
                mock_executor_class.return_value.__enter__.return_value = mock_executor
                
                mock_future = MagicMock()
                mock_future.result.return_value = ("test/repo", [])
                mock_executor.submit.return_value = mock_future
                
                with patch('concurrent.futures.as_completed', return_value=[mock_future]):
                    with patch('rich.progress.Progress'):
                        fetch_commits_parallel(
                            operation_id="op123",
                            repositories=["test/repo1", "test/repo2"],
                            since="2024-01-01",
                            until="2024-01-31"
                        )
                
                # Should use the configured max_workers
                mock_executor_class.assert_called_once_with(max_workers=8)
    
    def test_fallback_without_rich(self):
        """Test that parallel processing works without rich library."""
        with patch('hacktivity.core.parallel.get_config') as mock_config:
            mock_config.return_value.github = GitHubConfig(
                parallel_enabled=True, 
                max_workers=2
            )
            
            repositories = ["test/repo1"]
            
            def mock_worker(repo_name, operation_id, since, until, author_filter, max_days, progress):
                progress.mark_done(success=True)
                return repo_name, [{'sha': 'commit'}]
            
            with patch('hacktivity.core.parallel._worker', side_effect=mock_worker):
                # Mock ImportError for rich to test fallback
                with patch.dict('sys.modules', {'rich.progress': None}):
                    with patch('builtins.__import__', side_effect=ImportError):
                        result = fetch_commits_parallel(
                            operation_id="op123",
                            repositories=repositories,
                            since="2024-01-01",
                            until="2024-01-31"
                        )
                        
                        # Should still work without rich
                        assert len(result) == 1
                        assert "test/repo1" in result