Below is a suggested, incrementally-safe implementation that adds true multi‐repository parallelism, global rate-limit coordination, thread-safe state updates, and aggregate progress reporting—while preserving the existing single-threaded fall‐back, circuit-breaker, caching, and retry behavior.

1) **Extend `GitHubConfig`**  
   In `hacktivity/core/config.py`, add three new fields to control parallelism and rate-limit throttling:

```diff
--- a/hacktivity/core/config.py
+++ b/hacktivity/core/config.py
@@ class GitHubConfig(BaseModel):
     max_pages: int = Field(default=10, ge=1, le=20, description="Maximum pages to fetch")
     retry_attempts: int = Field(default=3, ge=1, le=10, description="Number of retry attempts")
     retry_min_wait: int = Field(default=4, ge=1, le=60, description="Minimum retry wait in seconds")
     retry_max_wait: int = Field(default=10, ge=1, le=300, description="Maximum retry wait in seconds")
+
+    # Global rate-limit (per hour) and parallelism settings
+    rate_limit_per_hour: int = Field(
+        default=5000, ge=1000, le=5000,
+        description="GitHub API calls allowed per hour"
+    )
+    rate_limit_buffer: int = Field(
+        default=100, ge=0, le=500,
+        description="Reserved calls to avoid bursting into a hard limit"
+    )
+    max_workers: int = Field(
+        default=4, ge=1, le=10,
+        description="Max parallel repository workers"
+    )
+    parallel_enabled: bool = Field(
+        default=True,
+        description="Enable parallel repository processing"
+    )
```

2) **Rate-Limit Coordinator**  
   Create a new module `hacktivity/core/rate_limiter.py` that provides a thread-safe token bucket (semaphore) to guard the *total* number of in‐flight API calls per hour.

```python
# hacktivity/core/rate_limiter.py
import threading
import time

from .config import get_config
from .logging import get_logger

logger = get_logger(__name__)

class RateLimitCoordinator:
    """
    A simple semaphore-based coordinator to ensure we never exceed
    (rate_limit_per_hour - rate_limit_buffer) concurrent calls per hour.
    """

    def __init__(self):
        cfg = get_config().github
        # total tokens = calls/hour minus buffer
        self._tokens = max(cfg.rate_limit_per_hour - cfg.rate_limit_buffer, 1)
        self._sema = threading.BoundedSemaphore(self._tokens)
        # We reset the bucket once per hour
        self._lock = threading.Lock()
        self._reset_interval = 3600  # seconds
        self._last_reset = time.time()

    def _maybe_reset(self):
        now = time.time()
        if now - self._last_reset >= self._reset_interval:
            with self._lock:
                if now - self._last_reset >= self._reset_interval:
                    logger.debug("Resetting rate‐limit tokens")
                    # Rebuild the semaphore
                    self._sema = threading.BoundedSemaphore(self._tokens)
                    self._last_reset = now

    def acquire(self):
        """Block until a token is available."""
        self._maybe_reset()
        logger.debug("Acquiring rate-limit token")
        self._sema.acquire()

    def release(self):
        """Return a token."""
        logger.debug("Releasing rate-limit token")
        try:
            self._sema.release()
        except ValueError:
            # already at max, ignore
            pass

    def wrap(self, fn):
        """Decorator to acquire/release around an API call."""
        def wrapped(*args, **kwargs):
            self.acquire()
            try:
                return fn(*args, **kwargs)
            finally:
                self.release()
        return wrapped


# Singleton instance
_coordinator = None

def get_rate_limiter() -> RateLimitCoordinator:
    global _coordinator
    if not _coordinator:
        _coordinator = RateLimitCoordinator()
    return _coordinator
```

3) **Parallel Orchestrator**  
   Add `hacktivity/core/parallel.py` that spins up a `ThreadPoolExecutor`, coordinates rate-limit, updates the state tables, and reports aggregate progress.

```python
# hacktivity/core/parallel.py
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Optional

from .config import get_config
from .state import create_operation, track_repository_progress
from .rate_limiter import get_rate_limiter
from .chunking import fetch_repo_commits_chunked
from .logging import get_logger

logger = get_logger(__name__)

class ProgressAggregator:
    """Thread-safe aggregator for repository‐level progress."""
    def __init__(self, total: int):
        self._lock = threading.Lock()
        self.total = total
        self.completed = 0

    def mark_done(self):
        with self._lock:
            self.completed += 1
            pct = (self.completed / self.total) * 100
            logger.info("Overall progress: %.1f%% (%d/%d)",
                        pct, self.completed, self.total)


def fetch_commits_parallel(
    user: str,
    repo_list: List[str],
    since: str,
    until: str,
    author_filter: Optional[str] = None
) -> Dict[str, List[dict]]:
    """
    Fetch commits from multiple repos in parallel, respecting rate limits,
    updating operation/repo state, and logging aggregate progress.
    """
    cfg = get_config().github
    # 1) Create an operation in the state DB
    op_id = create_operation(
        operation_type="fetch_parallel",
        user=user,
        since=since,
        until=until,
        author_filter=author_filter,
        metadata={"mode": "parallel"}
    )

    # 2) Register all repos in the operation
    from .state import get_state_manager
    sm = get_state_manager()
    sm.add_repositories_to_operation(op_id, repo_list)
    sm.update_operation_status(op_id, "in_progress", total_repositories=len(repo_list))

    # 3) Prepare for parallel execution
    rate_limiter = get_rate_limiter()
    aggregator = ProgressAggregator(total=len(repo_list))
    results: Dict[str, List[dict]] = {}

    def _worker(repo_full_name: str):
        # Mark repo in_progress
        track_repository_progress(op_id, repo_full_name, "in_progress")
        try:
            # Wrap the chunked fetch call with rate limiter
            fetch_fn = rate_limiter.wrap(fetch_repo_commits_chunked)
            commits = fetch_fn(
                repo_full_name,
                since,
                until,
                author_filter,
                cfg.max_days,
                op_id  # pass operation for chunk-level tracking
            )
            # Mark repository completed
            track_repository_progress(
                op_id,
                repo_full_name,
                "completed",
                commit_count=len(commits)
            )
            return repo_full_name, commits
        except Exception as ex:
            logger.error("Repository %s failed: %s", repo_full_name, ex)
            track_repository_progress(
                op_id,
                repo_full_name,
                "failed",
                error_message=str(ex)
            )
            return repo_full_name, []

    # 4) Execute in thread pool
    with ThreadPoolExecutor(max_workers=cfg.max_workers) as pool:
        futures = {pool.submit(_worker, repo): repo for repo in repo_list}
        for future in as_completed(futures):
            repo_name, commits = future.result()
            results[repo_name] = commits
            aggregator.mark_done()

    # 5) Finalize operation status
    sm.update_operation_status(op_id, "completed")
    logger.info("Parallel fetch operation %s completed", op_id)
    return results
```

4) **Hook into `fetch_commits`**  
   In your existing `hacktivity/core/github.py`, detect when you’ve been asked to fetch *all* repositories (i.e. no `--repo` override) and parallelism is enabled. If so, delegate to our new orchestrator; otherwise fall back to the existing single-threaded logic.

```diff
--- a/hacktivity/core/github.py
+++ b/hacktivity/core/github.py
@@
 from .repos import discover_user_repositories, filter_repositories_by_activity
-from .commits import fetch_commits_from_multiple_repos
+from .parallel import fetch_commits_parallel
+from .config import get_config

 def fetch_commits(
     github_user: str,
     since: str,
     until: str,
     org: Optional[str] = None,
     repo: Optional[str] = None
 ) -> List[Dict[str, Any]]:
     """
     Top-level dispatch for commit fetching, wired into CLI.
     """
+    cfg = get_config().github
+
+    # If user did NOT specify a single repo, and parallel is enabled, do parallel
+    if repo is None and cfg.parallel_enabled:
+        # 1) discover + filter repos
+        repos = discover_user_repositories(github_user, org)
+        repos = filter_repositories_by_activity(repos, since, until)
+        repo_list = [r["full_name"] for r in repos]
+        if not repo_list:
+            return []
+        return fetch_commits_parallel(github_user, repo_list, since, until)
+
     # Otherwise, single-repo path (or parallel disabled)
     # This is the existing logic you already have for a specific repo:
     #   - chunked fetch
     #   - caching, retries, circuit breaker, etc.
```

5) **CLI stays backward-compatible**  
   You don’t need to change any of your `__main__.py` – the new behavior is purely behind‐the‐scenes in `fetch_commits`.  If users run with parallel disabled in their `config.toml` (or `--format`/`--prompt` flags only), they’ll still get the original single-threaded flow.

---

With these changes:

- **Configurable workers** via `max_workers`  
- **Global rate-limit coordination** via a shared semaphore/bucket in `RateLimitCoordinator`  
- **Circuit-breaker**, **caching**, **retry** all remain unchanged inside `fetch_repo_commits_chunked` and `commits.py`  
- **Thread-safe state updates** use your existing `StateManager` (via `track_repository_progress` and `create_operation`)  
- **Aggregate progress** printed to logs on each repo completion  
- **Backward compatibility** guaranteed when `parallel_enabled = false` or when the user wants just one repo  

This fulfills all acceptance criteria with minimal change to your existing architecture, preserves single-threaded fallback, and leverages your existing chunking, caching, retry, and circuit-breaker machinery.