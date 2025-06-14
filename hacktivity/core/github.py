"""GitHub API integration module."""

import json
import subprocess
import sys
from typing import List, Optional, Dict, Any

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
        result = subprocess.run(["gh", "auth", "status"], check=True, capture_output=True, text=True)
        logger.debug("GitHub authentication status: %s", result.stderr)
    except subprocess.CalledProcessError as e:
        logger.error("You are not logged into the GitHub CLI ('gh').")
        logger.error("Please run 'gh auth login' to authenticate.")
        logger.error("For private organization repositories, ensure your token has 'repo' and 'read:org' scopes")
        sys.exit(1)
    
    # Check token scopes for private repo access
    _check_token_scopes()


def _check_token_scopes() -> None:
    """Check if the current GitHub token has required scopes for private org repos."""
    try:
        # Get current token scopes
        result = subprocess.run(
            ["gh", "api", "user", "-i"],
            check=True,
            capture_output=True,
            text=True
        )
        
        # Extract X-OAuth-Scopes header from response
        headers = result.stdout.split('\n')
        scopes_line = None
        for line in headers:
            if line.lower().startswith('x-oauth-scopes:'):
                scopes_line = line
                break
        
        if scopes_line:
            # Parse scopes from header
            scopes_str = scopes_line.split(':', 1)[1].strip()
            scopes = [s.strip() for s in scopes_str.split(',') if s.strip()]
            
            required_scopes = ['repo']  # repo scope covers private repos
            missing_scopes = [scope for scope in required_scopes if scope not in scopes]
            
            if missing_scopes:
                logger.warning("GitHub token missing required scopes: %s", missing_scopes)
                logger.warning("To access private organization repositories:")
                logger.warning("1. Go to https://github.com/settings/tokens")
                logger.warning("2. Edit your token to include 'repo' scope")
                logger.warning("3. Or re-run: gh auth login --scopes repo,read:org")
                logger.warning("Current scopes: %s", scopes)
            else:
                logger.debug("GitHub token has required scopes: %s", scopes)
        else:
            logger.debug("Could not determine GitHub token scopes")
            
    except subprocess.CalledProcessError:
        logger.debug("Could not check GitHub token scopes")


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


def fetch_commits_by_repository(user: str, since: str, until: str, org: Optional[str] = None, repo: Optional[str] = None) -> Dict[str, List[Dict[str, Any]]]:
    """
    Fetches commit activity from GitHub grouped by repository.
    Similar to fetch_commits but preserves repository structure.
    
    Args:
        user: GitHub username
        since: Start date in YYYY-MM-DD format
        until: End date in YYYY-MM-DD format
        org: Optional organization filter
        repo: Optional repository filter (e.g., 'owner/repo-name')
        
    Returns:
        Dictionary mapping repository names to lists of commit data
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
    
    def _fetch_repo_grouped_with_retry():
        # Generate cache key and check for cached results
        cache_key = f"repo_grouped:{_generate_cache_key(user, since, until, org, repo)}"
        cached_repo_commits = cache.get(cache_key)
        
        if cached_repo_commits is not None:
            logger.info("Using cached repository-grouped results for '%s' from %s to %s", user, since, until)
            return cached_repo_commits
        
        logger.info("Fetching repository-grouped commits for '%s' from %s to %s...", user, since, until)

        # Handle single repository case
        if repo:
            logger.info("Single repository mode: %s", repo)
            try:
                from .commits import fetch_repo_commits
                
                # Fetch commits from the specific repository
                raw_commits = fetch_repo_commits(repo, since, until, user)
                
                # Filter by author and add repository field
                filtered_commits = []
                for commit in raw_commits:
                    if commit.get('author_login') == user:
                        commit_with_repo = commit.copy()
                        commit_with_repo['repository'] = repo
                        filtered_commits.append(commit_with_repo)
                
                repo_commits = {repo: filtered_commits}
                
                logger.info("Found %d commits in repository '%s'", len(filtered_commits), repo)
                
                # Cache the successful results
                cache.set(cache_key, repo_commits)
                return repo_commits
                
            except Exception as e:
                logger.error("Error fetching from repository '%s': %s", repo, e)
                # Fall back to empty results for single repo errors
                cache.set(cache_key, {})
                return {}

        # Repository discovery and parallel processing mode
        try:
            # Discover repositories for the user
            logger.info("Discovering repositories for user '%s'%s", user, f" in org '{org}'" if org else "")
            repositories = discover_user_repositories(user, org)
            
            if not repositories:
                logger.warning("No repositories found for user '%s'", user)
                cache.set(cache_key, {})
                return {}
            
            # Filter repositories by activity in date range for efficiency
            active_repos = filter_repositories_by_activity(repositories, since, until)
            repo_names = [repo['full_name'] for repo in active_repos]
            
            if not repo_names:
                logger.info("No repositories have activity in the date range %s to %s", since, until)
                cache.set(cache_key, {})
                return {}
            
            logger.info("Processing %d repositories with potential activity", len(repo_names))
            
            # Generate operation ID for state tracking
            operation_id = f"repo_commits_{user}_{since}_{until}_{uuid.uuid4().hex[:8]}"
            
            # Use parallel processing to fetch commits from all repositories
            repo_commits = fetch_commits_parallel(
                operation_id=operation_id,
                repositories=repo_names,
                since=since,
                until=until,
                author_filter=user
            )
            
            # Filter each repository's commits by author and add repository context
            filtered_repo_commits = {}
            for repo_name, commits in repo_commits.items():
                filtered_commits = []
                for commit in commits:
                    if commit.get('author_login') == user:
                        commit_with_repo = commit.copy()
                        commit_with_repo['repository'] = repo_name
                        filtered_commits.append(commit_with_repo)
                
                if filtered_commits:  # Only include repos with user commits
                    filtered_repo_commits[repo_name] = filtered_commits
            
            total_commits = sum(len(commits) for commits in filtered_repo_commits.values())
            logger.info("Found %d commits across %d repositories", total_commits, len(filtered_repo_commits))
            
            # Cache the successful results
            cache.set(cache_key, filtered_repo_commits)
            return filtered_repo_commits
            
        except Exception as e:
            logger.error("Error fetching repository-grouped GitHub activity: %s", e)
            
            # Try to return cached results as fallback
            logger.info("Attempting to use cached results as fallback...")
            cached_repo_commits = cache.get(cache_key, max_age_hours=168)  # 7 days for fallback
            if cached_repo_commits is not None:
                logger.info("Using cached repository-grouped results due to error")
                return cached_repo_commits
            else:
                logger.warning("No cached repository-grouped results available for fallback")
                
            # Re-raise the original error if no fallback available
            raise
    
    # Apply retry decorator and execute
    return retry_decorator(_fetch_repo_grouped_with_retry)()