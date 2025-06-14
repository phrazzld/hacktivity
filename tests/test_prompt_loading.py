"""Unit tests for customizable prompt loading functionality."""

import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest
import click.testing

# Import the module
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from hacktivity.__main__ import load_prompts, cli


class TestPromptLoading:
    """Test prompt loading functionality."""
    
    def test_load_default_prompts(self):
        """Test loading default prompts from package directory."""
        with patch('hacktivity.__main__.Path.home') as mock_home:
            # Set up a temp home directory without user prompts
            temp_home = Path(tempfile.mkdtemp())
            mock_home.return_value = temp_home
            
            prompts = load_prompts()
            
            # Should have at least the default prompts
            assert 'standup' in prompts
            assert 'retro' in prompts
            assert 'weekly' in prompts
            assert len(prompts['standup']) > 0
            
    def test_load_user_prompts_override(self):
        """Test user prompts override default ones."""
        with patch('hacktivity.__main__.Path.home') as mock_home:
            # Set up a temp home directory with user prompts
            temp_home = Path(tempfile.mkdtemp())
            mock_home.return_value = temp_home
            
            # Create user prompts directory
            user_prompts_dir = temp_home / ".hacktivity" / "prompts"
            user_prompts_dir.mkdir(parents=True)
            
            # Create a custom standup prompt and a new custom prompt
            custom_standup = "Custom standup instructions"
            (user_prompts_dir / "standup.md").write_text(custom_standup)
            
            custom_pirate = "Summarize like a pirate, arr!"
            (user_prompts_dir / "pirate.md").write_text(custom_pirate)
            
            prompts = load_prompts()
            
            # User standup should override default
            assert prompts['standup'] == custom_standup
            
            # New custom prompt should be available
            assert 'pirate' in prompts
            assert prompts['pirate'] == custom_pirate
            
            # Other defaults should still exist
            assert 'retro' in prompts
            assert 'weekly' in prompts
            

class TestCLIPromptOptions:
    """Test CLI prompt options."""
    
    @patch('hacktivity.__main__.check_github_prerequisites')
    @patch('hacktivity.__main__.check_ai_prerequisites')
    @patch('hacktivity.__main__.get_github_user')
    @patch('hacktivity.__main__.fetch_commits')
    @patch('hacktivity.__main__.get_summary')
    def test_prompt_option(self, mock_summary, mock_fetch, mock_user, 
                          mock_ai_check, mock_gh_check):
        """Test --prompt option works with custom prompt names."""
        # Set up mocks
        mock_user.return_value = 'testuser'
        mock_fetch.return_value = ['commit 1', 'commit 2']
        mock_summary.return_value = 'Test summary'
        
        # Create a custom prompt
        with patch('hacktivity.__main__.Path.home') as mock_home:
            temp_home = Path(tempfile.mkdtemp())
            mock_home.return_value = temp_home
            
            user_prompts_dir = temp_home / ".hacktivity" / "prompts"
            user_prompts_dir.mkdir(parents=True)
            (user_prompts_dir / "custom.md").write_text("Custom prompt")
            
            # Run CLI with --prompt option
            runner = click.testing.CliRunner()
            result = runner.invoke(cli, ['--prompt', 'custom'])
            
            assert result.exit_code == 0
            mock_summary.assert_called_once()
            # Check that the custom prompt was used
            assert mock_summary.call_args[0][1] == "Custom prompt"
    
    @patch('hacktivity.__main__.check_github_prerequisites')
    @patch('hacktivity.__main__.check_ai_prerequisites')
    @patch('hacktivity.__main__.get_github_user')
    @patch('hacktivity.__main__.fetch_commits')
    @patch('hacktivity.__main__.get_summary')
    def test_type_option_backward_compatibility(self, mock_summary, mock_fetch, 
                                               mock_user, mock_ai_check, mock_gh_check):
        """Test --type option still works for backward compatibility."""
        # Set up mocks
        mock_user.return_value = 'testuser'
        mock_fetch.return_value = ['commit 1', 'commit 2']
        mock_summary.return_value = 'Test summary'
        
        # Run CLI with --type option
        runner = click.testing.CliRunner()
        result = runner.invoke(cli, ['--type', 'retro'])
        
        assert result.exit_code == 0
        mock_summary.assert_called_once()
    
    @patch('hacktivity.__main__.check_github_prerequisites')
    @patch('hacktivity.__main__.check_ai_prerequisites')
    @patch('hacktivity.__main__.get_github_user')
    @patch('hacktivity.__main__.fetch_commits')
    @patch('hacktivity.__main__.get_summary')
    @patch('hacktivity.__main__.logger')
    def test_prompt_overrides_type(self, mock_logger, mock_summary, mock_fetch,
                                  mock_user, mock_ai_check, mock_gh_check):
        """Test --prompt overrides --type when both are specified."""
        # Set up mocks
        mock_user.return_value = 'testuser'
        mock_fetch.return_value = ['commit 1', 'commit 2']
        mock_summary.return_value = 'Test summary'
        
        # Run CLI with both options
        runner = click.testing.CliRunner()
        result = runner.invoke(cli, ['--type', 'standup', '--prompt', 'weekly'])
        
        assert result.exit_code == 0
        # Should warn about using both
        mock_logger.warning.assert_called()
        
    @patch('hacktivity.__main__.check_github_prerequisites')
    @patch('hacktivity.__main__.check_ai_prerequisites')
    @patch('hacktivity.__main__.get_github_user')
    @patch('hacktivity.__main__.fetch_commits')
    @patch('hacktivity.__main__.load_prompts')
    def test_invalid_prompt_name(self, mock_load_prompts, mock_fetch, mock_user,
                                mock_ai_check, mock_gh_check):
        """Test error when invalid prompt name is provided."""
        # Set up mocks
        mock_load_prompts.return_value = {'standup': 'Test prompt', 'retro': 'Retro prompt'}
        mock_user.return_value = 'testuser'
        mock_fetch.return_value = ['commit 1', 'commit 2']
        
        runner = click.testing.CliRunner()
        result = runner.invoke(cli, ['--prompt', 'nonexistent'])
        
        assert result.exit_code != 0
        assert "Error: Prompt 'nonexistent' not found" in result.output
        assert "Available prompts:" in result.output