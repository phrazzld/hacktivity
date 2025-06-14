To implement parallel repository processing, we'll follow the detailed plan:

1.  **Extend Configuration**: Add parallel processing settings to `hacktivity/core/config.py`.
2.  **Create Rate Limiting Coordinator**: Implement `hacktivity/core/rate_limiter.py` to manage GitHub API rate limits across threads.
3.  **Create Parallel Processing Orchestrator**: Implement `hacktivity/core/parallel.py` to manage worker threads, distribute tasks, and aggregate progress.
4.  **Integrate with Main CLI**: Update `hacktivity/__main__.py` to use the new parallel processing entry point.
5.  **Refine Progress Tracking**: Ensure `state.py` and the new orchestrator work together for aggregate progress.

---

### 1. Extend Configuration (`hacktivity/core/config.py`)

We'll add `max_workers`, `rate_limit_buffer`, and `parallel_enabled` to the `GitHubConfig` model.

**`hacktivity/core/config.py`**
```python
"""Configuration management module."""

import os
import sys
from pathlib import Path
from typing import Optional

try:
    import tomllib  # Python 3.11+
except ImportError:
    try:
        import tomli as tomllib  # Fallback for older Python versions
    except ImportError:
        from .logging import get_logger
        logger = get_logger(__name__)
        logger.error("TOML support requires Python 3.11+ or 'tomli' package. Please install with 'pip install tomli'")
        sys.exit(1)

from pydantic import BaseModel, Field

from .logging import get_logger

logger = get_logger(__name__)


class CacheConfig(BaseModel):
    """Cache configuration settings."""
    max_age_hours: int = Field(default=24, ge=1, le=168, description="Cache TTL in hours")
    max_size_mb: int = Field(default=100, ge=10, le=1000, description="Cache size limit in MB")
    directory: Optional[str] = Field(default=None, description="Cache directory path (None for default)")


class GitHubConfig(BaseModel):
    """GitHub API configuration settings."""
    per_page: int = Field(default=100, ge=1, le=100, description="Items per API page")
    timeout_seconds: int = Field(default=60, ge=10, le=300, description="Request timeout in seconds")
    max_pages: int = Field(default=10, ge=1, le=20, description="Maximum pages to fetch")
    retry_attempts: int = Field(default=3, ge=1, le=10, description="Number of retry attempts")
    retry_min_wait: int = Field(default=4, ge=1, le=60, description="Minimum retry wait in seconds")
    retry_max_wait = Field(default=10, ge=1, le=300, description="Maximum retry wait in seconds")
    
    # Circuit Breaker Configuration
    cb_failure_threshold: int = Field(
        default=5, ge=1, le=20,
        description="Consecutive failures before opening the circuit."
    )
    cb_cooldown_sec: int = Field(
        default=60, ge=10, le=600,
        description="Seconds to wait in OPEN state before transitioning to HALF_OPEN."
    )

    # Parallel Processing Configuration (New)
    max_workers: int = Field(default=4, ge=1, le=10, description="Max parallel workers for repository processing")
    rate_limit_buffer: int = Field(default=100, ge=50, le=500, description="API calls to reserve as buffer before slowing down")
    parallel_enabled: bool = Field(default=True, description="Enable parallel repository processing")


class AIConfig(BaseModel):
    """AI model configuration settings."""
    model_name: str = Field(default="gemini-1.5-flash", description="AI model name")


class AppConfig(BaseModel):
    """Application-wide configuration settings."""
    log_level: str = Field(default="INFO", description="Log level (DEBUG, INFO, WARNING, ERROR)")
    default_prompt_type: str = Field(default="standup", description="Default prompt type")
    default_format: str = Field(default="markdown", description="Default output format (markdown, json, plain)")


class Config(BaseModel):
    """Root configuration model."""
    cache: CacheConfig = Field(default_factory=CacheConfig)
    github: GitHubConfig = Field(default_factory=GitHubConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    app: AppConfig = Field(default_factory=AppConfig)


def get_config_path() -> Path:
    """Get the path to the configuration file."""
    return Path.home() / ".hacktivity" / "config.toml"


def load_config() -> Config:
    """Load configuration from file with fallback to defaults.
    
    Returns:
        Config: Loaded configuration with defaults for missing values
    """
    config_path = get_config_path()
    
    if not config_path.exists():
        logger.info("No config file found at %s, using defaults", config_path)
        return Config()
    
    try:
        with open(config_path, 'rb') as f:
            config_data = tomllib.load(f)
        
        logger.debug("Loaded config from %s", config_path)
        return Config(**config_data)
        
    except Exception as e:
        logger.warning("Failed to load config from %s: %s. Using defaults.", config_path, e)
        return Config()


def save_default_config() -> None:
    """Save a default configuration file to help users get started."""
    config_path = get_config_path()
    config_dir = config_path.parent
    
    # Create config directory if it doesn't exist
    config_dir.mkdir(parents=True, exist_ok=True)
    
    if config_path.exists():
        logger.info("Config file already exists at %s", config_path)
        return
        
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

# Circuit Breaker Configuration
cb_failure_threshold = 5 # Consecutive failures before opening the circuit (1-20)
cb_cooldown_sec = 60     # Seconds to wait in OPEN state before transitioning to HALF_OPEN (10-600)

# Parallel Processing Configuration
max_workers = 4          # Max parallel workers for repository processing (1-10)
rate_limit_buffer = 100  # API calls to reserve as buffer before slowing down (50-500)
parallel_enabled = true  # Enable parallel repository processing (true/false)

[ai]
# AI model settings
model_name = "gemini-1.5-flash"  # AI model to use

[app]
# Application settings
log_level = "INFO"              # Log level: DEBUG, INFO, WARNING, ERROR
default_prompt_type = "standup" # Default prompt type: standup, retro, weekly
default_format = "markdown"     # Default output format: markdown, json, plain
'''
    
    try:
        config_path.write_text(default_config_toml)
        logger.info("Created default config file at %s", config_path)
    except Exception as e:
        logger.error("Failed to create default config file: %s", e)


# Global config instance
_config_instance: Optional[Config] = None


def get_config() -> Config:
    """Get the global configuration instance, loading it if necessary."""
    global _config_instance
    if _config_instance is None:
        _config_instance = load_config()
    return _config_instance


def reload_config() -> Config:
    """Reload configuration from file."""
    global _config_instance
    _config_instance = load_config()
    return _config_instance

```

---

### 2. Rate Limiting Coordinator (`hacktivity/core/rate_limiter.py`)

This new module will handle global API rate limit coordination. It will be a singleton to ensure all threads share the same rate limit state.

**`hacktivity/core/rate_limiter.py`**
```python
"""
Global rate limit coordinator for GitHub API calls.
Manages a shared token bucket/semaphore based on GitHub's X-RateLimit headers.
"""
import threading
import time
from typing import Optional, Any

from rich.console import Console

from .logging import get_logger

logger = get_logger(__name__)
console = Console() # For progress bar output, avoid logger for direct user feedback


class RateLimitCoordinator:
    """
    Coordinates GitHub API requests across multiple threads to respect rate limits.
    Acts as a shared token bucket/semaphore.
    """
    _instance: Optional['RateLimitCoordinator'] = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None: # Double-checked locking
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, config: Any):
        # Only initialize once
        if hasattr(self, '_initialized'):
            return
        self._initialized = True

        self._config = config.github
        self._semaphore = threading.Semaphore(self._config.rate_limit_buffer) # Initial buffer
        self._remaining_requests: int = 5000 # Default to max if not yet known
        self._reset_time: float = time.time() + 3600 # Default to 1 hour from now
        self._last_update_time: float = time.time()
        self._rate_limit_lock = threading.Lock() # Protects _remaining_requests and _reset_time

        logger.info(
            "RateLimitCoordinator initialized with buffer: %d, max workers: %d",
            self._config.rate_limit_buffer,
            self._config.max_workers
        )

    def acquire_token(self) -> None:
        """Acquires a token, blocking if rate limit buffer is low or reset is needed."""
        with self._rate_limit_lock:
            # Check if we are approaching the actual rate limit or need to wait for reset
            current_time = time.time()
            
            # If remaining is below buffer, we need to slow down
            # Or if reset time is very soon, and we don't have many requests left
            if self._remaining_requests <= self._config.rate_limit_buffer and current_time < self._reset_time:
                wait_duration = self._reset_time - current_time + 1 # Add 1 second buffer
                if wait_duration > 0:
                    console.log(
                        f"[bold yellow]Rate limit approaching ({self._remaining_requests} remaining). "
                        f"Waiting {int(wait_duration)}s until reset at {time.strftime('%H:%M:%S', time.localtime(self._reset_time))}[/bold yellow]"
                    )
                    time.sleep(wait_duration)
                    # After waiting, reset remaining and reset_time to reflect new window
                    self._remaining_requests = 5000 # Assume full reset
                    self._reset_time = time.time() + 3600 # Assume new hour window
                    console.log("[bold green]Rate limit window reset. Resuming operations.[/bold green]")

        # Acquire a token from the semaphore (this manages worker concurrency within the buffer)
        self._semaphore.acquire()
        logger.debug("Acquired rate limit token. Remaining: %d", self._remaining_requests)

    def release_token(self, headers: Optional[dict] = None) -> None:
        """Releases a token and updates rate limit status based on response headers."""
        with self._rate_limit_lock:
            if headers:
                try:
                    remaining = int(headers.get('x-ratelimit-remaining', self._remaining_requests))
                    reset = int(headers.get('x-ratelimit-reset', self._reset_time))
                    
                    # Only update if the information is newer or more restrictive
                    if remaining < self._remaining_requests or reset > self._reset_time:
                        self._remaining_requests = remaining
                        self._reset_time = reset
                        self._last_update_time = time.time()
                        logger.debug(
                            "Rate limit updated: Remaining=%d, Reset=%s",
                            self._remaining_requests,
                            time.strftime('%H:%M:%S', time.localtime(self._reset_time))
                        )
                except ValueError as e:
                    logger.warning("Could not parse rate limit headers: %s", e)
            
            # Ensure remaining is not negative
            self._remaining_requests = max(0, self._remaining_requests - 1)

        self._semaphore.release()
        logger.debug("Released rate limit token.")

    def get_status(self) -> dict:
        """Returns current rate limit status."""
        with self._rate_limit_lock:
            return {
                'remaining': self._remaining_requests,
                'reset_time': self._reset_time,
                'reset_in_seconds': max(0, int(self._reset_time - time.time()))
            }


# Global instance getter
_coordinator: Optional[RateLimitCoordinator] = None

def get_rate_limit_coordinator() -> RateLimitCoordinator:
    """Factory function to get the singleton RateLimitCoordinator instance."""
    global _coordinator
    if _coordinator is None:
        from .config import get_config
        config = get_config()
        _coordinator = RateLimitCoordinator(config)
    return _coordinator

```

Now, we need to modify `hacktivity/core/commits.py` and `hacktivity/core/repos.py` to use this coordinator.

**Modify `hacktivity/core/commits.py` to use `RateLimitCoordinator`:**

Find the `_fetch_commits_with_api` function. We need to acquire a token before the `gh api` call and release it afterwards, passing the response headers.

**`hacktivity/core/commits.py`** (changes within `_fetch_commits_with_api`)
```python
"""Repository-based commit fetching module for GitHub API."""

import json
import subprocess
import sys
from typing import List, Dict, Optional, Any

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from . import cache
from .circuit_breaker import protected_call, CircuitOpenError
from .logging import get_logger
from .rate_limiter import get_rate_limit_coordinator # Import the coordinator

logger = get_logger(__name__)

# Lazy import config to avoid circular imports
def _get_config():
    from .config import get_config
    return get_config()


def _generate_commit_cache_key(repo_full_name: str, since: str, until: str, author_filter: Optional[str] = None) -> str:
    """Generate a unique cache key for commit fetching.
    
    Args:
        repo_full_name: Repository full name (owner/repo)
        since: Start date in YYYY-MM-DD format
        until: End date in YYYY-MM-DD format
        author_filter: Optional author username filter
        
    Returns:
        Unique cache key string
    """
    author_part = author_filter or "all"
    return f"commits:{repo_full_name}:{since}:{until}:{author_part}"


def _parse_commit_data(api_commits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Parse commit data from GitHub API response.
    
    Args:
        api_commits: Raw commit data from GitHub API
        
    Returns:
        List of parsed commit dictionaries with key fields
    """
    parsed_commits = []
    
    for commit in api_commits:
        # Extract key commit information
        commit_data = commit.get('commit', {})
        author_data = commit_data.get('author', {})
        committer_data = commit_data.get('committer', {})
        github_author = commit.get('author') or {}
        
        parsed_commit = {
            'sha': commit.get('sha', ''),
            'message': commit_data.get('message', ''),
            'author_name': author_data.get('name', ''),
            'author_email': author_data.get('email', ''),
            'author_login': github_author.get('login', ''),
            'author_id': github_author.get('id'),
            'commit_date': author_data.get('date', ''),
            'committer_name': committer_data.get('name', ''),
            'committer_email': committer_data.get('email', ''),
            'committer_date': committer_data.get('date', ''),
            'url': commit.get('url', ''),
            'html_url': commit.get('html_url', '')
        }
        
        parsed_commits.append(parsed_commit)
    
    return parsed_commits


def _filter_commits_by_author(commits: List[Dict[str, Any]], author_login: str) -> List[Dict[str, Any]]:
    """Filter commits by author login.
    
    Args:
        commits: List of parsed commit dictionaries
        author_login: GitHub username to filter by
        
    Returns:
        Filtered list of commits by the specified author
    """
    return [commit for commit in commits if commit.get('author_login') == author_login]


def _fetch_commits_with_api(endpoint: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Fetch commits from GitHub API with pagination.
    
    Args:
        endpoint: GitHub API endpoint (e.g., 'repos/owner/repo/commits')
        params: Query parameters for the API call
        
    Returns:
        List of commit data from API
        
    Raises:
        subprocess.CalledProcessError: If API call fails
        subprocess.TimeoutExpired: If API call times out
    """
    config = _get_config()
    coordinator = get_rate_limit_coordinator() # Get the coordinator instance
    all_commits = []
    page = 1
    per_page = config.github.per_page
    
    while True:
        # Add query parameters
        query_params = params.copy()
        query_params.update({
            'per_page': str(per_page),
            'page': str(page)
        })
        
        # Build query string
        query_string = '&'.join(f"{k}={v}" for k, v in query_params.items())
        full_endpoint = f"{endpoint}?{query_string}"
        
        # Construct command for this specific page
        command = [
            "gh", "api",
            "-X", "GET", 
            full_endpoint,
            "--include-response-headers" # Request headers for rate limit info
        ]
        
        logger.debug("Fetching commits page %d from %s", page, endpoint)
        
        try:
            # Define the subprocess call as a zero-argument lambda for circuit breaker
            def api_runner():
                # Acquire a token before making the API call
                coordinator.acquire_token()
                
                process = subprocess.run(
                    command,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=config.github.timeout_seconds
                )
                return process
            
            # Wrap the runner with the circuit breaker
            # Use the logical endpoint path as the circuit breaker key
            result = protected_call(endpoint, api_runner)
            
            # Extract headers and body
            # gh api --include-response-headers puts headers first, then a blank line, then body
            parts = result.stdout.split('\n\n', 1)
            raw_headers = parts[0]
            json_body = parts[1] if len(parts) > 1 else '{}'

            response_headers = {}
            for line in raw_headers.split('\n'):
                if ': ' in line:
                    key, value = line.split(': ', 1)
                    response_headers[key.lower()] = value
            
            # Release the token, passing response headers for rate limit update
            coordinator.release_token(response_headers)

            # Parse the JSON response
            page_commits = json.loads(json_body)
            
            # Handle case where API returns dict instead of list (shouldn't happen for commit endpoints)
            if isinstance(page_commits, dict):
                # Some endpoints wrap results in a dict
                page_commits = page_commits.get('commits', [])
            
            # If no commits on this page, we're done
            if not page_commits:
                break
                
            all_commits.extend(page_commits)
            
            # If we got fewer items than requested per page, we've reached the end
            if len(page_commits) < per_page:
                break
                
            # Check configured page limit
            if page >= config.github.max_pages:
                logger.warning("Reached maximum page limit (%d) for commit fetching from %s", 
                             config.github.max_pages, endpoint)
                break
                
            page += 1
            
        except json.JSONDecodeError as e:
            logger.error("Error parsing GitHub API response from %s: %s", endpoint, e)
            break
        except Exception as e: # Catch all exceptions to ensure token release
            coordinator.release_token() # Release token even on failure
            raise # Re-raise the original exception
    
    logger.info("Fetched %d commits from %s", len(all_commits), endpoint)
    return all_commits


def fetch_repo_commits(repo_full_name: str, since: str, until: str, author_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Fetch commit activity from a specific repository using the GitHub API.
    
    This function uses the reliable /repos/{owner}/{repo}/commits endpoint
    instead of the search API, which is much more robust for large repositories.
    
    Args:
        repo_full_name: Repository full name (e.g., 'owner/repo-name')
        since: Start date in YYYY-MM-DD format
        until: End date in YYYY-MM-DD format  
        author_filter: Optional GitHub username to filter commits by
        
    Returns:
        List of commit dictionaries with metadata
        
    Raises:
        SystemExit: If API calls fail and no cached fallback is available
    """
    config = _get_config()
    
    # Generate cache key and check for cached results
    cache_key = _generate_commit_cache_key(repo_full_name, since, until, author_filter)
    # Use 365-day TTL since commits are immutable
    cached_commits = cache.get(cache_key, max_age_hours=8760)
    
    if cached_commits is not None:
        logger.info("Using cached commits for '%s' from %s to %s (%d commits)", 
                   repo_full_name, since, until, len(cached_commits))
        return cached_commits
    
    logger.info("Fetching commits for '%s' from %s to %s...", repo_full_name, since, until)
    if author_filter:
        logger.info("Will filter by author: %s", author_filter)
    
    # Create dynamic retry decorator with config values
    retry_decorator = retry(
        stop=stop_after_attempt(config.github.retry_attempts),
        wait=wait_exponential(multiplier=1, min=config.github