Of course. Here is a comprehensive, synthesized implementation plan for adding GraphQL support to your application, based on the provided instructions and incorporating the strongest insights from the model outputs.

This plan provides a complete, production-ready implementation with full code examples, adhering to your project's architectural principles.

---

### **T026: GraphQL Support Implementation**

This implementation introduces GraphQL as the primary data-fetching mechanism to significantly reduce API calls, with a seamless and automatic fallback to the existing REST API to ensure robustness and reliability.

### **Phase 1: Core GraphQL Infrastructure**

We begin by establishing the foundational components: updating the configuration and creating a new, dedicated module for all GraphQL interactions.

#### **1. Update Configuration (`hacktivity/core/config.py`)**

Add the required GraphQL settings to `GitHubConfig` and the default `config.toml` template.

```diff
--- a/hacktivity/core/config.py
+++ b/hacktivity/core/config.py
@@ -10,6 +10,14 @@
     retry_attempts: int = Field(default=3, ge=1, le=10, description="Number of retry attempts")
     retry_min_wait: int = Field(default=4, ge=1, le=60, description="Minimum retry wait in seconds")
     retry_max_wait: int = Field(default=10, ge=1, le=300, description="Maximum retry wait in seconds")
+
+    # GraphQL Configuration
+    graphql_enabled: bool = Field(default=True, description="Enable GraphQL API usage")
+    graphql_fallback_enabled: bool = Field(default=True, description="Enable automatic REST fallback on GraphQL errors")
+    graphql_batch_size: int = Field(default=10, ge=1, le=50, description="Repositories per GraphQL query batch")
+    graphql_timeout_seconds: int = Field(default=120, ge=30, le=600, description="Longer timeout for complex GraphQL queries")
+
     # Circuit Breaker Configuration
     cb_failure_threshold: int = Field(
         default=5, ge=1, le=20,
```

#### **2. Create GraphQL Module (`hacktivity/core/graphql.py`)**

This new module encapsulates all GraphQL logic, integrating cleanly with existing rate-limiting, circuit-breaking, and retry mechanisms.

```python
# hacktivity/core/graphql.py (new file)
"""
Core module for executing GitHub GraphQL queries.

This module provides a client for running GraphQL queries that is fully integrated
with the application's rate limiting, circuit breaking, and retry mechanisms.
It also includes a one-time availability check to avoid repeated failures.
"""

from __future__ import annotations

import json
import subprocess
import threading
from textwrap import dedent
from typing import Any, Dict

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .circuit_breaker import protected_call, CircuitOpenError
from .config import get_config
from .logging import get_logger
from .rate_limiter import get_rate_limit_coordinator

logger = get_logger(__name__)

# Logical circuit-breaker key for all GraphQL calls
_GRAPHQL_ENDPOINT = "graphql"


class GraphQLError(RuntimeError):
    """Raised when GitHub returns an `errors` array in the GraphQL response."""
    def __init__(self, errors: Any):
        super().__init__(f"GraphQL query failed with errors: {errors}")
        self.errors = errors


class GraphQLClient:
    """A client for executing GitHub GraphQL queries with integrated fault tolerance."""
    _availability_lock = threading.Lock()
    _is_available: bool | None = None

    def __init__(self):
        self.config = get_config().github

    @classmethod
    def is_available(cls) -> bool:
        """
        Checks if the GraphQL API is available by running a simple probe query.
        The result is cached for the lifetime of the application process to avoid
        repeated checks.
        """
        with cls._availability_lock:
            if cls._is_available is not None:
                return cls._is_available

            config = get_config().github
            if not config.github.graphql_enabled:
                logger.info("GraphQL is disabled in the configuration.")
                cls._is_available = False
                return False

            try:
                probe_query = "query { viewer { login } }"
                GraphQLClient().run_query(probe_query, {})
                logger.info("GraphQL availability probe successful.")
                cls._is_available = True
            except Exception as e:
                logger.warning(
                    "GraphQL availability probe failed: %s. "
                    "GraphQL will be disabled for this session.", e
                )
                cls._is_available = False

            return cls._is_available

    def _build_cli_command(self, query: str, variables: Dict[str, Any]) -> list[str]:
        """Builds the `gh api graphql` command with variables passed safely."""
        # Using -F for variables ensures proper JSON serialization by gh CLI
        cmd = ["gh", "api", "graphql", "-f", f"query={dedent(query).strip()}"]
        for key, value in variables.items():
            cmd.extend(["-F", f"{key}={json.dumps(value)}"])
        return cmd

    def run_query(self, query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
        """
        Executes a GraphQL query with retries, circuit breaking, and rate limiting.

        Returns:
            The 'data' field from the GraphQL JSON response.

        Raises:
            GraphQLError: If the API response contains logical errors.
            CircuitOpenError: If the circuit breaker is open.
            subprocess.TimeoutExpired: If the command times out.
            subprocess.CalledProcessError: For other command execution errors.
        """
        @retry(
            stop=stop_after_attempt(self.config.retry_attempts),
            wait=wait_exponential(
                multiplier=1, min=self.config.retry_min_wait, max=self.config.retry_max_wait
            ),
            retry=retry_if_exception_type(subprocess.TimeoutExpired),
            reraise=True,
        )
        def _runner() -> Dict[str, Any]:
            command = self._build_cli_command(query, variables)
            logger.debug("Executing GraphQL command: %s", " ".join(command))

            def _subprocess_call():
                get_rate_limit_coordinator().acquire()
                return subprocess.run(
                    command,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=self.config.graphql_timeout_seconds,
                )

            result = protected_call(_GRAPHQL_ENDPOINT, _subprocess_call)
            payload: Dict[str, Any] = json.loads(result.stdout)

            if "errors" in payload and payload["errors"]:
                raise GraphQLError(payload["errors"])

            return payload.get("data", {})

        return _runner()
```

### **Phase 2: Repository Discovery Integration**

Now, we'll update `repos.py` to use GraphQL first for fetching repositories, falling back to the existing REST implementation if necessary.

#### **Modify `hacktivity/core/repos.py`**

```diff
--- a/hacktivity/core/repos.py
+++ b/hacktivity/core/repos.py
@@ -1,13 +1,15 @@
 from __future__ import annotations
 
+import json
 import subprocess
 import sys
 from typing import Any, Dict, List, Optional
 
 from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
 
 from . import cache
 from .circuit_breaker import protected_call, CircuitOpenError
+from .graphql import GraphQLClient, GraphQLError
 from .logging import get_logger
 from .rate_limiter import get_rate_limit_coordinator
 from .config import get_config as _get_config
@@ -21,6 +23,73 @@
 # ... existing functions _get_config, _generate_repo_cache_key, _parse_repository_data ...
 
 
+def _discover_repos_with_graphql(user: str, org_filter: Optional[str]) -> List[Dict[str, Any]]:
+    """
+    Fetches repositories using the GitHub GraphQL API, handling pagination.
+    Normalizes the response to match the existing REST API data structure.
+    """
+    client = GraphQLClient()
+    all_repo_nodes = []
+    
+    # GraphQL query for repositories
+    query = """
+    query($login: String!, $first: Int!, $after: String) {
+      user(login: $login) {
+        repositories(first: $first, after: $after, affiliations: [OWNER, COLLABORATOR], orderBy: {field: UPDATED_AT, direction: DESC}) {
+          pageInfo { hasNextPage endCursor }
+          nodes {
+            name
+            nameWithOwner
+            isPrivate
+            isFork
+            isArchived
+            updatedAt
+            createdAt
+            defaultBranchRef { name }
+            stargazerCount
+            forkCount
+            owner { login }
+            primaryLanguage { name }
+          }
+        }
+      }
+    }
+    """
+    
+    variables = {"login": user, "first": 100, "after": None}
+    
+    while True:
+        data = client.run_query(query, variables)
+        repo_data = data.get("user", {}).get("repositories", {})
+        nodes = repo_data.get("nodes", [])
+        all_repo_nodes.extend(nodes)
+
+        page_info = repo_data.get("pageInfo", {})
+        if not page_info.get("hasNextPage"):
+            break
+        variables["after"] = page_info.get("endCursor")
+
+    # Normalize GraphQL nodes to the shape expected by the rest of the application
+    normalized_repos = [
+        {
+            "full_name": n["nameWithOwner"], "name": n["name"], "owner": n["owner"],
+            "private": n["isPrivate"], "fork": n["isFork"], "archived": n["isArchived"],
+            "updated_at": n["updatedAt"], "created_at": n["createdAt"],
+            "default_branch": (n.get("defaultBranchRef") or {}).get("name", "main"),
+            "language": (n.get("primaryLanguage") or {}).get("name"),
+            "stargazers_count": n["stargazerCount"], "forks_count": n["forkCount"],
+        } for n in all_repo_nodes
+    ]
+
+    # Apply organization filter if provided
+    if org_filter:
+        normalized_repos = [
+            r for r in normalized_repos if r["owner"]["login"].lower() == org_filter.lower()
+        ]
+        
+    return normalized_repos
+
+
 def discover_user_repositories(user: str, org_filter: Optional[str] = None) -> List[Dict[str, Any]]:
     """
     Discover all repositories accessible to a user.
@@ -37,6 +106,20 @@
         return cached_repos
 
     logger.info("Discovering repositories for '%s' (org: %s)...", user, org_filter or 'all')
+    
+    # --- GraphQL-First Approach ---
+    config = _get_config()
+    if GraphQLClient.is_available():
+        try:
+            logger.debug("Attempting repository discovery via GraphQL.")
+            graphql_repos = _discover_repos_with_graphql(user, org_filter)
+            logger.info("GraphQL discovery successful, found %d repositories.", len(graphql_repos))
+            cache.set(cache_key, graphql_repos)
+            return graphql_repos
+        except (GraphQLError, CircuitOpenError, subprocess.CalledProcessError) as e:
+            logger.warning("GraphQL discovery failed (%s). Falling back to REST.", e)
+            if not config.github.graphql_fallback_enabled:
+                raise
 
     # Create dynamic retry decorator with config values
     retry_decorator = retry(
```

### **Phase 3: Commit Fetching Integration**

This phase is critical for performance gains. We will implement batched commit fetching for multiple repositories in a single GraphQL call.

#### **Modify `hacktivity/core/commits.py`**

```diff
--- a/hacktivity/core/commits.py
+++ b/hacktivity/core/commits.py
@@ -5,9 +5,10 @@
 import subprocess
 import sys
 from typing import List, Dict, Optional, Any
 
 from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
 
 from . import cache
+from .graphql import GraphQLClient, GraphQLError
 from .circuit_breaker import protected_call, CircuitOpenError
 from .rate_limiter import get_rate_limit_coordinator
 from .logging import get_logger
@@ -19,6 +20,95 @@
 # ... existing functions _get_config, _generate_commit_cache_key, _parse_commit_data ...
 
 
+def _resolve_user_id_graphql(username: str) -> str | None:
+    """Resolves a GitHub username to its internal GraphQL node ID for filtering."""
+    client = GraphQLClient()
+    query = "query($login: String!) { user(login: $login) { id } }"
+    variables = {"login": username}
+    try:
+        data = client.run_query(query, variables)
+        return data.get("user", {}).get("id")
+    except Exception as e:
+        logger.warning("Could not resolve user ID for '%s': %s", username, e)
+        return None
+
+
+def _fetch_commits_with_graphql(
+    repo_list: List[str], since: str, until: str, author_id: str | None
+) -> Dict[str, List[Dict[str, Any]]]:
+    """
+    Fetches commits for a batch of repositories in a single GraphQL query.
+    This is a key performance optimization.
+    """
+    client = GraphQLClient()
+    repo_commits: Dict[str, List[Dict[str, Any]]] = {name: [] for name in repo_list}
+
+    # Build a dynamic query to fetch multiple repositories by alias
+    query_parts = []
+    variables = {
+        "since": f"{since}T00:00:00Z",
+        "until": f"{until}T23:59:59Z",
+        "author": {"id": author_id} if author_id else None,
+    }
+
+    for i, repo_full_name in enumerate(repo_list):
+        owner, name = repo_full_name.split("/", 1)
+        # Using aliases to query multiple distinct repositories in one go
+        query_parts.append(
+            f"""
+            repo{i}: repository(owner: "{owner}", name: "{name}") {{
+              nameWithOwner
+              defaultBranchRef {{
+                target {{
+                  ... on Commit {{
+                    history(first: 100, since: $since, until: $until, author: $author) {{
+                      nodes {{
+                        oid
+                        message
+                        author {{ name email date user {{ login }} }}
+                        committer {{ name email date }}
+                        url
+                      }}
+                    }}
+                  }}
+                }}
+              }}
+            }}
+            """
+        )
+    
+    query = f"""
+    query($since: GitTimestamp!, $until: GitTimestamp!, $author: CommitAuthor) {{
+      {"".join(query_parts)}
+    }}
+    """
+    
+    data = client.run_query(query, variables)
+
+    # Normalize the batched response
+    for i, repo_full_name in enumerate(repo_list):
+        repo_data = data.get(f"repo{i}")
+        if not repo_data:
+            continue
+
+        history = (
+            repo_data.get("defaultBranchRef", {})
+            .get("target", {})
+            .get("history", {})
+            .get("nodes", [])
+        )
+
+        # Reuse the existing REST parsing logic for consistency
+        # The shape is slightly different, so we create a compatible structure
+        commits_to_parse = [
+            {"sha": c["oid"], "commit": {"message": c["message"], "author": c["author"], "committer": c["committer"]}, "url": c["url"], "html_url": c["url"]}
+            for c in history
+        ]
+        repo_commits[repo_full_name] = _parse_commit_data(commits_to_parse)
+
+    return repo_commits
+
+
 def fetch_repo_commits(repo_full_name: str, since: str, until: str, author_filter: Optional[str] = None) -> List[Dict[str, Any]]:
     """
     Fetch commit activity from a specific repository using the GitHub API.
@@ -107,21 +197,49 @@
     """
     all_repo_commits = {}
     
-    for repo_full_name in repo_list:
+    # --- GraphQL-First Approach ---
+    config = _get_config()
+    if GraphQLClient.is_available():
+        author_id = None
+        if author_filter:
+            author_id = _resolve_user_id_graphql(author_filter)
+            if not author_id:
+                logger.warning("Could not resolve author '%s' to a GitHub ID. Proceeding without author filter for GraphQL.", author_filter)
+
         try:
-            repo_commits = fetch_repo_commits(repo_full_name, since, until, author_filter)
-            all_repo_commits[repo_full_name] = repo_commits
-            logger.info("Successfully fetched %d commits from '%s'", len(repo_commits), repo_full_name)
-        except Exception as e:
-            logger.warning("Failed to fetch commits from '%s': %s", repo_full_name, e)
-            # Continue with other repositories instead of failing entirely
-            all_repo_commits[repo_full_name] = []
-    
+            logger.debug("Attempting to fetch commits via GraphQL for %d repos.", len(repo_list))
+            # Process repositories in batches to stay within GraphQL limits
+            for i in range(0, len(repo_list), config.github.graphql_batch_size):
+                batch = repo_list[i:i + config.github.graphql_batch_size]
+                batch_commits = _fetch_commits_with_graphql(batch, since, until, author_id)
+                all_repo_commits.update(batch_commits)
+            
+            logger.info("GraphQL commit fetch successful for %d repositories.", len(repo_list))
+            # The rest of the function will handle caching and returning the result
+            total_commits = sum(len(commits) for commits in all_repo_commits.values())
+            logger.info("Fetched %d total commits from %d repositories", total_commits, len(repo_list))
+            return all_repo_commits
+            
+        except (GraphQLError, CircuitOpenError, subprocess.CalledProcessError) as e:
+            logger.warning("GraphQL commit fetching failed (%s). Falling back to REST.", e)
+            if not config.github.graphql_fallback_enabled:
+                raise
+
+    # --- Fallback to REST API ---
+    logger.info("Fetching commits via REST for %d repositories.", len(repo_list))
+    # ... existing REST loop remains here ...
+    for repo_full_name in repo_list:
+         try:
+             repo_commits = fetch_repo_commits(repo_full_name, since, until, author_filter)
+             all_repo_commits[repo_full_name] = repo_commits
+             logger.info("Successfully fetched %d commits from '%s'", len(repo_commits), repo_full_name)
+         except Exception as e:
+             logger.warning("Failed to fetch commits from '%s': %s", repo_full_name, e)
+             all_repo_commits[repo_full_name] = []
+
     total_commits = sum(len(commits) for commits in all_repo_commits.values())
     logger.info("Fetched %d total commits from %d repositories", total_commits, len(repo_list))
-    
     return all_repo_commits
-
```

### **Phase 4: Testing and Validation**

A robust testing strategy is crucial to validate this new implementation.

1.  **Unit Tests (`tests/test_graphql.py`)**:
    *   Mock `subprocess.run` to test `GraphQLClient.run_query`.
    *   Simulate successful responses, `GraphQLError` responses, and `subprocess` exceptions.
    *   Test the `is_available` probe and caching behavior.
    *   Verify the CLI command construction in `_build_cli_command`.

2.  **Integration Tests (`tests/test_repos.py`, `tests/test_commits.py`)**:
    *   Add tests that mock the `GraphQLClient` to return expected data and verify that the calling functions (`discover_user_repositories`, `fetch_commits_from_multiple_repos`) correctly parse the data.
    *   Add tests that mock `GraphQLClient` to raise exceptions, and assert that the application correctly falls back to the existing (mocked) REST API calls.

3.  **Performance Benchmarks**:
    *   Create a test script to run against a real user or organization with a large number of repositories.
    *   Run the script with `graphql_enabled = false` and then with `graphql_enabled = true`.
    *   Use logging or a counter to measure the total number of `gh api` calls in both scenarios, and assert that the reduction is over 50%.

This implementation fulfills all requirements of T026, providing a significant performance improvement while maintaining the system's existing reliability and