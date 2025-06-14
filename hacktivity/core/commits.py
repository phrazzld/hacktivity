"""Repository-based commit fetching module for GitHub API."""

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
            full_endpoint
        ]
        
        logger.debug("Fetching commits page %d from %s", page, endpoint)
        
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
            page_commits = json.loads(result.stdout)
            
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
    
    logger.info("Fetched %d commits from %s", len(all_commits), endpoint)
    return all_commits


def _resolve_user_id_graphql(username: str) -> str | None:
    """Resolves a GitHub username to its internal GraphQL node ID for filtering."""
    client = GraphQLClient()
    query = "query($login: String!) { user(login: $login) { id } }"
    variables = {"login": username}
    try:
        data = client.run_query(query, variables)
        return data.get("user", {}).get("id")
    except Exception as e:
        logger.warning("Could not resolve user ID for '%s': %s", username, e)
        return None


def _fetch_commits_with_graphql(
    repo_list: List[str], since: str, until: str, author_id: str | None
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Fetches commits for a batch of repositories in a single GraphQL query.
    This is a key performance optimization.
    """
    client = GraphQLClient()
    repo_commits: Dict[str, List[Dict[str, Any]]] = {name: [] for name in repo_list}

    # Build a dynamic query to fetch multiple repositories by alias
    query_parts = []
    variables = {
        "since": f"{since}T00:00:00Z",
        "until": f"{until}T23:59:59Z",
    }
    
    # Only add author filter if we have a valid author ID
    if author_id:
        variables["author"] = {"id": author_id}

    for i, repo_full_name in enumerate(repo_list):
        owner, name = repo_full_name.split("/", 1)
        # Using aliases to query multiple distinct repositories in one go
        author_clause = ", author: $author" if author_id else ""
        query_parts.append(
            f"""
            repo{i}: repository(owner: "{owner}", name: "{name}") {{
              nameWithOwner
              defaultBranchRef {{
                target {{
                  ... on Commit {{
                    history(first: 100, since: $since, until: $until{author_clause}) {{
                      nodes {{
                        oid
                        message
                        author {{ name email date user {{ login id }} }}
                        committer {{ name email date }}
                        url
                      }}
                    }}
                  }}
                }}
              }}
            }}
            """
        )
    
    query_args = "$since: GitTimestamp!, $until: GitTimestamp!"
    if author_id:
        query_args += ", $author: CommitAuthor"
        
    query = f"""
    query({query_args}) {{
      {"".join(query_parts)}
    }}
    """
    
    data = client.run_query(query, variables)

    # Normalize the batched response
    for i, repo_full_name in enumerate(repo_list):
        repo_data = data.get(f"repo{i}")
        if not repo_data:
            continue

        history = (
            repo_data.get("defaultBranchRef", {})
            .get("target", {})
            .get("history", {})
            .get("nodes", [])
        )

        # Transform GraphQL response to match REST format for existing parser
        commits_to_parse = []
        for c in history:
            commit_to_parse = {
                "sha": c["oid"], 
                "commit": {
                    "message": c["message"], 
                    "author": c["author"], 
                    "committer": c["committer"]
                }, 
                "url": c["url"], 
                "html_url": c["url"],
                "author": c["author"].get("user")  # GitHub user object
            }
            commits_to_parse.append(commit_to_parse)
            
        repo_commits[repo_full_name] = _parse_commit_data(commits_to_parse)

    return repo_commits


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
        wait=wait_exponential(multiplier=1, min=config.github.retry_min_wait, max=config.github.retry_max_wait),
        retry=retry_if_exception_type(subprocess.TimeoutExpired),
        reraise=True
    )
    
    def _fetch_with_retry():
        try:
            # Convert date strings to ISO 8601 format for GitHub API
            since_iso = f"{since}T00:00:00Z"
            until_iso = f"{until}T23:59:59Z"
            
            # Fetch commits using repository endpoint
            endpoint = f"repos/{repo_full_name}/commits"
            params = {
                'since': since_iso,
                'until': until_iso
            }
            
            api_commits = _fetch_commits_with_api(endpoint, params)
            
            # Parse commit data
            parsed_commits = _parse_commit_data(api_commits)
            
            # Filter by author if specified
            if author_filter:
                parsed_commits = _filter_commits_by_author(parsed_commits, author_filter)
                logger.info("Filtered to %d commits by author '%s'", len(parsed_commits), author_filter)
            
            logger.info("Found %d commits for repository '%s'", len(parsed_commits), repo_full_name)
            
            # Cache the successful results
            cache.set(cache_key, parsed_commits)
            return parsed_commits
            
        except CircuitOpenError:
            logger.warning(
                "Circuit open for '%s'. Falling back to extended-TTL cache.",
                repo_full_name
            )
            # On circuit open, try to find *any* cached data, even if it's stale
            stale_cached_commits = cache.get(cache_key, max_age_hours=168)  # 7 days
            if stale_cached_commits:
                logger.info("Using stale cached commits for '%s' (%d commits)", 
                           repo_full_name, len(stale_cached_commits))
                return stale_cached_commits
            # If no cache is available, re-raise to fail gracefully
            logger.error("No cached data available for %s during circuit open state.", repo_full_name)
            raise
            
        except subprocess.CalledProcessError as e:
            logger.error("Error fetching commits from repository '%s': %s", repo_full_name, e.stderr)
            # Try to parse the error for more helpful messages
            try:
                error_details = json.loads(e.stderr)
                if 'message' in error_details:
                    logger.error("GitHub API Error: %s", error_details['message'])
            except json.JSONDecodeError:
                pass  # Not a JSON error, just print the raw stderr
            sys.exit(1)
            
        except subprocess.TimeoutExpired as e:
            logger.error("Commit fetching timed out for repository '%s'. Retrying with exponential backoff...", repo_full_name)
            raise  # Let tenacity handle the retry
    
    # Apply retry decorator and execute
    return retry_decorator(_fetch_with_retry)()


def get_commit_count(repo_full_name: str, since: str, until: str, author_filter: Optional[str] = None) -> int:
    """
    Get the count of commits for a repository without fetching full details.
    
    This is useful for progress estimation and quick checks.
    
    Args:
        repo_full_name: Repository full name (e.g., 'owner/repo-name')
        since: Start date in YYYY-MM-DD format
        until: End date in YYYY-MM-DD format
        author_filter: Optional GitHub username to filter commits by
        
    Returns:
        Number of commits in the repository for the date range and author
    """
    commits = fetch_repo_commits(repo_full_name, since, until, author_filter)
    return len(commits)


def fetch_commits_from_multiple_repos(repo_list: List[str], since: str, until: str, author_filter: Optional[str] = None) -> Dict[str, List[Dict[str, Any]]]:
    """
    Fetch commits from multiple repositories.
    
    This is a convenience function for processing multiple repositories
    in the new repository-first architecture.
    
    Args:
        repo_list: List of repository full names (e.g., ['owner/repo1', 'owner/repo2'])
        since: Start date in YYYY-MM-DD format  
        until: End date in YYYY-MM-DD format
        author_filter: Optional GitHub username to filter commits by
        
    Returns:
        Dictionary mapping repository names to their commit lists
    """
    all_repo_commits = {}
    config = _get_config()
    
    # --- GraphQL-First Approach ---
    if GraphQLClient.is_available():
        author_id = None
        if author_filter:
            author_id = _resolve_user_id_graphql(author_filter)
            if not author_id:
                logger.warning("Could not resolve author '%s' to a GitHub ID. Proceeding without author filter for GraphQL.", author_filter)

        try:
            logger.debug("Attempting to fetch commits via GraphQL for %d repos.", len(repo_list))
            # Process repositories in batches to stay within GraphQL limits
            for i in range(0, len(repo_list), config.github.graphql_batch_size):
                batch = repo_list[i:i + config.github.graphql_batch_size]
                logger.debug("Processing GraphQL batch %d/%d (repos %d-%d)", 
                           i // config.github.graphql_batch_size + 1,
                           (len(repo_list) + config.github.graphql_batch_size - 1) // config.github.graphql_batch_size,
                           i + 1, min(i + config.github.graphql_batch_size, len(repo_list)))
                
                batch_commits = _fetch_commits_with_graphql(batch, since, until, author_id)
                all_repo_commits.update(batch_commits)
            
            logger.info("GraphQL commit fetch successful for %d repositories.", len(repo_list))
            
            # Apply author filter post-processing if we couldn't resolve the author ID
            if author_filter and not author_id:
                for repo_name, commits in all_repo_commits.items():
                    filtered_commits = _filter_commits_by_author(commits, author_filter)
                    all_repo_commits[repo_name] = filtered_commits
                    
            total_commits = sum(len(commits) for commits in all_repo_commits.values())
            logger.info("Fetched %d total commits from %d repositories", total_commits, len(repo_list))
            return all_repo_commits
            
        except (GraphQLError, CircuitOpenError, subprocess.CalledProcessError) as e:
            logger.warning("GraphQL commit fetching failed (%s). Falling back to REST.", e)
            if not config.github.graphql_fallback_enabled:
                raise

    # --- Fallback to REST API ---
    logger.info("Fetching commits via REST for %d repositories.", len(repo_list))
    for repo_full_name in repo_list:
        try:
            repo_commits = fetch_repo_commits(repo_full_name, since, until, author_filter)
            all_repo_commits[repo_full_name] = repo_commits
            logger.info("Successfully fetched %d commits from '%s'", len(repo_commits), repo_full_name)
        except Exception as e:
            logger.warning("Failed to fetch commits from '%s': %s", repo_full_name, e)
            # Continue with other repositories instead of failing entirely
            all_repo_commits[repo_full_name] = []

    total_commits = sum(len(commits) for commits in all_repo_commits.values())
    logger.info("Fetched %d total commits from %d repositories", total_commits, len(repo_list))
    return all_repo_commits


def aggregate_commits_by_author(repo_commits: Dict[str, List[Dict[str, Any]]], author_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Aggregate commits from multiple repositories, optionally filtering by author.
    
    Args:
        repo_commits: Dictionary mapping repository names to commit lists
        author_filter: Optional GitHub username to filter by
        
    Returns:
        Flat list of all commits, optionally filtered by author
    """
    all_commits = []
    
    for repo_name, commits in repo_commits.items():
        for commit in commits:
            # Add repository context to each commit
            commit_with_repo = commit.copy()
            commit_with_repo['repository'] = repo_name
            
            # Filter by author if specified
            if author_filter is None or commit.get('author_login') == author_filter:
                all_commits.append(commit_with_repo)
    
    # Sort by commit date (newest first)
    all_commits.sort(key=lambda c: c.get('commit_date', ''), reverse=True)
    
    logger.info("Aggregated %d commits across all repositories", len(all_commits))
    if author_filter:
        logger.info("Filtered to commits by author: %s", author_filter)
    
    return all_commits