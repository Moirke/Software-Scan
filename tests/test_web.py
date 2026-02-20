"""
Tests for web interface
"""
import unittest
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.web import app


class TestWebInterface(unittest.TestCase):

    def setUp(self):
        """Set up test client"""
        self.app = app.test_client()
        self.app.testing = True

    def test_index_page_loads(self):
        """Test main page loads"""
        response = self.app.get('/')
        self.assertEqual(response.status_code, 200)

    def test_api_scan_endpoint_exists(self):
        """Test API endpoint exists"""
        response = self.app.post('/api/scan', json={})
        # Should return error but endpoint exists
        self.assertIn(response.status_code, [400, 500])


if __name__ == '__main__':
    unittest.main()
