#!/usr/bin/env python3
"""
Entry point for Repository Scanner CLI
Wrapper script that handles import paths
"""
import sys
import os

# Add project root to Python path
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

# Initialise logging before any other application imports
from src.logging_config import configure_logging
configure_logging()

# Import and run CLI
from src.cli import main

if __name__ == '__main__':
    main()
