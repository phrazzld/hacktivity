To implement GraphQL support for the `hacktivity` project, I will follow the provided instructions, focusing on integrating GraphQL for repository discovery and commit fetching, ensuring automatic fallback to the existing REST API on errors, and updating the configuration.

Here's the breakdown of changes:

1.  **`hacktivity/core/config.py`**:
    *   Add new `graphql_enabled`, `graphql_fallback_enabled`, `graphql_batch_size`, and `graphql_timeout_seconds` fields to the `GitHubConfig` model.
    *   Update the `save_default_config` function to include these new settings in the default `config.toml` file.

2.  **`hacktivity/core/graphql.py` (New Module)**:
    *   This module will encapsulate all GraphQL specific logic.
    *   `execute_graphql_query`: Handles `gh api graphql` command execution, integrates with `rate_limiter` and `circuit_breaker`, parses JSON responses, and checks for GraphQL-specific errors within the response.
    *   `resolve_user_id`: A helper function to fetch a GitHub user's internal ID using a simple GraphQL query, which is necessary for filtering commits by author in the main GraphQL queries. This will be cached.

3.  **`hacktivity/core/repos.py`**:
    *   Introduce `_fetch_repositories_with_graphql`: This new internal function will use the `user { repositories }` GraphQL query to fetch a batch of repositories. It will handle cursor-based pagination and transform the GraphQL response into the existing repository dictionary format.
    *   Modify `discover_user_repositories`: This function will be updated to attempt fetching repositories via GraphQL first if `graphql_enabled` is true. If the GraphQL call fails (due to `CircuitOpenError`, other exceptions, or if `graphql_fallback_enabled` is false), it will gracefully fall back to the existing REST API logic. The existing caching mechanism will be preserved.

4.  **`hacktivity/core/commits.py`**:
    *   Introduce `_fetch_commits_for_repos_graphql`: This new internal function is crucial for achieving API call reduction. It will construct a batched GraphQL query to fetch commits for *multiple specific repositories* (using aliases for `repository(owner, name)`) and their default branch history within a single API call. It will handle pagination for commits within each repository's history and transform the nested GraphQL response into the expected commit dictionary format. It will also use the `author_id` resolved earlier.
    *   Modify `fetch_commits_from_multiple_repos`: This function will first resolve the `author_filter` username to an internal GitHub ID using `graphql.resolve_user_id`. Then, it will attempt to fetch commits for all provided repositories using `_fetch_commits_for_repos_graphql` in batches. If GraphQL fails or is disabled, it will fall back to iterating through the repositories and calling the existing REST-based `fetch_repo_commits` for each. The existing caching for the aggregated commits will remain.

These changes ensure a GraphQL-first approach for efficiency, with robust error handling and automatic fallback to maintain reliability.

---

```diff
--- a/hacktivity/core/circuit_breaker.py
+++ b/hacktivity/core/circuit_breaker.py
@@ -102,6 +102,11 @@
             config = get_config()
             db_dir = Path(config.cache.directory or (Path.home() / ".hacktivity" / "cache"))
             db_dir.mkdir(parents=True, exist_ok=True)
+            # Ensure circuit breaker DB is closed cleanly on exit
+            import atexit
+            if _STORE is None: # Only register if this is the first time creating store
+                atexit.register(lambda: _STORE.close() if _STORE else None)
+
             _STORE = _PersistentStore(db_dir / "circuit_breaker.sqlite3")
 
         if endpoint not in _BREAKERS:
--- a/hacktivity/core/commits.py
+++ b/hacktivity/core/commits.py
@@ -5,11 +5,12 @@
 import json
 import subprocess
 import sys
-from typing import List, Dict, Optional, Any
+from typing import List, Dict, Optional, Any, Tuple
 
 from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
 
 from . import cache
+from . import graphql as graphql_client
 from .circuit_breaker import protected_call, CircuitOpenError
 from .rate_limiter import get_rate_limit_coordinator
 from .logging import get_logger
@@ -107,6 +108,122 @@
     return all_commits
 
 
+def _fetch_commits_for_repos_graphql(
+    repo_full_names: List[str],
+    since: str,
+    until: str,
+    author_id: Optional[str] = None
+) -> Dict[str, List[Dict[str, Any]]]:
+    """
+    Fetch commits for multiple repositories using a single batched GraphQL query.
+    This function handles GraphQL-specific pagination for commits within each repo.
+    """
+    config = _get_config()
+    all_repo_commits: Dict[str, List[Dict[str, Any]]] = {name: [] for name in repo_full_names}
+    
+    # GraphQL query structure to fetch multiple repositories and their commit history
+    # We use aliases for each repository query to fetch them in one batch.
+    # Commit history is paginated with `first` and `after`.
+    
+    # Helper function to build a single repo query fragment
+    def _build_repo_fragment(index: int, owner: str, name: str, after_cursor: Optional[str] = None) -> str:
+        after_clause = f', after: "{after_cursor}"' if after_cursor else ''
+        author_clause = f', author: {{id: "{author_id}"}}' if author_id else ''
+        
+        return f"""
+            repo_{index}: repository(owner: "{owner}", name: "{name}") {{
+                nameWithOwner
+                defaultBranchRef {{
+                    target {{
+                        ... on Commit {{
+                            history(first: {config.github.per_page}, since: "{since}T00:00:00Z", until: "{until}T23:59:59Z"{author_clause}{after_clause}) {{
+                                pageInfo {{ hasNextPage endCursor }}
+                                nodes {{
+                                    oid
+                                    message
+                                    author {{ name email date user {{ login }} }}
+                                    committer {{ name email date }}
+                                    url
+                                }}
+                            }}
+                        }}
+                    }}
+                }}
+            }}
+        """
+
+    # Keep track of pagination state for each repo
+    repo_pagination_cursors: Dict[str, Optional[str]] = {name: None for name in repo_full_names}
+    repos_to_fetch_more = set(repo_full_names)
+
+    # Loop until all repos are fully fetched or max pages reached for any repo
+    while repos_to_fetch_more:
+        current_batch_query_parts = []
+        current_batch_repos = []
+        
+        # Select repos for the current batch query
+        for i, repo_name in enumerate(list(repos_to_fetch_more)[:config.github.graphql_batch_size]):
+            owner, name = repo_name.split('/')
+            current_batch_query_parts.append(
+                _build_repo_fragment(i, owner, name, repo_pagination_cursors[repo_name])
+            )
+            current_batch_repos.append(repo_name)
+        
+        # If no repos selected for this batch, break
+        if not current_batch_query_parts:
+            break
+
+        # Construct the full GraphQL query
+        graphql_query = "query {\n" + "\n".join(current_batch_query_parts) + "\n}"
+        
+        logger.debug("Fetching GraphQL commit batch for %d repos. Current repos: %s", 
+                     len(current_batch_repos), current_batch_repos)
+
+        try:
+            # Execute the GraphQL query
+            data = graphql_client.execute_graphql_query(
+                graphql_query, {}, "graphql_commits_batch"
+            )
+
+            # Process response for each repo in the batch
+            repos_to_fetch_more.clear() # Clear and re-add if more pages are needed
+            for i, repo_name in enumerate(current_batch_repos):
+                repo_data = data.get(f"repo_{i}")
+                if not repo_data:
+                    logger.warning("GraphQL response missing data for repo: %s", repo_name)
+                    continue
+                
+                # Extract commit history
+                default_branch_ref = repo_data.get('defaultBranchRef')
+                if not default_branch_ref:
+                    continue # No default branch, no commits
+
+                target = default_branch_ref.get('target')
+                if not target:
+                    continue # No target on default branch, no commits
+
+                history = target.get('history')
+                if not history:
+                    continue # No history data
+
+                page_info = history.get('pageInfo', {})
+                nodes = history.get('nodes', [])
+                
+                # Parse commits and add to total list for this repo
+                parsed_commits = _parse_commit_data([{'sha': n['oid'], 'commit': {'message': n['message'], 'author': n['author'], 'committer': n['committer']}, 'url': n['url'], 'html_url': f"https://github.com/{repo_name}/commit/{n['oid']}"} for n in nodes])
+                all_repo_commits[repo_name].extend(parsed_commits)
+                
+                # Check for next page of commits for this repo
+                if page_info.get('hasNextPage') and page_info.get('endCursor'):
+                    repo_pagination_cursors[repo_name] = page_info['endCursor']
+                    repos_to_fetch_more.add(repo_name) # Add back to process in next batch
+                    
+        except (CircuitOpenError, Exception) as e:
+            logger.warning("GraphQL commit fetching failed: %s. Falling back to REST for remaining repos.", e)
+            raise # Re-raise to trigger fallback in the caller
+    
+    return all_repo_commits
+
+
 def fetch_repo_commits(repo_full_name: str, since: str, until: str, author_filter: Optional[str] = None) -> List[Dict[str, Any]]:
     """
     Fetch commit activity from a specific repository using the GitHub API.
@@ -204,21 +321,50 @@
     """
     all_repo_commits = {}
     
-    for repo_full_name in repo_list:
-        try:
-            repo_commits = fetch_repo_commits(repo_full_name, since, until, author_filter)
-            all_repo_commits[repo_full_name] = repo_commits
-            logger.info("Successfully fetched %d commits from '%s'", len(repo_commits), repo_full_name)
-        except Exception as e:
-            logger.warning("Failed to fetch commits from '%s': %s", repo_full_name, e)
-            # Continue with other repositories instead of failing entirely
-            all_repo_commits[repo_full_name] = []
-    
+    config = _get_config()
+    use_graphql = config.github.graphql_enabled
+    
+    author_id: Optional[str] = None
+    if author_filter and use_graphql:
+        try:
+            author_id = graphql_client.resolve_user_id(author_filter)
+            if not author_id:
+                logger.warning("Could not resolve author ID for '%s'. GraphQL author filtering disabled.", author_filter)
+                # If author ID can't be resolved, GraphQL cannot filter by author effectively.
+                # We could still proceed with GraphQL and filter manually, but for now, fall back.
+                use_graphql = False
+        except Exception as e:
+            logger.warning("Failed to resolve author ID via GraphQL: %s. Falling back to REST.", e)
+            use_graphql = False
+
+    if use_graphql:
+        try:
+            logger.info("Attempting to fetch commits for %d repositories using GraphQL...", len(repo_list))
+            # GraphQL can fetch multiple repos' commits in one go
+            all_repo_commits = _fetch_commits_for_repos_graphql(
+                repo_list, since, until, author_id
+            )
+            logger.info("Successfully fetched commits via GraphQL for %d repositories.", len(repo_list))
+        except (CircuitOpenError, Exception) as e:
+            if config.github.graphql_fallback_enabled:
+                logger.warning("GraphQL commit fetching failed (%s). Falling back to REST API for %d repositories.", e, len(repo_list))
+                use_graphql = False
+            else:
+                logger.error("GraphQL commit fetching failed and fallback is disabled: %s", e)
+                raise # Re-raise if fallback is not allowed
+
+    if not use_graphql:
+        logger.info("Fetching commits for %d repositories using REST API...", len(repo_list))
+        for repo_full_name in repo_list:
+            try:
+                repo_commits = fetch_repo_commits(repo_full_name, since, until, author_filter)
+                all_repo_commits[repo_full_name] = repo_commits
+                logger.info("Successfully fetched %d commits from '%s' via REST", len(repo_commits), repo_full_name)
+            except Exception as e:
+                logger.warning("Failed to fetch commits from '%s' via REST: %s", repo_full_name, e)
+                all_repo_commits[repo_full_name] = [] # Continue with other repositories
+
     total_commits = sum(len(commits) for commits in all_repo_commits.values())
     logger.info("Fetched %d total commits from %d repositories", total_commits, len(repo_list))
-    
     return all_repo_commits
 
 
--- a/hacktivity/core/config.py
+++ b/hacktivity/core/config.py
@@ -62,6 +62,18 @@
         default=True,
         description="Enable parallel processing of repositories"
     )
+    
+    # GraphQL Configuration
+    graphql_enabled: bool = Field(
+        default=True,
+        description="Enable GraphQL API usage when available"
+    )
+    graphql_fallback_enabled: bool = Field(
+        default=True,
+        description="Enable automatic REST fallback when GraphQL fails"
+    )
+    graphql_batch_size: int = Field(default=10, ge=1, le=50, description="Repositories per GraphQL query")
+    # graphql_timeout_seconds is already covered by timeout_seconds
 
 
 class AIConfig(BaseModel):
@@ -131,6 +143,11 @@
 rate_limit_buffer = 100 # API calls to reserve as buffer (50-500)
 parallel_enabled = true # Enable parallel processing (true/false)
 
+# GraphQL Configuration
+graphql_enabled = true          # Enable GraphQL API usage
+graphql_fallback_enabled = true # Enable automatic REST fallback
+graphql_batch_size = 10         # Repositories per GraphQL query
+
 [ai]
 # AI model settings
 model_name = "gemini-1.5-flash"  # AI model to use
--- a/hacktivity/core/repos.py
+++ b/hack