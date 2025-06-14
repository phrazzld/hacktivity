"""Unit tests for repos module."""

import json
import subprocess
import time
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
    from hacktivity.core import repos
    from hacktivity.core.repos import (
        discover_user_repositories, _fetch_repositories_with_api,
        _generate_repo_cache_key, _parse_repository_data
    )


class TestHelperFunctions:
    """Test helper functions."""
    
    def test_generate_repo_cache_key(self):
        """Test repository cache key generation."""
        # No org filter
        key = _generate_repo_cache_key('user1')
        assert key == 'repos:user1:all'
        
        # With org filter
        key = _generate_repo_cache_key('user1', 'myorg')
        assert key == 'repos:user1:myorg'
        
        # With None org filter (should be treated as 'all')
        key = _generate_repo_cache_key('user1', None)
        assert key == 'repos:user1:all'
        
    def test_parse_repository_data(self):
        """Test parsing repository data from API response."""
        api_repos = [
            {
                'full_name': 'user/repo1',
                'name': 'repo1',
                'owner': {'login': 'user'},
                'private': False,
                'language': 'Python',
                'created_at': '2024-01-01T00:00:00Z',
                'updated_at': '2024-01-15T12:00:00Z',
                'archived': False,
                'fork': False
            },
            {
                'full_name': 'user/repo2',
                'name': 'repo2',
                'owner': {'login': 'user'},
                'private': True,
                'language': 'JavaScript',
                'created_at': '2024-02-01T00:00:00Z',
                'updated_at': '2024-02-15T12:00:00Z',
                'archived': False,
                'fork': True
            }
        ]
        
        parsed = _parse_repository_data(api_repos)
        
        assert len(parsed) == 2
        assert parsed[0]['full_name'] == 'user/repo1'
        assert parsed[0]['private'] is False
        assert parsed[0]['language'] == 'Python'
        assert parsed[1]['full_name'] == 'user/repo2'
        assert parsed[1]['private'] is True
        assert parsed[1]['fork'] is True


class TestFetchRepositoriesWithApi:
    """Test the internal API fetching function."""
    
    @patch('subprocess.run')
    @patch('hacktivity.core.config.get_config')
    def test_fetch_single_page(self, mock_config, mock_run):
        """Test fetching a single page of repositories."""
        # Setup config
        mock_config.return_value = MagicMock(
            github=MagicMock(per_page=100, timeout_seconds=60, max_pages=10)
        )
        
        # Setup API response
        api_response = [
            {
                'full_name': 'user/repo1',
                'name': 'repo1',
                'owner': {'login': 'user'},
                'private': False,
                'language': 'Python',
                'created_at': '2024-01-01T00:00:00Z',
                'updated_at': '2024-01-15T12:00:00Z',
                'archived': False,
                'fork': False
            }
        ]
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(api_response)
        )
        
        repos_data = _fetch_repositories_with_api('user/repos', {'affiliation': 'owner'})
        
        assert len(repos_data) == 1
        assert repos_data[0]['full_name'] == 'user/repo1'
        
        # Verify API call
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert 'gh' == call_args[0]
        assert 'api' == call_args[1]
        assert 'user/repos' in call_args[4]  # Should be in the endpoint parameter
        
    @patch('subprocess.run')
    @patch('hacktivity.core.config.get_config')
    def test_fetch_multiple_pages(self, mock_config, mock_run):
        """Test fetching multiple pages of repositories."""
        # Setup config
        mock_config.return_value = MagicMock(
            github=MagicMock(per_page=2, timeout_seconds=60, max_pages=10)
        )
        
        # Setup API responses
        responses = [
            # Page 1
            [
                {'full_name': 'user/repo1', 'name': 'repo1', 'owner': {'login': 'user'}, 
                 'private': False, 'language': 'Python', 'created_at': '2024-01-01T00:00:00Z',
                 'updated_at': '2024-01-15T12:00:00Z', 'archived': False, 'fork': False},
                {'full_name': 'user/repo2', 'name': 'repo2', 'owner': {'login': 'user'},
                 'private': False, 'language': 'JavaScript', 'created_at': '2024-01-02T00:00:00Z',
                 'updated_at': '2024-01-16T12:00:00Z', 'archived': False, 'fork': False}
            ],
            # Page 2
            [
                {'full_name': 'user/repo3', 'name': 'repo3', 'owner': {'login': 'user'},
                 'private': True, 'language': 'Go', 'created_at': '2024-01-03T00:00:00Z',
                 'updated_at': '2024-01-17T12:00:00Z', 'archived': False, 'fork': False}
            ]
        ]
        
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=json.dumps(resp))
            for resp in responses
        ]
        
        repos_data = _fetch_repositories_with_api('user/repos', {'affiliation': 'owner'})
        
        assert len(repos_data) == 3
        assert repos_data[0]['full_name'] == 'user/repo1'
        assert repos_data[1]['full_name'] == 'user/repo2'
        assert repos_data[2]['full_name'] == 'user/repo3'
        assert mock_run.call_count == 2
        
        # Verify pagination in API calls
        first_call = mock_run.call_args_list[0][0][0]
        second_call = mock_run.call_args_list[1][0][0]
        assert 'page=1' in first_call[4]
        assert 'page=2' in second_call[4]
        
    @patch('subprocess.run')
    @patch('hacktivity.core.config.get_config')
    def test_fetch_empty_response(self, mock_config, mock_run):
        """Test fetching when no repositories are found."""
        # Setup config
        mock_config.return_value = MagicMock(
            github=MagicMock(per_page=100, timeout_seconds=60, max_pages=10)
        )
        
        # Setup empty API response
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='[]'
        )
        
        repos_data = _fetch_repositories_with_api('user/repos', {})
        
        assert repos_data == []
        
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
            _fetch_repositories_with_api('user/repos', {})
            
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
            _fetch_repositories_with_api('user/repos', {})


class TestDiscoverUserRepositories:
    """Test the main repository discovery function."""
    
    @patch('hacktivity.core.config.get_config')
    @patch('hacktivity.core.repos.cache.get')
    @patch('hacktivity.core.repos._fetch_repositories_with_api')
    def test_discover_with_cache_hit(self, mock_fetch_api, mock_cache_get, mock_config):
        """Test discovering repositories when cache has valid data."""
        # Setup config with real values for cache initialization
        mock_config.return_value = MagicMock(
            github=MagicMock(retry_attempts=3, retry_min_wait=1, retry_max_wait=10),
            cache=MagicMock(max_size_mb=100, directory=None)
        )
        
        # Setup cache hit
        cached_repos = [
            {'full_name': 'user/cached-repo', 'name': 'cached-repo', 'owner': {'login': 'user'}}
        ]
        mock_cache_get.return_value = cached_repos
        
        repos_data = discover_user_repositories('user1')
        
        assert repos_data == cached_repos
        mock_fetch_api.assert_not_called()
        
        # Verify cache was checked with 7-day TTL (168 hours)
        mock_cache.get.assert_called_once_with('repos:user1:all', 168)
        
    @patch('hacktivity.core.config.get_config')
    @patch('hacktivity.core.repos.cache')
    @patch('hacktivity.core.repos._fetch_repositories_with_api')
    def test_discover_without_cache(self, mock_fetch_api, mock_cache, mock_config):
        """Test discovering repositories when cache miss."""
        # Setup config with real values for cache initialization
        mock_config.return_value = MagicMock(
            github=MagicMock(retry_attempts=3, retry_min_wait=1, retry_max_wait=10),
            cache=MagicMock(max_size_mb=100, directory=None)
        )
        
        # Setup cache miss
        mock_cache.get.return_value = None
        
        # Setup API responses
        user_repos = [
            {'full_name': 'user/repo1', 'name': 'repo1', 'owner': {'login': 'user'}}
        ]
        collaborator_repos = [
            {'full_name': 'other/repo2', 'name': 'repo2', 'owner': {'login': 'other'}}
        ]
        
        mock_fetch_api.side_effect = [user_repos, collaborator_repos]
        
        repos_data = discover_user_repositories('user1')
        
        # Should return combined results
        expected = user_repos + collaborator_repos
        assert repos_data == expected
        
        # Should call API twice (owned + collaborator)
        assert mock_fetch_api.call_count == 2
        
        # Should cache the result
        mock_cache.set.assert_called_once_with('repos:user1:all', expected)
        
    @patch('hacktivity.core.config.get_config')
    @patch('hacktivity.core.repos.cache')
    @patch('hacktivity.core.repos._fetch_repositories_with_api')
    def test_discover_with_org_filter(self, mock_fetch_api, mock_cache, mock_config):
        """Test discovering repositories with organization filter."""
        # Setup config with real values for cache initialization
        mock_config.return_value = MagicMock(
            github=MagicMock(retry_attempts=3, retry_min_wait=1, retry_max_wait=10),
            cache=MagicMock(max_size_mb=100, directory=None)
        )
        
        # Setup cache miss
        mock_cache.get.return_value = None
        
        # Setup API response
        org_repos = [
            {'full_name': 'myorg/repo1', 'name': 'repo1', 'owner': {'login': 'myorg'}}
        ]
        mock_fetch_api.return_value = org_repos
        
        repos_data = discover_user_repositories('user1', org_filter='myorg')
        
        assert repos_data == org_repos
        
        # Should only call API once for organization repos
        mock_fetch_api.assert_called_once_with('orgs/myorg/repos', {'type': 'all'})
        
        # Should cache with org-specific key
        mock_cache.set.assert_called_once_with('repos:user1:myorg', org_repos)
        
    @patch('hacktivity.core.config.get_config')
    @patch('hacktivity.core.repos.cache')
    @patch('hacktivity.core.repos._fetch_repositories_with_api')
    def test_discover_empty_results(self, mock_fetch_api, mock_cache, mock_config):
        """Test discovering when no repositories are found."""
        # Setup config with real values for cache initialization
        mock_config.return_value = MagicMock(
            github=MagicMock(retry_attempts=3, retry_min_wait=1, retry_max_wait=10),
            cache=MagicMock(max_size_mb=100, directory=None)
        )
        
        # Setup cache miss
        mock_cache.get.return_value = None
        
        # Setup empty API responses
        mock_fetch_api.return_value = []
        
        repos_data = discover_user_repositories('user1')
        
        assert repos_data == []
        
        # Should still cache empty results
        mock_cache.set.assert_called_once_with('repos:user1:all', [])
        
    @patch('hacktivity.core.config.get_config')
    @patch('hacktivity.core.repos.cache')
    @patch('hacktivity.core.repos._fetch_repositories_with_api')
    @patch('sys.exit')
    def test_discover_api_error(self, mock_exit, mock_fetch_api, mock_cache, mock_config):
        """Test handling API errors during discovery."""
        # Setup config with real values for cache initialization
        mock_config.return_value = MagicMock(
            github=MagicMock(retry_attempts=3, retry_min_wait=1, retry_max_wait=10),
            cache=MagicMock(max_size_mb=100, directory=None)
        )
        
        # Setup cache miss
        mock_cache.get.return_value = None
        
        # Setup API error
        error_response = json.dumps({'message': 'API rate limit exceeded'})
        mock_fetch_api.side_effect = subprocess.CalledProcessError(
            1, ['gh'], stderr=error_response
        )
        
        discover_user_repositories('user1')
        
        mock_exit.assert_called_once_with(1)
        
    @patch('hacktivity.core.config.get_config')
    @patch('hacktivity.core.repos.cache')
    @patch('hacktivity.core.repos._fetch_repositories_with_api')
    def test_discover_partial_failure(self, mock_fetch_api, mock_cache, mock_config):
        """Test handling when one API call fails but another succeeds."""
        # Setup config with real values for cache initialization
        mock_config.return_value = MagicMock(
            github=MagicMock(retry_attempts=3, retry_min_wait=1, retry_max_wait=10),
            cache=MagicMock(max_size_mb=100, directory=None)
        )
        
        # Setup cache miss
        mock_cache.get.return_value = None
        
        # Setup mixed responses - first succeeds, second fails
        user_repos = [
            {'full_name': 'user/repo1', 'name': 'repo1', 'owner': {'login': 'user'}}
        ]
        mock_fetch_api.side_effect = [
            user_repos,
            subprocess.CalledProcessError(1, ['gh'], stderr='{"message": "Forbidden"}')
        ]
        
        # For simplicity, we'll expect this to still exit on error
        # In the real implementation, we might want to return partial results
        with pytest.raises(subprocess.CalledProcessError):
            discover_user_repositories('user1')


class TestRepositoryFiltering:
    """Test repository filtering functionality."""
    
    def test_parse_repository_data_filtering(self):
        """Test that archived and certain fork repositories might be filtered."""
        api_repos = [
            {
                'full_name': 'user/active-repo',
                'name': 'active-repo',
                'owner': {'login': 'user'},
                'private': False,
                'language': 'Python',
                'created_at': '2024-01-01T00:00:00Z',
                'updated_at': '2024-01-15T12:00:00Z',
                'archived': False,
                'fork': False
            },
            {
                'full_name': 'user/archived-repo',
                'name': 'archived-repo', 
                'owner': {'login': 'user'},
                'private': False,
                'language': 'Python',
                'created_at': '2024-01-01T00:00:00Z',
                'updated_at': '2024-01-15T12:00:00Z',
                'archived': True,  # This might be filtered
                'fork': False
            }
        ]
        
        parsed = _parse_repository_data(api_repos)
        
        # For now, we include all repos, but this tests the data structure
        assert len(parsed) == 2
        assert parsed[0]['archived'] is False
        assert parsed[1]['archived'] is True