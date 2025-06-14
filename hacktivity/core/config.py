"""Configuration management module."""

import os
import sys
from pathlib import Path
from typing import Optional

try:
    import tomllib  # Python 3.11+
except ImportError:
    try:
        import tomli as tomllib  # Fallback for older Python versions
    except ImportError:
        from .logging import get_logger
        logger = get_logger(__name__)
        logger.error("TOML support requires Python 3.11+ or 'tomli' package. Please install with 'pip install tomli'")
        sys.exit(1)

from pydantic import BaseModel, Field

from .logging import get_logger

logger = get_logger(__name__)


class CacheConfig(BaseModel):
    """Cache configuration settings."""
    max_age_hours: int = Field(default=24, ge=1, le=168, description="Cache TTL in hours")
    max_size_mb: int = Field(default=100, ge=10, le=1000, description="Cache size limit in MB")
    directory: Optional[str] = Field(default=None, description="Cache directory path (None for default)")


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
    cb_cooldown_sec: int = Field(
        default=60, ge=10, le=600,
        description="Seconds to wait in OPEN state before transitioning to HALF_OPEN."
    )
    
    # Parallel Processing Configuration
    max_workers: int = Field(
        default=5, ge=1, le=10,
        description="Max parallel workers for repository processing"
    )
    rate_limit_buffer: int = Field(
        default=100, ge=50, le=500,
        description="API calls to reserve as a buffer to avoid hitting the hard rate limit"
    )
    parallel_enabled: bool = Field(
        default=True,
        description="Enable parallel processing of repositories"
    )


class AIConfig(BaseModel):
    """AI model configuration settings."""
    model_name: str = Field(default="gemini-1.5-flash", description="AI model name")
    
    # Batch Processing Configuration
    batch_enabled: bool = Field(default=True, description="Enable batch processing for AI summarization")
    batch_size: int = Field(default=1000, ge=1, le=5000, description="Number of commits per batch")
    batch_overlap: int = Field(default=50, ge=0, le=200, description="Number of commits to overlap between batches for context")
    max_retries: int = Field(default=3, ge=1, le=10, description="Maximum retries for failed batches")
    retry_delay: int = Field(default=5, ge=1, le=60, description="Delay in seconds between batch retries")
    
    def model_post_init(self, __context) -> None:
        """Validate batch configuration after initialization."""
        if self.batch_overlap >= self.batch_size:
            # Auto-fix dangerous overlap values
            safe_overlap = max(0, self.batch_size - 1)
            
            # Only show warning if debug logging is enabled
            logger = __import__('logging').getLogger(__name__)
            if logger.isEnabledFor(__import__('logging').DEBUG):
                logger.warning(
                    "batch_overlap (%d) >= batch_size (%d) would cause memory issues. "
                    "Auto-correcting to %d", 
                    self.batch_overlap, self.batch_size, safe_overlap
                )
            
            object.__setattr__(self, 'batch_overlap', safe_overlap)


class AppConfig(BaseModel):
    """Application-wide configuration settings."""
    log_level: str = Field(default="INFO", description="Log level (DEBUG, INFO, WARNING, ERROR)")
    default_prompt_type: str = Field(default="standup", description="Default prompt type")
    default_format: str = Field(default="markdown", description="Default output format (markdown, json, plain)")


class Config(BaseModel):
    """Root configuration model."""
    cache: CacheConfig = Field(default_factory=CacheConfig)
    github: GitHubConfig = Field(default_factory=GitHubConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    app: AppConfig = Field(default_factory=AppConfig)


def get_config_path() -> Path:
    """Get the path to the configuration file."""
    return Path.home() / ".hacktivity" / "config.toml"


def load_config() -> Config:
    """Load configuration from file with fallback to defaults.
    
    Returns:
        Config: Loaded configuration with defaults for missing values
    """
    config_path = get_config_path()
    
    if not config_path.exists():
        logger.info("No config file found at %s, using defaults", config_path)
        return Config()
    
    try:
        with open(config_path, 'rb') as f:
            config_data = tomllib.load(f)
        
        logger.debug("Loaded config from %s", config_path)
        return Config(**config_data)
        
    except Exception as e:
        logger.warning("Failed to load config from %s: %s. Using defaults.", config_path, e)
        return Config()


def save_default_config() -> None:
    """Save a default configuration file to help users get started."""
    config_path = get_config_path()
    config_dir = config_path.parent
    
    # Create config directory if it doesn't exist
    config_dir.mkdir(parents=True, exist_ok=True)
    
    if config_path.exists():
        logger.info("Config file already exists at %s", config_path)
        return
        
    default_config_toml = '''# Hacktivity Configuration File
# This file controls various aspects of hacktivity behavior.
# All settings are optional - if omitted, sensible defaults will be used.

[cache]
# Cache settings
max_age_hours = 24      # How long to keep cached results (1-168 hours)
max_size_mb = 100       # Maximum cache size in MB (10-1000)
# directory = "/custom/cache/path"  # Uncomment to override default cache location

[github]
# GitHub API settings
per_page = 100          # Items per API page (1-100)
timeout_seconds = 60    # Request timeout (10-300 seconds)
max_pages = 10          # Maximum pages to fetch (1-20)
retry_attempts = 3      # Number of retry attempts (1-10)
retry_min_wait = 4      # Minimum retry wait (1-60 seconds)
retry_max_wait = 10     # Maximum retry wait (1-300 seconds)

# Parallel Processing Configuration
max_workers = 5         # Max parallel workers for repository processing (1-10)
rate_limit_buffer = 100 # API calls to reserve as buffer (50-500)
parallel_enabled = true # Enable parallel processing (true/false)

# GraphQL Configuration
graphql_enabled = true          # Enable GraphQL API usage (true/false)
graphql_fallback_enabled = true # Enable automatic REST fallback (true/false)
graphql_batch_size = 10         # Repositories per GraphQL query batch (1-50)
graphql_timeout_seconds = 120   # Timeout for complex GraphQL queries (30-600)

[ai]
# AI model settings
model_name = "gemini-1.5-flash"  # AI model to use

# Batch Processing Configuration
batch_enabled = true            # Enable batch processing for AI summarization (true/false)
batch_size = 1000              # Number of commits per batch (1-5000)
batch_overlap = 50             # Number of commits to overlap between batches for context (0-200)
max_retries = 3                # Maximum retries for failed batches (1-10)
retry_delay = 5                # Delay in seconds between batch retries (1-60)

[app]
# Application settings
log_level = "INFO"              # Log level: DEBUG, INFO, WARNING, ERROR
default_prompt_type = "standup" # Default prompt type: standup, retro, weekly
default_format = "markdown"     # Default output format: markdown, json, plain
'''
    
    try:
        config_path.write_text(default_config_toml)
        logger.info("Created default config file at %s", config_path)
    except Exception as e:
        logger.error("Failed to create default config file: %s", e)


# Global config instance
_config_instance: Optional[Config] = None


def get_config() -> Config:
    """Get the global configuration instance, loading it if necessary."""
    global _config_instance
    if _config_instance is None:
        _config_instance = load_config()
    return _config_instance


def reload_config() -> Config:
    """Reload configuration from file."""
    global _config_instance
    _config_instance = load_config()
    return _config_instance