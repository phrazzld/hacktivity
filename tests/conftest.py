"""Pytest configuration and shared fixtures."""

import sys
from pathlib import Path

# Add parent directory to path so tests can import hacktivity modules
sys.path.insert(0, str(Path(__file__).parent.parent))

# Configure pytest-cov to measure coverage correctly
def pytest_configure(config):
    """Configure pytest settings."""
    config.option.verbose = True