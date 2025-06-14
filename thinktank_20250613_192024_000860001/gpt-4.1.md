Certainly! Here are clear, **step-by-step implementation instructions** for **adding GraphQL support with automatic fallback to REST** for **repository discovery and commit fetching** in your codebase, *fully aligned with your architecture, development philosophy, and the requirements of ticket T026*.

---

## PHASE 1: Core GraphQL Infrastructure

### 1. Create `core/graphql.py` Module

- **Purpose**: Centralize all GraphQL logic (query building, execution, response normalization, error handling, fallback coordination).
- **Location**: `hacktivity/core/graphql.py`
- **Initial contents**: 
    - `execute_graphql_query(query: str, variables: dict, timeout: int) -> dict`
    - Exception classes: `GraphQLUnavailableError`, `GraphQLQueryError`
    - Utility: `_is_graphql_available()`, `_should_fallback_to_rest(error: Exception) -> bool`
    - Logging and structured error reporting (use your logging infra).
    - Use the `gh api graphql` CLI via subprocess, as per your current architecture.

#### Example Skeleton
```python
import subprocess
import json
from typing import Dict, Any
from .logging import get_logger

logger = get_logger(__name__)

class GraphQLUnavailableError(Exception): ...
class GraphQLQueryError(Exception): ...

def _is_graphql_available() -> bool:
    # Try a trivial query (`{ viewer { login } }`), return True if succeeds.
    # Cache the result for future calls.
    ...

def execute_graphql_query(query: str, variables: Dict[str, Any], timeout: int) -> Dict[str, Any]:
    # Use subprocess to run: gh api graphql -F query="$query" -F var1=... ...
    # Parse output, handle errors, raise GraphQLQueryError on any GraphQL-specific error.
    ...
```
- **Test**: Create `tests/core/test_graphql.py`, mock subprocess calls, check error handling and parsing.

---

## PHASE 2: Repository Discovery Integration

### 2. Update `repos.py` to Prefer GraphQL

- **Preparation**: Import your new GraphQL module (`from .graphql import ...`).

#### 2.1. Build Efficient GraphQL Query
- Design a query that fetches all user/org repos, only required fields (see T026 spec).
- Support pagination using `pageInfo { hasNextPage endCursor }`.
- Accept filters for user/org as variables.

#### 2.2. Implement GraphQL-First Discovery
- In `discover_user_repositories`, **if GraphQL is enabled and available**:
    - Build GraphQL query and variables.
    - Call `execute_graphql_query`.
    - Parse response into the same normalized format as current REST.
    - **If any GraphQL error occurs (e.g., query failure, network, rate limit, or server errors):**
        - Log the error (structured).
        - If fallback is enabled, transparently use the existing REST-based logic.
        - If fallback is disabled, raise the error.
- **On fallback**: Log at WARN level with reason.

#### 2.3. Fallback Logic
- Use error-based fallback: on `GraphQLUnavailableError` or `GraphQLQueryError`, fall back to REST.
- Ensure circuit breaker is **not tripped** for GraphQL errors (per requirements).

#### 2.4. Caching/Rate Limiting
- GraphQL responses should be cached using the same cache key logic.
- Call `get_rate_limit_coordinator().acquire()` before GraphQL calls.

#### 2.5. Configuration Integration
- Add support for `[github] graphql_enabled`, `graphql_fallback_enabled`, `graphql_batch_size`, `graphql_timeout_seconds` (see config TOML snippet in the spec).
- Update `config.py`:
    - Add these fields to `GitHubConfig` (with appropriate defaults).
    - Document in TOML template.

#### 2.6. Tests
- Update `tests/test_repos.py` and `tests/utils/mock_api.py`:
    - Test normal GraphQL path.
    - Test fallback by simulating failures.
    - Ensure result shape is consistent with REST.

---

## PHASE 3: Commit Fetching Integration

### 3. Update `commits.py` to Prefer GraphQL

#### 3.1. Build Efficient Batch GraphQL Query
- Design a query that fetches recent commits for a batch of repositories in one call.
    - Example: for each repo, get default branch, then `.history(first: N, since, until, author)` (as in T026).
    - Paginate using `endCursor` if >100 commits per repo.
    - Support batch size via config (`graphql_batch_size`).
- **Optimize**: Only fetch required fields.

#### 3.2. Implement GraphQL-First Commit Fetching
- In `fetch_repo_commits` and `fetch_commits_from_multiple_repos`:
    - If GraphQL is enabled and available:
        - Build query+variables for batch of repos.
        - Call `execute_graphql_query`.
        - Parse to normalized commit dict format.
    - If GraphQL fails:
        - Fallback to current per-repo REST-based implementation for that repo or batch.
    - Log any fallback event.

#### 3.3. Fallback Logic
- Same pattern as repo discovery: on any GraphQL error, fallback to REST for that batch/repo.

#### 3.4. Rate Limiting, Caching, Circuit Breaker
- Use existing rate limiter before GraphQL calls.
- Use same cache key logic.
- Do **not** trip the REST circuit breaker for GraphQL errors.

#### 3.5. Tests
- Update `tests/test_commits.py` and integration tests:
    - Test GraphQL batch fetching and fallback.
    - Simulate rate limits, errors, and check fallback.
    - Assert API call count reduction in integration tests.

---

## PHASE 4: System Integration

### 4.1. Circuit Breaker Integration
- GraphQL failures **must not** open the REST endpoint's circuit breaker.
- Use a separate circuit for GraphQL endpoint if desired (but not required by spec).
- Fallback logic should be transparent and not degrade user experience.

### 4.2. Rate Limiting
- Both GraphQL and REST calls draw from the same rate limiter.

### 4.3. Caching
- Cache normalized GraphQL responses using the same cache keys as existing REST for deduplication.

### 4.4. Logging & Observability
- Log every fallback event, GraphQL errors, response times.
- Include context (user, operation, fallback reason) in logs.

---

## PHASE 5: Documentation & Configuration

- Update README and code comments:
    - Document GraphQL config options.
    - Describe fallback behavior, performance, troubleshooting.
- Update TOML config template with new options and comments.

---

## PHASE 6: Quality Gates & Testing

- Ensure **90%+ test coverage** for new code, especially fallback and error handling.
- Run all integration and regression tests—**all existing REST tests must continue to pass**.
- Add **performance tests** comparing API call counts pre/post GraphQL support.
- Validate that disabling GraphQL (config) restores pure REST operation.
- Validate cache hit rates and memory usage with large GraphQL responses.

---

## **Summary Table: Key Changes by File**

| File                                 | Action Summary                                                                                                       |
|---------------------------------------|---------------------------------------------------------------------------------------------------------------------|
| `core/graphql.py`                     | New module: GraphQL execution, error handling, fallback coordination                                                |
| `core/repos.py`                       | Use GraphQL for repo discovery when enabled; fallback to REST; caching; config; logging                             |
| `core/commits.py`                     | Use GraphQL for batch commit fetching; fallback to REST per repo; batching; config; logging                         |
| `core/config.py`                      | Add GraphQL config flags and docs; expose via `GitHubConfig`                                                        |
| `tests/test_repos.py`, `test_commits` | Test GraphQL path, fallback, config, response shapes, error handling                                                |
| `tests/utils/mock_api.py`             | Extend to mock GraphQL responses and errors                                                                         |
| `tests/test_large_scale_integration`  | Efficiency, fallback, and performance tests                                                                         |

---

## **Design Principles Applied**

- **Modularity**: GraphQL is an optional, swappable adapter; REST pipeline remains unchanged.
- **Testability**: All GraphQL code is tested via mocks; fallback and error paths are covered.
- **Simplicity**: Fallback is transparent; user/config controls are explicit.
- **No Breaking Changes**: REST remains the baseline and is always available.
- **Observability**: All fallback and error events are logged with context.

---

## **Success Criteria Check**

- GraphQL is used by default, with automatic REST fallback for errors/unavailability.
- 50%+ reduction in API calls for repo+commit fetching (validate in perf tests).
- All existing and new tests pass; code is fully documented, with config options exposed.

---

**Proceed by implementing each phase as above, with continuous test and integration validation at each step.** If you have questions about any specific detail (e.g., exact GraphQL query structures, error codes to catch for fallback, batch sizing logic), ask for clarification.