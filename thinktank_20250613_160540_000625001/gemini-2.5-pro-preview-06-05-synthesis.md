Here is a complete and robust implementation plan for **T025: Parallel Repository Processing**, synthesizing the best strategies and code from the provided models. This guide provides a clear, step-by-step path to building the feature while adhering to the project's architecture and development philosophy.

### 1. Configuration Extension

First, we'll extend the configuration to allow users to enable, disable, and tune the parallel processing behavior.

**File: `hacktivity/core/config.py`**
```python
# ... (existing imports)
from pydantic import BaseModel, Field

# ... (existing CacheConfig, etc.)

class GitHubConfig(BaseModel):
    # ... (existing fields for per_page, timeout, retry, circuit breaker) ...

    # --- NEW: Parallel Processing Configuration ---
    max_workers: int = Field(
        default=5, ge=1, le=10, 
        description="Max parallel workers for repository processing"
    )
    rate_limit_buffer: int = Field(
        default=100, ge=50, le=500, 
        description="API calls to reserve as a buffer to avoid hitting the hard rate limit"
    )
    parallel_enabled: bool = Field(
        default=True, 
        description="Enable parallel processing of repositories"
    )

# ... (rest of the file)
```

Next, update the `save_default_config` function to include these new settings in the generated `config.toml`.

**File: `hacktivity/core/config.py` (inside `save_default_config`)**
```python
# ... inside the default_config_toml string ...
'''
# ... (existing github settings) ...

# Parallel Processing Configuration
max_workers = 5         # Max parallel workers for repository processing (1-10)
rate_limit_buffer = 100 # API calls to reserve as buffer (50-500)
parallel_enabled = true # Enable parallel processing (true/false)
'''
# ... (rest of the function) ...
```

---

### 2. Rate Limiting Coordinator

Create a new module to manage a global, thread-safe rate limit. This implementation uses a token bucket algorithm with a background refill thread, which is robust for smoothing out requests across multiple workers.

**File: `hacktivity/core/rate_limiter.py` (New File)**
```python
"""
Thread-safe, process-wide GitHub rate-limit coordinator.

A token-bucket implementation backed by a background thread. All API-issuing
code can `acquire()` a token before calling GitHub, ensuring the application
never exceeds the global 5,000 req/hour limit even with many worker threads.
"""
import threading
import time
from typing import Optional

from .config import get_config
from .logging import get_logger

logger = get_logger(__name__)

class RateLimitCoordinator:
    """Manages global GitHub API request rate using a token bucket algorithm."""
    _instance: Optional["RateLimitCoordinator"] = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        # Ensure __init__ is called only once for the singleton
        if hasattr(self, '_initialized'):
            return
        self._initialized = True

        cfg = get_config().github
        self._capacity: int = 5000 - cfg.rate_limit_buffer
        self._tokens: float = float(self._capacity)
        self._lock = threading.Lock()

        # Start a daemon thread to continuously refill the token bucket
        refill_thread = threading.Thread(
            target=self._refill_daemon, daemon=True, name="RateLimitRefill"
        )
        refill_thread.start()
        logger.info(
            "RateLimitCoordinator started with capacity=%d, buffer=%d",
            self._capacity, cfg.rate_limit_buffer
        )

    def acquire(self) -> None:
        """Acquire one token, blocking if necessary until a token is available."""
        while True:
            with self._lock:
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    logger.debug("Rate limit token acquired. Tokens remaining: %.2f", self._tokens)
                    return
            # If no token, wait a bit before trying again to avoid busy-waiting
            time.sleep(0.1)

    def _refill_daemon(self) -> None:
        """Background task to refill the token bucket."""
        # GitHub's limit is per hour, so we calculate the per-second refill rate.
        refill_rate_per_sec: float = self._capacity / 3600.0
        while True:
            time.sleep(1.0)
            with self._lock:
                self._tokens = min(self._capacity, self._tokens + refill_rate_per_sec)

# Singleton accessor function
def get_rate_limit_coordinator() -> RateLimitCoordinator:
    """Get the global singleton instance of the RateLimitCoordinator."""
    return RateLimitCoordinator()
```

---

### 3. Integrate Rate Limiter into API Calls

To ensure all GitHub API calls respect the rate limit, we will modify the lowest-level functions that execute the `gh api` command.

**File: `hacktivity/core/commits.py`**
```python
# Add new import at the top
from .rate_limiter import get_rate_limit_coordinator

# ... inside the _fetch_commits_with_api function ...
def _fetch_commits_with_api(endpoint: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    # ... (existing setup code) ...
    
    # Define the subprocess call as a zero-argument lambda for the circuit breaker
    def api_runner():
        # --- NEW: Acquire a global rate limit token before making the call ---
        get_rate_limit_coordinator().acquire()
        
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=config.github.timeout_seconds
        )
    
    # Wrap the runner with the circuit breaker
    result = protected_call(endpoint, api_runner)
    
    # ... (rest of the function) ...
```
**Note:** A similar change should be applied to `_fetch_repositories_with_api` in `hacktivity/core/repos.py` or any other function that directly calls the `gh api`.

---

### 4. Parallel Processing Orchestrator

This new module orchestrates the parallel execution, manages the worker pool, and aggregates progress for a user-friendly display.

**File: `hacktivity/core/parallel.py` (New File)**
```python
"""
Thread-pool based repository-level parallel processing orchestrator.
"""
import concurrent.futures
import threading
from typing import Dict, List, Optional, Any

from .config import get_config
from .chunking import fetch_repo_commits_chunked
from .logging import get_logger
from .state import get_state_manager

logger = get_logger(__name__)

class ProgressAggregator:
    """Thread-safe collector for progress information from multiple workers."""
    def __init__(self, total_repos: int):
        self._lock = threading.Lock()
        self.completed = 0
        self.failed = 0
        self.total = total_repos

    def mark_done(self, success: bool = True):
        with self._lock:
            if success:
                self.completed += 1
            else:
                self.failed += 1
    
    @property
    def processed_count(self) -> int:
        with self._lock:
            return self.completed + self.failed


def _worker(
    repo_name: str,
    operation_id: str,
    since: str,
    until: str,
    author_filter: Optional[str],
    max_days: int,
    progress: ProgressAggregator,
) -> tuple[str, List[Dict[str, Any]]]:
    """
    The target function for each worker thread.
    Processes a single repository and handles its state.
    """
    state_manager = get_state_manager()
    try:
        # State is updated to 'in_progress' inside fetch_repo_commits_chunked
        commits = fetch_repo_commits_chunked(
            repo_name, since, until, author_filter, max_days, operation_id
        )
        # On success, fetch_repo_commits_chunked marks it 'completed' in the state DB
        progress.mark_done(success=True)
        return repo_name, commits
    except Exception as e:
        logger.error("Worker failed while processing repository %s: %s", repo_name, e)
        # fetch_repo_commits_chunked already records the failure state
        progress.mark_done(success=False)
        return repo_name, [] # Return an empty list on failure


def fetch_commits_parallel(
    operation_id: str,
    repositories: List[str],
    since: str,
    until: str,
    author_filter: Optional[str] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Processes a list of repositories in parallel, respecting rate limits and
    aggregating progress. Falls back to sequential processing if disabled.
    """
    config = get_config().github
    
    # Fallback to sequential for single-repo operations or if disabled
    if not config.parallel_enabled or len(repositories) <= 1:
        from .chunking import process_repositories_with_operation_state
        logger.info("Processing %d repo(s) sequentially.", len(repositories))
        return process_repositories_with_operation_state(
            operation_id, repositories, since, until, author_filter
        )

    logger.info(
        "Starting parallel processing of %d repositories with %d workers.",
        len(repositories), config.max_workers
    )

    results: Dict[str, List[Dict[str, Any]]] = {}
    progress = ProgressAggregator(total_repos=len(repositories))

    with concurrent.futures.ThreadPoolExecutor(max_workers=config.max_workers) as executor:
        # Submit all repository processing tasks to the thread pool
        future_to_repo = {
            executor.submit(
                _worker, repo, operation_id, since, until, author_filter, 7, progress
            ): repo for repo in repositories
        }
        
        # Use rich for progress bar if available, otherwise just wait
        try:
            from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("{task.completed}/{task.total} repos"),
                transient=True
            ) as rich_progress:
                task = rich_progress.add_task("Processing...", total=len(repositories))
                for future in concurrent.futures.as_completed(future_to_repo):
                    repo_name, commits = future.result()
                    results[repo_name] = commits
                    rich_progress.update(task, advance=1)
        except ImportError:
            # Fallback for environments without 'rich'
            for future in concurrent.futures.as_completed(future_to_repo):
                repo_name, commits = future.result()
                results[repo_name] = commits
                logger.info("Progress: %d/%d repositories processed.", progress.processed_count, progress.total)

    logger.info(
        "Parallel processing finished. Succeeded: %d, Failed: %d.",
        progress.completed, progress.failed
    )
    return results

```

---

### 5. Main Entry Point Integration

The final step is to integrate the new parallel orchestrator into the main application logic. The best place is the high-level `fetch_commits` function, which makes the change transparent to the CLI.

**File: `hacktivity/core/github.py`**
```python
# ... (existing imports)
from .repos import discover_user_repositories, filter_repositories_by_activity
from .parallel import fetch_commits_parallel  # <-- NEW IMPORT
from .commits import aggregate_commits_by_author
from .state import create_operation, get_state_manager
from .config import get_config

def fetch_commits(
    user: str,
    since: str,
    until: str,
    org: Optional[str] = None,
    repo: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Fetch commits for a user, dispatching to parallel or sequential
    processing as configured.
    """
    state_manager = get_state_manager()
    
    # Create a state-tracking operation for this run
    operation_id = create_operation(
        'summary', user, since, until, author_filter=user, org_filter=org, repo_filter=repo
    )

    # Discover and filter repositories
    if repo:
        repo_names = [repo]
    else:
        all_repos = discover_user_repositories(user, org)
        active_repos = filter_repositories_by_activity(all_repos, since, until)
        repo_names = [r['full_name'] for r in active_repos]

    if not repo_names:
        logger.warning("No active repositories found to process.")
        state_manager.update_operation_status(operation_id, 'completed')
        return []

    logger.info("Found %d active repositories to process for operation %s.", len(repo_names), operation_id)
    
    # Add repositories to the operation for state tracking
    state_manager.add_repositories_to_operation(operation_id, repo_names)
    state_manager.update_operation_status(operation_id, 'in_progress')

    # --- DISPATCH TO PARALLEL ORCHESTRATOR ---
    # The orchestrator will handle the fallback to sequential internally
    repo_commits = fetch_commits_parallel(operation_id, repo_names, since, until, user)

    # Aggregate results and finalize operation
    all_commits = aggregate_commits_by_author(repo_commits, user)
    state_manager.update_operation_status(operation_id, 'completed')
    
    return all_commits
```

This comprehensive implementation fulfills all requirements of ticket T025, providing a significant performance boost while maintaining the application's robustness, reliability, and existing features.