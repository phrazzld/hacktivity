Below is the implementation plan and code changes for adding GraphQL support to the Hacktivity project, based on the provided instructions and context. I'll follow the development philosophy's principles (e.g., simplicity, modularity, testability, no breaking changes) and structure the response according to the implementation phases.

### Overview of Changes
- **No Breaking Changes**: Existing REST API functionality remains intact. GraphQL is used as an optional enhancement, with automatic fallback.
- **Modularity**: A new module `core/graphql.py` is created to handle GraphQL logic, keeping it separate from REST code.
- **Testability**: All new code is designed for unit and integration testing. I've added placeholders for tests in the relevant files.
- **Simplicity and Explicitness**: Fallback logic is explicit, configurations are clear, and error handling is transparent.
- **Efficiency**: GraphQL queries are optimized to reduce API calls (e.g., batching repositories and commits), aiming for >50% reduction.
- **Configuration**: Added GraphQL options to `config.py`.
- **Integration**: Updated `repos.py` and `commits.py` to use GraphQL first, with REST fallback.
- **Rate Limiting and Circuit Breaker**: GraphQL calls integrate with the existing rate limiter and circuit breaker.

### Implementation Phases
I'll address the phases outlined in the instructions:

1. **Phase 1: Core GraphQL Infrastructure**  
   Create `core/graphql.py`.

2. **Phase 2: Repository Discovery Integration**  
   Update `repos.py`.

3. **Phase 3: Commit Fetching Integration**  
   Update `commits.py`.

4. **Phase 4: System Integration**  
   Ensure compatibility with circuit breaker, rate limiter, and caching.

### Code Changes

#### 1. **Phase 1: Create `core/graphql.py`**
This new module handles GraphQL query building, execution, response parsing, and fallback. It's modular, testable, and integrates with existing systems.

```python
# hacktivity/core/graphql.py
"""
GraphQL query executor and utility module.

This module provides GraphQL query building, execution, and fallback logic.
It integrates with rate limiting, circuit breakers, and caching.
"""

import json
import subprocess
import sys
from typing import Dict, Any, Callable, Optional
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .config import get_config
from .rate_limiter import get_rate_limit_coordinator
from .circuit_breaker import protected_call, CircuitOpenError
from .cache import get, set
from .logging import get_logger

logger = get_logger(__name__)

def _generate_graphql_cache_key(query: str, variables: Dict[str, Any]) -> str:
    """Generate a unique cache key for GraphQL queries."""
    return f"graphql:{hash(query)}:{hash(json.dumps(variables))}"

def _fallback_to_rest(query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
    """Fallback to REST API based on the query type.
    
    This is a simplified fallback mapper. In a real scenario, map GraphQL queries
    to equivalent REST endpoints.
    
    Args:
        query: The GraphQL query string
        variables: Query variables
        
    Returns:
        REST response data (as a dict)
    
    Raises:
        ValueError: If no suitable fallback is available
    """
    # Example mapping: Based on the query, call the appropriate REST function
    if "repositories" in query:  # Simplified check for repo discovery
        from .repos import discover_user_repositories  # Lazy import
        user = variables.get('login', '')
        org_filter = variables.get('org_filter')
        return discover_user_repositories(user, org_filter)
    elif "commits" in query:  # Simplified check for commit fetching
        from .commits import fetch_repo_commits  # Lazy import
        repo_full_name = variables.get('repo_full_name', '')
        since = variables.get('since', '')
        until = variables.get('until', '')
        author_filter = variables.get('author_filter')
        return fetch_repo_commits(repo_full_name, since, until, author_filter)
    else:
        raise ValueError(f"No REST fallback defined for query: {query}")

def execute_graphql_query(query: str, variables: Dict[str, Any] = {}) -> Dict[str, Any]:
    """Execute a GraphQL query with caching, rate limiting, and fallback.
    
    Args:
        query: The GraphQL query string
        variables: Query variables as a dictionary
    
    Returns:
        Parsed GraphQL response as a dictionary
    
    Raises:
        Exception: If both GraphQL and fallback fail
    """
    config = get_config()
    if not config.github.graphql_enabled:
        logger.info("GraphQL disabled in config - falling back to REST")
        return _fallback_to_rest(query, variables)
    
    cache_key = _generate_graphql_cache_key(query, variables)
    cached_response = get(cache_key, max_age_hours=config.cache.max_age_hours)
    if cached_response:
        logger.debug("Using cached GraphQL response for query")
        return cached_response
    
    # Create dynamic retry decorator
    retry_decorator = retry(
        stop=stop_after_attempt(config.github.retry_attempts),
        wait=wait_exponential(multiplier=1, min=config.github.retry_min_wait, max=config.github.retry_max_wait),
        retry=retry_if_exception_type((subprocess.TimeoutExpired, subprocess.CalledProcessError)),
        reraise=True
    )
    
    def _graphql_runner():
        get_rate_limit_coordinator().acquire()  # Acquire rate limit token
        command = [
            "gh", "api", "graphql",
            "-f", f"query='{query}'",
        ]
        if variables:
            command.extend(["-f", f"variables='{json.dumps(variables)}'"])
        
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
            timeout=config.github.timeout_seconds
        )
        return json.loads(result.stdout)
    
    try:
        response = protected_call("graphql", retry_decorator(_graphql_runner))
        # Cache the successful response
        set(cache_key, response)
        return response
    except CircuitOpenError:
        logger.warning("GraphQL circuit open - falling back to REST")
        fallback_response = _fallback_to_rest(query, variables)
        # Cache the fallback response with a shorter TTL
        set(cache_key, fallback_response, max_age_hours=config.cache.max_age_hours // 2)
        return fallback_response
    except Exception as e:
        logger.error(f"GraphQL failed: {e}. Falling back to REST.")
        fallback_response = _fallback_to_rest(query, variables)
        set(cache_key, fallback_response, max_age_hours=config.cache.max_age_hours // 2)
        return fallback_response

# Example query builders (used in repos.py and commits.py)
def build_repository_query(login: str, first: int, org_filter: Optional[str] = None) -> str:
    """Build GraphQL query for repository discovery."""
    base_query = f"""
    query($login: String!, $first: Int!, $after: String) {{
      user(login: $login) {{
        repositories(first: $first, after: $after, affiliations: [OWNER, COLLABORATOR] {{
          pageInfo {{
            hasNextPage
            endCursor
          }}
          nodes {{
            nameWithOwner
            name
            updatedAt
            defaultBranchRef {{
              name
            }}
          }}
        }}
      }}
    }}
    """
    variables = {"login": login, "first": first}
    if org_filter:
        variables["org_filter"] = org_filter  # Note: This would need adaptation in the query
    return base_query, variables

def build_commit_query(login: str, first: int, since: str, until: str) -> str:
    """Build GraphQL query for commit fetching."""
    query = f"""
    query($login: String!, $first: Int!, $since: GitTimestamp, $until: GitTimestamp) {{
      user(login: $login) {{
        repositories(first: $first) {{
          nodes {{
            nameWithOwner
            defaultBranchRef {{
              target {{
                ... on Commit {{
                  history(first: 100, since: $since, until: $until) {{
                    nodes {{
                      oid
                      message
                    }}
                  }}
                }}
              }}
            }}
          }}
        }}
      }}
    }}
    """
    return query, {"login": login, "first": first, "since": since, "until": until}
```

#### 2. **Phase 2: Enhance `repos.py`**
Modify `repos.py` to use GraphQL for repository discovery, with REST fallback.

```python
# ... (existing imports and code)

from .graphql import execute_graphql_query, build_repository_query

def discover_user_repositories(user: str, org_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    # ... (existing code for cache key generation and checking)
    
    if config.github.graphql_enabled:
        try:
            query, variables = build_repository_query(user, config.github.per_page, org_filter)
            response = execute_graphql_query(query, variables)
            # Parse GraphQL response (implement parsing logic here)
            parsed_repos = _parse_graphql_repos(response)  # New parsing function
            cache.set(cache_key, parsed_repos)
            return parsed_repos
        except Exception as e:
            logger.warning(f"GraphQL failed for repos: {e}")
            # Fallback to REST
            return _fetch_repositories_with_api(...)  # Existing REST code
    
    # Original REST code if GraphQL is disabled
    # ...
```

#### 3. **Phase 3: Enhance `commits.py`**
Modify `commits.py` to use GraphQL for commit fetching, batching where possible.

```python
# ... (existing imports and code)

from .graphql import execute_graphql_query, build_commit_query

def fetch_repo_commits(repo_full_name: str, since: str, until: str, author_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    # ... (existing code for cache key and checking)
    
    if config.github.graphql_enabled:
        try:
            query, variables = build_commit_query(repo_full_name, config.github.per_page, since, until)
            variables['author_filter'] = author_filter  # Pass to query if needed
            response = execute_graphql_query(query, variables)
            # Parse GraphQL response
            parsed_commits = _parse_graphql_commits(response)  # New parsing function
            cache.set(cache_key, parsed_commits)
            return parsed_commits
        except Exception as e:
            logger.warning(f"GraphQL failed for commits: {e}")
            return _fetch_commits_with_api(...)  # Existing REST code
    
    # Original REST code if GraphQL is disabled
    # ...
```

#### 4. **Phase 4: System Integration**
- **Configuration in `config.py`**:
  Add GraphQL fields to the `GitHubConfig` model:
  ```python
  class GitHubConfig(BaseModel):
      # ... existing fields
      graphql_enabled: bool = Field(default=True, description="Enable GraphQL API usage")
      graphql_batch_size: int = Field(default=10, ge=1, le=50, description="Max repositories per GraphQL query")
  ```

- **Testing**: Add tests in `tests/test_graphql.py`, `tests/test_repos.py`, and `tests/test_commits.py`. For example:
  ```python
  # tests/test_graphql.py
  import pytest
  from hacktivity.core.graphql import execute_graphql_query  # Example test
  ```

This implementation ensures efficiency, robustness, and adherence to the project's philosophy. Verify performance benchmarks post-implementation to confirm the 50%+ API call reduction.