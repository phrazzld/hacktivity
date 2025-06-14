Let's implement parallel repository processing for the hacktivity project as outlined in ticket T025. This will involve creating a new `parallel.py` module, updating configuration settings, and integrating with existing components for thread-safe operations and progress tracking.

I'll provide the implementation for the core components needed to achieve parallel processing while respecting the constraints and requirements specified in the instructions.

### 1. Configuration Extension
First, let's extend the configuration to include parallel processing settings as suggested.

**File: `hacktivity/core/config.py`**
```python
# Add to existing imports if needed
class GitHubConfig(BaseModel):
    """GitHub API configuration settings."""
    per_page: int = Field(default=100, ge=1, le=100, description="Items per API page")
    timeout_seconds: int = Field(default=60, ge=10, le=300, description="Request timeout in seconds")
    max_pages: int = Field(default=10, ge=1, le=20, description="Maximum pages to fetch")
    retry_attempts: int = Field(default=3, ge=1, le=10, description="Number of retry attempts")
    retry_min_wait: int = Field(default=4, ge=1, le=60, description="Minimum retry wait in seconds")
    retry_max_wait: int = Field(default=10, ge=1, le=300, description="Maximum retry wait in seconds")
    # Circuit Breaker Configuration
    cb_failure_threshold: int = Field(
        default=5, ge=1, le=20,
        description="Consecutive failures before opening the circuit."
    )
    cb_cooldown_sec: int = Field(
        default=60, ge=10, le=600,
        description="Seconds to wait in OPEN state before transitioning to HALF_OPEN."
    )
    max_workers: int = Field(default=4, ge=1, le=10, description="Max parallel workers")
    rate_limit_buffer: int = Field(default=100, ge=50, le=500, description="API calls to reserve as buffer")
    parallel_enabled: bool = Field(default=True, description="Enable parallel processing")
```

Update the default configuration template in `save_default_config()` function to include these new fields.

**File: `hacktivity/core/config.py` (inside `save_default_config` function)**
```python
default_config_toml = '''# Hacktivity Configuration File
# This file controls various aspects of hacktivity behavior.
# All settings are optional - if omitted, sensible defaults will be used.

[cache]
# Cache settings
max_age_hours = 24      # How long to keep cached results (1-168 hours)
max_size_mb = 100       # Maximum cache size in MB (10-1000)
# directory = "/custom/cache/path"  # Uncomment to override default cache location

[github]
# GitHub API settings
per_page = 100          # Items per API page (1-100)
timeout_seconds = 60    # Request timeout (10-300 seconds)
max_pages = 10          # Maximum pages to fetch (1-20)
retry_attempts = 3      # Number of retry attempts (1-10)
retry_min_wait = 4      # Minimum retry wait (1-60 seconds)
retry_max_wait = 10     # Maximum retry wait (1-300 seconds)
max_workers = 4         # Max parallel workers for processing (1-10)
rate_limit_buffer = 100 # API calls to reserve as buffer (50-500)
parallel_enabled = true # Enable parallel processing

[ai]
# AI model settings
model_name = "gemini-1.5-flash"  # AI model to use

[app]
# Application settings
log_level = "INFO"              # Log level: DEBUG, INFO, WARNING, ERROR
default_prompt_type = "standup" # Default prompt type: standup, retro, weekly
default_format = "markdown"     # Default output format: markdown, json, plain
'''
```

### 2. Rate Limiting Coordinator
Let's create a new file for rate limiting coordination to ensure we respect GitHub API limits across parallel workers.

**File: `hacktivity/core/rate_limiter.py`**
```python
"""Rate limiting coordinator for GitHub API calls across parallel workers."""

import threading
import time
from typing import Optional

from .config import get_config
from .logging import get_logger

logger = get_logger(__name__)

class RateLimitCoordinator:
    """Coordinates rate limiting for GitHub API calls across multiple threads."""
    
    def __init__(self):
        config = get_config()
        # GitHub rate limit is typically 5000 requests/hour for authenticated users
        self.rate_limit_per_hour = 5000 - config.github.rate_limit_buffer
        self.requests_per_hour = 0
        self.lock = threading.Lock()
        self.reset_time = time.time() + 3600  # Reset every hour
        logger.info("Initialized rate limit coordinator with limit of %d requests/hour", self.rate_limit_per_hour)
    
    def acquire(self, timeout: Optional[float] = None) -> bool:
        """Attempt to acquire a slot for an API call.
        
        Args:
            timeout: Maximum time to wait for a slot in seconds, or None for no wait.
            
        Returns:
            bool: True if slot acquired, False if timed out or rate limit exceeded.
        """
        start_time = time.time()
        while True:
            with self.lock:
                current_time = time.time()
                # Reset counter if an hour has passed
                if current_time >= self.reset_time:
                    self.requests_per_hour = 0
                    self.reset_time = current_time + 3600
                    logger.info("Rate limit counter reset for new hour")
                
                if self.requests_per_hour < self.rate_limit_per_hour:
                    self.requests_per_hour += 1
                    logger.debug("API slot acquired, request count: %d/%d", 
                                self.requests_per_hour, self.rate_limit_per_hour)
                    return True
                
                # Rate limit exceeded, calculate wait time until reset
                wait_time = self.reset_time - current_time
                if timeout is not None and (time.time() - start_time + wait_time) > timeout:
                    logger.warning("Rate limit wait timed out after %.2f seconds", timeout)
                    return False
                
                # Backpressure: wait until reset or timeout
                logger.warning("Rate limit reached (%d/%d). Waiting for reset in %.2f seconds", 
                              self.requests_per_hour, self.rate_limit_per_hour, wait_time)
                if timeout is None or wait_time < timeout:
                    time.sleep(wait_time)
                else:
                    return False
    
    def get_remaining_requests(self) -> int:
        """Get the number of remaining requests before hitting the rate limit.
        
        Returns:
            int: Number of remaining requests.
        """
        with self.lock:
            current_time = time.time()
            if current_time >= self.reset_time:
                self.requests_per_hour = 0
                self.reset_time = current_time + 3600
            return self.rate_limit_per_hour - self.requests_per_hour
    
    def get_time_until_reset(self) -> float:
        """Get the time in seconds until the rate limit resets.
        
        Returns:
            float: Seconds until reset.
        """
        with self.lock:
            return max(0, self.reset_time - time.time())


# Global instance for rate limiting coordinator
_rate_limit_coordinator: Optional[RateLimitCoordinator] = None
_rate_limit_lock = threading.Lock()


def get_rate_limit_coordinator() -> RateLimitCoordinator:
    """Get the global rate limit coordinator instance."""
    global _rate_limit_coordinator
    with _rate_limit_lock:
        if _rate_limit_coordinator is None:
            _rate_limit_coordinator = RateLimitCoordinator()
        return _rate_limit_coordinator
```

### 3. Parallel Processing Orchestrator
Now, let's create the core parallel processing logic in a new file.

**File: `hacktivity/core/parallel.py`**
```python
"""Parallel processing orchestrator for repository operations."""

import threading
import queue
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from rich.progress import Progress, TaskID

from .chunking import fetch_repo_commits_chunked
from .config import get_config
from .logging import get_logger
from .rate_limiter import get_rate_limit_coordinator
from .state import track_repository_progress

logger = get_logger(__name__)

class ProgressAggregator:
    """Thread-safe progress aggregation for parallel repository processing."""
    
    def __init__(self, total_repos: int):
        self.total_repos = total_repos
        self.completed_repos = 0
        self.total_commits = 0
        self.lock = threading.Lock()
        self.progress = Progress()
        self.task_id: Optional[TaskID] = None
        
    def start(self):
        """Start the progress bar."""
        self.task_id = self.progress.add_task("[cyan]Processing repositories...", total=self.total_repos)
        self.progress.start()
        
    def stop(self):
        """Stop the progress bar."""
        self.progress.stop()
        
    def update_repo_progress(self, repo_name: str, status: str, commit_count: int = 0):
        """Update progress for a repository."""
        with self.lock:
            if status == 'completed':
                self.completed_repos += 1
                self.total_commits += commit_count
                self.progress.update(self.task_id, advance=1, description=f"[cyan]Processed {self.completed_repos}/{self.total_repos} repos ({self.total_commits} commits)")
            elif status == 'failed':
                self.completed_repos += 1
                self.progress.update(self.task_id, advance=1, description=f"[red]Processed {self.completed_repos}/{self.total_repos} repos (some failures)")
            logger.debug("Progress update: %s is %s, total completed: %d/%d", repo_name, status, self.completed_repos, self.total_repos)

class RepositoryWorker:
    """Worker class for processing a single repository in a separate thread."""
    
    def __init__(self, rate_limiter, since: str, until: str, author_filter: Optional[str], max_days: int, operation_id: Optional[str], progress: ProgressAggregator):
        self.rate_limiter = rate_limiter
        self.since = since
        self.until = until
        self.author_filter = author_filter
        self.max_days = max_days
        self.operation_id = operation_id
        self.progress = progress
        
    def process_repository(self, repo_full_name: str) -> Dict[str, Any]:
        """Process a single repository with rate limiting.
        
        Args:
            repo_full_name: Full name of the repository (e.g., 'owner/repo-name')
            
        Returns:
            Dict with repository name and list of commits
        """
        try:
            logger.info("Starting processing of repository: %s", repo_full_name)
            if self.operation_id:
                track_repository_progress(self.operation_id, repo_full_name, 'in_progress')
            
            # Acquire rate limit slot before proceeding
            if not self.rate_limiter.acquire(timeout=300):  # 5 minutes timeout
                raise RuntimeError("Failed to acquire rate limit slot within timeout")
                
            commits = fetch_repo_commits_chunked(
                repo_full_name,
                self.since,
                self.until,
                self.author_filter,
                self.max_days,
                self.operation_id
            )
            
            self.progress.update_repo_progress(repo_full_name, 'completed', len(commits))
            return {'repo': repo_full_name, 'commits': commits, 'status': 'success'}
            
        except Exception as e:
            logger.error("Error processing repository %s: %s", repo_full_name, str(e))
            if self.operation_id:
                track_repository_progress(self.operation_id, repo_full_name, 'failed', error_message=str(e))
            self.progress.update_repo_progress(repo_full_name, 'failed')
            return {'repo': repo_full_name, 'commits': [], 'status': 'failed', 'error': str(e)}

def fetch_commits_parallel(repositories: List[str], since: str, until: str, author_filter: Optional[str] = None, operation_id: Optional[str] = None) -> Dict[str, List[Dict[str, Any]]]:
    """Fetch commits from multiple repositories in parallel.
    
    Args:
        repositories: List of repository full names to process
        since: Start date in YYYY-MM-DD format
        until: End date in YYYY-MM-DD format
        author_filter: Optional GitHub username to filter commits by
        operation_id: Optional operation ID for state tracking
        
    Returns:
        Dictionary mapping repository names to their commit lists
    """
    config = get_config()
    rate_limiter = get_rate_limit_coordinator()
    max_workers = config.github.max_workers if config.github.parallel_enabled else 1
    
    progress = ProgressAggregator(len(repositories))
    worker = RepositoryWorker(rate_limiter, since, until, author_filter, 7, operation_id, progress)
    
    all_results = {}
    futures = []
    
    logger.info("Starting parallel processing of %d repositories with %d workers", len(repositories), max_workers)
    progress.start()
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for repo in repositories:
            future = executor.submit(worker.process_repository, repo)
            futures.append(future)
        
        for future in as_completed(futures):
            result = future.result()
            all_results[result['repo']] = result['commits']
            if result['status'] == 'failed':
                logger.error("Repository %s failed: %s", result['repo'], result.get('error', 'Unknown error'))
    
    progress.stop()
    logger.info("Completed parallel processing of %d repositories", len(repositories))
    return all_results
```

### 4. Main CLI Integration
Update the main CLI to support parallel processing with a fallback to sequential processing if disabled.

**File: `hacktivity/__main__.py` (Update in the `summary` function or related areas)**
```python
# Inside the summary function, after fetching repositories, replace the call to fetch commits
# with the parallel version if enabled in config

from hacktivity.core.parallel import fetch_commits_parallel
from hacktivity.core.commits import fetch_commits_from_multiple_repos

# After repository discovery (assuming repos are discovered in github.py or similar)
config = get_config()
if config.github.parallel_enabled:
    repo_commits = fetch_commits_parallel(repos, since, until, github_user, operation_id=operation_id)
else:
    repo_commits = fetch_commits_from_multiple_repos(repos, since, until, github_user)
```

### 5. Update `github.py` to Use Parallel Fetching
Ensure the `fetch_commits` function in `github.py` uses the parallel processing if enabled.

**File: `hacktivity/core/github.py`**
```python
def fetch_commits(user: str, since: str, until: str, org: Optional[str] = None, repo: Optional[str] = None) -> List[Dict[str, Any]]:
    """Fetch commits for the user within the specified date range.
    
    Args:
        user: GitHub username
        since: Start date in YYYY-MM-DD format
        until: End date in YYYY-MM-DD format
        org: Optional organization filter
        repo: Optional specific repository filter
        
    Returns:
        List of commit dictionaries
    """
    from .repos import discover_user_repositories, filter_repositories_by_activity
    from .parallel import fetch_commits_parallel
    from .commits import fetch_commits_from_multiple_repos, aggregate_commits_by_author
    from .state import create_operation
    from .config import get_config
    
    config = get_config()
    
    # Create operation for state tracking
    operation_id = create_operation('summary', user, since, until, author_filter=user, org_filter=org, repo_filter=repo)
    
    # Discover repositories
    repos = discover_user_repositories(user, org)
    repos = filter_repositories_by_activity(repos, since, until)
    
    if repo:
        repos = [r for r in repos if r['full_name'] == repo]
        if not repos:
            logger.warning("Specified repository %s not found or inaccessible", repo)
            return []
    
    logger.info("Processing %d repositories for activity", len(repos))
    repo_names = [r['full_name'] for r in repos]
    
    # Use parallel processing if enabled
    if config.github.parallel_enabled:
        repo_commits = fetch_commits_parallel(repo_names, since, until, user, operation_id)
    else:
        repo_commits = fetch_commits_from_multiple_repos(repo_names, since, until, user)
    
    # Aggregate commits
    all_commits = aggregate_commits_by_author(repo_commits, user)
    return all_commits
```

This implementation provides a robust foundation for parallel repository processing while respecting GitHub rate limits, ensuring thread safety with SQLite state management, and providing real-time progress tracking using the Rich library. It integrates seamlessly with existing components and maintains backward compatibility by allowing sequential processing if parallel processing is disabled in the configuration.