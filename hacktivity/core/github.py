"""GitHub API integration module."""

import json
import subprocess
import sys
from typing import List, Optional

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from . import cache
from .circuit_breaker import protected_call, CircuitOpenError
from .rate_limiter import get_rate_limit_coordinator
from .logging import get_logger

logger = get_logger(__name__)

# Lazy import config to avoid circular imports
def _get_config():
    from .config import get_config
    return get_config()


def _is_rate_limit_error(error_details: dict) -> bool:
    """Check if the error indicates a GitHub API rate limit.
    
    Args:
        error_details: Parsed JSON error response from GitHub API
        
    Returns:
        True if this is a rate limit error, False otherwise
    """
    message = error_details.get('message', '').lower()
    return 'rate limit' in message and 'exceeded' in message


def _extract_rate_limit_reset_time(error_details: dict) -> Optional[str]:
    """Extract rate limit reset time from error response.
    
    Args:
        error_details: Parsed JSON error response from GitHub API
        
    Returns:
        Human-readable reset time string, or None if not found
    """
    # Try to get reset time from rate.reset field (Unix timestamp)
    rate_info = error_details.get('rate', {})
    reset_timestamp = rate_info.get('reset')
    
    if reset_timestamp:
        import datetime
        try:
            reset_time = datetime.datetime.fromtimestamp(reset_timestamp)
            return reset_time.strftime('%Y-%m-%d %H:%M:%S')
        except (ValueError, TypeError):
            pass
    
    # If no structured reset time, return None
    return None




def _generate_cache_key(user: str, since: str, until: str, org: Optional[str] = None, repo: Optional[str] = None) -> str:
    """Generate a unique cache key for the GitHub API query.
    
    Args:
        user: GitHub username
        since: Start date in YYYY-MM-DD format
        until: End date in YYYY-MM-DD format
        org: Optional organization filter
        repo: Optional repository filter
        
    Returns:
        Unique cache key string
    """
    org_part = org or "none"
    repo_part = repo or "none"
    return f"commits:{user}:{since}:{until}:{org_part}:{repo_part}"


def check_github_prerequisites() -> None:
    """Checks if the gh CLI is installed and the user is authenticated."""
    # Check for gh CLI
    try:
        subprocess.run(["gh", "--version"], check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        logger.error("The GitHub CLI ('gh') is not installed or not in your PATH.")
        logger.error("Please install it from https://cli.github.com/")
        sys.exit(1)

    # Check for gh auth status
    try:
        subprocess.run(["gh", "auth", "status"], check=True, capture_output=True)
    except subprocess.CalledProcessError:
        logger.error("You are not logged into the GitHub CLI ('gh').")
        logger.error("Please run 'gh auth login' to authenticate.")
        sys.exit(1)


def get_github_user() -> str:
    """Fetches the authenticated GitHub username using the gh CLI."""
    try:
        result = subprocess.run(
            ["gh", "api", "user", "--jq", ".login"],
            check=True,
            capture_output=True,
            text=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        logger.error("Error fetching GitHub user: %s", e.stderr)
        sys.exit(1)


def fetch_commits(user: str, since: str, until: str, org: Optional[str] = None, repo: Optional[str] = None) -> List[str]:
    """
    Fetches commit activity from GitHub using repository-first parallel processing.
    Focuses on commits authored by the user in the given time frame.
    
    Args:
        user: GitHub username
        since: Start date in YYYY-MM-DD format
        until: End date in YYYY-MM-DD format
        org: Optional organization filter
        repo: Optional repository filter (e.g., 'owner/repo-name')
        
    Returns:
        List of commit messages
    """
    import uuid
    from .repos import discover_user_repositories, filter_repositories_by_activity
    from .parallel import fetch_commits_parallel
    from .commits import aggregate_commits_by_author
    
    config = _get_config()
    
    # Create dynamic retry decorator with config values
    retry_decorator = retry(
        stop=stop_after_attempt(config.github.retry_attempts),
        wait=wait_exponential(multiplier=1, min=config.github.retry_min_wait, max=config.github.retry_max_wait),
        retry=retry_if_exception_type(subprocess.TimeoutExpired),
        reraise=True
    )
    
    def _fetch_with_retry():
        # Generate cache key and check for cached results
        cache_key = _generate_cache_key(user, since, until, org, repo)
        cached_commits = cache.get(cache_key)
        
        if cached_commits is not None:
            logger.info("Using cached results for '%s' from %s to %s (%d commits)", user, since, until, len(cached_commits))
            return cached_commits
        
        logger.info("Fetching commits for '%s' from %s to %s...", user, since, until)

        # Handle single repository case
        if repo:
            logger.info("Single repository mode: %s", repo)
            try:
                from .commits import fetch_repo_commits
                
                # Fetch commits from the specific repository
                repo_commits = {repo: fetch_repo_commits(repo, since, until, user)}
                
                # Aggregate and extract messages
                all_commits = aggregate_commits_by_author(repo_commits, user)
                commit_messages = [commit['message'] for commit in all_commits]
                
                logger.info("Found %d commits in repository '%s'", len(commit_messages), repo)
                
                # Cache the successful results
                cache.set(cache_key, commit_messages)
                return commit_messages
                
            except Exception as e:
                logger.error("Error fetching from repository '%s': %s", repo, e)
                # Fall back to empty results for single repo errors
                cache.set(cache_key, [])
                return []

        # Repository discovery and parallel processing mode
        try:
            # Discover repositories for the user
            logger.info("Discovering repositories for user '%s'%s", user, f" in org '{org}'" if org else "")
            repositories = discover_user_repositories(user, org)
            
            if not repositories:
                logger.warning("No repositories found for user '%s'", user)
                cache.set(cache_key, [])
                return []
            
            # Filter repositories by activity in date range for efficiency
            active_repos = filter_repositories_by_activity(repositories, since, until)
            repo_names = [repo['full_name'] for repo in active_repos]
            
            if not repo_names:
                logger.info("No repositories have activity in the date range %s to %s", since, until)
                cache.set(cache_key, [])
                return []
            
            logger.info("Processing %d repositories with potential activity", len(repo_names))
            
            # Generate operation ID for state tracking
            operation_id = f"commits_{user}_{since}_{until}_{uuid.uuid4().hex[:8]}"
            
            # Use parallel processing to fetch commits from all repositories
            repo_commits = fetch_commits_parallel(
                operation_id=operation_id,
                repositories=repo_names,
                since=since,
                until=until,
                author_filter=user
            )
            
            # Aggregate commits by author and extract messages
            all_commits = aggregate_commits_by_author(repo_commits, user)
            commit_messages = [commit['message'] for commit in all_commits]
            
            logger.info("Found %d commits across %d repositories", len(commit_messages), len(repo_names))
            
            # Cache the successful results
            cache.set(cache_key, commit_messages)
            return commit_messages
            
        except CircuitOpenError:
            logger.warning(
                "Circuit open for commit search (user: '%s', %s to %s). Falling back to extended-TTL cache.",
                user, since, until
            )
            # On circuit open, try to find *any* cached data, even if it's stale
            stale_cached_commits = cache.get(cache_key, max_age_hours=168)  # 7 days
            if stale_cached_commits:
                logger.info("Using stale cached commits for '%s' (%d commits)", 
                           user, len(stale_cached_commits))
                return stale_cached_commits
            # If no cache is available, re-raise to fail gracefully
            logger.error("No cached commit data available for %s during circuit open state.", user)
            raise

        except Exception as e:
            logger.error("Error fetching GitHub activity: %s", e)
            
            # Try to return cached results as fallback
            logger.info("Attempting to use cached results as fallback...")
            cached_commits = cache.get(cache_key, max_age_hours=168)  # 7 days for fallback
            if cached_commits is not None:
                logger.info("Using cached results (%d commits) due to error", len(cached_commits))
                return cached_commits
            else:
                logger.warning("No cached results available for fallback")
                
            # Re-raise the original error if no fallback available
            raise
    
    # Apply retry decorator and execute
    return retry_decorator(_fetch_with_retry)()