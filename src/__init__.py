"""
Repository Scanner - Prohibited Words Detection Tool
Supports scanning code repositories including compressed archives.
"""

__version__ = "1.0.0"
__author__ = "Repository Scanner Team"

from .scanner import ProhibitedWordScanner, ArchiveExtractor

__all__ = ['ProhibitedWordScanner', 'ArchiveExtractor']
