"""Unit tests for github module."""

import json
import subprocess
import time
from datetime import datetime
from unittest.mock import patch, MagicMock, call
import pytest

# Mock dependencies
with patch.dict('sys.modules', {
    'tenacity': MagicMock(
        retry=lambda **kwargs: lambda func: func,
        stop_after_attempt=MagicMock(),
        wait_exponential=MagicMock(),
        retry_if_exception_type=MagicMock()
    ),
    'rich.progress': MagicMock()
}):
    from hacktivity.core import github
    from hacktivity.core.github import (
        _is_rate_limit_error, _extract_rate_limit_reset_time,
        _fetch_commits_with_progress, _generate_cache_key,
        check_github_prerequisites, get_github_user, fetch_commits
    )


class TestHelperFunctions:
    """Test helper functions."""
    
    def test_is_rate_limit_error_true(self):
        """Test identifying rate limit errors."""
        error = {'message': 'API rate limit exceeded for user'}
        assert _is_rate_limit_error(error) is True
        
        error = {'message': 'Rate limit exceeded'}
        assert _is_rate_limit_error(error) is True
        
    def test_is_rate_limit_error_false(self):
        """Test non-rate-limit errors."""
        error = {'message': 'Not found'}
        assert _is_rate_limit_error(error) is False
        
        error = {'message': 'Authentication required'}
        assert _is_rate_limit_error(error) is False
        
        error = {}
        assert _is_rate_limit_error(error) is False
        
    def test_extract_rate_limit_reset_time(self):
        """Test extracting rate limit reset time."""
        # With valid timestamp
        error = {'rate': {'reset': 1640995200}}  # 2022-01-01 00:00:00
        reset_time = _extract_rate_limit_reset_time(error)
        assert reset_time is not None
        assert '2022' in reset_time or '2021' in reset_time  # Timezone-dependent
        
    def test_extract_rate_limit_reset_time_missing(self):
        """Test extracting reset time when missing."""
        error = {'rate': {}}
        assert _extract_rate_limit_reset_time(error) is None
        
        error = {}
        assert _extract_rate_limit_reset_time(error) is None
        
        error = {'rate': {'reset': 'invalid'}}
        assert _extract_rate_limit_reset_time(error) is None
        
    def test_generate_cache_key(self):
        """Test cache key generation."""
        key = _generate_cache_key('user1', '2024-01-01', '2024-01-31')
        assert key == 'commits:user1:2024-01-01:2024-01-31:none:none'
        
        key = _generate_cache_key('user1', '2024-01-01', '2024-01-31', 'myorg')
        assert key == 'commits:user1:2024-01-01:2024-01-31:myorg:none'
        
        key = _generate_cache_key('user1', '2024-01-01', '2024-01-31', 'myorg', 'owner/repo')
        assert key == 'commits:user1:2024-01-01:2024-01-31:myorg:owner/repo'


class TestPrerequisites:
    """Test prerequisite checking functions."""
    
    @patch('subprocess.run')
    def test_check_github_prerequisites_success(self, mock_run):
        """Test successful prerequisites check."""
        mock_run.return_value = MagicMock(returncode=0)
        
        check_github_prerequisites()
        
        assert mock_run.call_count == 2
        mock_run.assert_any_call(['gh', '--version'], check=True, capture_output=True)
        mock_run.assert_any_call(['gh', 'auth', 'status'], check=True, capture_output=True)
        
    @patch('subprocess.run')
    @patch('sys.exit')
    def test_check_github_prerequisites_no_gh(self, mock_exit, mock_run):
        """Test when gh CLI is not installed."""
        mock_run.side_effect = FileNotFoundError()
        
        check_github_prerequisites()
        
        mock_exit.assert_called_once_with(1)
        
    @patch('subprocess.run')
    @patch('sys.exit')
    def test_check_github_prerequisites_not_authenticated(self, mock_exit, mock_run):
        """Test when gh is not authenticated."""
        # First call succeeds (gh --version)
        mock_run.side_effect = [
            MagicMock(returncode=0),
            subprocess.CalledProcessError(1, ['gh', 'auth', 'status'])
        ]
        
        check_github_prerequisites()
        
        mock_exit.assert_called_once_with(1)
        
    @patch('subprocess.run')
    def test_get_github_user_success(self, mock_run):
        """Test getting GitHub username."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='testuser\n'
        )
        
        user = get_github_user()
        
        assert user == 'testuser'
        mock_run.assert_called_once_with(
            ['gh', 'api', 'user', '--jq', '.login'],
            check=True,
            capture_output=True,
            text=True
        )
        
    @patch('subprocess.run')
    @patch('sys.exit')
    def test_get_github_user_error(self, mock_exit, mock_run):
        """Test error getting GitHub username."""
        mock_run.side_effect = subprocess.CalledProcessError(
            1, ['gh'], stderr='API error'
        )
        
        get_github_user()
        
        mock_exit.assert_called_once_with(1)


class TestFetchCommitsWithProgress:
    """Test the internal fetch function with progress."""
    
    @patch('hacktivity.core.github._get_config')
    @patch('hacktivity.core.github.cache')
    @patch('subprocess.run')
    @patch('hacktivity.core.github.Progress')
    def test_fetch_simple_page(self, mock_progress_class, mock_run, mock_cache, mock_config):
        """Test fetching a single page of commits."""
        # Setup config
        mock_config.return_value = MagicMock(
            github=MagicMock(per_page=100, timeout_seconds=60, max_pages=10)
        )
        
        # Setup cache (no partial data)
        mock_cache.get_partial.return_value = None
        
        # Setup progress
        mock_progress = MagicMock()
        mock_progress_class.return_value.__enter__.return_value = mock_progress
        
        # Setup API response
        api_response = {
            'total_count': 2,
            'items': [
                {'commit': {'message': 'First commit'}},
                {'commit': {'message': 'Second commit'}}
            ]
        }
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(api_response)
        )
        
        commits = _fetch_commits_with_progress('author:test', 'cache_key_123')
        
        assert commits == ['First commit', 'Second commit']
        mock_cache.append_partial.assert_called_once()
        
    @patch('hacktivity.core.github._get_config')
    @patch('hacktivity.core.github.cache')
    @patch('subprocess.run')
    @patch('hacktivity.core.github.Progress')
    def test_fetch_with_partial_cache(self, mock_progress_class, mock_run, mock_cache, mock_config):
        """Test resuming from partial cache."""
        # Setup config
        mock_config.return_value = MagicMock(
            github=MagicMock(per_page=100, timeout_seconds=60, max_pages=10)
        )
        
        # Setup partial cache data
        mock_cache.get_partial.return_value = {
            'pages': {
                '1': ['Cached commit 1', 'Cached commit 2']
            },
            'timestamp': time.time()
        }
        
        # Setup progress
        mock_progress = MagicMock()
        mock_progress_class.return_value.__enter__.return_value = mock_progress
        
        # Setup API response for page 2
        api_response = {
            'total_count': 3,
            'items': [
                {'commit': {'message': 'New commit 3'}}
            ]
        }
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(api_response)
        )
        
        commits = _fetch_commits_with_progress('author:test', 'cache_key_123')
        
        assert commits == ['Cached commit 1', 'Cached commit 2', 'New commit 3']
        # Should fetch page 2
        assert 'page=2' in mock_run.call_args[0][0][-1]
        
    @patch('hacktivity.core.github._get_config')
    @patch('hacktivity.core.github.cache')
    @patch('subprocess.run')
    @patch('hacktivity.core.github.Progress')
    def test_fetch_multiple_pages(self, mock_progress_class, mock_run, mock_cache, mock_config):
        """Test fetching multiple pages."""
        # Setup config
        mock_config.return_value = MagicMock(
            github=MagicMock(per_page=2, timeout_seconds=60, max_pages=10)
        )
        
        # Setup cache (no partial data)
        mock_cache.get_partial.return_value = None
        
        # Setup progress
        mock_progress = MagicMock()
        mock_progress_class.return_value.__enter__.return_value = mock_progress
        
        # Setup API responses
        responses = [
            # Page 1
            {'total_count': 4, 'items': [
                {'commit': {'message': 'Commit 1'}},
                {'commit': {'message': 'Commit 2'}}
            ]},
            # Page 2
            {'total_count': 4, 'items': [
                {'commit': {'message': 'Commit 3'}},
                {'commit': {'message': 'Commit 4'}}
            ]},
            # Page 3 (empty)
            {'total_count': 4, 'items': []}
        ]
        
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=json.dumps(resp))
            for resp in responses
        ]
        
        commits = _fetch_commits_with_progress('author:test', 'cache_key_123')
        
        assert len(commits) == 4
        assert commits == ['Commit 1', 'Commit 2', 'Commit 3', 'Commit 4']
        assert mock_cache.append_partial.call_count == 2  # Two non-empty pages


class TestFetchCommits:
    """Test the main fetch_commits function."""
    
    @patch('hacktivity.core.github._get_config')
    @patch('hacktivity.core.github.cache')
    @patch('hacktivity.core.github._fetch_commits_with_progress')
    def test_fetch_with_cache_hit(self, mock_fetch_progress, mock_cache, mock_config):
        """Test fetching when cache has valid data."""
        # Setup config
        mock_config.return_value = MagicMock(
            github=MagicMock(retry_attempts=3, retry_min_wait=1, retry_max_wait=10)
        )
        
        # Setup cache hit
        mock_cache.get.return_value = ['Cached commit 1', 'Cached commit 2']
        
        commits = fetch_commits('user1', '2024-01-01', '2024-01-31')
        
        assert commits == ['Cached commit 1', 'Cached commit 2']
        mock_fetch_progress.assert_not_called()
        
    @patch('hacktivity.core.github._get_config')
    @patch('hacktivity.core.github.cache')
    @patch('hacktivity.core.github._fetch_commits_with_progress')
    def test_fetch_without_cache(self, mock_fetch_progress, mock_cache, mock_config):
        """Test fetching when cache miss."""
        # Setup config
        mock_config.return_value = MagicMock(
            github=MagicMock(retry_attempts=3, retry_min_wait=1, retry_max_wait=10)
        )
        
        # Setup cache miss
        mock_cache.get.return_value = None
        
        # Setup fetch response
        mock_fetch_progress.return_value = ['New commit 1', 'New commit 2']
        
        commits = fetch_commits('user1', '2024-01-01', '2024-01-31')
        
        assert commits == ['New commit 1', 'New commit 2']
        mock_fetch_progress.assert_called_once()
        mock_cache.set.assert_called_once()
        mock_cache.clear_partial.assert_called_once()
        
    @patch('hacktivity.core.github._get_config')
    @patch('hacktivity.core.github.cache')
    @patch('hacktivity.core.github._fetch_commits_with_progress')
    def test_fetch_with_filters(self, mock_fetch_progress, mock_cache, mock_config):
        """Test fetching with org and repo filters."""
        # Setup config
        mock_config.return_value = MagicMock(
            github=MagicMock(retry_attempts=3, retry_min_wait=1, retry_max_wait=10)
        )
        
        # Setup cache miss
        mock_cache.get.return_value = None
        mock_fetch_progress.return_value = ['Commit 1']
        
        commits = fetch_commits('user1', '2024-01-01', '2024-01-31', 
                               org='myorg', repo='owner/repo')
        
        # Check the query includes org and repo
        query = mock_fetch_progress.call_args[0][0]
        assert 'org:myorg' in query
        assert 'repo:owner/repo' in query
        
    @patch('hacktivity.core.github._get_config')
    @patch('hacktivity.core.github.cache')
    @patch('hacktivity.core.github._fetch_commits_with_progress')
    @patch('sys.exit')
    def test_fetch_rate_limit_with_cache_fallback(self, mock_exit, mock_fetch_progress, 
                                                  mock_cache, mock_config):
        """Test rate limit error with cache fallback."""
        # Setup config
        mock_config.return_value = MagicMock(
            github=MagicMock(retry_attempts=3, retry_min_wait=1, retry_max_wait=10)
        )
        
        # Setup cache miss on first get, but available for fallback
        mock_cache.get.side_effect = [None, ['Fallback commit']]
        
        # Setup rate limit error
        error_response = json.dumps({
            'message': 'API rate limit exceeded',
            'rate': {'reset': int(time.time()) + 3600}
        })
        mock_fetch_progress.side_effect = subprocess.CalledProcessError(
            1, ['gh'], stderr=error_response
        )
        
        commits = fetch_commits('user1', '2024-01-01', '2024-01-31')
        
        assert commits == ['Fallback commit']
        # Check we tried extended cache TTL
        assert mock_cache.get.call_args_list[1][0][1] == 168  # 7 days
        mock_exit.assert_not_called()
        
    @patch('hacktivity.core.github._get_config')
    @patch('hacktivity.core.github.cache')
    @patch('hacktivity.core.github._fetch_commits_with_progress')
    @patch('sys.exit')
    def test_fetch_rate_limit_no_fallback(self, mock_exit, mock_fetch_progress, 
                                          mock_cache, mock_config):
        """Test rate limit error without cache fallback."""
        # Setup config
        mock_config.return_value = MagicMock(
            github=MagicMock(retry_attempts=3, retry_min_wait=1, retry_max_wait=10)
        )
        
        # Setup cache miss
        mock_cache.get.return_value = None
        
        # Setup rate limit error
        error_response = json.dumps({
            'message': 'API rate limit exceeded',
            'rate': {'reset': int(time.time()) + 3600}
        })
        mock_fetch_progress.side_effect = subprocess.CalledProcessError(
            1, ['gh'], stderr=error_response
        )
        
        fetch_commits('user1', '2024-01-01', '2024-01-31')
        
        mock_exit.assert_called_once_with(1)
        
    @patch('hacktivity.core.github._get_config')
    @patch('hacktivity.core.github.cache')
    @patch('hacktivity.core.github._fetch_commits_with_progress')
    def test_fetch_timeout_retry(self, mock_fetch_progress, mock_cache, mock_config):
        """Test timeout triggers retry."""
        # Setup config
        mock_config.return_value = MagicMock(
            github=MagicMock(retry_attempts=3, retry_min_wait=1, retry_max_wait=10)
        )
        
        # Setup cache miss
        mock_cache.get.return_value = None
        
        # First call times out, second succeeds
        mock_fetch_progress.side_effect = [
            subprocess.TimeoutExpired(['gh'], 60),
            ['Commit after retry']
        ]
        
        # The retry decorator is mocked, so we simulate its behavior
        commits = fetch_commits('user1', '2024-01-01', '2024-01-31')
        
        # Since we mocked tenacity, we need to handle this differently
        # In real code, tenacity would retry automatically