"""Unit tests for multi-level caching system."""

import tempfile
import time
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from hacktivity.core.cache import MultiLevelCache, CacheLevel


class TestCacheLevel:
    """Test the CacheLevel class."""
    
    def test_cache_level_creation(self):
        """Test creating a CacheLevel."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir) / "test_cache"
            level = CacheLevel(
                name="test",
                cache_dir=cache_dir,
                default_ttl_hours=24,
                max_size_mb=50
            )
            
            assert level.name == "test"
            assert level.default_ttl_hours == 24
            assert cache_dir.exists()
    
    def test_cache_level_set_and_get(self):
        """Test basic set and get operations."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir) / "test_cache"
            level = CacheLevel(
                name="test",
                cache_dir=cache_dir,
                default_ttl_hours=1,
                max_size_mb=10
            )
            
            # Set and get a value
            level.set("test_key", {"data": "test_value"})
            result = level.get("test_key")
            
            assert result == {"data": "test_value"}
    
    def test_cache_level_ttl_expiration(self):
        """Test TTL expiration."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir) / "test_cache"
            level = CacheLevel(
                name="test",
                cache_dir=cache_dir,
                default_ttl_hours=1,
                max_size_mb=10
            )
            
            # Set a value
            level.set("test_key", {"data": "test_value"})
            
            # Should be retrievable immediately
            result = level.get("test_key")
            assert result == {"data": "test_value"}
            
            # Should be expired when checked with 0 TTL
            result = level.get("test_key", max_age_hours=0)
            assert result is None
    
    def test_cache_level_custom_ttl(self):
        """Test custom TTL override."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir) / "test_cache"
            level = CacheLevel(
                name="test",
                cache_dir=cache_dir,
                default_ttl_hours=24,
                max_size_mb=10
            )
            
            # Set a value
            level.set("test_key", {"data": "test_value"})
            
            # Should be retrievable with default TTL
            result = level.get("test_key")
            assert result == {"data": "test_value"}
            
            # Should be retrievable with longer custom TTL
            result = level.get("test_key", max_age_hours=48)
            assert result == {"data": "test_value"}


class TestMultiLevelCache:
    """Test the MultiLevelCache class."""
    
    def setup_method(self):
        """Set up test environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.cache_dir = Path(self.temp_dir) / "cache"
        self.multi_cache = MultiLevelCache(base_cache_dir=self.cache_dir)
    
    def teardown_method(self):
        """Clean up test environment."""
        if hasattr(self, 'multi_cache'):
            self.multi_cache.close()
    
    def test_multi_cache_initialization(self):
        """Test multi-level cache initialization."""
        # Check that cache levels were created
        assert "repos" in self.multi_cache._levels
        assert "commits" in self.multi_cache._levels
        assert "summaries" in self.multi_cache._levels
        assert "chunks" in self.multi_cache._levels
        
        # Check cache directories exist
        assert (self.cache_dir / "repos").exists()
        assert (self.cache_dir / "commits").exists()
        assert (self.cache_dir / "summaries").exists()
        assert (self.cache_dir / "chunks").exists()
    
    def test_auto_routing_by_key_prefix(self):
        """Test automatic routing based on key prefixes."""
        # Repository data
        self.multi_cache.set("repos:user:testuser", {"repos": ["repo1", "repo2"]})
        result = self.multi_cache.get("repos:user:testuser")
        assert result == {"repos": ["repo1", "repo2"]}
        
        # Commit data
        self.multi_cache.set("commits:owner/repo:2024-01-01:2024-01-31:all", [{"sha": "abc123"}])
        result = self.multi_cache.get("commits:owner/repo:2024-01-01:2024-01-31:all")
        assert result == [{"sha": "abc123"}]
        
        # Summary data  
        self.multi_cache.set("summary:user:2024-01-01:2024-01-31", "Daily summary")
        result = self.multi_cache.get("summary:user:2024-01-01:2024-01-31")
        assert result == "Daily summary"
        
        # Chunk state data
        self.multi_cache.set("chunk_state:owner/repo:2024-01-01:2024-01-31:all", {"chunks": {}})
        result = self.multi_cache.get("chunk_state:owner/repo:2024-01-01:2024-01-31:all")
        assert result == {"chunks": {}}
    
    def test_fallback_to_default_cache(self):
        """Test fallback to default cache for unknown prefixes."""
        # Unknown prefix should go to default cache level
        self.multi_cache.set("unknown:test:key", {"data": "value"})
        result = self.multi_cache.get("unknown:test:key")
        assert result == {"data": "value"}
    
    def test_different_ttls_per_level(self):
        """Test that different cache levels have different default TTLs."""
        repos_level = self.multi_cache._levels["repos"]
        commits_level = self.multi_cache._levels["commits"]
        summaries_level = self.multi_cache._levels["summaries"]
        
        # Check default TTLs
        assert repos_level.default_ttl_hours == 7 * 24  # 7 days
        assert commits_level.default_ttl_hours == 365 * 24  # 365 days  
        assert summaries_level.default_ttl_hours == 30 * 24  # 30 days
    
    def test_size_limits_per_level(self):
        """Test that different cache levels have appropriate size limits."""
        repos_level = self.multi_cache._levels["repos"]
        commits_level = self.multi_cache._levels["commits"]
        summaries_level = self.multi_cache._levels["summaries"]
        chunks_level = self.multi_cache._levels["chunks"]
        
        # Should have reasonable size allocations (total 100MB default)
        assert repos_level.max_size_mb == 10
        assert commits_level.max_size_mb == 50
        assert summaries_level.max_size_mb == 20
        assert chunks_level.max_size_mb == 20
    
    def test_cache_info_aggregation(self):
        """Test cache information aggregation across levels."""
        # Add some data to different levels
        self.multi_cache.set("repos:user:test", {"repos": []})
        self.multi_cache.set("commits:repo:test", [])
        self.multi_cache.set("summary:test", "summary")
        
        info = self.multi_cache.get_cache_info()
        
        assert "total_levels" in info
        assert info["total_levels"] == 4
        assert "levels" in info
        assert len(info["levels"]) == 4
        
        # Should include info for each level
        level_names = [level["name"] for level in info["levels"]]
        assert "repos" in level_names
        assert "commits" in level_names
        assert "summaries" in level_names
        assert "chunks" in level_names
    
    def test_clear_specific_level(self):
        """Test clearing a specific cache level."""
        # Add data to multiple levels
        self.multi_cache.set("repos:test", {"data": "repos"})
        self.multi_cache.set("commits:test", {"data": "commits"})
        
        # Clear only repos level
        self.multi_cache.clear_level("repos")
        
        # Repos data should be gone
        assert self.multi_cache.get("repos:test") is None
        
        # Commits data should still exist
        assert self.multi_cache.get("commits:test") == {"data": "commits"}
    
    def test_clear_all_levels(self):
        """Test clearing all cache levels."""
        # Add data to multiple levels
        self.multi_cache.set("repos:test", {"data": "repos"})
        self.multi_cache.set("commits:test", {"data": "commits"})
        self.multi_cache.set("summary:test", "summary")
        
        # Clear all
        self.multi_cache.clear()
        
        # All data should be gone
        assert self.multi_cache.get("repos:test") is None
        assert self.multi_cache.get("commits:test") is None
        assert self.multi_cache.get("summary:test") is None
    
    def test_backward_compatibility_with_existing_api(self):
        """Test that existing cache API still works."""
        # These should work exactly like the old cache
        self.multi_cache.set("old_style_key", {"data": "value"})
        result = self.multi_cache.get("old_style_key")
        assert result == {"data": "value"}
        
        # Should respect custom TTL
        result = self.multi_cache.get("old_style_key", max_age_hours=1)
        assert result == {"data": "value"}
    
    def test_cache_warming_interface(self):
        """Test cache warming interface."""
        # Should have methods for warming different cache levels
        assert hasattr(self.multi_cache, 'warm_repos_cache')
        assert hasattr(self.multi_cache, 'warm_commits_cache')
        
        # Basic warming should work without errors
        try:
            repos_data = [{"full_name": "test/repo", "name": "repo"}]
            self.multi_cache.warm_repos_cache("testuser", repos_data)
            
            commits_data = [{"sha": "abc123", "message": "test"}]
            self.multi_cache.warm_commits_cache("test/repo", "2024-01-01", "2024-01-31", commits_data)
        except Exception as e:
            pytest.fail(f"Cache warming should not raise exceptions: {e}")


class TestCacheIntegration:
    """Test integration with existing cache usage patterns."""
    
    def setup_method(self):
        """Set up test environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.cache_dir = Path(self.temp_dir) / "cache"
    
    def test_integration_with_repos_module_patterns(self):
        """Test integration with repository module cache patterns."""
        multi_cache = MultiLevelCache(base_cache_dir=self.cache_dir)
        
        # Test repository cache key pattern: repos:user:org_filter
        cache_key = "repos:testuser:all"
        repos_data = [
            {"full_name": "user/repo1", "name": "repo1"},
            {"full_name": "user/repo2", "name": "repo2"}
        ]
        
        # Should automatically route to repos cache
        multi_cache.set(cache_key, repos_data)
        result = multi_cache.get(cache_key, max_age_hours=168)  # 7 days as in repos.py
        
        assert result == repos_data
        multi_cache.close()
    
    def test_integration_with_commits_module_patterns(self):
        """Test integration with commits module cache patterns."""
        multi_cache = MultiLevelCache(base_cache_dir=self.cache_dir)
        
        # Test commit cache key pattern: commits:repo:since:until:author
        cache_key = "commits:owner/repo:2024-01-01:2024-01-31:all"
        commits_data = [
            {"sha": "abc123", "message": "First commit"},
            {"sha": "def456", "message": "Second commit"}
        ]
        
        # Should automatically route to commits cache
        multi_cache.set(cache_key, commits_data)
        result = multi_cache.get(cache_key, max_age_hours=8760)  # 365 days as in commits.py
        
        assert result == commits_data
        multi_cache.close()
    
    def test_integration_with_chunk_state_patterns(self):
        """Test integration with chunk state cache patterns."""
        multi_cache = MultiLevelCache(base_cache_dir=self.cache_dir)
        
        # Test chunk state key pattern: chunk_state:repo:since:until:author
        cache_key = "chunk_state:owner/repo:2024-01-01:2024-01-31:all"
        chunk_data = {
            'chunks': {'0': {'status': 'completed'}},
            'chunk_results': {'0': []},
            'last_updated': '2024-01-01T10:00:00'
        }
        
        # Should automatically route to chunks cache
        multi_cache.set(cache_key, chunk_data)
        result = multi_cache.get(cache_key, max_age_hours=720)  # 30 days as in chunking.py
        
        assert result == chunk_data
        multi_cache.close()


class TestPerformanceAndScaling:
    """Test performance and scaling characteristics."""
    
    def test_cache_level_isolation(self):
        """Test that cache levels are isolated from each other."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir) / "cache"
            multi_cache = MultiLevelCache(base_cache_dir=cache_dir)
            
            # Fill up repos cache with data
            for i in range(100):
                multi_cache.set(f"repos:user{i}:all", {"repos": [f"repo{j}" for j in range(10)]})
            
            # Fill up commits cache with data  
            for i in range(50):
                multi_cache.set(f"commits:repo{i}:2024-01-01:2024-01-31:all", 
                               [{"sha": f"commit{j}"} for j in range(20)])
            
            # Each cache should operate independently
            repos_info = multi_cache._levels["repos"].get_stats()
            commits_info = multi_cache._levels["commits"].get_stats()
            
            # Should have data in both caches
            assert repos_info["entries"] > 0
            assert commits_info["entries"] > 0
            
            # Clearing one shouldn't affect the other
            multi_cache.clear_level("repos")
            
            repos_info_after = multi_cache._levels["repos"].get_stats()
            commits_info_after = multi_cache._levels["commits"].get_stats()
            
            assert repos_info_after["entries"] == 0
            assert commits_info_after["entries"] == commits_info["entries"]
            
            multi_cache.close()