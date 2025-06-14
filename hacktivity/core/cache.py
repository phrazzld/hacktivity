"""File-based caching module with multi-level cache support."""

import os
import sys
import time
from pathlib import Path
from typing import Any, Optional, Dict, List

try:
    import diskcache
except ImportError:
    from .logging import get_logger
    logger = get_logger(__name__)
    logger.error("The 'diskcache' library is required. Please install it with 'pip install diskcache'")
    sys.exit(1)

from .logging import get_logger

logger = get_logger(__name__)

# Lazy import config to avoid circular imports
def _get_config():
    from .config import get_config
    return get_config()


class CacheLevel:
    """A single cache level with specific TTL and size management."""
    
    def __init__(self, name: str, cache_dir: Path, default_ttl_hours: int, max_size_mb: int):
        """Initialize a cache level.
        
        Args:
            name: Name of this cache level (e.g., 'repos', 'commits')
            cache_dir: Directory for this cache level
            default_ttl_hours: Default TTL in hours for this cache level
            max_size_mb: Maximum size in MB for this cache level
        """
        self.name = name
        self.cache_dir = cache_dir
        self.default_ttl_hours = default_ttl_hours
        self.max_size_mb = max_size_mb
        
        # Ensure cache directory exists
        cache_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize diskcache with size limit
        self._cache = diskcache.Cache(
            str(cache_dir),
            size_limit=max_size_mb * 1024 * 1024  # Convert MB to bytes
        )
        
        logger.debug("Initialized cache level '%s' at %s (TTL: %dh, Size: %dMB)", 
                    name, cache_dir, default_ttl_hours, max_size_mb)
    
    def set(self, key: str, value: Any) -> None:
        """Store a value in this cache level.
        
        Args:
            key: Cache key
            value: Value to store
        """
        try:
            # Store value with current timestamp
            cache_entry = {
                'value': value,
                'timestamp': time.time()
            }
            self._cache[key] = cache_entry
            logger.debug("Cached in %s level: %s", self.name, key)
        except Exception as e:
            logger.warning("Failed to cache data in %s level: %s", self.name, e)
    
    def get(self, key: str, max_age_hours: Optional[int] = None) -> Optional[Any]:
        """Retrieve a value from this cache level.
        
        Args:
            key: Cache key
            max_age_hours: Maximum age in hours (None uses level default)
            
        Returns:
            Cached value if found and not expired, None otherwise
        """
        try:
            if max_age_hours is None:
                max_age_hours = self.default_ttl_hours
                
            cache_entry = self._cache.get(key)
            if cache_entry is None:
                return None
                
            # Check if entry has expired
            current_time = time.time()
            entry_time = cache_entry.get('timestamp', 0)
            age_seconds = current_time - entry_time
            max_age_seconds = max_age_hours * 3600
            
            if age_seconds > max_age_seconds:
                # Entry has expired, remove it
                del self._cache[key]
                logger.debug("Expired cache entry in %s level: %s", self.name, key)
                return None
                
            logger.debug("Cache hit in %s level: %s", self.name, key)
            return cache_entry.get('value')
            
        except Exception as e:
            logger.warning("Failed to retrieve cached data from %s level: %s", self.name, e)
            return None
    
    def clear(self) -> None:
        """Clear all cached data in this level."""
        try:
            self._cache.clear()
            logger.info("Cleared %s cache level", self.name)
        except Exception as e:
            logger.warning("Failed to clear %s cache level: %s", self.name, e)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get statistics for this cache level.
        
        Returns:
            Dictionary with cache statistics
        """
        try:
            stats = {
                'name': self.name,
                'entries': len(self._cache),
                'size_bytes': self._cache.volume(),
                'size_mb': round(self._cache.volume() / (1024 * 1024), 2),
                'max_size_mb': self.max_size_mb,
                'default_ttl_hours': self.default_ttl_hours,
                'directory': str(self.cache_dir)
            }
            return stats
        except Exception as e:
            logger.warning("Failed to get stats for %s cache level: %s", self.name, e)
            return {
                'name': self.name,
                'error': str(e)
            }
    
    def close(self) -> None:
        """Close this cache level."""
        try:
            self._cache.close()
        except Exception as e:
            logger.warning("Failed to close %s cache level: %s", self.name, e)


class MultiLevelCache:
    """Multi-level cache system with specialized storage for different data types."""
    
    def __init__(self, base_cache_dir: Optional[Path] = None, total_size_mb: Optional[int] = None):
        """Initialize multi-level cache system.
        
        Args:
            base_cache_dir: Base directory for all cache levels (None for default)
            total_size_mb: Total size limit across all cache levels (None for config default)
        """
        # Load config for defaults
        config = _get_config()
        
        if base_cache_dir is None:
            if config.cache.directory is None:
                base_cache_dir = Path.home() / ".hacktivity" / "cache"
            else:
                base_cache_dir = Path(config.cache.directory)
        
        if total_size_mb is None:
            total_size_mb = config.cache.max_size_mb
        
        self.base_cache_dir = base_cache_dir
        self.total_size_mb = total_size_mb
        
        # Initialize cache levels with size allocation
        self._levels = self._initialize_cache_levels()
        
        # Cache key routing patterns
        self._routing_patterns = {
            'repos:': 'repos',
            'commits:': 'commits', 
            'summary:': 'summaries',
            'summaries:': 'summaries',
            'chunk_state:': 'chunks',
            'chunks:': 'chunks'
        }
        
        logger.info("Initialized multi-level cache with %d levels (Total: %dMB)", 
                   len(self._levels), total_size_mb)
    
    def _initialize_cache_levels(self) -> Dict[str, CacheLevel]:
        """Initialize all cache levels with appropriate size allocation.
        
        Returns:
            Dictionary mapping level names to CacheLevel instances
        """
        # Size allocation strategy (total 100MB default):
        # - repos: 10MB (metadata is small but numerous)
        # - commits: 50MB (largest data volume) 
        # - summaries: 20MB (AI-generated content)
        # - chunks: 20MB (state tracking data)
        
        size_allocation = {
            'repos': max(int(self.total_size_mb * 0.10), 5),      # 10% minimum 5MB
            'commits': max(int(self.total_size_mb * 0.50), 20),   # 50% minimum 20MB
            'summaries': max(int(self.total_size_mb * 0.20), 10), # 20% minimum 10MB  
            'chunks': max(int(self.total_size_mb * 0.20), 5)      # 20% minimum 5MB
        }
        
        # TTL settings optimized for each data type
        ttl_settings = {
            'repos': 7 * 24,       # 7 days - repository metadata changes infrequently
            'commits': 365 * 24,   # 365 days - commit data is immutable
            'summaries': 30 * 24,  # 30 days - summaries may become stale
            'chunks': 30 * 24      # 30 days - chunk state for resumability
        }
        
        levels = {}
        for level_name in ['repos', 'commits', 'summaries', 'chunks']:
            level_dir = self.base_cache_dir / level_name
            levels[level_name] = CacheLevel(
                name=level_name,
                cache_dir=level_dir,
                default_ttl_hours=ttl_settings[level_name],
                max_size_mb=size_allocation[level_name]
            )
        
        return levels
    
    def _route_key_to_level(self, key: str) -> str:
        """Route a cache key to the appropriate cache level.
        
        Args:
            key: Cache key
            
        Returns:
            Cache level name
        """
        # Check for specific routing patterns
        for pattern, level_name in self._routing_patterns.items():
            if key.startswith(pattern):
                return level_name
        
        # Default to repos level for unknown patterns (backward compatibility)
        return 'repos'
    
    def set(self, key: str, value: Any) -> None:
        """Store a value in the appropriate cache level.
        
        Args:
            key: Cache key
            value: Value to store
        """
        level_name = self._route_key_to_level(key)
        self._levels[level_name].set(key, value)
    
    def get(self, key: str, max_age_hours: Optional[int] = None) -> Optional[Any]:
        """Retrieve a value from the appropriate cache level.
        
        Args:
            key: Cache key
            max_age_hours: Maximum age in hours (None uses level default)
            
        Returns:
            Cached value if found and not expired, None otherwise
        """
        level_name = self._route_key_to_level(key)
        return self._levels[level_name].get(key, max_age_hours)
    
    def clear_level(self, level_name: str) -> None:
        """Clear a specific cache level.
        
        Args:
            level_name: Name of cache level to clear
        """
        if level_name in self._levels:
            self._levels[level_name].clear()
        else:
            logger.warning("Unknown cache level: %s", level_name)
    
    def clear(self) -> None:
        """Clear all cache levels."""
        for level in self._levels.values():
            level.clear()
        logger.info("Cleared all cache levels")
    
    def get_cache_info(self) -> Dict[str, Any]:
        """Get comprehensive cache information across all levels.
        
        Returns:
            Dictionary with aggregated cache statistics
        """
        levels_info = []
        total_entries = 0
        total_size_mb = 0
        
        for level in self._levels.values():
            stats = level.get_stats()
            levels_info.append(stats)
            
            if 'entries' in stats:
                total_entries += stats['entries']
            if 'size_mb' in stats:
                total_size_mb += stats['size_mb']
        
        return {
            'total_levels': len(self._levels),
            'total_entries': total_entries,
            'total_size_mb': round(total_size_mb, 2),
            'max_total_size_mb': self.total_size_mb,
            'base_directory': str(self.base_cache_dir),
            'levels': levels_info
        }
    
    def warm_repos_cache(self, user: str, repos_data: List[Dict[str, Any]], org_filter: Optional[str] = None) -> None:
        """Warm the repository cache with pre-fetched data.
        
        Args:
            user: GitHub username
            repos_data: Repository data to cache
            org_filter: Optional organization filter
        """
        from .repos import _generate_repo_cache_key
        cache_key = _generate_repo_cache_key(user, org_filter)
        self.set(cache_key, repos_data)
        logger.info("Warmed repos cache for user %s: %d repositories", user, len(repos_data))
    
    def warm_commits_cache(self, repo_full_name: str, since: str, until: str, 
                          commits_data: List[Dict[str, Any]], author_filter: Optional[str] = None) -> None:
        """Warm the commits cache with pre-fetched data.
        
        Args:
            repo_full_name: Repository full name
            since: Start date
            until: End date  
            commits_data: Commit data to cache
            author_filter: Optional author filter
        """
        from .commits import _generate_commit_cache_key
        cache_key = _generate_commit_cache_key(repo_full_name, since, until, author_filter)
        self.set(cache_key, commits_data)
        logger.info("Warmed commits cache for %s (%s to %s): %d commits", 
                   repo_full_name, since, until, len(commits_data))
    
    def close(self) -> None:
        """Close all cache levels."""
        for level in self._levels.values():
            level.close()
        logger.debug("Closed multi-level cache")


def clear() -> None:
    """Clear all cached data in the global cache."""
    get_multi_cache().clear()


def get_cache_info() -> Dict[str, Any]:
    """Get comprehensive cache information.
    
    Returns:
        Dictionary with cache statistics
    """
    return get_multi_cache().get_cache_info()


class Cache:
    """File-based cache using diskcache with TTL support."""
    
    def __init__(self, cache_dir: Optional[str] = None, max_size_mb: Optional[int] = None):
        """Initialize the cache.
        
        Args:
            cache_dir: Directory for cache storage. Defaults to config or ~/.hacktivity/cache/
            max_size_mb: Maximum cache size in megabytes. Defaults to config value.
        """
        # Load config for defaults
        config = _get_config()
        
        if cache_dir is None:
            cache_dir = config.cache.directory
            if cache_dir is None:
                cache_dir = Path.home() / ".hacktivity" / "cache"
            else:
                cache_dir = Path(cache_dir)
        else:
            cache_dir = Path(cache_dir)
            
        if max_size_mb is None:
            max_size_mb = config.cache.max_size_mb
            
        # Ensure cache directory exists
        cache_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize diskcache with size limit
        self._cache = diskcache.Cache(
            str(cache_dir),
            size_limit=max_size_mb * 1024 * 1024  # Convert MB to bytes
        )
    
    def set(self, key: str, value: Any) -> None:
        """Store a value in the cache.
        
        Args:
            key: Cache key
            value: Value to store
        """
        try:
            # Store value with current timestamp
            cache_entry = {
                'value': value,
                'timestamp': time.time()
            }
            self._cache[key] = cache_entry
        except Exception as e:
            # Gracefully handle cache errors - don't crash the application
            logger.warning("Failed to cache data: %s", e)
    
    def get(self, key: str, max_age_hours: Optional[int] = None) -> Optional[Any]:
        """Retrieve a value from the cache if it's not expired.
        
        Args:
            key: Cache key
            max_age_hours: Maximum age in hours before considering expired (None uses config default)
            
        Returns:
            Cached value if found and not expired, None otherwise
        """
        try:
            if max_age_hours is None:
                max_age_hours = _get_config().cache.max_age_hours
                
            cache_entry = self._cache.get(key)
            if cache_entry is None:
                return None
                
            # Check if entry has expired
            current_time = time.time()
            entry_time = cache_entry.get('timestamp', 0)
            age_seconds = current_time - entry_time
            max_age_seconds = max_age_hours * 3600
            
            if age_seconds > max_age_seconds:
                # Entry has expired, remove it
                del self._cache[key]
                return None
                
            return cache_entry.get('value')
            
        except Exception as e:
            # Gracefully handle cache errors - don't crash the application
            logger.warning("Failed to retrieve cached data: %s", e)
            return None
    
    def clear(self) -> None:
        """Clear all cached data."""
        try:
            self._cache.clear()
        except Exception as e:
            logger.warning("Failed to clear cache: %s", e)
    
    def append_partial(self, key: str, batch: Any, page: int) -> None:
        """Append a batch of data to partial cache results.
        
        Args:
            key: Base cache key
            batch: Data batch to append
            page: Page number for this batch
        """
        try:
            partial_key = f"{key}:partial"
            
            # Get existing partial data or create new
            partial_data = self._cache.get(partial_key, {
                'pages': {},
                'timestamp': time.time()
            })
            
            # Add this page to the partial data
            partial_data['pages'][str(page)] = batch
            partial_data['timestamp'] = time.time()  # Update timestamp
            
            # Store updated partial data
            self._cache[partial_key] = partial_data
            
        except Exception as e:
            # Gracefully handle cache errors
            logger.warning("Failed to append partial cache data: %s", e)
    
    def get_partial(self, key: str, max_age_hours: Optional[int] = None) -> Optional[dict]:
        """Retrieve partial cache results.
        
        Args:
            key: Base cache key
            max_age_hours: Maximum age in hours before considering expired (None uses config default)
            
        Returns:
            Dictionary with 'pages' and metadata, or None if not found/expired
        """
        try:
            if max_age_hours is None:
                max_age_hours = _get_config().cache.max_age_hours
                
            partial_key = f"{key}:partial"
            partial_data = self._cache.get(partial_key)
            
            if partial_data is None:
                return None
                
            # Check if partial data has expired
            current_time = time.time()
            data_time = partial_data.get('timestamp', 0)
            age_seconds = current_time - data_time
            max_age_seconds = max_age_hours * 3600
            
            if age_seconds > max_age_seconds:
                # Partial data has expired, remove it
                del self._cache[partial_key]
                return None
                
            return partial_data
            
        except Exception as e:
            logger.warning("Failed to retrieve partial cache data: %s", e)
            return None
    
    def clear_partial(self, key: str) -> None:
        """Clear partial cache data for a specific key.
        
        Args:
            key: Base cache key
        """
        try:
            partial_key = f"{key}:partial"
            if partial_key in self._cache:
                del self._cache[partial_key]
        except Exception as e:
            logger.warning("Failed to clear partial cache: %s", e)

    def close(self) -> None:
        """Close the cache (cleanup)."""
        try:
            self._cache.close()
        except Exception as e:
            logger.warning("Failed to close cache: %s", e)


# Global cache instances
_cache_instance = None
_multi_cache_instance = None


def get_cache() -> Cache:
    """Get the global cache instance (legacy interface)."""
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = Cache()
    return _cache_instance


def get_multi_cache() -> MultiLevelCache:
    """Get the global multi-level cache instance."""
    global _multi_cache_instance
    if _multi_cache_instance is None:
        _multi_cache_instance = MultiLevelCache()
    return _multi_cache_instance


def get(key: str, max_age_hours: Optional[int] = None) -> Optional[Any]:
    """Retrieve a value from the global cache with multi-level routing.
    
    Args:
        key: Cache key
        max_age_hours: Maximum age in hours before considering expired (None uses level default)
        
    Returns:
        Cached value if found and not expired, None otherwise
    """
    return get_multi_cache().get(key, max_age_hours)


def set(key: str, value: Any) -> None:
    """Store a value in the global cache with multi-level routing.
    
    Args:
        key: Cache key
        value: Value to store
    """
    get_multi_cache().set(key, value)


def append_partial(key: str, batch: Any, page: int) -> None:
    """Append a batch of data to partial cache results.
    
    Args:
        key: Base cache key
        batch: Data batch to append
        page: Page number for this batch
    """
    # Use legacy cache for partial operations (maintains compatibility)
    get_cache().append_partial(key, batch, page)


def get_partial(key: str, max_age_hours: Optional[int] = None) -> Optional[dict]:
    """Retrieve partial cache results.
    
    Args:
        key: Base cache key
        max_age_hours: Maximum age in hours before considering expired (None uses config default)
        
    Returns:
        Dictionary with 'pages' and metadata, or None if not found/expired
    """
    # Use legacy cache for partial operations (maintains compatibility)
    return get_cache().get_partial(key, max_age_hours)


def clear_partial(key: str) -> None:
    """Clear partial cache data for a specific key.
    
    Args:
        key: Base cache key
    """
    # Use legacy cache for partial operations (maintains compatibility)
    get_cache().clear_partial(key)