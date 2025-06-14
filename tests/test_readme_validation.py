"""Test that README.md provides complete user guidance."""

import subprocess
import sys
from pathlib import Path
import pytest


class TestReadmeValidation:
    """Test README.md completeness and accuracy."""
    
    def test_readme_exists(self):
        """Test that README.md exists."""
        readme_path = Path(__file__).parent.parent / "README.md"
        assert readme_path.exists()
        assert readme_path.is_file()
    
    def test_readme_has_required_sections(self):
        """Test that README has all required sections."""
        readme_path = Path(__file__).parent.parent / "README.md"
        content = readme_path.read_text()
        
        required_sections = [
            "# Hacktivity",
            "## Installation",
            "## Quick Start", 
            "## Environment Variables",
            "## Usage",
            "## Configuration",
            "## Prompt Customization",
            "## Output Examples",
            "## Troubleshooting"
        ]
        
        for section in required_sections:
            assert section in content, f"Missing required section: {section}"
    
    def test_readme_installation_commands_valid(self):
        """Test that installation commands in README are valid."""
        readme_path = Path(__file__).parent.parent / "README.md"
        content = readme_path.read_text()
        
        # Check pip install command is mentioned
        assert "pip install hacktivity" in content
        assert "pip install ." in content
        
        # Check init command is documented
        assert "hacktivity init" in content
    
    def test_readme_examples_match_cli(self):
        """Test that CLI examples in README match actual CLI."""
        readme_path = Path(__file__).parent.parent / "README.md"
        content = readme_path.read_text()
        
        # Check key CLI options are documented
        assert "--since" in content
        assert "--until" in content
        assert "--prompt" in content
        assert "--format" in content
        assert "--org" in content
        assert "--repo" in content
        
        # Check output formats are documented
        assert "markdown" in content
        assert "json" in content  
        assert "plain" in content
        
        # Check prompt types are documented
        assert "standup" in content
        assert "retro" in content
        assert "weekly" in content
    
    def test_readme_environment_variables_documented(self):
        """Test that required environment variables are documented."""
        readme_path = Path(__file__).parent.parent / "README.md"
        content = readme_path.read_text()
        
        # Check required env vars are documented
        assert "GITHUB_TOKEN" in content
        assert "GEMINI_API_KEY" in content
        
        # Check there are instructions for getting API keys
        assert "GitHub Settings" in content or "github.com/settings" in content
        assert "Google AI Studio" in content or "makersuite.google.com" in content
    
    def test_readme_config_example_valid(self):
        """Test that config example in README matches actual config structure."""
        readme_path = Path(__file__).parent.parent / "README.md"
        content = readme_path.read_text()
        
        # Check config sections are documented
        assert "[cache]" in content
        assert "[github]" in content  
        assert "[ai]" in content
        assert "[app]" in content
        
        # Check key config options are documented
        assert "max_age_hours" in content
        assert "per_page" in content
        assert "model_name" in content
        assert "default_prompt_type" in content
        assert "default_format" in content
    
    def test_readme_output_examples_valid(self):
        """Test that output examples in README are realistic."""
        readme_path = Path(__file__).parent.parent / "README.md"
        content = readme_path.read_text()
        
        # Check markdown example
        assert "--- Git Activity Summary ---" in content
        
        # Check JSON example structure
        assert '"summary":' in content
        assert '"metadata":' in content
        assert '"user":' in content
        assert '"since":' in content
        
        # Check plain text example
        assert "Git Activity Summary" in content
        assert "===================" in content
    
    def test_readme_troubleshooting_covers_common_issues(self):
        """Test that troubleshooting section covers expected issues."""
        readme_path = Path(__file__).parent.parent / "README.md"
        content = readme_path.read_text()
        
        # Check common issues are covered
        assert "No activity found" in content
        assert "API Rate Limits" in content
        assert "Authentication Errors" in content
        assert "Gemini API Errors" in content
        
        # Check debugging guidance
        assert "DEBUG" in content
        assert "cache" in content.lower()
    
    def test_readme_quick_start_complete(self):
        """Test that quick start section provides complete workflow."""
        readme_path = Path(__file__).parent.parent / "README.md" 
        content = readme_path.read_text()
        
        # Check quick start has all necessary steps
        quick_start_section = content[content.find("## Quick Start"):content.find("## Environment Variables")]
        
        assert "hacktivity init" in quick_start_section
        assert "GITHUB_TOKEN" in quick_start_section  
        assert "GEMINI_API_KEY" in quick_start_section
        assert "hacktivity" in quick_start_section
    
    def test_readme_has_practical_examples(self):
        """Test that README includes practical usage examples."""
        readme_path = Path(__file__).parent.parent / "README.md"
        content = readme_path.read_text()
        
        # Check for practical command examples
        assert "hacktivity --since 2024-01-01 --until 2024-01-07" in content
        assert "hacktivity --format json" in content
        assert "hacktivity --org mycompany" in content
        assert "hacktivity --prompt standup" in content
        
        # Check for custom prompt example
        assert "~/.hacktivity/prompts/" in content
        assert ".md" in content