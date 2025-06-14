"""Unit tests for cache module."""

import time
from unittest.mock import MagicMock, patch, PropertyMock
import pytest

# Mock diskcache before importing cache module
with patch.dict('sys.modules', {'diskcache': MagicMock()}):
    from hacktivity.core import cache


class TestCache:
    """Test the Cache class."""
    
    @patch('hacktivity.core.cache._get_config')
    @patch('diskcache.Cache')
    def test_init_default_config(self, mock_diskcache, mock_get_config):
        """Test cache initialization with default config."""
        mock_config = MagicMock()
        mock_config.cache.directory = None
        mock_config.cache.max_size_mb = 100
        mock_get_config.return_value = mock_config
        
        cache_instance = cache.Cache()
        
        mock_diskcache.assert_called_once()
        call_args = mock_diskcache.call_args[0]
        assert '.hacktivity/cache' in call_args[0]
        
    @patch('hacktivity.core.cache._get_config')
    @patch('diskcache.Cache')
    def test_init_custom_directory(self, mock_diskcache, mock_get_config):
        """Test cache initialization with custom directory."""
        mock_config = MagicMock()
        mock_config.cache.directory = '/custom/cache'
        mock_config.cache.max_size_mb = 200
        mock_get_config.return_value = mock_config
        
        cache_instance = cache.Cache()
        
        mock_diskcache.assert_called_once()
        call_args = mock_diskcache.call_args[0]
        assert call_args[0] == '/custom/cache'
        
    def test_set(self):
        """Test setting a value in cache."""
        cache_instance = cache.Cache()
        cache_instance._cache = {}
        
        cache_instance.set('test_key', 'test_value')
        
        assert 'test_key' in cache_instance._cache
        stored = cache_instance._cache['test_key']
        assert stored['value'] == 'test_value'
        assert 'timestamp' in stored
        
    def test_set_error_handling(self, caplog):
        """Test set handles errors gracefully."""
        cache_instance = cache.Cache()
        cache_instance._cache = MagicMock()
        cache_instance._cache.__setitem__.side_effect = Exception("Disk error")
        
        cache_instance.set('test_key', 'test_value')
        
        assert "Failed to cache data" in caplog.text
        
    @patch('hacktivity.core.cache._get_config')
    def test_get_not_expired(self, mock_get_config):
        """Test getting a non-expired value."""
        mock_config = MagicMock()
        mock_config.cache.max_age_hours = 24
        mock_get_config.return_value = mock_config
        
        cache_instance = cache.Cache()
        cache_instance._cache = {
            'test_key': {
                'value': 'test_value',
                'timestamp': time.time() - 3600  # 1 hour ago
            }
        }
        
        result = cache_instance.get('test_key')
        assert result == 'test_value'
        
    @patch('hacktivity.core.cache._get_config')
    def test_get_expired(self, mock_get_config):
        """Test getting an expired value returns None."""
        mock_config = MagicMock()
        mock_config.cache.max_age_hours = 1
        mock_get_config.return_value = mock_config
        
        cache_instance = cache.Cache()
        cache_instance._cache = MagicMock()
        cache_instance._cache.get.return_value = {
            'value': 'test_value',
            'timestamp': time.time() - 7200  # 2 hours ago
        }
        
        result = cache_instance.get('test_key', max_age_hours=1)
        assert result is None
        
    def test_get_nonexistent(self):
        """Test getting a non-existent key returns None."""
        cache_instance = cache.Cache()
        cache_instance._cache = MagicMock()
        cache_instance._cache.get.return_value = None
        
        result = cache_instance.get('nonexistent')
        assert result is None
        
    def test_get_error_handling(self, caplog):
        """Test get handles errors gracefully."""
        cache_instance = cache.Cache()
        cache_instance._cache = MagicMock()
        cache_instance._cache.get.side_effect = Exception("Disk error")
        
        result = cache_instance.get('test_key')
        
        assert result is None
        assert "Failed to retrieve cached data" in caplog.text
        
    def test_clear(self):
        """Test clearing the cache."""
        cache_instance = cache.Cache()
        cache_instance._cache = MagicMock()
        
        cache_instance.clear()
        
        cache_instance._cache.clear.assert_called_once()
        
    def test_clear_error_handling(self, caplog):
        """Test clear handles errors gracefully."""
        cache_instance = cache.Cache()
        cache_instance._cache = MagicMock()
        cache_instance._cache.clear.side_effect = Exception("Disk error")
        
        cache_instance.clear()
        
        assert "Failed to clear cache" in caplog.text
        
    def test_append_partial(self):
        """Test appending partial cache data."""
        cache_instance = cache.Cache()
        cache_instance._cache = MagicMock()
        cache_instance._cache.get.return_value = {'pages': {}, 'timestamp': time.time()}
        
        cache_instance.append_partial('test_key', ['data1', 'data2'], 1)
        
        cache_instance._cache.__setitem__.assert_called_once()
        key, value = cache_instance._cache.__setitem__.call_args[0]
        assert key == 'test_key:partial'
        assert value['pages']['1'] == ['data1', 'data2']
        
    def test_append_partial_error_handling(self, caplog):
        """Test append_partial handles errors gracefully."""
        cache_instance = cache.Cache()
        cache_instance._cache = MagicMock()
        cache_instance._cache.get.side_effect = Exception("Disk error")
        
        cache_instance.append_partial('test_key', ['data'], 1)
        
        assert "Failed to append partial cache data" in caplog.text
        
    @patch('hacktivity.core.cache._get_config')
    def test_get_partial_not_expired(self, mock_get_config):
        """Test getting non-expired partial data."""
        mock_config = MagicMock()
        mock_config.cache.max_age_hours = 24
        mock_get_config.return_value = mock_config
        
        cache_instance = cache.Cache()
        cache_instance._cache = MagicMock()
        cache_instance._cache.get.return_value = {
            'pages': {'1': ['data']},
            'timestamp': time.time() - 3600  # 1 hour ago
        }
        
        result = cache_instance.get_partial('test_key')
        assert result is not None
        assert result['pages']['1'] == ['data']
        
    @patch('hacktivity.core.cache._get_config')
    def test_get_partial_expired(self, mock_get_config):
        """Test getting expired partial data returns None."""
        mock_config = MagicMock()
        mock_config.cache.max_age_hours = 1
        mock_get_config.return_value = mock_config
        
        cache_instance = cache.Cache()
        cache_instance._cache = MagicMock()
        cache_instance._cache.get.return_value = {
            'pages': {'1': ['data']},
            'timestamp': time.time() - 7200  # 2 hours ago
        }
        cache_instance._cache.__contains__.return_value = True
        
        result = cache_instance.get_partial('test_key', max_age_hours=1)
        assert result is None
        
    def test_clear_partial(self):
        """Test clearing partial cache data."""
        cache_instance = cache.Cache()
        cache_instance._cache = MagicMock()
        cache_instance._cache.__contains__.return_value = True
        
        cache_instance.clear_partial('test_key')
        
        cache_instance._cache.__delitem__.assert_called_once_with('test_key:partial')
        
    def test_clear_partial_nonexistent(self):
        """Test clearing non-existent partial cache."""
        cache_instance = cache.Cache()
        cache_instance._cache = MagicMock()
        cache_instance._cache.__contains__.return_value = False
        
        cache_instance.clear_partial('test_key')
        
        cache_instance._cache.__delitem__.assert_not_called()
        
    def test_close(self):
        """Test closing the cache."""
        cache_instance = cache.Cache()
        cache_instance._cache = MagicMock()
        
        cache_instance.close()
        
        cache_instance._cache.close.assert_called_once()


class TestCacheGlobals:
    """Test global cache functions."""
    
    @patch('hacktivity.core.cache.get_cache')
    def test_get_global(self, mock_get_cache):
        """Test global get function."""
        mock_cache = MagicMock()
        mock_cache.get.return_value = 'test_value'
        mock_get_cache.return_value = mock_cache
        
        result = cache.get('test_key', max_age_hours=12)
        
        mock_cache.get.assert_called_once_with('test_key', 12)
        assert result == 'test_value'
        
    @patch('hacktivity.core.cache.get_cache')
    def test_set_global(self, mock_get_cache):
        """Test global set function."""
        mock_cache = MagicMock()
        mock_get_cache.return_value = mock_cache
        
        cache.set('test_key', 'test_value')
        
        mock_cache.set.assert_called_once_with('test_key', 'test_value')
        
    @patch('hacktivity.core.cache.get_cache')
    def test_append_partial_global(self, mock_get_cache):
        """Test global append_partial function."""
        mock_cache = MagicMock()
        mock_get_cache.return_value = mock_cache
        
        cache.append_partial('test_key', ['data'], 1)
        
        mock_cache.append_partial.assert_called_once_with('test_key', ['data'], 1)
        
    @patch('hacktivity.core.cache.get_cache')
    def test_get_partial_global(self, mock_get_cache):
        """Test global get_partial function."""
        mock_cache = MagicMock()
        mock_cache.get_partial.return_value = {'pages': {}}
        mock_get_cache.return_value = mock_cache
        
        result = cache.get_partial('test_key', max_age_hours=12)
        
        mock_cache.get_partial.assert_called_once_with('test_key', 12)
        assert result == {'pages': {}}
        
    @patch('hacktivity.core.cache.get_cache')
    def test_clear_partial_global(self, mock_get_cache):
        """Test global clear_partial function."""
        mock_cache = MagicMock()
        mock_get_cache.return_value = mock_cache
        
        cache.clear_partial('test_key')
        
        mock_cache.clear_partial.assert_called_once_with('test_key')
        
    @patch('hacktivity.core.cache.Cache')
    def test_get_cache_singleton(self, mock_cache_class):
        """Test get_cache returns singleton."""
        # Reset global instance
        cache._cache_instance = None
        
        instance1 = cache.get_cache()
        instance2 = cache.get_cache()
        
        # Should only create one instance
        mock_cache_class.assert_called_once()
        assert instance1 is instance2