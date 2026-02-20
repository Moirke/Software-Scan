"""
WSGI entry point for production deployment.

Usage with Gunicorn:
    gunicorn --workers 4 --timeout 600 --bind 127.0.0.1:5000 wsgi:app

Worker count guideline: (2 x CPU cores) + 1
Timeout set to 600 s to allow large repo clones to complete.
For better concurrency without more processes:
    gunicorn --workers 2 --worker-class gthread --threads 4 --timeout 600 --bind 127.0.0.1:5000 wsgi:app
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.web import app  # noqa: F401 — re-exported for Gunicorn
