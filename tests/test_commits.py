"""Unit tests for commits module."""

import json
import subprocess
import time
from datetime import datetime
from unittest.mock import patch, MagicMock
import pytest

# Mock dependencies
with patch.dict('sys.modules', {
    'tenacity': MagicMock(
        retry=lambda **kwargs: lambda func: func,
        stop_after_attempt=MagicMock(),
        wait_exponential=MagicMock(),
        retry_if_exception_type=MagicMock()
    )
}):
    from hacktivity.core import commits
    from hacktivity.core.commits import (
        fetch_repo_commits, _fetch_commits_with_api, _parse_commit_data,
        _generate_commit_cache_key, _filter_commits_by_author
    )


class TestHelperFunctions:
    """Test helper functions."""
    
    def test_generate_commit_cache_key(self):
        """Test commit cache key generation."""
        # Basic key without author filter
        key = _generate_commit_cache_key('owner/repo', '2024-01-01', '2024-01-31')
        assert key == 'commits:owner/repo:2024-01-01:2024-01-31:all'
        
        # Key with author filter
        key = _generate_commit_cache_key('owner/repo', '2024-01-01', '2024-01-31', 'user1')
        assert key == 'commits:owner/repo:2024-01-01:2024-01-31:user1'
        
        # Key with None author (should be treated as 'all')
        key = _generate_commit_cache_key('owner/repo', '2024-01-01', '2024-01-31', None)
        assert key == 'commits:owner/repo:2024-01-01:2024-01-31:all'
        
    def test_parse_commit_data(self):
        """Test parsing commit data from API response."""
        api_commits = [
            {
                'sha': 'abc123',
                'commit': {
                    'message': 'First commit',
                    'author': {
                        'name': 'John Doe',
                        'email': 'john@example.com',
                        'date': '2024-01-01T10:00:00Z'
                    },
                    'committer': {
                        'name': 'John Doe', 
                        'email': 'john@example.com',
                        'date': '2024-01-01T10:00:00Z'
                    }
                },
                'author': {
                    'login': 'johndoe',
                    'id': 123
                },
                'url': 'https://api.github.com/repos/owner/repo/commits/abc123'
            },
            {
                'sha': 'def456',
                'commit': {
                    'message': 'Second commit\n\nWith description',
                    'author': {
                        'name': 'Jane Smith',
                        'email': 'jane@example.com', 
                        'date': '2024-01-02T15:30:00Z'
                    },
                    'committer': {
                        'name': 'Jane Smith',
                        'email': 'jane@example.com',
                        'date': '2024-01-02T15:30:00Z'
                    }
                },
                'author': {
                    'login': 'janesmith',
                    'id': 456
                },
                'url': 'https://api.github.com/repos/owner/repo/commits/def456'
            }
        ]
        
        parsed = _parse_commit_data(api_commits)
        
        assert len(parsed) == 2
        assert parsed[0]['sha'] == 'abc123'
        assert parsed[0]['message'] == 'First commit'
        assert parsed[0]['author_login'] == 'johndoe'
        assert parsed[0]['author_name'] == 'John Doe'
        assert parsed[0]['author_email'] == 'john@example.com'
        assert parsed[0]['commit_date'] == '2024-01-01T10:00:00Z'
        
        assert parsed[1]['sha'] == 'def456'
        assert parsed[1]['message'] == 'Second commit\n\nWith description'
        assert parsed[1]['author_login'] == 'janesmith'
        
    def test_filter_commits_by_author(self):
        """Test filtering commits by author login."""
        commits = [
            {
                'sha': 'abc123',
                'author_login': 'johndoe',
                'message': 'First commit'
            },
            {
                'sha': 'def456', 
                'author_login': 'janesmith',
                'message': 'Second commit'
            },
            {
                'sha': 'ghi789',
                'author_login': 'johndoe',
                'message': 'Third commit'
            }
        ]
        
        # Filter by specific author
        filtered = _filter_commits_by_author(commits, 'johndoe')
        assert len(filtered) == 2
        assert filtered[0]['sha'] == 'abc123'
        assert filtered[1]['sha'] == 'ghi789'
        
        # Filter by different author
        filtered = _filter_commits_by_author(commits, 'janesmith')
        assert len(filtered) == 1
        assert filtered[0]['sha'] == 'def456'
        
        # Filter by non-existent author
        filtered = _filter_commits_by_author(commits, 'nonexistent')
        assert len(filtered) == 0


class TestFetchCommitsWithApi:
    """Test the internal API fetching function."""
    
    @patch('subprocess.run')
    @patch('hacktivity.core.config.get_config')
    def test_fetch_single_page(self, mock_config, mock_run):
        """Test fetching a single page of commits."""
        # Setup config
        mock_config.return_value = MagicMock(
            github=MagicMock(per_page=100, timeout_seconds=60, max_pages=10)
        )
        
        # Setup API response
        api_response = [
            {
                'sha': 'abc123',
                'commit': {
                    'message': 'Test commit',
                    'author': {
                        'name': 'Test User',
                        'email': 'test@example.com',
                        'date': '2024-01-01T10:00:00Z'
                    },
                    'committer': {
                        'name': 'Test User',
                        'email': 'test@example.com', 
                        'date': '2024-01-01T10:00:00Z'
                    }
                },
                'author': {
                    'login': 'testuser',
                    'id': 123
                },
                'url': 'https://api.github.com/repos/owner/repo/commits/abc123'
            }
        ]
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(api_response)
        )
        
        commits_data = _fetch_commits_with_api('repos/owner/repo/commits', {
            'since': '2024-01-01T00:00:00Z',
            'until': '2024-01-31T23:59:59Z'
        })
        
        assert len(commits_data) == 1
        assert commits_data[0]['sha'] == 'abc123'
        
        # Verify API call
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert 'gh' == call_args[0]
        assert 'api' == call_args[1]
        assert 'repos/owner/repo/commits' in call_args[4]
        
    @patch('subprocess.run')
    @patch('hacktivity.core.config.get_config')
    def test_fetch_multiple_pages(self, mock_config, mock_run):
        """Test fetching multiple pages of commits."""
        # Setup config
        mock_config.return_value = MagicMock(
            github=MagicMock(per_page=2, timeout_seconds=60, max_pages=10)
        )
        
        # Setup API responses
        responses = [
            # Page 1
            [
                {'sha': 'commit1', 'commit': {'message': 'First', 'author': {'name': 'User', 'email': 'user@example.com', 'date': '2024-01-01T10:00:00Z'}, 'committer': {'name': 'User', 'email': 'user@example.com', 'date': '2024-01-01T10:00:00Z'}}, 'author': {'login': 'user', 'id': 1}, 'url': 'https://api.github.com/repos/owner/repo/commits/commit1'},
                {'sha': 'commit2', 'commit': {'message': 'Second', 'author': {'name': 'User', 'email': 'user@example.com', 'date': '2024-01-02T10:00:00Z'}, 'committer': {'name': 'User', 'email': 'user@example.com', 'date': '2024-01-02T10:00:00Z'}}, 'author': {'login': 'user', 'id': 1}, 'url': 'https://api.github.com/repos/owner/repo/commits/commit2'}
            ],
            # Page 2
            [
                {'sha': 'commit3', 'commit': {'message': 'Third', 'author': {'name': 'User', 'email': 'user@example.com', 'date': '2024-01-03T10:00:00Z'}, 'committer': {'name': 'User', 'email': 'user@example.com', 'date': '2024-01-03T10:00:00Z'}}, 'author': {'login': 'user', 'id': 1}, 'url': 'https://api.github.com/repos/owner/repo/commits/commit3'}
            ]
        ]
        
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=json.dumps(resp))
            for resp in responses
        ]
        
        commits_data = _fetch_commits_with_api('repos/owner/repo/commits', {
            'since': '2024-01-01T00:00:00Z',
            'until': '2024-01-31T23:59:59Z'
        })
        
        assert len(commits_data) == 3
        assert commits_data[0]['sha'] == 'commit1'
        assert commits_data[1]['sha'] == 'commit2'
        assert commits_data[2]['sha'] == 'commit3'
        assert mock_run.call_count == 2
        
        # Verify pagination in API calls
        first_call = mock_run.call_args_list[0][0][0]
        second_call = mock_run.call_args_list[1][0][0]
        assert 'page=1' in first_call[4]
        assert 'page=2' in second_call[4]
        
    @patch('subprocess.run')
    @patch('hacktivity.core.config.get_config')
    def test_fetch_empty_response(self, mock_config, mock_run):
        """Test fetching when no commits are found."""
        # Setup config
        mock_config.return_value = MagicMock(
            github=MagicMock(per_page=100, timeout_seconds=60, max_pages=10)
        )
        
        # Setup empty API response
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='[]'
        )
        
        commits_data = _fetch_commits_with_api('repos/owner/repo/commits', {})
        
        assert commits_data == []
        
    @patch('subprocess.run')
    @patch('hacktivity.core.config.get_config')
    def test_fetch_api_error(self, mock_config, mock_run):
        """Test handling API errors."""
        # Setup config
        mock_config.return_value = MagicMock(
            github=MagicMock(per_page=100, timeout_seconds=60, max_pages=10)
        )
        
        # Setup API error
        mock_run.side_effect = subprocess.CalledProcessError(
            1, ['gh'], stderr='{"message": "Not Found"}'
        )
        
        with pytest.raises(subprocess.CalledProcessError):
            _fetch_commits_with_api('repos/owner/repo/commits', {})
            
    @patch('subprocess.run')
    @patch('hacktivity.core.config.get_config')
    def test_fetch_timeout(self, mock_config, mock_run):
        """Test handling timeouts."""
        # Setup config
        mock_config.return_value = MagicMock(
            github=MagicMock(per_page=100, timeout_seconds=60, max_pages=10)
        )
        
        # Setup timeout
        mock_run.side_effect = subprocess.TimeoutExpired(['gh'], 60)
        
        with pytest.raises(subprocess.TimeoutExpired):
            _fetch_commits_with_api('repos/owner/repo/commits', {})


class TestFetchRepoCommits:
    """Test the main repository commit fetching function."""
    
    @patch('hacktivity.core.config.get_config')
    @patch('hacktivity.core.commits.cache.get')
    def test_fetch_with_cache_hit(self, mock_cache_get, mock_config):
        """Test fetching when cache has valid data."""
        # Setup config with real values for cache initialization
        mock_config.return_value = MagicMock(
            github=MagicMock(retry_attempts=3, retry_min_wait=1, retry_max_wait=10, 
                           timeout_seconds=60, per_page=100, max_pages=10),
            cache=MagicMock(max_size_mb=100, directory=None)
        )
        
        # Setup cache hit
        cached_commits = [
            {'sha': 'cached123', 'message': 'Cached commit', 'author_login': 'user1'}
        ]
        mock_cache_get.return_value = cached_commits
        
        commits_data = fetch_repo_commits('owner/repo', '2024-01-01', '2024-01-31')
        
        assert commits_data == cached_commits
        
        # Verify cache was checked with 365-day TTL (commits are immutable)
        mock_cache_get.assert_called_once_with('commits:owner/repo:2024-01-01:2024-01-31:all', 8760)
        
    @patch('hacktivity.core.config.get_config')
    @patch('hacktivity.core.commits.cache.set')
    @patch('hacktivity.core.commits.cache.get')
    @patch('hacktivity.core.commits._fetch_commits_with_api')
    def test_fetch_without_cache(self, mock_fetch_api, mock_cache_get, mock_cache_set, mock_config):
        """Test fetching when cache miss."""
        # Setup config with real values
        mock_config.return_value = MagicMock(
            github=MagicMock(retry_attempts=3, retry_min_wait=1, retry_max_wait=10,
                           timeout_seconds=60, per_page=100, max_pages=10),
            cache=MagicMock(max_size_mb=100, directory=None)
        )
        
        # Setup cache miss
        mock_cache_get.return_value = None
        
        # Setup API response
        api_commits = [
            {
                'sha': 'abc123',
                'commit': {
                    'message': 'New commit',
                    'author': {'name': 'User', 'email': 'user@example.com', 'date': '2024-01-01T10:00:00Z'},
                    'committer': {'name': 'User', 'email': 'user@example.com', 'date': '2024-01-01T10:00:00Z'}
                },
                'author': {'login': 'user1', 'id': 123},
                'url': 'https://api.github.com/repos/owner/repo/commits/abc123'
            }
        ]
        mock_fetch_api.return_value = api_commits
        
        commits_data = fetch_repo_commits('owner/repo', '2024-01-01', '2024-01-31')
        
        # Should return parsed results
        assert len(commits_data) == 1
        assert commits_data[0]['sha'] == 'abc123'
        assert commits_data[0]['message'] == 'New commit'
        assert commits_data[0]['author_login'] == 'user1'
        
        # Should call API
        mock_fetch_api.assert_called_once()
        
        # Should cache the result
        mock_cache_set.assert_called_once()
        
    @patch('hacktivity.core.config.get_config')
    @patch('hacktivity.core.commits.cache.get')
    @patch('hacktivity.core.commits._fetch_commits_with_api')
    def test_fetch_with_author_filter(self, mock_fetch_api, mock_cache_get, mock_config):
        """Test fetching with author filter."""
        # Setup config with real values
        mock_config.return_value = MagicMock(
            github=MagicMock(retry_attempts=3, retry_min_wait=1, retry_max_wait=10,
                           timeout_seconds=60, per_page=100, max_pages=10),
            cache=MagicMock(max_size_mb=100, directory=None)
        )
        
        # Setup cache miss
        mock_cache_get.return_value = None
        
        # Setup API response with multiple authors
        api_commits = [
            {
                'sha': 'abc123',
                'commit': {
                    'message': 'Commit by user1',
                    'author': {'name': 'User One', 'email': 'user1@example.com', 'date': '2024-01-01T10:00:00Z'},
                    'committer': {'name': 'User One', 'email': 'user1@example.com', 'date': '2024-01-01T10:00:00Z'}
                },
                'author': {'login': 'user1', 'id': 123},
                'url': 'https://api.github.com/repos/owner/repo/commits/abc123'
            },
            {
                'sha': 'def456', 
                'commit': {
                    'message': 'Commit by user2',
                    'author': {'name': 'User Two', 'email': 'user2@example.com', 'date': '2024-01-02T10:00:00Z'},
                    'committer': {'name': 'User Two', 'email': 'user2@example.com', 'date': '2024-01-02T10:00:00Z'}
                },
                'author': {'login': 'user2', 'id': 456},
                'url': 'https://api.github.com/repos/owner/repo/commits/def456'
            }
        ]
        mock_fetch_api.return_value = api_commits
        
        commits_data = fetch_repo_commits('owner/repo', '2024-01-01', '2024-01-31', 'user1')
        
        # Should return only commits by user1
        assert len(commits_data) == 1
        assert commits_data[0]['sha'] == 'abc123'
        assert commits_data[0]['author_login'] == 'user1'
        
        # Should cache with author-specific key
        expected_cache_key = 'commits:owner/repo:2024-01-01:2024-01-31:user1'
        mock_cache_get.assert_called_once_with(expected_cache_key, 8760)
        
    @patch('hacktivity.core.config.get_config')
    @patch('hacktivity.core.commits.cache.get')
    @patch('hacktivity.core.commits._fetch_commits_with_api')
    @patch('sys.exit')
    def test_fetch_api_error(self, mock_exit, mock_fetch_api, mock_cache_get, mock_config):
        """Test handling API errors during fetch."""
        # Setup config with real values
        mock_config.return_value = MagicMock(
            github=MagicMock(retry_attempts=3, retry_min_wait=1, retry_max_wait=10,
                           timeout_seconds=60, per_page=100, max_pages=10),
            cache=MagicMock(max_size_mb=100, directory=None)
        )
        
        # Setup cache miss
        mock_cache_get.return_value = None
        
        # Setup API error
        error_response = json.dumps({'message': 'Repository not found'})
        mock_fetch_api.side_effect = subprocess.CalledProcessError(
            1, ['gh'], stderr=error_response
        )
        
        fetch_repo_commits('owner/repo', '2024-01-01', '2024-01-31')
        
        mock_exit.assert_called_once_with(1)