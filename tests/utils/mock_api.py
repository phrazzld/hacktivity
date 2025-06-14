"""A sophisticated, stateful mock for the GitHub API via subprocess.run."""
import json
import re
import time
from unittest.mock import Mock
from typing import Dict, List, Any, Optional, Union, Callable
import subprocess

from ..fixtures.large_repo_dataset import generate_repo_data


class MockAPI:
    """
    Sophisticated mock for GitHub API calls through subprocess.run.
    
    Supports:
    - Repository discovery and commit fetching
    - Pagination with configurable page sizes
    - Failure injection for specific repositories/endpoints
    - API call counting for efficiency testing
    - Rate limiting simulation
    - Realistic response timing
    """
    
    def __init__(self, commit_map: Dict[str, List[Dict[str, Any]]], repo_list: Optional[List[Dict[str, Any]]] = None):
        """
        Initialize the mock API.
        
        Args:
            commit_map: Dictionary mapping repository names to commit lists
            repo_list: Optional list of repository data for discovery endpoints
        """
        self.commit_map = commit_map
        self.repo_list = repo_list or self._generate_repo_list()
        self.api_call_count = 0
        self.failure_configs = {}
        self.rate_limit_remaining = 5000
        self.rate_limit_reset_time = int(time.time()) + 3600
        self.response_delay = 0.0  # Simulate network latency
        
        # Track API calls by endpoint for analysis
        self.call_history = []
        
    def _generate_repo_list(self) -> List[Dict[str, Any]]:
        """Generate repository list from commit map."""
        from datetime import datetime
        repos = []
        for repo_name in self.commit_map.keys():
            repo_data = generate_repo_data(repo_name, datetime.now())
            repos.append(repo_data)
        return repos
    
    def set_failure(self, repo_name_pattern: str, exception_type: type, count: int = 1, 
                   endpoint_pattern: str = None):
        """
        Configure an endpoint for a specific repo to fail.
        
        Args:
            repo_name_pattern: Regex pattern to match repository names
            exception_type: Exception type to raise
            count: Number of times to fail (remaining count)
            endpoint_pattern: Optional endpoint pattern to match
        """
        key = f"{repo_name_pattern}:{endpoint_pattern or 'any'}"
        self.failure_configs[key] = {
            "exception_type": exception_type,
            "remaining": count,
            "repo_pattern": repo_name_pattern,
            "endpoint_pattern": endpoint_pattern
        }
    
    def set_rate_limit(self, remaining: int, reset_time: Optional[int] = None):
        """Set current rate limit status."""
        self.rate_limit_remaining = remaining
        if reset_time:
            self.rate_limit_reset_time = reset_time
    
    def set_response_delay(self, delay_seconds: float):
        """Set artificial delay for all API responses."""
        self.response_delay = delay_seconds
    
    def _should_fail(self, repo_name: str, endpoint: str) -> Optional[Exception]:
        """Check if this call should fail based on configured failures."""
        for key, config in self.failure_configs.items():
            repo_pattern, endpoint_pattern = key.split(':', 1)
            
            # Check if repository matches
            if not re.search(repo_pattern, repo_name):
                continue
                
            # Check if endpoint matches (if specified)
            if endpoint_pattern != 'any' and not re.search(endpoint_pattern, endpoint):
                continue
                
            # Check if we still have failures remaining
            if config["remaining"] > 0:
                config["remaining"] -= 1
                
                # Create the appropriate exception
                if config["exception_type"] == subprocess.TimeoutExpired:
                    return subprocess.TimeoutExpired(['gh'], 30)
                elif config["exception_type"] == subprocess.CalledProcessError:
                    return subprocess.CalledProcessError(1, ['gh'], stderr="API Error")
                else:
                    return config["exception_type"]("Simulated failure")
        
        return None
    
    def _handle_repo_discovery(self, endpoint: str) -> Dict[str, Any]:
        """Handle repository discovery endpoints."""
        # Parse pagination parameters
        page_match = re.search(r'page=(\d+)', endpoint)
        per_page_match = re.search(r'per_page=(\d+)', endpoint)
        
        page = int(page_match.group(1)) if page_match else 1
        per_page = int(per_page_match.group(1)) if per_page_match else 30
        
        # Calculate pagination
        start_index = (page - 1) * per_page
        end_index = start_index + per_page
        
        # Return paginated repository list
        response_data = self.repo_list[start_index:end_index]
        return response_data
    
    def _handle_commit_fetching(self, endpoint: str, repo_name: str) -> List[Dict[str, Any]]:
        """Handle commit fetching endpoints."""
        # Parse pagination and date parameters
        page_match = re.search(r'page=(\d+)', endpoint)
        per_page_match = re.search(r'per_page=(\d+)', endpoint)
        since_match = re.search(r'since=([^&]+)', endpoint)
        until_match = re.search(r'until=([^&]+)', endpoint)
        
        page = int(page_match.group(1)) if page_match else 1
        per_page = int(per_page_match.group(1)) if per_page_match else 30
        
        # Get commits for this repository
        repo_commits = self.commit_map.get(repo_name, [])
        
        # Apply date filtering if specified
        if since_match or until_match:
            filtered_commits = []
            for commit in repo_commits:
                commit_date = commit.get('commit_date', '')
                
                # Simple date filtering (in real implementation, would parse ISO dates)
                if since_match and commit_date < since_match.group(1):
                    continue
                if until_match and commit_date > until_match.group(1):
                    continue
                    
                filtered_commits.append(commit)
            repo_commits = filtered_commits
        
        # Apply pagination
        start_index = (page - 1) * per_page
        end_index = start_index + per_page
        
        return repo_commits[start_index:end_index]
    
    def mock_subprocess_run(self, command: List[str], *args, **kwargs) -> Mock:
        """
        The main function to patch subprocess.run with.
        
        Args:
            command: The command being executed
            *args: Additional arguments
            **kwargs: Additional keyword arguments
            
        Returns:
            Mock result object with stdout/stderr
        """
        if self.response_delay > 0:
            time.sleep(self.response_delay)
        
        self.api_call_count += 1
        self.rate_limit_remaining = max(0, self.rate_limit_remaining - 1)
        
        # Extract endpoint from command
        if len(command) < 4 or command[0] != 'gh' or command[1] != 'api':
            raise ValueError(f"Unexpected command format: {command}")
        
        endpoint = command[3]
        self.call_history.append({
            'endpoint': endpoint,
            'timestamp': time.time(),
            'rate_limit_remaining': self.rate_limit_remaining
        })
        
        # Check rate limiting
        if self.rate_limit_remaining <= 0:
            error_response = {
                'message': 'API rate limit exceeded',
                'rate': {
                    'limit': 5000,
                    'remaining': 0,
                    'reset': self.rate_limit_reset_time
                }
            }
            raise subprocess.CalledProcessError(
                1, command, stderr=json.dumps(error_response)
            )
        
        # Determine response based on endpoint type
        response_data = []
        repo_name = None
        
        # Repository discovery endpoints
        if re.search(r'(user/repos|orgs/.+/repos)', endpoint):
            response_data = self._handle_repo_discovery(endpoint)
        
        # Commit fetching endpoints
        elif re.search(r'repos/([^/]+/[^/]+)/commits', endpoint):
            repo_match = re.search(r'repos/([^/]+/[^/]+)/commits', endpoint)
            repo_name = repo_match.group(1)
            
            # Check for configured failures
            failure = self._should_fail(repo_name, endpoint)
            if failure:
                raise failure
            
            response_data = self._handle_commit_fetching(endpoint, repo_name)
        
        # Search endpoints
        elif re.search(r'search/commits', endpoint):
            # Extract query parameters for search
            query_match = re.search(r'q=([^&]+)', endpoint)
            if query_match:
                # Simple query parsing for author and repo filters
                query = query_match.group(1)
                
                # Aggregate results from matching repositories
                for repo, commits in self.commit_map.items():
                    if f"repo:{repo}" in query or "repo:" not in query:
                        response_data.extend(commits[:10])  # Limit for search
            
            # Wrap in search response format
            response_data = {
                'total_count': len(response_data),
                'items': [{'commit': {'message': c.get('commit', {}).get('message', '')}} for c in response_data]
            }
        
        # User info endpoint
        elif endpoint == 'user':
            response_data = {
                'login': 'test-user',
                'id': 12345,
                'name': 'Test User'
            }
        
        # Create mock result
        mock_result = Mock()
        mock_result.stdout = json.dumps(response_data)
        mock_result.stderr = ""
        mock_result.returncode = 0
        
        return mock_result
    
    def get_call_statistics(self) -> Dict[str, Any]:
        """Get statistics about API calls made."""
        endpoint_counts = {}
        for call in self.call_history:
            endpoint = call['endpoint']
            # Normalize endpoint for counting
            normalized = re.sub(r'/repos/[^/]+/[^/]+/', '/repos/{owner}/{repo}/', endpoint)
            normalized = re.sub(r'page=\d+', 'page={n}', normalized)
            normalized = re.sub(r'per_page=\d+', 'per_page={n}', normalized)
            
            endpoint_counts[normalized] = endpoint_counts.get(normalized, 0) + 1
        
        return {
            'total_calls': self.api_call_count,
            'rate_limit_remaining': self.rate_limit_remaining,
            'endpoint_counts': endpoint_counts,
            'call_history': self.call_history
        }
    
    def reset_stats(self):
        """Reset call statistics."""
        self.api_call_count = 0
        self.call_history = []
        self.rate_limit_remaining = 5000


class SimulatedInterruption(Exception):
    """Custom exception for controlled interruption simulation."""
    pass


class MockAPIBuilder:
    """Builder class for creating configured MockAPI instances."""
    
    @staticmethod
    def for_large_scale_test(num_repos: int = 100, commits_per_repo: int = 100) -> MockAPI:
        """Create MockAPI configured for large-scale testing."""
        from ..fixtures.large_repo_dataset import create_large_dataset
        
        commit_map = create_large_dataset(num_repos, commits_per_repo)
        return MockAPI(commit_map)
    
    @staticmethod
    def for_interruption_test(num_repos: int = 10, commits_per_repo: int = 50, 
                            interrupt_repo_index: int = 4) -> MockAPI:
        """Create MockAPI configured for interruption testing."""
        from ..fixtures.large_repo_dataset import create_large_dataset
        
        commit_map = create_large_dataset(num_repos, commits_per_repo)
        repo_names = list(commit_map.keys())
        
        mock_api = MockAPI(commit_map)
        
        # Configure one repository to fail
        if interrupt_repo_index < len(repo_names):
            failing_repo = repo_names[interrupt_repo_index]
            mock_api.set_failure(failing_repo, SimulatedInterruption, count=1)
        
        return mock_api
    
    @staticmethod
    def for_circuit_breaker_test(num_repos: int = 5, commits_per_repo: int = 10,
                               failing_repo_index: int = 2) -> MockAPI:
        """Create MockAPI configured for circuit breaker testing."""
        from ..fixtures.large_repo_dataset import create_large_dataset
        
        commit_map = create_large_dataset(num_repos, commits_per_repo)
        repo_names = list(commit_map.keys())
        
        mock_api = MockAPI(commit_map)
        
        # Configure one repository to consistently fail
        if failing_repo_index < len(repo_names):
            failing_repo = repo_names[failing_repo_index]
            mock_api.set_failure(failing_repo, subprocess.TimeoutExpired, count=100)
        
        return mock_api
    
    @staticmethod  
    def for_uneven_workload_test() -> MockAPI:
        """Create MockAPI with uneven repository workloads."""
        from ..fixtures.large_repo_dataset import create_uneven_workload_dataset
        
        commit_map = create_uneven_workload_dataset()
        return MockAPI(commit_map)