#!/usr/bin/env python3
"""
Sample application file with prohibited words for demo purposes
"""

# TODO: Refactor this code to use environment variables
# FIXME: Security issue - hardcoded credentials

import os
import json

class DatabaseConnection:
    """Sample database connection class"""

    def __init__(self):
        # Bad practice: hardcoded password
        self.password = "admin123"
        self.api_key = "secret_key_here_12345"
        self.host = "localhost"

    def connect(self):
        """Connect to database"""
        # XXX: This is a temporary hack
        print(f"Connecting with password: {self.password}")
        return True

def load_config():
    """Load configuration"""
    # TODO: Move this to a proper config file
    config = {
        'api_key': 'hardcoded_api_key',
        'secret': 'application_secret'
    }
    return config

def main():
    """Main application entry point"""
    # HACK: Quick fix for production
    db = DatabaseConnection()
    db.connect()

    config = load_config()
    print(f"Loaded config with {len(config)} keys")

    # DELETEME: Remove this debug code
    print("Application started")

if __name__ == "__main__":
    main()
