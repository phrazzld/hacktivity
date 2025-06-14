# T026: GraphQL Support Implementation Instructions

## Task Overview
**Ticket:** T026 · Feature · P2: add GraphQL support for efficiency
**Priority:** P2 (Medium)
**Context:** GraphQL can fetch more data in fewer requests

## Requirements Analysis

### Done-When Criteria
1. **GraphQL used by default when available** - Primary API approach
2. **Automatic fallback to REST on errors** - Seamless degradation
3. **Reduces API calls by 50%+** - Measurable efficiency improvement

### Action Items
1. Implement GraphQL queries for repository data
2. Add fallback to REST API when GraphQL fails
3. Optimize queries to minimize API calls
4. Handle GraphQL-specific rate limits

## Current Architecture Analysis

### API Call Pattern
The current system uses GitHub CLI (`gh api`) for all API interactions:
- **Repository Discovery**: `/user/repos`, `/orgs/{org}/repos` endpoints
- **Commit Fetching**: `/repos/{owner}/{repo}/commits` with pagination
- **Circuit Breaker**: Per-endpoint failure isolation
- **Rate Limiting**: Global token bucket (5000 req/hour coordination)

### Key Integration Points
1. **`repos.py`**: Repository discovery with pagination
2. **`commits.py`**: Per-repository commit fetching
3. **`circuit_breaker.py`**: Failure isolation per endpoint
4. **`rate_limiter.py`**: Global API rate coordination
5. **`config.py`**: Configuration management

### Current API Command Pattern
```python
command = ["gh", "api", "-X", "GET", endpoint]
result = subprocess.run(command, capture_output=True, text=True)
response_data = json.loads(result.stdout)
```

## GraphQL Integration Requirements

### GitHub GraphQL API Capabilities
GitHub's GraphQL API v4 allows fetching repository and commit data in single queries:
- **Repository + Commit Data**: Single query can fetch repo metadata + commit history
- **Batch Operations**: Multiple repositories in one query
- **Field Selection**: Only fetch required fields
- **Pagination**: Cursor-based pagination with `first`/`after` parameters

### Efficiency Targets
- **50%+ API Call Reduction**: Batch repository discovery + commit fetching
- **Optimized Data Fetching**: Select only required fields
- **Improved Performance**: Fewer round trips, more data per request

### GitHub CLI GraphQL Support
The `gh api graphql` command supports GraphQL queries:
```bash
gh api graphql -f query='query { viewer { login } }'
```

## Implementation Strategy

### 1. GraphQL Query Design
Design efficient queries that combine repository discovery and commit fetching:

**Repository Discovery with Recent Commits:**
```graphql
query($login: String!, $first: Int!, $since: GitTimestamp, $until: GitTimestamp) {
  user(login: $login) {
    repositories(first: $first, orderBy: {field: UPDATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        nameWithOwner
        name
        owner { login }
        updatedAt
        defaultBranchRef {
          target {
            ... on Commit {
              history(first: 100, since: $since, until: $until, author: {id: $authorId}) {
                pageInfo { hasNextPage endCursor }
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
  }
}
```

### 2. Architecture Components

**New Module: `core/graphql.py`**
- GraphQL query builder and executor
- Query optimization and batching
- Response parsing and normalization
- Fallback coordination with REST API

**Enhanced Modules:**
- **`repos.py`**: GraphQL-first repository discovery with REST fallback
- **`commits.py`**: GraphQL batch commit fetching with REST fallback
- **`config.py`**: GraphQL configuration options

### 3. Fallback Strategy
- **Availability Detection**: Test GraphQL availability on first use
- **Error-Based Fallback**: Fallback on specific GraphQL errors
- **Per-Operation Fallback**: Repository discovery vs commit fetching can fallback independently
- **Circuit Breaker Integration**: GraphQL failures trigger fallback, not circuit opening

### 4. Configuration Options
```toml
[github]
# GraphQL Configuration
graphql_enabled = true          # Enable GraphQL API usage
graphql_fallback_enabled = true # Enable automatic REST fallback
graphql_batch_size = 10         # Repositories per GraphQL query
graphql_timeout_seconds = 120   # Longer timeout for complex queries
```

## Key Files to Examine

### Core Modules
- `hacktivity/core/repos.py` - Repository discovery patterns
- `hacktivity/core/commits.py` - Commit fetching patterns
- `hacktivity/core/config.py` - Configuration system
- `hacktivity/core/circuit_breaker.py` - Failure isolation patterns
- `hacktivity/core/rate_limiter.py` - Rate limiting coordination

### Testing Infrastructure
- `tests/test_repos.py` - Repository discovery test patterns
- `tests/test_commits.py` - Commit fetching test patterns
- `tests/utils/mock_api.py` - API mocking patterns
- `tests/test_large_scale_integration.py` - Integration test patterns

## Design Principles

### Architectural Tenets
- **Modularity**: GraphQL support as optional enhancement, not replacement
- **Testability**: All GraphQL interactions testable through mocking
- **Simplicity**: Fallback behavior should be transparent to users
- **Explicitness**: Clear configuration and error handling

### Integration Constraints
- **No Breaking Changes**: Existing REST API functionality must remain intact
- **Circuit Breaker Compatibility**: GraphQL failures should integrate with existing circuit breaker
- **Rate Limiting**: GraphQL queries count toward same 5000 req/hour limit
- **Caching**: GraphQL responses must integrate with existing cache system

## Testing Strategy

### Unit Tests
- GraphQL query building and execution
- Fallback mechanism triggered by various error conditions
- Configuration validation and defaults
- Response parsing and normalization

### Integration Tests
- End-to-end GraphQL + fallback workflows
- Large-scale performance comparison (API call reduction)
- Circuit breaker behavior with GraphQL failures
- Rate limiting coordination

### Performance Benchmarks
- API call count comparison (GraphQL vs REST)
- Response time measurements
- Memory usage with larger GraphQL responses
- Cache efficiency with combined data

## Implementation Phases

### Phase 1: Core GraphQL Infrastructure
1. Create `core/graphql.py` module
2. Implement basic GraphQL query execution
3. Add configuration options
4. Create foundational tests

### Phase 2: Repository Discovery Integration
1. Enhance `repos.py` with GraphQL queries
2. Implement fallback mechanism
3. Add comprehensive test coverage
4. Performance measurement

### Phase 3: Commit Fetching Integration
1. Enhance `commits.py` with GraphQL batch queries
2. Optimize query efficiency
3. Integrate with existing chunking system
4. Comprehensive testing

### Phase 4: System Integration
1. Circuit breaker integration
2. Rate limiting coordination
3. Cache integration
4. Large-scale integration tests

## Success Criteria

### Functional Requirements
- GraphQL queries work for repository discovery and commit fetching
- Automatic fallback to REST on any GraphQL failure
- Transparent operation - users see no difference in functionality
- All existing tests continue to pass

### Performance Requirements
- 50%+ reduction in API calls for typical workloads
- No regression in response time for small datasets
- Improved performance for large datasets (100+ repositories)
- Memory usage remains within established bounds

### Quality Requirements
- 90%+ test coverage for new GraphQL code
- Comprehensive error handling and logging
- Clear configuration documentation
- Integration with existing monitoring and metrics

This implementation will significantly improve API efficiency while maintaining the robustness and reliability of the existing REST-based architecture.