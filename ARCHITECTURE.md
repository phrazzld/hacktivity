# Architecture Decision Record: Repository-First Processing

## Status
**Accepted** - Implemented in version 2.0

## Context

The original hacktivity implementation used GitHub's Search API (`/search/commits`) to find all commits by a user across repositories. While this approach was simple, it had several critical limitations when processing large datasets:

### Problems with Search API Approach

1. **Reliability Issues**
   - Search API has lower rate limits (30 requests/minute vs 5000/hour)
   - Frequent timeouts on large queries (6+ months, 100+ repositories)
   - Inconsistent results due to search index delays
   - No pagination control over large result sets

2. **Scale Limitations**
   - Cannot reliably process enterprise-scale datasets
   - Single point of failure affects entire operation
   - No incremental progress tracking
   - Memory usage grows linearly with dataset size

3. **Robustness Concerns**
   - No fault isolation between repositories
   - Single repository access issues fail entire operation
   - No resumability on interruption
   - Poor error visibility and debugging

## Decision

We have redesigned hacktivity to use a **repository-first architecture** with the following core components:

### 1. Repository Discovery (`core/repos.py`)
- Uses `/user/repos` and `/orgs/{org}/repos` endpoints
- Discovers all accessible repositories upfront
- Caches repository metadata for 7 days
- Supports filtering by organization and activity

### 2. Per-Repository Processing (`core/commits.py`)
- Uses reliable `/repos/{owner}/{repo}/commits` endpoint
- Processes each repository independently
- Handles pagination automatically
- Filters by author after fetching

### 3. Date Range Chunking (`core/chunking.py`)
- Breaks large date ranges into weekly chunks
- Processes chunks independently for memory efficiency
- Enables granular progress tracking and resumability
- Handles chunk-level failures gracefully

### 4. Operation State Management (`core/state.py`)
- SQLite database tracks operation progress
- Repository-level and chunk-level state persistence
- Automatic resume on restart
- Comprehensive error tracking and retry management

### 5. Parallel Processing (`core/parallel.py`)
- Configurable concurrent repository processing (1-10 workers)
- Thread-safe coordination with global rate limiting
- Independent failure isolation per repository
- Aggregate progress tracking across workers

### 6. Circuit Breaker Protection (`core/circuit_breaker.py`)
- Per-endpoint failure isolation
- Automatic circuit opening after consecutive failures
- Graceful degradation to cached data
- Self-healing with configurable cooldown periods

### 7. Rate Limiting Coordination (`core/rate_limiter.py`)
- Global token bucket algorithm (5000 req/hour GitHub limit)
- Thread-safe coordination across parallel workers
- Configurable buffer reservation
- Background token replenishment

## Consequences

### Positive

1. **Enterprise Scale Reliability**
   - Handles 100+ repositories with 10,000+ commits reliably
   - Processes year-long date ranges without timeouts
   - Fault isolation prevents cascade failures
   - Memory usage remains bounded (<500MB for large operations)

2. **Resumability and Robustness**
   - Operations can be interrupted and resumed automatically
   - Granular state tracking per repository and chunk
   - Failed repositories don't affect others
   - Comprehensive error reporting and debugging

3. **Performance and Efficiency**
   - Parallel processing reduces total processing time
   - Intelligent caching with multi-level TTLs
   - Optimal API usage patterns
   - Configurable performance tuning

4. **Operational Visibility**
   - Real-time progress tracking
   - Performance metrics and monitoring
   - Detailed state information for debugging
   - Circuit breaker status and health monitoring

### Negative

1. **Implementation Complexity**
   - Significantly more complex codebase
   - Multiple coordinated components
   - Thread safety considerations
   - State management overhead

2. **Storage Requirements**
   - SQLite database for state management
   - Larger cache footprint with multi-level caching
   - More disk I/O for state persistence

3. **Initial Latency**
   - Repository discovery step adds upfront latency
   - State initialization overhead
   - Circuit breaker learning period

## Implementation Details

### Data Flow
```
User Request
    ↓
Repository Discovery (repos.py)
    ↓
Activity Filtering (by updated_at)
    ↓
Parallel Orchestrator (parallel.py)
    ↓
Per-Repository Processing (commits.py)
    ↓
Date Range Chunking (chunking.py)
    ↓ 
State Management (state.py)
    ↓
Result Aggregation
```

### Failure Handling
- **Repository Level**: Circuit breaker isolates failing repositories
- **Chunk Level**: Failed chunks are retried independently
- **Operation Level**: Partial failures don't prevent completion
- **System Level**: Interruptions are recovered automatically

### Performance Characteristics
- **Throughput**: 100+ commits/second processing
- **Concurrency**: Up to 10 parallel repository workers
- **Memory**: <500MB for 100,000+ commits
- **API Efficiency**: <5000 calls for 100 repositories

### Configuration Options
```toml
[github]
# Parallel processing
max_workers = 5
parallel_enabled = true
rate_limit_buffer = 100

# Circuit breaker
cb_failure_threshold = 5
cb_cooldown_sec = 300

# Retry behavior
retry_attempts = 3
retry_min_wait = 1
retry_max_wait = 60
```

## Alternatives Considered

### 1. GraphQL API
- **Pros**: More efficient data fetching, fewer API calls
- **Cons**: Complex query building, GitHub GraphQL limitations
- **Status**: Future enhancement (T026)

### 2. Event Sourcing
- **Pros**: Complete audit trail, replay capability
- **Cons**: Over-engineering for current requirements
- **Status**: Rejected for current scope

### 3. Database Persistence
- **Pros**: Rich querying, better data management
- **Cons**: External dependency, deployment complexity
- **Status**: SQLite chosen as lightweight alternative

## Monitoring and Metrics

The new architecture includes comprehensive monitoring:

### Performance Metrics
- Processing throughput (commits/second)
- Memory usage tracking
- API call efficiency
- Worker utilization

### Health Indicators
- Circuit breaker status per endpoint
- Rate limit consumption
- Cache hit ratios
- Error rates by repository

### Operational Metrics
- Operation completion rates
- Resume frequency
- Average processing time
- Repository failure patterns

## Future Enhancements

1. **GraphQL Integration** (T026)
   - More efficient data fetching
   - Reduced API call volume
   - Better query optimization

2. **Batch AI Processing** (T027)
   - Batch commits for AI efficiency
   - Reduced AI API calls
   - Better cost optimization

3. **Operation Monitoring** (T030)
   - Real-time operation dashboards
   - ETA calculations
   - Performance analytics

## References

- [GitHub REST API Documentation](https://docs.github.com/en/rest)
- [Circuit Breaker Pattern](https://martinfowler.com/bliki/CircuitBreaker.html)
- [Token Bucket Algorithm](https://en.wikipedia.org/wiki/Token_bucket)
- [Test-Driven Development](https://martinfowler.com/bliki/TestDrivenDevelopment.html)

---

*This architecture prioritizes robustness and scalability over simplicity, enabling hacktivity to handle enterprise-scale GitHub activity processing reliably.*