Here are detailed, actionable implementation instructions for **T025: Parallel Repository Processing** in hacktivity, designed to align with your architecture, codebase, and development philosophy.

---

## 1. Configuration Extension

**File:** `hacktivity/core/config.py`

**Goal:** Add fields for parallel processing and rate limiting.

**Actions:**

- Extend `GitHubConfig`:
    ```python
    class GitHubConfig(BaseModel):
        # ... existing fields ...
        max_workers: int = Field(default=4, ge=1, le=10, description="Max parallel workers")
        rate_limit_buffer: int = Field(default=100, ge=50, le=500, description="API calls to reserve as buffer")
        parallel_enabled: bool = Field(default=True, description="Enable parallel processing")
    ```
- Ensure these fields are loaded from TOML (add to default config output as well).
- **Test:** Unit-test config load with/without these fields.

---

## 2. Rate Limiting Coordinator

**File:** `hacktivity/core/rate_limiter.py` (new)

**Goal:** Limit aggregate API calls across threads, respect GitHub rate limits, provide backpressure.

**Actions:**

- Implement `RateLimitCoordinator`:
    - Use a thread-safe `threading.Semaphore` or `BoundedSemaphore` to represent remaining API quota (`remaining = (rate_limit - buffer)`).
    - Provide:
        - `acquire(n: int = 1, timeout: Optional[float] = None)`: Blocks if quota not available.
        - `release(n: int = 1)`: For rare cases (e.g., on error).
        - `update_limits(remaining: int, reset_time: datetime)`: Update from headers.
        - `wait_for_reset()`: Block workers until reset.
        - `get_status()`: For progress/monitoring.
    - Periodically (or after each API call), fetch and update current rate-limit status via `gh api rate_limit` or API headers.
    - Integration point: All GitHub API calls (repos, commits, etc.) must acquire quota before proceeding.
    - Backpressure: If quota is low, workers block, waiting for reset.
    - **Thread safety:** Use locks and per-process singleton pattern.
    - **Testing:** Unit tests for acquiring, releasing, and updating limits under concurrent load.

---

## 3. Parallel Processing Orchestrator

**File:** `hacktivity/core/parallel.py` (new)

**Goal:** Efficiently process multiple repositories in parallel with progress aggregation and rate limiting.

**Actions:**

- **Main entry:** `fetch_commits_parallel(...)`
    - Signature: Same as `process_repositories_with_operation_state`.
    - Decides whether to run parallel or fallback to sequential depending on config.
- **Worker Pool:**
    - Use `concurrent.futures.ThreadPoolExecutor` or `ThreadPool` from `concurrent.futures` for CPU-bound (subprocess) work.
    - Limit pool size to `max_workers` from config.
- **RepositoryWorker:**
    - Each worker:
        - Acquires rate limit quota before each chunked API call.
        - Calls `fetch_repo_commits_chunked(...)` (from `chunking.py`) for its repo.
        - Handles exceptions/failures, logs, and updates state via `StateManager` (thread-safe).
        - Integrates with circuit breaker: failures propagate, circuit breaker logic already present.
    - Fault isolation: Failure in one repo does not affect others.
- **ProgressAggregator:**
    - Shared, thread-safe progress state (e.g., using `threading.Lock`).
    - Periodically (or on events), aggregates:
        - Total repositories
        - Completed/failed/pending
        - Per-repo and per-chunk progress if available
        - Total commits
    - Exposes to progress tracker/bar.
- **Queue:**
    - Use a thread-safe `queue.Queue` to distribute work, supporting work stealing.
- **Result aggregation:**
    - Collect results in a thread-safe dict keyed by repository.
- **Integration:**
    - Should use `process_repositories_with_operation_state` logic for operation and state tracking, but distribute repo processing across threads.
- **Testing:** Unit/integration tests for running N repos in parallel, observing aggregate progress, handling rate limits, and error cases.

---

## 4. Main CLI Integration

**File:** `hacktivity/__main__.py`

**Goal:** Allow user to enable/disable parallel processing, preserve CLI.

**Actions:**

- When user triggers a multi-repo summary/fetch:
    - Load config: check `github.parallel_enabled`.
    - If enabled, call `fetch_commits_parallel(...)`.
    - If not, fallback to sequential processing (existing logic).
- Add CLI flag (optional): `--parallel/--no-parallel` to override config.
- **Backward compatibility:** If only one repo, always process sequentially.
- **Testing:** Manual/integration test: CLI with/without parallel, confirm output and state.

---

## 5. Progress Tracking Enhancement

**Files:** (likely `parallel.py`, `chunking.py`, and CLI)

**Goal:** Show accurate, real-time, aggregate progress for repositories/chunks.

**Actions:**

- Implement `ProgressAggregator`:
    - Receives per-repo and per-chunk updates from workers (via thread-safe callbacks or shared state).
    - Maintains aggregate counters and status.
- Integrate with existing `rich` progress bar logic.
    - Use `rich.progress.Progress` with multiple bars or tasks as needed.
    - Show:
        - Total repos: completed/failed/total
        - Per-repo progress (optionally: per-chunk)
        - Aggregate commit count
    - Update progress live (interval or on worker updates).
- Ensure thread safety (updates from multiple threads).
- **Testing:** Run demo with >1 repo, observe that progress is correct and responsive.

---

## 6. Rate Limiting Enforcement across Workers

**Goal:** Prevent exceeding GitHub API quotas with parallel requests.

**Actions:**

- All GitHub API calls (including from subprocesses, if possible) must acquire quota from `RateLimitCoordinator` before proceeding.
- If close to limit, workers block until reset.
- Circuit breaker remains per-endpoint; works in tandem with the coordinator.
- **Testing:** Simulate low rate limit, ensure workers block and resume after reset.

---

## 7. Thread-Safe State Management

**Goal:** Ensure data consistency and integrity.

**Actions:**

- All updates to shared state (operation, repository, progress) must go through existing SQLite-based `StateManager` in `state.py`, which is already thread-safe.
- Progress and result aggregation must use thread-safe structures (`threading.Lock`, etc.).
- **Testing:** Stress/integration tests with many concurrent updates.

---

## 8. Error Handling / Fault Isolation

**Goal:** Robust parallel execution.

**Actions:**

- Worker exceptions:
    - Log and record failure in state.
    - Do not crash other threads.
    - Allow partial completion (operation marked as partial if some workers fail).
- Circuit breaker:
    - If endpoint fails repeatedly, circuit opens; further calls short-circuit until cooldown.
    - Workers encountering an open circuit should retry/requeue as appropriate.
- **Testing:** Simulate API failures, check that other repos proceed and state is correct.

---

## 9. Testing and Load Testing

**Files:** `tests/` (existing test suite)

**Goal:** Validate reliability, correctness, and speedup.

**Actions:**

- **Unit tests:** For `RateLimitCoordinator`, progress aggregation, worker pool.
- **Integration tests:** For parallel repo processing, state tracking, rate limiting.
- **Load tests:** Process N repos (ideally with fake/mock API) and confirm:
    - Throughput increases
    - No rate limit violations
    - State is consistent after interruption/resume

---

## 10. Documentation

- Update README/config docs: describe parallelization, new config options, and any new CLI flags.
- Add docstrings/comments per [Development Philosophy](../docs/DEVELOPMENT_PHILOSOPHY.md), especially for new modules and concurrency boundaries.

---

## 11. Backward Compatibility & Incremental Adoption

- Default to `parallel_enabled = True` in config, but allow users to disable (for troubleshooting).
- Sequential code path remains; fallback and test both.
- If only one repo, always process sequentially.

---

## 12. Observability

- Ensure all logs from workers include repo/context info.
- Log rate limiting events, circuit breaker transitions, worker failures, operation/resume events.
- Include correlation/operation IDs in logs where possible.

---

# Example High-Level Flow

1. **CLI**: User invokes summary/fetch.
2. **Config**: Check for parallel enabled and max_workers.
3. **Repository Discovery**: Get repo list (from `repos.py`).
4. **Operation State**: Create operation in state DB.
5. **Parallel Orchestrator**: Start up to `max_workers` threads.
6. **Each Worker**:
   - Get next repo from queue.
   - For each chunk:
      - Acquire rate limit slot.
      - Call GitHub API (via chunking/commits logic).
      - Update state/progress.
      - On circuit breaker, fallback to cache or retry.
   - On completion/failure, update repository progress in state.
7. **Progress Bar**: Aggregate and show live status.
8. **After all done**: Aggregate results, update final operation status, print summary/output.

---

# Code Skeletons & Integration Points

**`rate_limiter.py`**
```python
import threading
import time
from datetime import datetime, timedelta

class RateLimitCoordinator:
    def __init__(self, limit: int, buffer: int):
        self._lock = threading.Lock()
        self._quota = threading.BoundedSemaphore(limit - buffer)
        self._remaining = limit - buffer
        self._reset_time = datetime.utcnow()
        # ... setup background thread to update rate limit status ...
    def acquire(self, n=1, timeout=None):
        for _ in range(n):
            self._quota.acquire(timeout=timeout)
    def release(self, n=1):
        for _ in range(n):
            self._quota.release()
    def update_limits(self, remaining, reset_time):
        with self._lock:
            # update self._remaining, self._reset_time
            # adjust semaphore as needed
            ...
    def wait_for_reset(self):
        # block until reset
        ...
    def get_status(self):
        # return dict of remaining, reset_time
        ...
```

**`parallel.py`**
```python
import concurrent.futures
import threading
from queue import Queue

def fetch_commits_parallel(operation_id, repositories, since, until, author_filter, max_days):
    config = get_config()
    max_workers = config.github.max_workers
    results = {}
    progress = ProgressAggregator(...)
    repo_queue = Queue()
    for repo in repositories:
        repo_queue.put(repo)
    def worker():
        while not repo_queue.empty():
            repo = repo_queue.get()
            try:
                res = fetch_repo_commits_chunked(repo, since, until, author_filter, max_days, operation_id)
                # update results/progress
            except Exception as e:
                # log, update state/progress
            finally:
                repo_queue.task_done()
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(worker) for _ in range(max_workers)]
        # Optionally: monitor futures, aggregate progress
    # Wait for all to finish, then aggregate results/progress
    return results
```

---

# Final Notes

- **Keep It Simple:** Don’t over-complicate; start by parallelizing at the repository level only.
- **No Internal Mocking:** For tests, use only fakes for *external* APIs.
- **Documentation and Observability:** Favor explicit structure, logging, and error handling.
- **Thread Safety:** All shared state (rate limit, progress, state DB) must be protected.
- **Graceful Degradation:** If rate limit/circuit breaker hits, workers block or resume as appropriate.
- **Partial Failure:** If some repos fail, mark operation as partial, record errors clearly, return partial results.

---

**Implement the above in small, testable increments; use feature flags for safe rollout.**