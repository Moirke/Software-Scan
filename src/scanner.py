"""
Repository Scanner - Core scanning functionality
Supports searching through code repositories including compressed/archived files
"""
from __future__ import annotations
import json
import logging
import os
import re
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import List, Dict, Set, Tuple

import yaml

from src.logging_config import LOGGER_NAME


class ArchiveExtractor:
    """Handles extraction of various archive formats"""
    
    @staticmethod
    def extract_zip(archive_path: str, extract_dir: str) -> None:
        """Extract ZIP files"""
        with zipfile.ZipFile(archive_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)
    
    @staticmethod
    def extract_tar(archive_path: str, extract_dir: str) -> None:
        """Extract TAR files (including .tar.gz, .tar.bz2)"""
        with tarfile.open(archive_path, 'r:*') as tar_ref:
            tar_ref.extractall(extract_dir)
    
    @staticmethod
    def extract_rpm(archive_path: str, extract_dir: str) -> None:
        """Extract RPM files using rpm2cpio and cpio"""
        import subprocess
        _log = logging.getLogger(LOGGER_NAME)
        try:
            result = subprocess.run(
                f"cd {extract_dir} && rpm2cpio {archive_path} | cpio -idmv 2>/dev/null",
                shell=True,
                capture_output=True
            )
            if result.returncode != 0:
                _log.warning('archive_extraction_failed path=%s error="rpm2cpio non-zero exit"', archive_path)
        except Exception as e:
            _log.warning('archive_extraction_failed path=%s error=%r', archive_path, str(e))
    
    @staticmethod
    def extract_docker_image(image_path: str, extract_dir: str) -> None:
        """Extract Docker image tar file"""
        _log = logging.getLogger(LOGGER_NAME)
        try:
            if tarfile.is_tarfile(image_path):
                with tarfile.open(image_path, 'r') as tar:
                    tar.extractall(extract_dir)

                for root, _, files in os.walk(extract_dir):
                    for file in files:
                        if file.endswith('.tar'):
                            layer_path = os.path.join(root, file)
                            layer_extract = os.path.join(root, file.replace('.tar', '_layer'))
                            os.makedirs(layer_extract, exist_ok=True)
                            try:
                                with tarfile.open(layer_path, 'r') as layer_tar:
                                    layer_tar.extractall(layer_extract)
                            except Exception as layer_exc:
                                _log.warning('archive_extraction_failed path=%s error=%r', layer_path, str(layer_exc))
        except Exception as e:
            _log.warning('archive_extraction_failed path=%s error=%r', image_path, str(e))


class ProhibitedWordScanner:
    """Main scanner class for finding prohibited words in repositories"""
    
    ARCHIVE_EXTENSIONS = {
        '.zip': 'extract_zip',
        '.tar': 'extract_tar',
        '.tar.gz': 'extract_tar',
        '.tgz': 'extract_tar',
        '.tar.bz2': 'extract_tar',
        '.tbz2': 'extract_tar',
        '.tar.xz': 'extract_tar',
        '.rpm': 'extract_rpm',
    }
    
    MAX_ARCHIVE_DEPTH = 10  # maximum levels of nested archive extraction

    BINARY_EXTENSIONS = {
        '.exe', '.dll', '.so', '.dylib', '.bin', '.class', 
        '.pyc', '.pyo', '.o', '.a', '.lib', '.obj',
        '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.ico',
        '.mp3', '.mp4', '.avi', '.mov', '.wav',
        '.pdf', '.doc', '.docx', '.xls', '.xlsx'
    }
    
    def __init__(self, config_path: str, logger: logging.Logger | None = None):
        """Initialize scanner with configuration file"""
        self._log = logger or logging.getLogger(LOGGER_NAME)
        self.config = self._load_config(config_path)
        self.case_sensitive = self.config.get('case_sensitive', False)
        self.max_file_size = self.config.get('max_file_size_mb', 10) * 1024 * 1024
        self.prohibited_words = self._load_prohibited_words()
        self.excluded_paths = [
            os.path.normpath(os.path.abspath(p))
            for p in self.config.get('excluded_paths', [])
        ]
        self.temp_dirs = []
        self.depth_limit_hits = 0   # archives skipped due to MAX_ARCHIVE_DEPTH
        
    def _load_config(self, config_path: str) -> Dict:
        """Load configuration from YAML or JSON file"""
        with open(config_path, 'r') as f:
            if config_path.endswith('.yaml') or config_path.endswith('.yml'):
                return yaml.safe_load(f)
            elif config_path.endswith('.json'):
                return json.load(f)
            else:
                raise ValueError("Config file must be .yaml, .yml, or .json")
    
    def _load_prohibited_words(self) -> List[str]:
        """
        Parse the prohibited words/patterns source and compile each entry.

        Supported line formats (one per line):
          password              — plain word, word-boundary matched (exact/partial)
          "regex:"              — quoted literal, substring matched (always partial)
          regex:AKIA[0-9A-Z]+   — regex pattern, matched as-is (always exact)
          # comment             — ignored

        Compiled patterns are stored in self._compiled_patterns.
        Returns a list of display names stored as self.prohibited_words.
        """
        words_file = self.config.get('prohibited_words_file')
        if words_file:
            with open(words_file, 'r', encoding='utf-8') as f:
                raw_lines = [line.rstrip('\n') for line in f]
        else:
            raw_lines = [str(w) for w in self.config.get('prohibited_words', [])]

        flags = 0 if self.case_sensitive else re.IGNORECASE
        self._compiled_patterns: List[Dict] = []
        display_names: List[str] = []

        for raw in raw_lines:
            stripped = raw.strip()
            if not stripped or stripped.startswith('#'):
                continue

            if stripped.startswith('"') and stripped.endswith('"') and len(stripped) >= 2:
                # ── Quoted literal: strip quotes, match as substring ───────
                value = stripped[1:-1]
                if not value:
                    continue
                self._compiled_patterns.append({
                    'type':    'literal',
                    'display': stripped,
                    'pattern': re.compile(re.escape(value), flags),
                })

            elif stripped.startswith('regex:'):
                # ── Regex pattern ─────────────────────────────────────────
                raw_pattern = stripped[len('regex:'):]
                if not raw_pattern:
                    continue
                try:
                    compiled = re.compile(raw_pattern, flags)
                except re.error as exc:
                    self._log.warning(
                        'invalid_regex_skipped pattern=%r error=%r',
                        raw_pattern, str(exc),
                    )
                    continue
                self._compiled_patterns.append({
                    'type':    'regex',
                    'display': stripped,
                    'pattern': compiled,
                })

            else:
                # ── Plain word: word-boundary matching ────────────────────
                self._compiled_patterns.append({
                    'type':            'word',
                    'display':         stripped,
                    'pattern_exact':   re.compile(r'\b' + re.escape(stripped) + r'\b', flags),
                    'pattern_partial': re.compile(re.escape(stripped), flags),
                })

            display_names.append(stripped)

        return display_names
    
    def _is_binary_file(self, filepath: str) -> bool:
        """Check if file is likely binary"""
        ext = Path(filepath).suffix.lower()
        if ext in self.BINARY_EXTENSIONS:
            return True
        
        try:
            with open(filepath, 'rb') as f:
                chunk = f.read(8192)
                if b'\x00' in chunk:  # Null bytes indicate binary
                    return True
        except Exception:
            return True
        
        return False
    
    def _is_archive(self, filepath: str) -> Tuple[bool, str]:
        """Check if file is an archive and return extraction method"""
        filepath_lower = filepath.lower()
        
        # Check for docker image naming patterns
        if 'docker' in filepath_lower and filepath_lower.endswith('.tar'):
            return True, 'extract_docker_image'
        
        for ext, method in self.ARCHIVE_EXTENSIONS.items():
            if filepath_lower.endswith(ext):
                return True, method
        
        return False, None
    
    def _extract_archive(self, archive_path: str) -> str:
        """Extract archive to temporary directory"""
        temp_dir = tempfile.mkdtemp(prefix='repo_scanner_')
        self.temp_dirs.append(temp_dir)
        
        is_archive, method = self._is_archive(archive_path)
        if is_archive:
            extractor = ArchiveExtractor()
            extract_method = getattr(extractor, method)
            extract_method(archive_path, temp_dir)
        
        return temp_dir
    
    def _is_excluded(self, path: str) -> bool:
        """Return True if path falls under any configured excluded_paths entry."""
        if not self.excluded_paths:
            return False
        norm = os.path.normpath(os.path.abspath(path))
        for excl in self.excluded_paths:
            try:
                if os.path.commonpath([norm, excl]) == excl:
                    return True
            except ValueError:
                pass  # Different drives on Windows
        return False

    def _search_in_file(self, filepath: str) -> List[Dict]:
        """Search for prohibited words/patterns in a single file.

        Match classification:
          'exact'   — plain word at a word boundary, or a regex pattern match
          'partial' — plain word as a substring, or a quoted literal match
        """
        results = []

        if self._is_binary_file(filepath):
            self._log.debug('file_skipped_binary path=%s', filepath)
            return results

        try:
            file_size = os.path.getsize(filepath)
            if file_size > self.max_file_size:
                self._log.warning(
                    'file_skipped_size path=%s size_mb=%.1f limit_mb=%d',
                    filepath, file_size / 1024 / 1024,
                    self.config.get('max_file_size_mb', 10),
                )
                return results

            self._log.debug('file_scanning path=%s', filepath)
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                for line_num, line in enumerate(f, 1):
                    for entry in self._compiled_patterns:
                        display = entry['display']

                        if entry['type'] == 'word':
                            # Two-pass: exact (word-boundary) then partial (substring)
                            exact_positions: Set[int] = set()
                            for match in re.finditer(entry['pattern_exact'], line):
                                exact_positions.add(match.start())
                                results.append({
                                    'file':            filepath,
                                    'line_number':     line_num,
                                    'line_content':    line.strip(),
                                    'prohibited_word': display,
                                    'position':        match.start(),
                                    'match_type':      'exact',
                                })
                                self._log.debug(
                                    'match_found path=%s line=%d word=%r match_type=exact',
                                    filepath, line_num, display,
                                )
                            for match in re.finditer(entry['pattern_partial'], line):
                                if match.start() not in exact_positions:
                                    results.append({
                                        'file':            filepath,
                                        'line_number':     line_num,
                                        'line_content':    line.strip(),
                                        'prohibited_word': display,
                                        'position':        match.start(),
                                        'match_type':      'partial',
                                    })
                                    self._log.debug(
                                        'match_found path=%s line=%d word=%r match_type=partial',
                                        filepath, line_num, display,
                                    )

                        elif entry['type'] == 'literal':
                            # Quoted string: substring match, always partial
                            for match in re.finditer(entry['pattern'], line):
                                results.append({
                                    'file':            filepath,
                                    'line_number':     line_num,
                                    'line_content':    line.strip(),
                                    'prohibited_word': display,
                                    'position':        match.start(),
                                    'match_type':      'partial',
                                })
                                self._log.debug(
                                    'match_found path=%s line=%d word=%r match_type=partial',
                                    filepath, line_num, display,
                                )

                        elif entry['type'] == 'regex':
                            # Regex pattern: always exact (user controls boundaries)
                            for match in re.finditer(entry['pattern'], line):
                                results.append({
                                    'file':            filepath,
                                    'line_number':     line_num,
                                    'line_content':    line.strip(),
                                    'prohibited_word': display,
                                    'position':        match.start(),
                                    'match_type':      'exact',
                                })
                                self._log.debug(
                                    'match_found path=%s line=%d word=%r match_type=exact',
                                    filepath, line_num, display,
                                )

        except PermissionError as e:
            self._log.warning('file_skipped_permission path=%s error=%r', filepath, str(e))
        except Exception as e:
            self._log.error('file_read_error path=%s error=%r', filepath, str(e))

        return results
    
    def scan_directory(self, repo_path: str, recursive: bool = True,
                       on_progress=None) -> List[Dict]:
        """Scan directory for prohibited words.

        on_progress: optional callable(files_scanned: int, current_file: str)
            Called just before each file is scanned.  Throttle on the caller
            side if the volume of events needs to be reduced.
        """
        all_results  = []
        scanned_files = set()
        files_scanned = 0

        def scan_path(path: str, is_extracted: bool = False, depth: int = 0):
            nonlocal files_scanned
            if self._is_excluded(path):
                return

            if os.path.isfile(path):
                if path in scanned_files:
                    return
                scanned_files.add(path)

                is_archive, fmt = self._is_archive(path)
                if is_archive:
                    if depth >= self.MAX_ARCHIVE_DEPTH:
                        self._log.warning(
                            'archive_depth_limit_reached path=%s depth=%d limit=%d',
                            path, depth, self.MAX_ARCHIVE_DEPTH,
                        )
                        self.depth_limit_hits += 1
                        return
                    self._log.info('archive_extracting path=%s format=%s depth=%d',
                                   path, fmt or 'unknown', depth)
                    extract_dir = self._extract_archive(path)
                    scan_path(extract_dir, is_extracted=True, depth=depth + 1)
                else:
                    if on_progress is not None:
                        on_progress(files_scanned, path)
                    results = self._search_in_file(path)
                    files_scanned += 1
                    all_results.extend(results)

            elif os.path.isdir(path):
                for root, _, files in os.walk(path):
                    for file in files:
                        scan_path(os.path.join(root, file), is_extracted, depth)
                    if not recursive and not is_extracted:
                        break

        scan_path(repo_path)
        return all_results
    
    def cleanup(self):
        """Clean up temporary directories"""
        for temp_dir in self.temp_dirs:
            try:
                shutil.rmtree(temp_dir)
            except Exception as e:
                self._log.warning('cleanup_failed path=%s error=%r', temp_dir, str(e))
        self.temp_dirs = []
    
    def format_results(self, results: List[Dict]) -> str:
        """Format results as readable text, grouped by match type."""
        if not results:
            return "No prohibited words found!"

        exact   = [r for r in results if r.get('match_type') == 'exact']
        partial = [r for r in results if r.get('match_type') == 'partial']

        output = [f"\n{'='*80}"]
        output.append(
            f"SCAN RESULTS: Found {len(results)} violation(s)  "
            f"[{len(exact)} exact, {len(partial)} partial]"
        )
        output.append('='*80 + '\n')

        def _render_group(group, label):
            if not group:
                return
            output.append(f"\n{label}")
            output.append('-' * len(label))
            by_file = {}
            for result in group:
                file = result['file']
                if file not in by_file:
                    by_file[file] = []
                by_file[file].append(result)
            for file, violations in by_file.items():
                output.append(f"\n  File: {file}")
                output.append(f"  Violations: {len(violations)}")
                output.append("  " + "-" * 60)
                for v in violations:
                    output.append(f"    Line {v['line_number']}: Found '{v['prohibited_word']}'")
                    output.append(f"      {v['line_content']}")
                    output.append("")

        _render_group(exact,   "EXACT MATCHES (whole word)")
        _render_group(partial, "PARTIAL MATCHES (substring)")

        return '\n'.join(output)
