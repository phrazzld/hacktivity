An elegant and efficient solution requires a multi-faceted approach, integrating GraphQL as the primary data-fetching mechanism while retaining the robust, battle-tested REST implementation as a seamless fallback. This ensures high performance for typical scenarios and maintains reliability in the face of API errors or edge cases.

The implementation will be phased, focusing on creating a core GraphQL execution module, then integrating it into the commit and repository fetching logic.

### Phase 1: Core GraphQL Infrastructure

First, I'll establish the foundational components for GraphQL integration. This includes updating the configuration and creating a new module for executing GraphQL queries.

**1. Update Configuration (`config.py`)**

I'll add GraphQL-specific settings to the `GitHubConfig` model and the default configuration file, as specified in the requirements.

```python
# hacktivity/core/config.py

# ... existing imports ...
from pydantic import BaseModel, Field

# ... existing models ...

class GitHubConfig(BaseModel):
    """GitHub API configuration settings."""
    per_page: int = Field(default=100, ge=1, le=100, description="Items per API page")
    timeout_seconds: int = Field(default=60, ge=10, le=300, description="Request timeout in seconds")
    max_pages: int = Field(default=10, ge=1, le=20, description="Maximum pages to fetch")
    retry_attempts: int = Field(default=3, ge=1, le=10, description="Number of retry attempts")
    retry_min_wait: int = Field(default=4, ge=1, le=60, description="Minimum retry wait in seconds")
    retry_max_wait: int = Field(default=10, ge=1, le=300, description="Maximum retry wait in seconds")

    # GraphQL Configuration
    graphql_enabled: bool = Field(default=True, description="Enable GraphQL API usage")
    graphql_fallback_enabled: bool = Field(default=True, description="Enable automatic REST fallback on GraphQL errors")
    graphql_batch_size: int = Field(default=10, ge=1, le=50, description="Repositories per GraphQL query batch")
    graphql_timeout_seconds: int = Field(default=120, ge=30, le=600, description="Longer timeout for complex GraphQL queries")

    # Circuit Breaker Configuration
    cb_failure_threshold: int = Field(
        default=5, ge=1, le=20,
        description="Consecutive failures before opening the circuit."
    )
    # ... rest of the model ...

# ...

def save_default_config() -> None:
    # ...
    default_config_toml = '''# Hacktivity Configuration File
# ...
[github]
# GitHub API settings
per_page = 100          # Items per API page (1-100)
timeout_seconds = 60    # Request timeout (10-300 seconds)
max_pages = 10          # Maximum pages to fetch (1-20)
retry_attempts = 3      # Number of retry attempts (1-10)
retry_min_wait = 4      # Minimum retry wait (1-60 seconds)
retry_max_wait = 10     # Maximum retry wait (1-300 seconds)

# GraphQL Configuration
graphql_enabled = true          # Enable GraphQL API usage
graphql_fallback_enabled = true # Enable automatic REST fallback on GraphQL errors
graphql_batch_size = 10         # Repositories per GraphQL query batch (1-50)
graphql_timeout_seconds = 120   # Longer timeout for complex GraphQL queries (30-600)

# Parallel Processing Configuration
max_workers = 5         # Max parallel workers for repository processing (1-10)
# ... rest of the file ...
'''
    # ...
```

**2. Create GraphQL Execution Module (`graphql.py`)**

This new module will encapsulate all direct interactions with the `gh api graphql` command, providing a clean interface for the rest of the application.

```python
# hacktivity/core/graphql.py

"""
Core module for executing