Below is a first‐cut implementation of GraphQL support.  It introduces a new module `hacktivity/core/graphql.py` that:

• Provides a thin `GraphQLClient` on top of `gh api graphql`  
• Respects the existing rate‐limiter and circuit-breaker  
• Caches availability of GraphQL at startup  
• Exposes two high-level methods:  
   – `fetch_repositories_via_graphql(...)` (to replace `_fetch_repositories_with_api`)  
   – `fetch_commits_via_graphql(...)` (to replace `_fetch_commits_with_api`)  
• Falls back to the REST helpers in `repos.py`/`commits.py` on errors  

You can wire these into `repos.py` and `commits.py` by checking `config.github.graphql_enabled` and catching a `GraphQLUnavailableError` or any transport error to reroute into the existing REST flows.

--- hacktivity/core/graphql.py ---

```python
"""
GraphQL support module:
- Builds and executes GitHub GraphQL queries via `gh api graphql`
- Integrates with rate limiter and circuit breaker
- Falls back to REST on errors
"""
from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .config import get_config
from .circuit_breaker import protected_call, CircuitOpenError, get_circuit
from .rate_limiter import get_rate_limit_coordinator
from .logging import get_logger

logger = get_logger(__name__)


class GraphQLUnavailableError(RuntimeError):
    """Raised when GraphQL is disabled or consistently failing."""


class GraphQLClient:
    """
    A simple GitHub GraphQL client using `gh api graphql`.
    Respects the same rate limits and circuit breaker as REST.
    """
    _availability_lock = threading.Lock()
    _is_available: Optional[bool] = None

    def __init__(self):
        cfg = get_config().github
        self.timeout = cfg.timeout_seconds * 2  # GraphQL can take longer
        self.batch_size = cfg.graphql_batch_size
        self.circuit = get_circuit("graphql")
        self.rate_limiter = get_rate_limit_coordinator()

    @classmethod
    def is_available(cls) -> bool:
        """
        Probe once at startup to see if GraphQL works.
        Caches result for process lifetime.
        """
        with cls._availability_lock:
            if cls._is_available is not None:
                return cls._is_available

            cfg = get_config().github
            if not cfg.graphql_enabled:
                cls._is_available = False
                return False

            probe = "query { viewer { login } }"
            try:
                GraphQLClient()._execute(probe, {})
                cls._is_available = True
            except Exception:
                logger.warning("GraphQL probe failed; falling back to REST")
                cls._is_available = False
            return cls._is_available

    def _execute(self, query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a GraphQL query via `gh api graphql`,
        wrapped in rate-limiter + circuit-breaker.
        """
        def runner():
            # Acquire token for rate-limiting
            self.rate_limiter.acquire()
            # Build `gh api graphql` command
            cmd = [
                "gh", "api", "graphql",
                "-f", f"query={query}"
            ]
            for name, val in variables.items():
                cmd += ["-f", f"{name}={json.dumps(val)}"]

            proc = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            return json.loads(proc.stdout)

        try:
            return protected_call("graphql", lambda: runner())
        except CircuitOpenError as ce:
            # circuit for GraphQL is open
            raise GraphQLUnavailableError(str(ce))
        except subprocess.CalledProcessError as e:
            logger.error("GraphQL call failed: %s", e.stderr.strip())
            raise
        except Exception:
            # any other error triggers fallback
            raise

    def fetch_repositories(
        self,
        login: str,
        first: int,
        after: Optional[str] = None
    ) -> Tuple[list[Dict[str, Any]], Optional[str]]:
        """
        Fetch user's repositories via GraphQL.
        Returns (nodes, endCursor) for pagination.
        """
        query = """
        query($login: String!, $first: Int!, $after: String) {
          user(login: $login) {
            repositories(first: $first, after: $after, orderBy: {field: UPDATED_AT, direction: DESC}) {
              pageInfo { endCursor hasNextPage }
              nodes {
                nameWithOwner
                name
                owner { login }
                updatedAt
                defaultBranchRef { name }
                # you can add more fields here
              }
            }
          }
        }
        """
        vars = {"login": login, "first": first, "after": after}
        result = self._execute(query, vars)
        repos = result["data"]["user"]["repositories"]["nodes"]
        page_info = result["data"]["user"]["repositories"]["pageInfo"]
        return repos, (page_info["endCursor"] if page_info["hasNextPage"] else None)

    def fetch_commits(
        self,
        owner: str,
        name: str,
        first: int,
        since: str,
        until: str,
        after: Optional[str] = None,
        author_id: Optional[str] = None
    ) -> Tuple[list[Dict[str, Any]], Optional[str]]:
        """
        Fetch commits of a repo via GraphQL.
        """
        query = """
        query(
          $owner: String!, $name: String!,
          $first: Int!, $after: String,
          $since: GitTimestamp!, $until: GitTimestamp!,
          $authorId: ID
        ) {
          repository(owner: $owner, name: $name) {
            defaultBranchRef {
              target {
                ... on Commit {
                  history(
                    first: $first, after: $after,
                    since: $since, until: $until,
                    author: { id: $authorId }
                  ) {
                    pageInfo { endCursor hasNextPage }
                    nodes {
                      oid
                      message
                      url
                      committedDate
                      author { name email date user { login } }
                      committer { name email date }
                    }
                  }
                }
              }
            }
          }
        }
        """
        vars: Dict[str, Any] = {
            "owner": owner,
            "name": name,
            "first": first,
            "after": after,
            "since": since,
            "until": until,
            "authorId": author_id,
        }
        result = self._execute(query, vars)
        hist = (
            result["data"]["repository"]["defaultBranchRef"]["target"]["history"]
        )
        commits = hist["nodes"]
        page_info = hist["pageInfo"]
        return commits, (page_info["endCursor"] if page_info["hasNextPage"] else None)
```

--- Integrating into `repos.py` and `commits.py` ---

1. In both modules, at the top:

   ```python
   from .graphql import GraphQLClient, GraphQLUnavailableError
   ```

2. Where you currently call the REST helper (e.g. `_fetch_repositories_with_api`), wrap it:

   ```python
   config = _get_config()
   if GraphQLClient.is_available() and config.github.graphql_enabled:
       try:
           client = GraphQLClient()
           repos = []
           cursor = None
           while True:
               batch, cursor = client.fetch_repositories(user, config.github.graphql_batch_size, cursor)
               repos.extend(batch)
               if not cursor:
                   break
           # parse/massage repos into the same shape as _parse_repository_data
           parsed = _parse_repository_data(repos)
           # apply dedupe, cache, etc.
           ...
           return parsed
       except (GraphQLUnavailableError, Exception):
           logger.warning("GraphQL path failed; falling back to REST discovery")
   # Fall back to original retry_decorator(_discover_with_retry)()
   ```

3. Similarly for the commit-fetcher in `commits.py`.

4. Add these new config keys to your `GitHubConfig` in `config.py`:

   ```python
   graphql_enabled: bool = True
   graphql_fallback_enabled: bool = True
   graphql_batch_size: int = 10
   ```

5. Update your TOML default in `save_default_config()` accordingly.

---

This approach keeps the GraphQL support:

• Fully optional (controlled by config)  
• Safe (circuit-breaker + rate-limiter)  
• Testable (you can mock `GraphQLClient._execute`)  
• Transparent (falling back to existing REST flows)

Next steps:

• Fill in any additional fields you need in the queries  
• Add unit tests for `GraphQLClient` (mocking `subprocess.run`)  
• Add integration tests (using `tests/utils/mock_api.py`)  
• Measure your API calls reduction with and without GraphQL  

With this in place you’ll satisfy “GraphQL used by default”, “Automatic REST fallback on errors”, and you should see a significant drop in the number of API calls.