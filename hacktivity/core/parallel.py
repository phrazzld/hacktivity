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