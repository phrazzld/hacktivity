"""Repository discovery module for GitHub API."""

import json
import subprocess
import sys
from typing import List, Dict, Optional, Any

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from . import cache
from .circuit_breaker import protected_call, CircuitOpenError
from .graphql import GraphQLClient, GraphQLError
from .rate_limiter import get_rate_limit_coordinator
from .logging import get_logger

logger = get_logger(__name__)

# Lazy import config to avoid circular imports
def _get_config():
    from .config import get_config
    return get_config()


def _generate_repo_cache_key(user: str, org_filter: Optional[str] = None) -> str:
    """Generate a unique cache key for repository discovery.
    
    Args:
        user: GitHub username
        org_filter: Optional organization filter
        
    Returns:
        Unique cache key string
    """
    org_part = org_filter or "all"
    return f"repos:{user}:{org_part}"


def _parse_repository_data(api_repos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Parse repository data from GitHub API response.
    
    Args:
        api_repos: Raw repository data from GitHub API
        
    Returns:
        List of parsed repository dictionaries with key fields
    """
    parsed_repos = []
    
    for repo in api_repos:
        # Extract key repository information
        parsed_repo = {
            'full_name': repo.get('full_name', ''),
            'name': repo.get('name', ''),
            'owner': repo.get('owner', {}),
            'private': repo.get('private', False),
            'language': repo.get('language'),
            'created_at': repo.get('created_at', ''),
            'updated_at': repo.get('updated_at', ''),
            'archived': repo.get('archived', False),
            'fork': repo.get('fork', False),
            'default_branch': repo.get('default_branch', 'main'),
            'size': repo.get('size', 0),
            'stargazers_count': repo.get('stargazers_count', 0),
            'forks_count': repo.get('forks_count', 0)
        }
        
        parsed_repos.append(parsed_repo)
    
    return parsed_repos


def _fetch_repositories_with_api(endpoint: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Fetch repositories from GitHub API with pagination.
    
    Args:
        endpoint: GitHub API endpoint (e.g., 'user/repos', 'orgs/myorg/repos')
        params: Query parameters for the API call
        
    Returns:
        List of repository data from API
        
    Raises:
        subprocess.CalledProcessError: If API call fails
        subprocess.TimeoutExpired: If API call times out
    """
    config = _get_config()
    all_repos = []
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
            full_endpoint
        ]
        
        logger.debug("Fetching repositories page %d from %s", page, endpoint)
        
        try:
            # Define the subprocess call as a zero-argument lambda for circuit breaker
            def api_runner():
                # Acquire a global rate limit token before making the call
                get_rate_limit_coordinator().acquire()
                
                return subprocess.run(
                    command,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=config.github.timeout_seconds
                )
            
            # Wrap the runner with the circuit breaker
            # Use the logical endpoint path as the circuit breaker key
            result = protected_call(endpoint, api_runner)
            
            # Parse the JSON response
            page_repos = json.loads(result.stdout)
            
            # Handle case where API returns dict instead of list (shouldn't happen for repo endpoints)
            if isinstance(page_repos, dict):
                # Some endpoints wrap results in a dict with 'repositories' key
                page_repos = page_repos.get('repositories', [])
            
            # If no repos on this page, we're done
            if not page_repos:
                break
                
            all_repos.extend(page_repos)
            
            # If we got fewer items than requested per page, we've reached the end
            if len(page_repos) < per_page:
                break
                
            # Check configured page limit
            if page >= config.github.max_pages:
                logger.warning("Reached maximum page limit (%d) for repository discovery", 
                             config.github.max_pages)
                break
                
            page += 1
            
        except json.JSONDecodeError as e:
            logger.error("Error parsing GitHub API response from %s: %s", endpoint, e)
            break
    
    logger.info("Fetched %d repositories from %s", len(all_repos), endpoint)
    return all_repos


def _discover_repos_with_graphql(user: str, org_filter: Optional[str]) -> List[Dict[str, Any]]:
    """
    Fetches repositories using the GitHub GraphQL API, handling pagination.
    Normalizes the response to match the existing REST API data structure.
    """
    client = GraphQLClient()
    all_repo_nodes = []
    
    if org_filter:
        # GraphQL query for organization repositories
        query = """
        query($login: String!, $first: Int!, $after: String) {
          organization(login: $login) {
            repositories(first: $first, after: $after, orderBy: {field: UPDATED_AT, direction: DESC}) {
              pageInfo { hasNextPage endCursor }
              nodes {
                name
                nameWithOwner
                isPrivate
                isFork
                isArchived
                updatedAt
                createdAt
                defaultBranchRef { name }
                stargazerCount
                forkCount
                diskUsage
                owner { login }
                primaryLanguage { name }
              }
            }
          }
        }
        """
        variables = {"login": org_filter, "first": 100, "after": None}
        
        while True:
            data = client.run_query(query, variables)
            repo_data = data.get("organization", {}).get("repositories", {})
            nodes = repo_data.get("nodes", [])
            all_repo_nodes.extend(nodes)

            page_info = repo_data.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            variables["after"] = page_info.get("endCursor")
            
    else:
        # GraphQL query for user repositories (owned + collaborator)
        query = """
        query($login: String!, $first: Int!, $after: String) {
          user(login: $login) {
            repositories(first: $first, after: $after, affiliations: [OWNER, COLLABORATOR], orderBy: {field: UPDATED_AT, direction: DESC}) {
              pageInfo { hasNextPage endCursor }
              nodes {
                name
                nameWithOwner
                isPrivate
                isFork
                isArchived
                updatedAt
                createdAt
                defaultBranchRef { name }
                stargazerCount
                forkCount
                diskUsage
                owner { login }
                primaryLanguage { name }
              }
            }
          }
        }
        """
        variables = {"login": user, "first": 100, "after": None}
        
        while True:
            data = client.run_query(query, variables)
            repo_data = data.get("user", {}).get("repositories", {})
            nodes = repo_data.get("nodes", [])
            all_repo_nodes.extend(nodes)

            page_info = repo_data.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            variables["after"] = page_info.get("endCursor")

    # Normalize GraphQL nodes to the shape expected by the rest of the application
    normalized_repos = []
    for n in all_repo_nodes:
        normalized_repo = {
            "full_name": n["nameWithOwner"], 
            "name": n["name"], 
            "owner": n["owner"],
            "private": n["isPrivate"], 
            "fork": n["isFork"], 
            "archived": n["isArchived"],
            "updated_at": n["updatedAt"], 
            "created_at": n["createdAt"],
            "default_branch": (n.get("defaultBranchRef") or {}).get("name", "main"),
            "language": (n.get("primaryLanguage") or {}).get("name"),
            "stargazers_count": n["stargazerCount"], 
            "forks_count": n["forkCount"],
            "size": n.get("diskUsage", 0)
        }
        normalized_repos.append(normalized_repo)
        
    return normalized_repos


def discover_user_repositories(user: str, org_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Discover all repositories accessible to a user.
    
    This function finds repositories in two ways:
    1. If org_filter is provided: fetch repositories from that organization
    2. If no org_filter: fetch user's own repos + repos they collaborate on
    
    Results are cached for 7 days (168 hours) to minimize API calls.
    
    Args:
        user: GitHub username
        org_filter: Optional organization name to filter by
        
    Returns:
        List of repository dictionaries with metadata
        
    Raises:
        SystemExit: If API calls fail and no cached fallback is available
    """
    config = _get_config()
    
    # Generate cache key and check for cached results
    cache_key = _generate_repo_cache_key(user, org_filter)
    cached_repos = cache.get(cache_key, max_age_hours=168)  # 7 days TTL
    
    if cached_repos is not None:
        logger.info("Using cached repository list for '%s' (org: %s) - %d repositories", 
                   user, org_filter or 'all', len(cached_repos))
        return cached_repos
    
    logger.info("Discovering repositories for '%s' (org: %s)...", user, org_filter or 'all')
    
    # --- GraphQL-First Approach ---
    if GraphQLClient.is_available():
        try:
            logger.debug("Attempting repository discovery via GraphQL.")
            graphql_repos = _discover_repos_with_graphql(user, org_filter)
            logger.info("GraphQL discovery successful, found %d repositories.", len(graphql_repos))
            
            # Remove duplicates and parse the same way as REST
            parsed_repos = _parse_repository_data(graphql_repos)
            seen_repos = set()
            unique_repos = []
            for repo in parsed_repos:
                if repo['full_name'] not in seen_repos:
                    seen_repos.add(repo['full_name'])
                    unique_repos.append(repo)
            
            # Cache the successful results
            cache.set(cache_key, unique_repos)
            return unique_repos
            
        except (GraphQLError, CircuitOpenError, subprocess.CalledProcessError) as e:
            logger.warning("GraphQL discovery failed (%s). Falling back to REST.", e)
            if not config.github.graphql_fallback_enabled:
                raise
    
    # --- Fallback to REST API ---
    logger.info("Using REST API for repository discovery.")
    
    # Create dynamic retry decorator with config values
    retry_decorator = retry(
        stop=stop_after_attempt(config.github.retry_attempts),
        wait=wait_exponential(multiplier=1, min=config.github.retry_min_wait, max=config.github.retry_max_wait),
        retry=retry_if_exception_type(subprocess.TimeoutExpired),
        reraise=True
    )
    
    def _discover_with_retry():
        try:
            all_repos = []
            
            if org_filter:
                # Fetch repositories from specific organization
                logger.info("Fetching repositories from organization: %s", org_filter)
                org_repos = _fetch_repositories_with_api(
                    f"orgs/{org_filter}/repos",
                    {'type': 'all'}  # all, public, private, forks, sources, member
                )
                all_repos.extend(org_repos)
                
            else:
                # Fetch user's own repositories
                logger.info("Fetching user's own repositories")
                user_repos = _fetch_repositories_with_api(
                    "user/repos",
                    {'affiliation': 'owner', 'sort': 'updated', 'direction': 'desc'}
                )
                all_repos.extend(user_repos)
                
                # Fetch repositories user collaborates on
                logger.info("Fetching repositories user collaborates on")
                collab_repos = _fetch_repositories_with_api(
                    "user/repos", 
                    {'affiliation': 'collaborator', 'sort': 'updated', 'direction': 'desc'}
                )
                all_repos.extend(collab_repos)
            
            # Parse and clean repository data
            parsed_repos = _parse_repository_data(all_repos)
            
            # Remove duplicates based on full_name (can happen with collaborator repos)
            seen_repos = set()
            unique_repos = []
            for repo in parsed_repos:
                if repo['full_name'] not in seen_repos:
                    seen_repos.add(repo['full_name'])
                    unique_repos.append(repo)
            
            logger.info("Found %d unique repositories (filtered from %d total)", 
                       len(unique_repos), len(parsed_repos))
            
            # Cache the successful results
            cache.set(cache_key, unique_repos)
            return unique_repos
            
        except CircuitOpenError:
            logger.warning(
                "Circuit open for repository discovery (user: '%s', org: %s). Falling back to extended-TTL cache.",
                user, org_filter or 'all'
            )
            # On circuit open, try to find *any* cached data, even if it's stale
            stale_cached_repos = cache.get(cache_key, max_age_hours=168)  # 7 days
            if stale_cached_repos:
                logger.info("Using stale cached repositories for '%s' (org: %s) - %d repositories", 
                           user, org_filter or 'all', len(stale_cached_repos))
                return stale_cached_repos
            # If no cache is available, re-raise to fail gracefully
            logger.error("No cached repository data available for %s during circuit open state.", user)
            raise
            
        except subprocess.CalledProcessError as e:
            logger.error("Error discovering repositories: %s", e.stderr)
            # Try to parse the error for more helpful messages
            try:
                error_details = json.loads(e.stderr)
                if 'message' in error_details:
                    logger.error("GitHub API Error: %s", error_details['message'])
            except json.JSONDecodeError:
                pass  # Not a JSON error, just print the raw stderr
            sys.exit(1)
            
        except subprocess.TimeoutExpired as e:
            logger.error("Repository discovery timed out. Retrying with exponential backoff...")
            raise  # Let tenacity handle the retry
    
    # Apply retry decorator and execute
    return retry_decorator(_discover_with_retry)()


def get_repository_count(user: str, org_filter: Optional[str] = None) -> int:
    """
    Get the count of repositories for a user without fetching full details.
    
    This is useful for progress estimation and quick checks.
    
    Args:
        user: GitHub username
        org_filter: Optional organization name to filter by
        
    Returns:
        Number of repositories accessible to the user
    """
    repos = discover_user_repositories(user, org_filter)
    return len(repos)


def filter_repositories_by_activity(repos: List[Dict[str, Any]], 
                                   since: str, 
                                   until: str) -> List[Dict[str, Any]]:
    """
    Filter repositories that might have activity in the given date range.
    
    This is a heuristic filter based on repository updated_at timestamps.
    It's not perfect but helps prioritize which repos to check for commits.
    
    Args:
        repos: List of repository dictionaries
        since: Start date in YYYY-MM-DD format
        until: End date in YYYY-MM-DD format
        
    Returns:
        Filtered list of repositories likely to have activity in date range
    """
    from datetime import datetime, timezone
    
    try:
        since_dt = datetime.fromisoformat(since + "T00:00:00Z").replace(tzinfo=timezone.utc)
        until_dt = datetime.fromisoformat(until + "T23:59:59Z").replace(tzinfo=timezone.utc)
    except ValueError as e:
        logger.warning("Invalid date format for filtering: %s", e)
        return repos  # Return all repos if date parsing fails
    
    filtered_repos = []
    for repo in repos:
        try:
            # Parse repository's last update time
            updated_at = repo.get('updated_at', '')
            if not updated_at:
                # Include repos without update timestamp to be safe
                filtered_repos.append(repo)
                continue
                
            # GitHub timestamps are in ISO format with Z suffix
            repo_updated = datetime.fromisoformat(updated_at.replace('Z', '+00:00'))
            
            # Include if repository was updated within or after our date range
            # We use a broader range (updated since 30 days before 'since') to be safe
            from datetime import timedelta
            extended_since = since_dt - timedelta(days=30)
            
            if repo_updated >= extended_since:
                filtered_repos.append(repo)
                
        except (ValueError, TypeError) as e:
            logger.debug("Error parsing updated_at for repo %s: %s", 
                        repo.get('full_name', 'unknown'), e)
            # Include repos with parsing errors to be safe
            filtered_repos.append(repo)
    
    logger.info("Filtered %d repositories (from %d total) likely to have activity in date range", 
               len(filtered_repos), len(repos))
    return filtered_repos