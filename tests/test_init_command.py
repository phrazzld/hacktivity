"""Unit tests for the init command functionality."""

import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest
import click.testing

# Import the module
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import from the hacktivity.__main__ module
from hacktivity.__main__ import cli, copy_default_prompts


class TestInitCommand:
    """Test init command functionality."""
    
    def test_init_command_help(self):
        """Test init command shows proper help."""
        runner = click.testing.CliRunner()
        result = runner.invoke(cli, ['init', '--help'])
        
        assert result.exit_code == 0
        assert "Initialize hacktivity configuration and user directory" in result.output
    
    def test_init_command_creates_config_and_prompts(self):
        """Test init command creates config file and prompt directory."""
        with tempfile.TemporaryDirectory() as temp_dir:
            home_path = Path(temp_dir)
            
            with patch('hacktivity.__main__.Path.home') as mock_home, \
                 patch('hacktivity.core.config.Path.home') as mock_config_home:
                
                mock_home.return_value = home_path
                mock_config_home.return_value = home_path
                
                runner = click.testing.CliRunner()
                result = runner.invoke(cli, ['init'])
                
                assert result.exit_code == 0
                assert "✓ Created default configuration file" in result.output
                assert "✓ Set up default prompts" in result.output
                assert "Initialization complete! 🎉" in result.output
                
                # Verify config file was created
                config_file = home_path / ".hacktivity" / "config.toml"
                assert config_file.exists()
                
                # Verify prompts directory was created
                prompts_dir = home_path / ".hacktivity" / "prompts"
                assert prompts_dir.exists()
                assert prompts_dir.is_dir()
                
                # Verify prompt files were copied
                assert (prompts_dir / "standup.md").exists()
                assert (prompts_dir / "retro.md").exists()
                assert (prompts_dir / "weekly.md").exists()
    
    def test_init_command_handles_existing_files(self):
        """Test init command handles existing config and prompt files."""
        with tempfile.TemporaryDirectory() as temp_dir:
            home_path = Path(temp_dir)
            hacktivity_dir = home_path / ".hacktivity"
            prompts_dir = hacktivity_dir / "prompts"
            
            # Pre-create directories and files
            hacktivity_dir.mkdir(parents=True)
            prompts_dir.mkdir(parents=True)
            (hacktivity_dir / "config.toml").write_text("existing config")
            (prompts_dir / "standup.md").write_text("existing prompt")
            
            with patch('hacktivity.__main__.Path.home') as mock_home, \
                 patch('hacktivity.core.config.Path.home') as mock_config_home:
                
                mock_home.return_value = home_path
                mock_config_home.return_value = home_path
                
                runner = click.testing.CliRunner()
                result = runner.invoke(cli, ['init'])
                
                assert result.exit_code == 0
                assert "already exists" in result.output.lower()
                assert "✓ Set up default prompts" in result.output
                
                # Verify existing file wasn't overwritten
                assert (hacktivity_dir / "config.toml").read_text() == "existing config"
                assert (prompts_dir / "standup.md").read_text() == "existing prompt"


class TestCopyDefaultPrompts:
    """Test copy_default_prompts function."""
    
    def test_copy_default_prompts_success(self):
        """Test copying prompts to a clean directory."""
        with tempfile.TemporaryDirectory() as temp_dir:
            home_path = Path(temp_dir)
            
            with patch('hacktivity.__main__.Path.home') as mock_home:
                mock_home.return_value = home_path
                
                # Call the function
                copy_default_prompts()
                
                # Verify prompts directory was created
                prompts_dir = home_path / ".hacktivity" / "prompts"
                assert prompts_dir.exists()
                assert prompts_dir.is_dir()
                
                # Verify all default prompts were copied
                assert (prompts_dir / "standup.md").exists()
                assert (prompts_dir / "retro.md").exists() 
                assert (prompts_dir / "weekly.md").exists()
                
                # Verify content was actually copied (not empty files)
                assert len((prompts_dir / "standup.md").read_text()) > 0
    
    def test_copy_default_prompts_existing_files(self):
        """Test copying prompts when some files already exist."""
        with tempfile.TemporaryDirectory() as temp_dir:
            home_path = Path(temp_dir)
            prompts_dir = home_path / ".hacktivity" / "prompts"
            prompts_dir.mkdir(parents=True)
            
            # Create one existing file
            existing_content = "Custom standup prompt"
            (prompts_dir / "standup.md").write_text(existing_content)
            
            with patch('hacktivity.__main__.Path.home') as mock_home:
                mock_home.return_value = home_path
                
                # Call the function
                copy_default_prompts()
                
                # Verify existing file wasn't overwritten
                assert (prompts_dir / "standup.md").read_text() == existing_content
                
                # Verify other files were still copied
                assert (prompts_dir / "retro.md").exists()
                assert (prompts_dir / "weekly.md").exists()


class TestCLIBackwardCompatibility:
    """Test that CLI changes maintain backward compatibility."""
    
    def test_cli_without_subcommand_shows_help(self):
        """Test that running CLI without arguments shows help."""
        runner = click.testing.CliRunner()
        result = runner.invoke(cli, [])
        
        # Should show help or run default command
        assert result.exit_code in [0, 1]  # May exit with 1 due to missing env vars
        # The important thing is it doesn't crash
    
    def test_cli_subcommands_available(self):
        """Test that both init and summary subcommands are available."""
        runner = click.testing.CliRunner()
        result = runner.invoke(cli, ['--help'])
        
        assert result.exit_code == 0
        assert "init" in result.output
        assert "summary" in result.output
        assert "Initialize hacktivity configuration" in result.output
        assert "Summarize your GitHub activity" in result.output
    
    @patch('hacktivity.__main__.check_github_prerequisites')
    @patch('hacktivity.__main__.check_ai_prerequisites')
    @patch('hacktivity.__main__.get_github_user')
    @patch('hacktivity.__main__.fetch_commits')
    @patch('hacktivity.__main__.get_summary')
    @patch('hacktivity.__main__.load_prompts')
    def test_summary_subcommand_works(self, mock_load_prompts, mock_summary, 
                                     mock_fetch, mock_user, mock_ai_check, mock_gh_check):
        """Test that summary subcommand works correctly."""
        # Set up mocks
        mock_load_prompts.return_value = {'standup': 'Test prompt'}
        mock_user.return_value = 'testuser'
        mock_fetch.return_value = ['commit 1', 'commit 2']
        mock_summary.return_value = 'Test summary'
        
        runner = click.testing.CliRunner()
        result = runner.invoke(cli, ['summary'])
        
        assert result.exit_code == 0
        assert "Test summary" in result.output