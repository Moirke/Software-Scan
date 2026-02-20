#!/usr/bin/env python3
"""
Entry point for Repository Scanner Web Interface
Wrapper script that handles import paths
"""
import sys
import os

# Add project root to Python path
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

# Import and run web app
from src.web import app

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
