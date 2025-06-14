"""Unit tests for config module."""

import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open
import pytest

# Mock dependencies before importing
with patch.dict('sys.modules', {
    'tomllib': MagicMock(load=lambda f: {}),
    'tomli': MagicMock(load=lambda f: {})
}):
    from hacktivity.core import config
    from hacktivity.core.config import (
        CacheConfig, GitHubConfig, AIConfig, AppConfig, Config,
        get_config_path, load_config, save_default_config, get_config, reload_config
    )


class TestConfigModels:
    """Test Pydantic configuration models."""
    
    def test_cache_config_defaults(self):
        """Test CacheConfig with defaults."""
        cfg = CacheConfig()
        assert cfg.max_age_hours == 24
        assert cfg.max_size_mb == 100
        assert cfg.directory is None
        
    def test_cache_config_validation(self):
        """Test CacheConfig validation."""
        # Valid values
        cfg = CacheConfig(max_age_hours=48, max_size_mb=500)
        assert cfg.max_age_hours == 48
        assert cfg.max_size_mb == 500
        
        # Invalid values should raise
        with pytest.raises(Exception):  # Pydantic ValidationError
            CacheConfig(max_age_hours=200)  # > 168
            
        with pytest.raises(Exception):
            CacheConfig(max_size_mb=5)  # < 10
            
    def test_github_config_defaults(self):
        """Test GitHubConfig with defaults."""
        cfg = GitHubConfig()
        assert cfg.per_page == 100
        assert cfg.timeout_seconds == 60
        assert cfg.max_pages == 10
        assert cfg.retry_attempts == 3
        assert cfg.retry_min_wait == 4
        assert cfg.retry_max_wait == 10
        
    def test_github_config_validation(self):
        """Test GitHubConfig validation."""
        # Valid values
        cfg = GitHubConfig(per_page=50, timeout_seconds=120, max_pages=5)
        assert cfg.per_page == 50
        assert cfg.timeout_seconds == 120
        assert cfg.max_pages == 5
        
        # Invalid values should raise
        with pytest.raises(Exception):
            GitHubConfig(per_page=200)  # > 100
            
        with pytest.raises(Exception):
            GitHubConfig(timeout_seconds=5)  # < 10
            
    def test_ai_config_defaults(self):
        """Test AIConfig with defaults."""
        cfg = AIConfig()
        assert cfg.model_name == "gemini-1.5-flash"
        
    def test_ai_config_custom(self):
        """Test AIConfig with custom model."""
        cfg = AIConfig(model_name="custom-model")
        assert cfg.model_name == "custom-model"
        
    def test_app_config_defaults(self):
        """Test AppConfig with defaults."""
        cfg = AppConfig()
        assert cfg.log_level == "INFO"
        assert cfg.default_prompt_type == "standup"
        
    def test_app_config_custom(self):
        """Test AppConfig with custom values."""
        cfg = AppConfig(log_level="DEBUG", default_prompt_type="retro")
        assert cfg.log_level == "DEBUG"
        assert cfg.default_prompt_type == "retro"
        
    def test_config_root_defaults(self):
        """Test root Config with all defaults."""
        cfg = Config()
        assert isinstance(cfg.cache, CacheConfig)
        assert isinstance(cfg.github, GitHubConfig)
        assert isinstance(cfg.ai, AIConfig)
        assert isinstance(cfg.app, AppConfig)
        
    def test_config_root_nested(self):
        """Test root Config with nested values."""
        cfg = Config(
            cache={'max_age_hours': 48},
            github={'per_page': 50},
            ai={'model_name': 'gpt-4'},
            app={'log_level': 'DEBUG'}
        )
        assert cfg.cache.max_age_hours == 48
        assert cfg.github.per_page == 50
        assert cfg.ai.model_name == 'gpt-4'
        assert cfg.app.log_level == 'DEBUG'


class TestConfigFunctions:
    """Test configuration loading functions."""
    
    def test_get_config_path(self):
        """Test config path generation."""
        path = get_config_path()
        assert isinstance(path, Path)
        assert str(path).endswith('.hacktivity/config.toml')
        
    @patch('hacktivity.core.config.Path.exists')
    @patch('hacktivity.core.config.logger')
    def test_load_config_no_file(self, mock_logger, mock_exists):
        """Test loading config when file doesn't exist."""
        mock_exists.return_value = False
        
        cfg = load_config()
        
        assert isinstance(cfg, Config)
        # Should use all defaults
        assert cfg.cache.max_age_hours == 24
        mock_logger.info.assert_called_with(
            "No config file found at %s, using defaults",
            get_config_path()
        )
        
    @patch('hacktivity.core.config.Path.exists')
    @patch('builtins.open', new_callable=mock_open, read_data=b'[cache]\nmax_age_hours = 48\n')
    @patch('hacktivity.core.config.tomllib')
    def test_load_config_from_file(self, mock_toml, mock_file, mock_exists):
        """Test loading config from file."""
        mock_exists.return_value = True
        mock_toml.load.return_value = {'cache': {'max_age_hours': 48}}
        
        cfg = load_config()
        
        assert cfg.cache.max_age_hours == 48
        # Other values should be defaults
        assert cfg.github.per_page == 100
        
    @patch('hacktivity.core.config.Path.exists')
    @patch('builtins.open', side_effect=Exception("File error"))
    @patch('hacktivity.core.config.logger')
    def test_load_config_file_error(self, mock_logger, mock_file, mock_exists):
        """Test loading config handles file errors."""
        mock_exists.return_value = True
        
        cfg = load_config()
        
        assert isinstance(cfg, Config)
        # Should fall back to defaults
        assert cfg.cache.max_age_hours == 24
        mock_logger.warning.assert_called()
        
    @patch('hacktivity.core.config.Path.mkdir')
    @patch('hacktivity.core.config.Path.exists')
    @patch('hacktivity.core.config.Path.write_text')
    def test_save_default_config_new(self, mock_write, mock_exists, mock_mkdir):
        """Test saving default config when none exists."""
        mock_exists.return_value = False
        
        save_default_config()
        
        mock_mkdir.assert_called_once_with(parents=True, exist_ok=True)
        mock_write.assert_called_once()
        written_content = mock_write.call_args[0][0]
        assert '# Hacktivity Configuration File' in written_content
        assert 'max_age_hours = 24' in written_content
        
    @patch('hacktivity.core.config.Path.exists')
    @patch('hacktivity.core.config.logger')
    def test_save_default_config_exists(self, mock_logger, mock_exists):
        """Test saving default config when file already exists."""
        mock_exists.return_value = True
        
        save_default_config()
        
        mock_logger.info.assert_called_with(
            "Config file already exists at %s",
            get_config_path()
        )
        
    @patch('hacktivity.core.config.Path.mkdir')
    @patch('hacktivity.core.config.Path.exists')
    @patch('hacktivity.core.config.Path.write_text', side_effect=Exception("Write error"))
    @patch('hacktivity.core.config.logger')
    def test_save_default_config_error(self, mock_logger, mock_write, mock_exists, mock_mkdir):
        """Test saving default config handles errors."""
        mock_exists.return_value = False
        
        save_default_config()
        
        mock_logger.error.assert_called_with(
            "Failed to create default config file: %s",
            "Write error"
        )
        
    @patch('hacktivity.core.config.load_config')
    def test_get_config_singleton(self, mock_load):
        """Test get_config returns singleton."""
        # Reset global instance
        config._config_instance = None
        mock_load.return_value = Config(cache={'max_age_hours': 72})
        
        cfg1 = get_config()
        cfg2 = get_config()
        
        # Should only load once
        mock_load.assert_called_once()
        assert cfg1 is cfg2
        assert cfg1.cache.max_age_hours == 72
        
    @patch('hacktivity.core.config.load_config')
    def test_reload_config(self, mock_load):
        """Test reload_config forces reload."""
        # Set up initial config
        config._config_instance = Config(cache={'max_age_hours': 24})
        
        # Mock new config
        mock_load.return_value = Config(cache={'max_age_hours': 48})
        
        new_cfg = reload_config()
        
        mock_load.assert_called_once()
        assert new_cfg.cache.max_age_hours == 48
        assert config._config_instance is new_cfg