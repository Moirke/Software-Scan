"""
Tests for CLI interface
"""
import unittest
from unittest.mock import patch, MagicMock
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src import cli


class TestCLI(unittest.TestCase):

    @patch('src.cli.ProhibitedWordScanner')
    def test_cli_basic_execution(self, mock_scanner):
        """Test CLI runs without errors"""
        # Placeholder for CLI tests
        pass


if __name__ == '__main__':
    unittest.main()
