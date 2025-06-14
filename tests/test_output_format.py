"""Unit tests for output format functionality."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest
import click.testing

# Import the module
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import from the hacktivity.__main__ module
from hacktivity.__main__ import format_output, cli
hacktivity_module = sys.modules['hacktivity.__main__']


class TestFormatOutput:
    """Test output formatting functions."""
    
    def test_format_output_markdown(self):
        """Test markdown format output."""
        summary = "## Today's Work\n\n- Fixed bug in auth\n- Updated documentation"
        metadata = {
            "user": "testuser",
            "since": "2024-01-01",
            "until": "2024-01-02",
            "prompt_type": "standup",
            "org": "all",
            "repo": "all"
        }
        
        result = format_output(summary, "markdown", metadata)
        
        assert "--- Git Activity Summary ---" in result
        assert summary in result
        assert "--------------------------" in result
        
    def test_format_output_json(self):
        """Test JSON format output."""
        summary = "Fixed authentication bug and updated docs"
        metadata = {
            "user": "testuser",
            "since": "2024-01-01",
            "until": "2024-01-02",
            "prompt_type": "standup",
            "org": "myorg",
            "repo": "myrepo"
        }
        
        result = format_output(summary, "json", metadata)
        
        # Should be valid JSON
        parsed = json.loads(result)
        assert parsed["summary"] == summary
        assert parsed["metadata"]["user"] == "testuser"
        assert parsed["metadata"]["since"] == "2024-01-01"
        assert parsed["metadata"]["until"] == "2024-01-02"
        assert parsed["metadata"]["prompt_type"] == "standup"
        
    def test_format_output_plain(self):
        """Test plain text format output."""
        summary = "**Fixed** the `auth` bug and *updated* docs"
        metadata = {
            "user": "testuser",
            "since": "2024-01-01",
            "until": "2024-01-02",
            "prompt_type": "retro",
            "org": "all",
            "repo": "all"
        }
        
        result = format_output(summary, "plain", metadata)
        
        # Should have no markdown formatting
        assert "**" not in result
        assert "*" not in result
        assert "`" not in result
        
        # Should have plain text headers
        assert "Git Activity Summary" in result
        assert "===================" in result
        assert "User: testuser" in result
        assert "Period: 2024-01-01 to 2024-01-02" in result
        assert "Prompt: retro" in result
        
    def test_format_output_removes_markdown_headers(self):
        """Test that plain format removes markdown headers."""
        summary = "# Main Header\n## Subheader\n### Small header\nContent"
        metadata = {"user": "test", "since": "2024-01-01", "until": "2024-01-02", 
                    "prompt_type": "standup", "org": "all", "repo": "all"}
        
        result = format_output(summary, "plain", metadata)
        
        # Markdown headers should be removed
        assert "# " not in result
        assert "## " not in result
        assert "### " not in result
        assert "Main Header" in result
        assert "Subheader" in result
        assert "Small header" in result


class TestCLIFormatOption:
    """Test CLI format option."""
    
    @patch.object(hacktivity_module, 'load_prompts')
    @patch.object(hacktivity_module, 'check_github_prerequisites')
    @patch.object(hacktivity_module, 'check_ai_prerequisites')
    @patch.object(hacktivity_module, 'get_github_user')
    @patch.object(hacktivity_module, 'fetch_commits')
    @patch.object(hacktivity_module, 'get_summary')
    def test_format_option_json(self, mock_summary, mock_fetch, mock_user, 
                               mock_ai_check, mock_gh_check, mock_load_prompts):
        """Test --format json produces valid JSON output."""
        # Set up mocks
        mock_load_prompts.return_value = {'standup': 'Test prompt'}
        mock_user.return_value = 'testuser'
        mock_fetch.return_value = ['commit 1', 'commit 2']
        mock_summary.return_value = 'Test summary of work'
        
        # Run CLI with --format json
        runner = click.testing.CliRunner()
        result = runner.invoke(cli, ['--format', 'json'])
        
        if result.exit_code != 0:
            print(f"Error output: {result.output}")
            print(f"Exception: {result.exception}")
        
        assert result.exit_code == 0
        
        # Output should be valid JSON
        output_json = json.loads(result.output)
        assert output_json["summary"] == "Test summary of work"
        assert output_json["metadata"]["user"] == "testuser"
        
    @patch.object(hacktivity_module, 'load_prompts')
    @patch.object(hacktivity_module, 'check_github_prerequisites')
    @patch.object(hacktivity_module, 'check_ai_prerequisites')
    @patch.object(hacktivity_module, 'get_github_user')
    @patch.object(hacktivity_module, 'fetch_commits')
    @patch.object(hacktivity_module, 'get_summary')
    def test_format_option_plain(self, mock_summary, mock_fetch, mock_user,
                                mock_ai_check, mock_gh_check, mock_load_prompts):
        """Test --format plain produces plain text output."""
        # Set up mocks
        mock_load_prompts.return_value = {'standup': 'Test prompt'}
        mock_user.return_value = 'testuser'
        mock_fetch.return_value = ['commit 1', 'commit 2']
        mock_summary.return_value = '**Bold** text with `code`'
        
        # Run CLI with --format plain
        runner = click.testing.CliRunner()
        result = runner.invoke(cli, ['--format', 'plain'])
        
        assert result.exit_code == 0
        assert "Git Activity Summary" in result.output
        assert "===================" in result.output
        assert "**" not in result.output  # Markdown should be stripped
        assert "`" not in result.output
        
    @patch.object(hacktivity_module, 'load_prompts')
    @patch.object(hacktivity_module, 'check_github_prerequisites')
    @patch.object(hacktivity_module, 'check_ai_prerequisites')
    @patch.object(hacktivity_module, 'get_github_user')
    @patch.object(hacktivity_module, 'fetch_commits')
    @patch.object(hacktivity_module, 'get_summary')
    def test_format_option_markdown_default(self, mock_summary, mock_fetch, 
                                           mock_user, mock_ai_check, mock_gh_check, mock_load_prompts):
        """Test default format is markdown."""
        # Set up mocks
        mock_load_prompts.return_value = {'standup': 'Test prompt'}
        mock_user.return_value = 'testuser'
        mock_fetch.return_value = ['commit 1', 'commit 2']
        mock_summary.return_value = 'Test summary'
        
        # Run CLI without format option
        runner = click.testing.CliRunner()
        result = runner.invoke(cli, [])
        
        assert result.exit_code == 0
        assert "--- Git Activity Summary ---" in result.output
        assert "--------------------------" in result.output
        
    @patch.object(hacktivity_module, 'load_prompts')
    @patch.object(hacktivity_module, 'check_github_prerequisites')
    @patch.object(hacktivity_module, 'check_ai_prerequisites')
    @patch.object(hacktivity_module, 'get_github_user')
    @patch.object(hacktivity_module, 'fetch_commits')
    @patch.object(hacktivity_module, 'get_summary')
    def test_format_with_filters(self, mock_summary, mock_fetch, mock_user,
                                mock_ai_check, mock_gh_check, mock_load_prompts):
        """Test format option works with org/repo filters."""
        # Set up mocks
        mock_load_prompts.return_value = {'standup': 'Test prompt'}
        mock_user.return_value = 'testuser'
        mock_fetch.return_value = ['commit 1', 'commit 2']
        mock_summary.return_value = 'Filtered work summary'
        
        # Run CLI with format and filters
        runner = click.testing.CliRunner()
        result = runner.invoke(cli, [
            '--format', 'json',
            '--org', 'myorg',
            '--repo', 'myorg/myrepo'
        ])
        
        assert result.exit_code == 0
        
        # Check JSON includes filter metadata
        output_json = json.loads(result.output)
        assert output_json["metadata"]["org"] == "myorg"
        assert output_json["metadata"]["repo"] == "myorg/myrepo"