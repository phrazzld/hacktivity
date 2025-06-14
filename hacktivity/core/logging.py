"""Structured logging configuration for hacktivity."""

import logging
import sys
from typing import Optional


def setup_logging(level: Optional[str] = None, debug: bool = False) -> None:
    """Configure structured logging for the application.
    
    Args:
        level: Log level string (DEBUG, INFO, WARNING, ERROR, CRITICAL)
               If None, uses WARNING for clean output or DEBUG for debug mode
        debug: If True, enables debug-level logging and verbose output
    """
    # Determine log level based on debug flag
    if debug:
        numeric_level = logging.DEBUG
    elif level is not None:
        # Convert string level to logging constant
        numeric_level = getattr(logging, level.upper(), logging.WARNING)
    else:
        # Default to WARNING for clean output (only warnings, errors, critical)
        numeric_level = logging.WARNING
    
    # Configure root logger
    logging.basicConfig(
        level=numeric_level,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        stream=sys.stderr,  # All logging goes to stderr, leaving stdout for user output
        force=True  # Reconfigure if already set up
    )
    
    # Suppress noisy third-party loggers
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('requests').setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance for the given module name.
    
    Args:
        name: Module name, typically __name__
        
    Returns:
        Logger instance
    """
    return logging.getLogger(name)