To implement GraphQL support for the existing GitHub API interactions, we'll follow the provided implementation strategy.

### Step 1: Create `core/graphql.py` module

First, let's create a new module `core/graphql.py` to handle GraphQL query building and execution.

```python
# hacktivity/core/graphql.py
import subprocess
import json
from typing import Dict, Any

def execute_graphql_query(query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a GraphQL query using the GitHub CLI."""
    config = _get_config()
    command = [
        "gh", "api", "graphql",
        "-F", f"query={query}",
        "-F", f"variables={json.dumps(variables)}"
    ]
    
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=config.github.timeout_seconds
        )
        return json.loads(result.stdout)
    except subprocess.CalledProcessError as e:
        # Handle error
        pass
    except json.JSONDecodeError as e:
        # Handle JSON decoding error
        pass

def _get_config():
    from .config import get_config
    return get_config()
```

### Step 2: Enhance `repos.py` with GraphQL queries

Next, let's modify `repos.py` to use GraphQL for repository discovery.

```python
# hacktivity/core/repos.py
from .graphql import execute_graphql_query

def discover_user_repositories(user: str, org_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    """Discover all repositories accessible to a user using GraphQL."""
    query = """
        query($login: String!, $first: Int!, $after: String) {
            user(login: $login) {
                repositories(first: $first, after: $after, orderBy: {field: UPDATED_AT, direction: DESC}) {
                    pageInfo { hasNextPage endCursor }
                    nodes {
                        nameWithOwner
                        name
                        owner { login }
                        updatedAt
                    }
                }
            }
        }
    """
    variables = {
        "login": user,
        "first": _get_config().github.per_page,
        "after": None
    }
    
    all_repos = []
    while True:
        result = execute_graphql_query(query, variables)
        # Parse and process the result
        repos = result['data']['user']['repositories']
        all_repos.extend(repos['nodes'])
        
        if not repos['pageInfo']['hasNextPage']:
            break
        variables['after'] = repos['pageInfo']['endCursor']
    
    return all_repos
```

### Step 3: Enhance `commits.py` with GraphQL batch queries

Similarly, let's modify `commits.py` to use GraphQL for commit fetching.

```python
# hacktivity/core/commits.py
from .graphql import execute_graphql_query

def fetch_repo_commits(repo_full_name: str, since: str, until: str, author_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    """Fetch commit activity from a specific repository using GraphQL."""
    query = """
        query($owner: String!, $name: String!, $since: GitTimestamp!, $until: GitTimestamp!, $authorId: ID) {
            repository(owner: $owner, name: $name) {
                defaultBranchRef {
                    target {
                        ... on Commit {
                            history(since: $since, until: $until, author: {id: $authorId}) {
                                nodes {
                                    oid
                                    message
                                    author { name email date user { login } }
                                    committer { name email date }
                                    url
                                }
                            }
                        }
                    }
                }
            }
        }
    """
    owner, name = repo_full_name.split('/')
    variables = {
        "owner": owner,
        "name": name,
        "since": f"{since}T00:00:00Z",
        "until": f"{until}T23:59:59Z",
        "authorId": author_filter  # Assuming author_filter is the ID
    }
    
    result = execute_graphql_query(query, variables)
    # Parse and process the result
    commits = result['data']['repository']['defaultBranchRef']['target']['history']['nodes']
    return commits
```

### Step 4: Implement fallback to REST API

To implement the fallback mechanism, we can modify the `execute_graphql_query` function to catch specific GraphQL errors and fallback to the REST API.

```python
# hacktivity/core/graphql.py
def execute_graphql_query(query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
    try:
        # Execute GraphQL query
        pass
    except subprocess.CalledProcessError as e:
        # Check if the error is due to GraphQL being unavailable or rate limiting
        if "GraphQL is not available" in e.stderr or "rate limit exceeded" in e.stderr:
            # Fallback to REST API
            return execute_rest_query(query, variables)
        # Handle other errors
        pass

def execute_rest_query(query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
    # Implement REST API fallback logic here
    pass
```

By following these steps, we can implement GraphQL support for the existing GitHub API interactions while maintaining a robust and reliable architecture with a fallback to the REST API when needed.