# Hacktivity Enhancement Plan

## Product Requirements Document

### Vision
Transform hacktivity from a functional prototype into a robust enterprise-grade tool that reliably processes massive amounts of GitHub activity data across extended time periods, prioritizing completeness and resilience over speed.

### Core Principles
1. **Robustness First**: Handle years of data across hundreds of repositories without failure
2. **Data Completeness**: Never miss commits due to API limitations or timeouts
3. **Progressive Processing**: Support resumable operations for multi-hour/day data fetches
4. **Fault Tolerance**: Gracefully handle and recover from all API failures
5. **Scalability**: Efficient processing of datasets with 100,000+ commits

## Current State Analysis

### Strengths
- Clear, focused purpose
- Minimal dependencies
- Leverages existing tools (gh CLI)
- Functional for small-scale queries

### Critical Issues
1. **GitHub Search API Limitations**: Consistently times out with date-filtered queries
2. **No Support for Large-Scale Data**: Cannot handle broad date ranges or high-volume repositories
3. **Single Point of Failure**: Relies entirely on unreliable search/commits endpoint
4. **Incomplete Data Retrieval**: No mechanism to ensure all commits are captured
5. **Poor Performance at Scale**: Linear processing becomes impractical for large datasets

### User Requirements
- "Must work with date ranges spanning multiple years"
- "Need to process hundreds of repositories with thousands of commits each"
- "Should handle repositories with 50,000+ commits in a date range"
- "Must complete successfully even if it takes hours or days"
- "Speed is not an issue - prioritize robustness and quality of output"

## Proposed Solution

### Architecture Overview

```
hacktivity/
├── hacktivity.py       # CLI entry point (minimal)
├── core/
│   ├── __init__.py
│   ├── github.py       # Repository-based data fetching
│   ├── repos.py        # Repository discovery and management
│   ├── commits.py      # Commit fetching with chunking
│   ├── ai.py          # AI integration with batch processing
│   ├── cache.py       # Multi-level caching system
│   ├── state.py       # Operation state management
│   └── config.py      # Configuration management
├── prompts/           # User-customizable prompt templates
│   ├── standup.md
│   ├── retro.md
│   └── weekly.md
├── .hacktivity/       # User config directory
│   ├── config.toml    # User settings
│   ├── cache/         # Multi-level cache storage
│   │   ├── repos/     # Repository metadata cache
│   │   ├── commits/   # Commit data cache
│   │   └── summaries/ # AI summary cache
│   └── state/         # Operation state for resume
└── requirements.txt   # Dependencies
```

### Key Enhancements

#### 1. Repository-First Architecture
```python
# Phase 1: Discover all repositories
repos = discover_user_repositories(user, org_filter)
save_repo_list_to_cache(repos)

# Phase 2: Fetch commits per repository
for repo in repos:
    # Use reliable endpoint that doesn't timeout
    commits = fetch_repo_commits(repo, since, until)
    cache_commits_incrementally(repo, commits)
    
# Phase 3: Aggregate and filter
all_commits = aggregate_commits_from_cache()
user_commits = filter_by_author(all_commits, user)
```

#### 2. Hierarchical Date Chunking
```python
# Break large date ranges into manageable chunks
chunks = create_date_chunks(since, until, max_days=7)

for chunk in chunks:
    # Process each chunk independently
    result = process_date_chunk(chunk)
    save_chunk_state(chunk, result)
    
    # Support pause/resume
    if should_pause():
        save_resume_state(current_chunk_index)
        return
```

#### 3. Multi-Level Caching Architecture
```toml
# ~/.hacktivity/config.toml
[cache]
# L1: Repository metadata (rarely changes)
repo_cache_ttl_days = 7

# L2: Commit data (immutable)
commit_cache_ttl_days = 365

# L3: Summary cache (regeneratable)
summary_cache_ttl_days = 30

[processing]
chunk_size_days = 7
max_parallel_repos = 5
batch_size_commits = 1000
resume_on_failure = true

[github]
# Use multiple API approaches
prefer_graphql = true
fallback_to_rest = true
max_retries_per_endpoint = 5
```

#### 4. Progressive Data Processing
```python
# Process data as it arrives, not all at once
class ProgressiveProcessor:
    def __init__(self, state_manager):
        self.state = state_manager
        
    def process_repository(self, repo):
        # Check if already processed
        if self.state.is_complete(repo):
            return self.state.get_result(repo)
            
        # Resume from last position
        last_commit = self.state.get_last_processed(repo)
        
        # Stream commits in batches
        for batch in stream_commits(repo, after=last_commit):
            process_batch(batch)
            self.state.save_progress(repo, batch[-1])
```

#### 5. Fault-Tolerant State Management
```python
# Comprehensive state tracking for resilience
class OperationState:
    def __init__(self, operation_id):
        self.id = operation_id
        self.start_time = now()
        self.repositories = []
        self.processed_repos = set()
        self.failed_repos = {}
        self.total_commits = 0
        
    def mark_repo_complete(self, repo, commit_count):
        self.processed_repos.add(repo)
        self.total_commits += commit_count
        self.save_to_disk()
        
    def mark_repo_failed(self, repo, error, retry_count):
        self.failed_repos[repo] = {
            'error': error,
            'retries': retry_count,
            'last_attempt': now()
        }
        self.save_to_disk()
```

## Implementation Plan

### Phase 1: Repository Discovery (Week 1)
1. Implement repository discovery module
2. Create repository metadata caching
3. Add repository filtering logic
4. Build GraphQL query support

**Success Criteria**: Can discover and cache all user repositories

### Phase 2: Robust Data Fetching (Week 2-3)
1. Implement repository-based commit fetching
2. Add date range chunking system
3. Create progressive processing pipeline
4. Build comprehensive state management

**Success Criteria**: Can fetch 100,000+ commits without failure

### Phase 3: Scalability & Performance (Week 4)
1. Implement parallel repository processing
2. Add multi-level caching system
3. Create batch processing for AI summaries
4. Optimize memory usage for large datasets

**Success Criteria**: Can process year-long date ranges efficiently

### Phase 4: Resilience & Recovery (Week 5)
1. Add circuit breaker patterns
2. Implement operation resume capability
3. Create fallback API strategies
4. Add comprehensive error recovery

**Success Criteria**: Operations can survive multi-day execution with interruptions

## Technical Decisions

### Architecture Choices
- **Repository-First vs Search API**: Search API is unreliable for large date ranges
- **SQLite for State Management**: Robust ACID guarantees for long-running operations
- **Hierarchical Caching**: Different TTLs for different data types
- **Progressive Processing**: Memory-efficient for massive datasets
- **GraphQL + REST Fallback**: Maximum compatibility and reliability

### Dependencies
```txt
# requirements.txt
google-generativeai>=0.3.0
click>=8.0            # CLI framework
python-dateutil>=2.8  # Date parsing
pydantic>=2.0        # Config validation
diskcache>=5.0       # Caching layer
rich>=13.0           # Progress tracking
sqlite3              # State management (stdlib)
aiohttp>=3.8         # Async HTTP for parallel fetching
tenacity>=8.0        # Retry logic
```

### Testing Strategy
- Unit tests for core functions
- Integration tests with mock APIs
- End-to-end test with real (rate-limited) APIs
- Manual testing checklist for releases

## Success Metrics

1. **Scale**: Successfully process 100,000+ commits across 1+ year date ranges
2. **Reliability**: 100% completion rate even with network interruptions
3. **Robustness**: Graceful handling of all GitHub API failure modes
4. **Data Completeness**: Zero missed commits due to API limitations
5. **Resumability**: Can pause/resume operations at any point

## Non-Goals

- Real-time performance (speed is explicitly not a priority)
- Web interface
- Multi-user support
- Supporting non-GitHub platforms
- Minimizing API calls (robustness over efficiency)

## Migration Path

1. Current users can continue using `main.py`
2. New version installed as `hacktivity` command
3. Automatic import of environment variables to config file
4. Backward compatible CLI arguments

## Future Considerations

Once core enhancements are complete, consider:
- Additional prompt templates (PR descriptions, release notes)
- Export to different formats (JIRA, Confluence)
- Local git repository analysis
- Team summary aggregation (multiple users)

## Decision Log

| Decision | Rationale |
|----------|-----------|
| File-based cache | Simple, portable, no dependencies |
| TOML for config | Human-readable, standard library support |
| Click over argparse | Better UX, easier testing |
| Single package | Easier installation and distribution |
| No async | Unnecessary complexity for sequential operations |

## Summary

This plan transforms hacktivity from a prototype limited by GitHub's Search API into an enterprise-grade tool capable of processing massive amounts of GitHub data. The focus is on absolute reliability and data completeness, using a repository-first architecture that bypasses the unreliable search endpoints.

Key innovations:
- Repository-based fetching avoids search API timeouts
- Hierarchical chunking handles any date range size
- Progressive processing supports multi-day operations
- Comprehensive state management enables full resumability

Total implementation time: ~5 weeks
Result: A tool that can reliably process years of GitHub activity across hundreds of repositories