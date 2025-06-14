"""Unit tests for package installation."""

import subprocess
import sys
from pathlib import Path
import pytest


class TestPackageInstallation:
    """Test package installation and entry points."""
    
    def test_hacktivity_command_exists(self):
        """Test that hacktivity command is available after installation."""
        # Check if hacktivity command can be imported
        try:
            from hacktivity.__main__ import main
            assert callable(main)
        except ImportError:
            pytest.fail("Could not import hacktivity main function")
    
    def test_entry_point_help(self):
        """Test that the entry point shows help correctly."""
        # Run hacktivity --help
        result = subprocess.run(
            [sys.executable, '-m', 'hacktivity', '--help'],
            capture_output=True,
            text=True
        )
        
        assert result.returncode == 0
        assert "Summarize your GitHub activity" in result.stdout
        assert "--since" in result.stdout
        assert "--format" in result.stdout
    
    def test_package_prompts_included(self):
        """Test that package includes prompt files."""
        import hacktivity
        package_dir = Path(hacktivity.__file__).parent
        prompts_dir = package_dir / "prompts"
        
        assert prompts_dir.exists()
        assert prompts_dir.is_dir()
        
        # Check for default prompts
        assert (prompts_dir / "standup.md").exists()
        assert (prompts_dir / "retro.md").exists()
        assert (prompts_dir / "weekly.md").exists()
    
    def test_pyproject_toml_exists(self):
        """Test that pyproject.toml exists and has correct structure."""
        project_root = Path(__file__).parent.parent
        pyproject_path = project_root / "pyproject.toml"
        
        assert pyproject_path.exists()
        
        # Read and verify basic structure
        content = pyproject_path.read_text()
        assert "[build-system]" in content
        assert "[project]" in content
        assert "[project.scripts]" in content
        assert 'hacktivity = "hacktivity.__main__:main"' in content
        assert '[tool.setuptools.package-data]' in content
        assert 'hacktivity = ["prompts/*.md"]' in content