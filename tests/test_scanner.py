"""
Unit tests for scanner.py core functionality
"""
import unittest
import tempfile
import os
from src.scanner import ProhibitedWordScanner, ArchiveExtractor


class TestProhibitedWordScanner(unittest.TestCase):

    def setUp(self):
        """Set up test fixtures"""
        self.test_config = tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False)
        self.test_config.write("""prohibited_words:
  - password
  - secret
case_sensitive: false
max_file_size_mb: 10
""")
        self.test_config.close()

    def tearDown(self):
        """Clean up"""
        os.unlink(self.test_config.name)

    def test_scanner_initialization(self):
        """Test scanner initializes correctly"""
        scanner = ProhibitedWordScanner(self.test_config.name)
        self.assertEqual(len(scanner.prohibited_words), 2)
        self.assertIn('password', scanner.prohibited_words)

    def test_scanner_finds_violations(self):
        """Test scanner detects prohibited words"""
        # Create temp file with violation
        test_file = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.py')
        test_file.write("password = 'secret'\n")
        test_file.close()

        scanner = ProhibitedWordScanner(self.test_config.name)
        results = scanner._search_in_file(test_file.name)

        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]['prohibited_word'], 'password')

        os.unlink(test_file.name)


if __name__ == '__main__':
    unittest.main()
