### Step 1: Design GraphQL Queries for Repository and Commit Data

We need to create GraphQL queries that can fetch both repository metadata and commit history in a single request. Here’s how we’ll approach it:

1. **Repository Discovery Query**: Fetch all repositories accessible to the user (including owned and collaborated repositories) with pagination.
2. **Commit History Query**: For each repository, fetch the commit history within a given date range, filtered by author if needed.

Since the GitHub GraphQL API doesn't support filtering commits by author directly in the query, we'll need to fetch all commits and filter them on the client side. 

#### Repository Discovery Query

This query will fetch the user's repositories and organizations they belong to, with pagination. 

```graphql
query($login: String!, $first: Int!, $after: String) {
  user(login: $login) {
    repositories(first: $first, after: $after, ownerAffiliations: [OWNER, COLLABORATOR], orderBy: {field: UPDATED_AT, direction: DESC}) {
      pageInfo {
        hasNextPage
        endCursor
      }
      nodes {
        nameWithOwner
        name
        owner {
          login
        }
        updatedAt
        defaultBranchRef {
          name
        }
      }
    }
  }
}
```

#### Commit History Query

This query will fetch commits for a given repository within a specified date range, with pagination. 

```graphql
query($repoOwner: String!, $repoName: String!, $since: GitTimestamp!, $until: GitTimestamp!, $authorId: ID, $first: Int!, $after: String) {
  repository(owner: $repoOwner, name: $repoName) {
    defaultBranchRef {
      target {
        ... on Commit {
          history(first: $first, after: $after, since: $since, until: $until, author: {id: $authorId}) {
            pageInfo {
              hasNextPage
              endCursor
            }
            nodes {
              oid
              message
              author {
                name
                email
                date
                user {
                  login
                }
              }
              committer {
                name
                email
                date
              }
              url
            }
          }
        }
      }
    }
  }
}
```

Note: The `author` filter in the commit history is optional (`author: {id: $authorId}`). If `$authorId` is not provided, all commits in the range will be returned. We can filter commits by author on the client side if needed.

### Step 2: Implement GraphQL Query Execution

We'll create a new module `core/graphql.py` to handle GraphQL queries and responses. This module will:

1. Construct GraphQL queries with dynamic variables.
2. Execute queries using the `gh api graphql` command.
3. Parse the JSON response and handle pagination.
4. Normalize the response to match the existing data structures.

Here’s an outline of `core/graphql.py`:

```python
# core/graphql.py

import json
import subprocess
from typing import Any, Dict, List, Optional, Tuple

from .logging import get_logger
from .config import get_config
from .circuit_breaker import protected_call, CircuitOpenError
from .rate_limiter import get_rate_limit_coordinator

logger = get_logger(__name__)

# GraphQL queries
REPOSITORY_DISCOVERY_QUERY = """
query($login: String!, $first: Int!, $after: String) {
  user(login: $login) {
    repositories(first: $first, after: $after, ownerAffiliations: [OWNER, COLLABORATOR], orderBy: {field: UPDATED_AT, direction: DESC}) {
      pageInfo {
        hasNextPage
        endCursor
      }
      nodes {
        nameWithOwner
        name
        owner {
          login
        }
        updatedAt
        defaultBranchRef {
          name
        }
      }
    }
  }
}
"""

COMMIT_HISTORY_QUERY = """
query($repoOwner: String!, $repoName: String!, $since: GitTimestamp!, $until: GitTimestamp!, $authorId: ID, $first: Int!, $after: String) {
  repository(owner: $repoOwner, name: $repoName) {
    defaultBranchRef {
      target {
        ... on Commit {
          history(first: $first, after: $after, since: $since, until: $until, author: {id: $authorId}) {
            pageInfo {
              hasNextPage
              endCursor
            }
            nodes {
              oid
              message
              author {
                name
                email
                date
                user {
                  login
                }
              }
              committer {
                name
                email
                date
              }
              url
            }
          }
        }
      }
    }
  }
}
"""

def _execute_graphql_query(query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a GraphQL query using the `gh api graphql` command."""
    config = get_config()
    command = [
        "gh", "api", "graphql",
        "-F", f"query={query}",
        "-f", f"variables={json.dumps(variables)}"
    ]

    def api_runner():
        get_rate_limit_coordinator().acquire()
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=config.github.timeout_seconds
        )

    try:
        result = protected_call("graphql", api_runner)
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        logger.error("Error parsing GraphQL response: %s", e)
        raise
    except subprocess.CalledProcessError as e:
        logger.error("GraphQL query failed: %s", e.stderr)
        raise
    except CircuitOpenError as e:
        logger.warning("Circuit open for GraphQL. Falling back to REST.")
        raise

def discover_user_repositories_graphql(user: str) -> List[Dict[str, Any]]:
    """Discover user repositories using GraphQL."""
    all_repos = []
    variables = {
        "login": user,
        "first": 100,
        "after": None
    }
    has_next_page = True

    while has_next_page:
        response = _execute_graphql_query(REPOSITORY_DISCOVERY_QUERY, variables)
        repos_data = response["data"]["user"]["repositories"]
        all_repos.extend(repos_data["nodes"])
        has_next_page = repos_data["pageInfo"]["hasNextPage"]
        variables["after"] = repos_data["pageInfo"]["endCursor"]

    return _parse_repository_data_graphql(all_repos)

def _parse_repository_data_graphql(api_repos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Parse GraphQL response for repositories."""
    parsed_repos = []
    for repo in api_repos:
        parsed_repo = {
            'full_name': repo['nameWithOwner'],
            'name': repo['name'],
            'owner': {'login': repo['owner']['login']},
            'updated_at': repo['updatedAt'],
            'default_branch': repo['defaultBranchRef']['name'] if repo['defaultBranchRef'] else 'main'
        }
        parsed_repos.append(parsed_repo)
    return parsed_repos

def fetch_repo_commits_graphql(repo_full_name: str, since: str, until: str, author_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    """Fetch commits for a repository using GraphQL."""
    owner, repo_name = repo_full_name.split("/")
    since_iso = f"{since}T00:00:00Z"
    until_iso = f"{until}T23:59:59Z"

    all_commits = []
    variables = {
        "repoOwner": owner,
        "repoName": repo_name,
        "since": since_iso,
        "until": until_iso,
        "authorId": None,  # We'll handle author filtering client-side
        "first": 100,
        "after": None
    }
    has_next_page = True

    while has_next_page:
        response = _execute_graphql_query(COMMIT_HISTORY_QUERY, variables)
        commit_data = response["data"]["repository"]["defaultBranchRef"]["target"]["history"]
        all_commits.extend(commit_data["nodes"])
        has_next_page = commit_data["pageInfo"]["hasNextPage"]
        variables["after"] = commit_data["pageInfo"]["endCursor"]

    parsed_commits = _parse_commit_data_graphql(all_commits)
    if author_filter:
        parsed_commits = _filter_commits_by_author_graphql(parsed_commits, author_filter)
    return parsed_commits

def _parse_commit_data_graphql(api_commits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Parse GraphQL response for commits."""
    parsed_commits = []
    for commit in api_commits:
        parsed_commit = {
            'sha': commit['oid'],
            'message': commit['message'],
            'author_name': commit['author']['name'],
            'author_email': commit['author']['email'],
            'author_login': commit['author']['user']['login'] if commit['author']['user'] else None,
            'commit_date': commit['author']['date'],
            'committer_name': commit['committer']['name'],
            'committer_email': commit['committer']['email'],
            'committer_date': commit['committer']['date'],
            'url': commit['url']
        }
        parsed_commits.append(parsed_commit)
    return parsed_commits

def _filter_commits_by_author_graphql(commits: List[Dict[str, Any]], author_login: str) -> List[Dict[str, Any]]:
    """Filter commits by author login."""
    return [commit for commit in commits if commit['author_login'] == author_login]
```

### Step 3: Integrate GraphQL into `repos.py` and `commits.py`

We need to update `repos.py` and `commits.py` to use GraphQL as the primary method and fall back to REST if GraphQL fails or is disabled.

#### `repos.py` Updates

1. Add a `use_graphql` flag to the `discover_user_repositories` function to toggle between GraphQL and REST.
2. Call `discover_user_repositories_graphql` from `core/graphql.py` if `use_graphql=True`.
3. Fall back to REST if GraphQL fails or is disabled.

```python
# repos.py

# ... existing imports ...

from .graphql import discover_user_repositories_graphql

# ... existing code ...

def discover_user_repositories(user: str, org_filter: Optional[str] = None, use_graphql: bool = True) -> List[Dict[str, Any]]:
    """Discover all repositories accessible to a user, using GraphQL by default with fallback to REST."""
    if use_graphql:
        try:
            if org_filter:
                logger.warning("GraphQL repository discovery does not support org filtering. Falling back to REST.")
                raise NotImplementedError("GraphQL does not support org filtering")
            return discover_user_repositories_graphql(user)
        except (CircuitOpenError, NotImplementedError, subprocess.CalledProcessError, json.JSONDecodeError) as e:
            logger.warning("GraphQL repository discovery failed: %s. Falling back to REST.", e)

    # ... rest of the existing REST implementation ...
```

#### `commits.py` Updates

1. Add a `use_graphql` flag to the `fetch_repo_commits` function to toggle between GraphQL and REST.
2. Call `fetch_repo_commits_graphql` from `core/graphql.py` if `use_graphql=True`.
3. Fall back to REST if GraphQL fails or is disabled.

```python
# commits.py

# ... existing imports ...

from .graphql import fetch_repo_commits_graphql

# ... existing code ...

def fetch_repo_commits(repo_full_name: str, since: str, until: str, author_filter: Optional[str] = None, use_graphql: bool = True) -> List[Dict[str, Any]]:
    """Fetch commit activity using GraphQL by default with fallback to REST."""
    if use_graphql:
        try:
            return fetch_repo_commits_graphql(repo_full_name, since, until, author_filter)
        except (CircuitOpenError, subprocess.CalledProcessError, json.JSONDecodeError) as e:
            logger.warning("GraphQL commit fetch failed: %s. Falling back to REST.", e)

    # ... rest of the existing REST implementation ...
```

### Step 4: Update Configuration to Enable/Disable GraphQL

Add a `graphql_enabled` configuration option in `config.py` to control whether GraphQL is used by default.

```python
# config.py

class GitHubConfig(BaseModel):
    # ... existing fields ...
    graphql_enabled: bool = Field(default=True, description="Enable GraphQL API usage")
    # ... existing fields ...
```

Then, in `repos.py` and `commits.py`, use this configuration to set the `use_graphql` flag:

```python
# repos.py

def discover_user_repositories(user: str, org_filter: Optional[str] = None, use_graphql: Optional[bool] = None) -> List[Dict[str, Any]]:
    """Discover all repositories accessible to a user, using GraphQL by default with fallback to REST."""
    if use_graphql is None:
        use_graphql = _get_config().github.graphql_enabled

    # ... rest of the function ...

# commits.py

def fetch_repo_commits(repo_full_name: str, since: str, until: str, author_filter: Optional[str] = None, use_graphql: Optional[bool] = None) -> List[Dict[str, Any]]:
    """Fetch commit activity using GraphQL by default with fallback to REST."""
    if use_graphql is None:
        use_graphql = _get_config().github.graphql_enabled

    # ... rest of the function ...
```

### Step 5: Update `circuit_breaker.py` to Handle GraphQL Endpoints

We need to ensure that the circuit breaker can handle GraphQL endpoints appropriately. Currently, the circuit breaker is endpoint-specific, so we can treat `graphql` as a logical endpoint and use it as the key in `protected_call`.

This is already handled in the `_execute_graphql_query` function in `core/graphql.py`:

```python
result = protected_call("graphql", api_runner)
```

### Step 6: Testing and Benchmarking

1. Write unit tests for `core/graphql.py` to ensure the GraphQL queries are constructed and parsed correctly.
2. Write integration tests to verify that the GraphQL queries return the expected data.
3. Benchmark the performance of GraphQL vs. REST to ensure we achieve the desired 50% reduction in API calls.

### Step 7: Document the Changes

Update the documentation to reflect the new GraphQL support, including:

1. How to enable/disable GraphQL in the configuration.
2. The fallback mechanism to REST.
3. Any limitations or differences in functionality between GraphQL and REST (e.g., org filtering not supported in GraphQL).

### Summary of Changes

1. Created `core/graphql.py` to handle GraphQL queries and responses.
2. Updated `repos.py` and `commits.py` to use GraphQL by default with fallback to REST.
3. Added a `graphql_enabled` configuration option in `config.py`.
4. Ensured the circuit breaker handles GraphQL endpoints correctly.
5. Planned testing and documentation updates.

This implementation follows the core philosophy by prioritizing simplicity, modularity, and testability while leveraging GraphQL to reduce API calls and improve efficiency.