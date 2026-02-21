"""
Comprehensive tests for scanner.py

Design note: all tests write scan content into self.scan_dir (a subdirectory
of self.tmpdir). Config files and the prohibited-words file live at the root
of self.tmpdir so they are never accidentally included in a scan.
"""
import io
import os
import shutil
import tarfile
import tempfile
import unittest
import zipfile
import yaml
from unittest.mock import patch, MagicMock

from src.scanner import ProhibitedWordScanner, ArchiveExtractor


# ── Shared constants ───────────────────────────────────────────────────────

PROHIBITED_WORDS = ['password', 'secret', 'api_key']
CLEAN_CONTENT    = 'This file has no prohibited content whatsoever.\n'
DIRTY_CONTENT    = "password = 'hunter2'\n"   # contains 'password'


# ── In-memory archive builders ─────────────────────────────────────────────

def _make_zip(files: dict) -> bytes:
    """Return bytes of a ZIP archive. files: {name: str|bytes}"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            if isinstance(content, str):
                content = content.encode()
            zf.writestr(name, content)
    return buf.getvalue()


def _make_tar(files: dict, fmt: str = 'gz') -> bytes:
    """Return bytes of a TAR archive. files: {name: str|bytes}"""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode=f'w:{fmt}') as tf:
        for name, content in files.items():
            data = content.encode() if isinstance(content, str) else content
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


# ── Base test case ─────────────────────────────────────────────────────────

class ScannerTestCase(unittest.TestCase):
    """
    Provides:
      self.tmpdir   — root temp dir (config files live here)
      self.scan_dir — subdirectory that tests write scan targets into
      self.words_file / self.config_path — default scanner setup
    """

    def setUp(self):
        self.tmpdir   = tempfile.mkdtemp(prefix='scanner_test_')
        self.scan_dir = os.path.join(self.tmpdir, 'scan')
        os.makedirs(self.scan_dir)

        self.words_file  = os.path.join(self.tmpdir, 'words.txt')
        with open(self.words_file, 'w') as f:
            f.write('\n'.join(PROHIBITED_WORDS) + '\n')

        self.config_path = self._write_config()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_config(self, **overrides) -> str:
        cfg = {
            'prohibited_words_file': self.words_file,
            'case_sensitive':        False,
            'max_file_size_mb':      10,
        }
        cfg.update(overrides)
        path = os.path.join(self.tmpdir, 'config.yaml')
        with open(path, 'w') as f:
            yaml.dump(cfg, f)
        return path

    def make_scanner(self, **config_overrides) -> ProhibitedWordScanner:
        path = self._write_config(**config_overrides) if config_overrides else self.config_path
        return ProhibitedWordScanner(path)

    def write_text(self, rel_path: str, content: str) -> str:
        """Write a text file into scan_dir. Returns absolute path."""
        full = os.path.join(self.scan_dir, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'w') as f:
            f.write(content)
        return full

    def write_bytes(self, rel_path: str, content: bytes) -> str:
        """Write a binary file into scan_dir. Returns absolute path."""
        full = os.path.join(self.scan_dir, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'wb') as f:
            f.write(content)
        return full

    def scan(self, scanner=None) -> list:
        """Run scan_directory on scan_dir and return results."""
        if scanner is None:
            scanner = self.make_scanner()
        results = scanner.scan_directory(self.scan_dir)
        scanner.cleanup()
        return results


# ══════════════════════════════════════════════════════════════════════════════
# Initialisation
# ══════════════════════════════════════════════════════════════════════════════

class TestScannerInit(ScannerTestCase):

    def test_words_loaded_from_file(self):
        scanner = self.make_scanner()
        for word in PROHIBITED_WORDS:
            self.assertIn(word, scanner.prohibited_words)

    def test_words_loaded_inline(self):
        path = os.path.join(self.tmpdir, 'inline.yaml')
        with open(path, 'w') as f:
            yaml.dump({'prohibited_words': ['inline_word'], 'case_sensitive': False}, f)
        scanner = ProhibitedWordScanner(path)
        self.assertIn('inline_word', scanner.prohibited_words)

    def test_case_sensitive_flag_respected(self):
        scanner = self.make_scanner(case_sensitive=True)
        self.assertTrue(scanner.case_sensitive)

    def test_case_insensitive_lowercases_words(self):
        # When case_sensitive=False the stored words should be lower-cased
        scanner = self.make_scanner(case_sensitive=False)
        for word in scanner.prohibited_words:
            self.assertEqual(word, word.lower())

    def test_excluded_paths_normalised(self):
        excl = os.path.join(self.scan_dir, 'config')
        scanner = self.make_scanner(excluded_paths=[excl])
        expected = os.path.normpath(os.path.abspath(excl))
        self.assertIn(expected, scanner.excluded_paths)

    def test_json_config_supported(self):
        import json
        path = os.path.join(self.tmpdir, 'config.json')
        with open(path, 'w') as f:
            json.dump({'prohibited_words': ['jsonword'], 'case_sensitive': False}, f)
        scanner = ProhibitedWordScanner(path)
        self.assertIn('jsonword', scanner.prohibited_words)


# ══════════════════════════════════════════════════════════════════════════════
# Word matching behaviour
# ══════════════════════════════════════════════════════════════════════════════

class TestWordMatching(ScannerTestCase):

    def test_finds_prohibited_word(self):
        self.write_text('app.py', DIRTY_CONTENT)
        results = self.scan()
        self.assertGreater(len(results), 0)
        self.assertTrue(any(r['prohibited_word'] == 'password' for r in results))

    def test_clean_file_no_violations(self):
        self.write_text('clean.py', CLEAN_CONTENT)
        self.assertEqual(self.scan(), [])

    def test_word_boundary_no_exact_suffix(self):
        # "passwords" must not produce an EXACT match — only partial
        self.write_text('f.py', 'passwords = []\n')
        results = self.scan()
        exact = [r for r in results if r.get('match_type') == 'exact']
        self.assertEqual(exact, [], "Embedded word should not be exact")

    def test_word_boundary_no_exact_prefix(self):
        # "notpassword" must not produce an EXACT match — only partial
        self.write_text('f.py', 'notpassword = True\n')
        results = self.scan()
        exact = [r for r in results if r.get('match_type') == 'exact']
        self.assertEqual(exact, [], "Embedded word should not be exact")

    def test_word_boundary_matches_with_punctuation(self):
        self.write_text('f.py', 'password: hunter2\n')
        results = self.scan()
        self.assertTrue(any(r['prohibited_word'] == 'password' for r in results))

    def test_word_boundary_matches_at_line_start(self):
        self.write_text('f.py', 'password=hunter2\n')
        results = self.scan()
        self.assertTrue(any(r['prohibited_word'] == 'password' for r in results))

    def test_case_insensitive_matches_uppercase(self):
        self.write_text('f.py', 'PASSWORD = "x"\n')
        self.assertGreater(len(self.scan()), 0)

    def test_case_insensitive_matches_mixed_case(self):
        self.write_text('f.py', 'Password = "x"\n')
        self.assertGreater(len(self.scan()), 0)

    def test_case_sensitive_no_match_for_uppercase(self):
        self.write_text('f.py', 'PASSWORD = "x"\n')
        scanner = self.make_scanner(case_sensitive=True)
        results = scanner.scan_directory(self.scan_dir)
        scanner.cleanup()
        self.assertEqual(results, [])

    def test_case_sensitive_matches_exact_case(self):
        self.write_text('f.py', 'password = "x"\n')
        scanner = self.make_scanner(case_sensitive=True)
        results = scanner.scan_directory(self.scan_dir)
        scanner.cleanup()
        self.assertGreater(len(results), 0)

    def test_result_contains_required_fields(self):
        self.write_text('f.py', 'password = "x"\n')
        results = self.scan()
        self.assertGreater(len(results), 0)
        r = results[0]
        for field in ('file', 'line_number', 'line_content', 'prohibited_word', 'position', 'match_type'):
            self.assertIn(field, r, msg=f"Missing field: {field}")

    def test_correct_line_number_reported(self):
        self.write_text('f.py', 'clean line\npassword = "x"\nclean again\n')
        results = self.scan()
        self.assertTrue(any(r['line_number'] == 2 for r in results))

    def test_multiple_words_same_line(self):
        self.write_text('f.py', 'password = secret\n')
        results = self.scan()
        words_found = {r['prohibited_word'] for r in results}
        self.assertGreaterEqual(len(words_found), 2)

    def test_violations_across_multiple_files(self):
        self.write_text('a.py', 'password = "x"\n')
        self.write_text('b.py', 'secret = "y"\n')
        results = self.scan()
        files_hit = {r['file'] for r in results}
        self.assertEqual(len(files_hit), 2)


# ══════════════════════════════════════════════════════════════════════════════
# Binary file detection
# ══════════════════════════════════════════════════════════════════════════════

class TestBinaryDetection(ScannerTestCase):

    def test_known_binary_extension_skipped(self):
        self.write_bytes('app.exe', b'password binary content here')
        self.assertEqual(self.scan(), [])

    def test_image_extension_skipped(self):
        self.write_bytes('logo.png', b'password\x89PNG\r\n')
        self.assertEqual(self.scan(), [])

    def test_null_byte_detected_as_binary(self):
        self.write_bytes('data.dat', b'password\x00more content')
        self.assertEqual(self.scan(), [])

    def test_plain_text_file_scanned(self):
        self.write_text('script.sh', '#!/bin/bash\npassword=hunter2\n')
        self.assertGreater(len(self.scan()), 0)

    def test_no_extension_text_file_scanned(self):
        self.write_text('Makefile', 'password = hunter2\n')
        self.assertGreater(len(self.scan()), 0)


# ══════════════════════════════════════════════════════════════════════════════
# File size limit
# ══════════════════════════════════════════════════════════════════════════════

class TestFileSizeLimit(ScannerTestCase):

    def test_file_over_limit_skipped(self):
        # Use max_file_size_mb=0 so even tiny files exceed the limit
        self.write_text('big.txt', 'password = "secret"\n' * 100)
        scanner = self.make_scanner(max_file_size_mb=0)
        results = scanner.scan_directory(self.scan_dir)
        scanner.cleanup()
        self.assertEqual(results, [])

    def test_file_under_limit_scanned(self):
        self.write_text('small.txt', 'password = "x"\n')
        self.assertGreater(len(self.scan()), 0)


# ══════════════════════════════════════════════════════════════════════════════
# ZIP archive scanning
# ══════════════════════════════════════════════════════════════════════════════

class TestZipScanning(ScannerTestCase):

    def test_zip_violation_detected(self):
        self.write_bytes('archive.zip', _make_zip({'config.py': DIRTY_CONTENT}))
        results = self.scan()
        self.assertGreater(len(results), 0)
        self.assertTrue(any(r['prohibited_word'] == 'password' for r in results))

    def test_clean_zip_no_violations(self):
        self.write_bytes('archive.zip', _make_zip({'readme.txt': CLEAN_CONTENT}))
        self.assertEqual(self.scan(), [])

    def test_zip_multiple_files_all_checked(self):
        self.write_bytes('archive.zip', _make_zip({
            'clean.txt':      CLEAN_CONTENT,
            'dirty.py':       DIRTY_CONTENT,
            'also_dirty.yaml': 'api_key: abc123\n',
        }))
        results = self.scan()
        words_found = {r['prohibited_word'] for r in results}
        self.assertIn('password', words_found)
        self.assertIn('api_key',  words_found)

    def test_zip_binary_member_skipped(self):
        # .exe member inside ZIP — should not crash and should return no results
        self.write_bytes('archive.zip', _make_zip({'app.exe': b'password\x00binary'}))
        results = self.scan()
        self.assertEqual(results, [])

    def test_nested_zip_violation_detected(self):
        """ZIP inside a ZIP: scanner must recurse into inner archive."""
        inner = _make_zip({'inner.py': DIRTY_CONTENT})
        outer = _make_zip({'inner.zip': inner})
        self.write_bytes('outer.zip', outer)
        results = self.scan()
        self.assertGreater(len(results), 0)

    def test_zip_and_plain_file_both_scanned(self):
        self.write_bytes('archive.zip', _make_zip({'f.py': DIRTY_CONTENT}))
        self.write_text('plain.py', 'api_key = "exposed"\n')
        results = self.scan()
        words_found = {r['prohibited_word'] for r in results}
        self.assertIn('password', words_found)
        self.assertIn('api_key',  words_found)


# ══════════════════════════════════════════════════════════════════════════════
# TAR archive scanning
# ══════════════════════════════════════════════════════════════════════════════

class TestTarScanning(ScannerTestCase):

    def test_tar_gz_violation_detected(self):
        self.write_bytes('archive.tgz', _make_tar({'config.py': DIRTY_CONTENT}, fmt='gz'))
        self.assertGreater(len(self.scan()), 0)

    def test_tar_bz2_violation_detected(self):
        self.write_bytes('archive.tar.bz2', _make_tar({'config.py': DIRTY_CONTENT}, fmt='bz2'))
        self.assertGreater(len(self.scan()), 0)

    def test_plain_tar_violation_detected(self):
        self.write_bytes('archive.tar', _make_tar({'config.py': DIRTY_CONTENT}, fmt=''))
        self.assertGreater(len(self.scan()), 0)

    def test_clean_tar_no_violations(self):
        self.write_bytes('archive.tgz', _make_tar({'readme.txt': CLEAN_CONTENT}))
        self.assertEqual(self.scan(), [])

    def test_tar_multiple_members(self):
        self.write_bytes('archive.tgz', _make_tar({
            'clean.txt': CLEAN_CONTENT,
            'dirty.py':  DIRTY_CONTENT,
        }))
        self.assertGreater(len(self.scan()), 0)


# ══════════════════════════════════════════════════════════════════════════════
# RPM archive scanning
# ══════════════════════════════════════════════════════════════════════════════

class TestRpmScanning(ScannerTestCase):

    @patch('subprocess.run')
    def test_rpm_content_scanned_when_extraction_succeeds(self, mock_run):
        """
        When rpm2cpio succeeds, any extracted files with prohibited words
        must be reported. The mock simulates a successful extraction by writing
        a file with a prohibited word into the extract directory.
        """
        import re

        def fake_rpm2cpio(cmd, **kwargs):
            if isinstance(cmd, str) and 'rpm2cpio' in cmd:
                m = re.match(r'^cd (.+) && rpm2cpio', cmd)
                if m:
                    extract_dir = m.group(1)
                    os.makedirs(extract_dir, exist_ok=True)
                    with open(os.path.join(extract_dir, 'app.conf'), 'w') as f:
                        f.write(DIRTY_CONTENT)
            result = MagicMock()
            result.returncode = 0
            return result

        mock_run.side_effect = fake_rpm2cpio
        self.write_bytes('package.rpm', b'fake rpm payload')
        results = self.scan()
        self.assertGreater(len(results), 0)

    @patch('subprocess.run')
    def test_rpm_extraction_failure_is_graceful(self, mock_run):
        """If rpm2cpio returns non-zero the scanner must not raise an exception."""
        mock_run.return_value = MagicMock(returncode=1)
        self.write_bytes('package.rpm', b'fake rpm payload')
        try:
            results = self.scan()
        except Exception as exc:
            self.fail(f"Scanner raised on RPM failure: {exc}")

    def test_zipped_rpm_does_not_crash(self):
        """
        A ZIP containing a .rpm entry must not crash the scanner regardless
        of whether rpm2cpio is installed.
        """
        self.write_bytes('release.zip', _make_zip({'package.rpm': b'fake rpm payload'}))
        try:
            self.scan()
        except Exception as exc:
            self.fail(f"Scanning ZIP-with-RPM raised: {exc}")

    @patch('subprocess.run')
    def test_zipped_rpm_content_found(self, mock_run):
        """
        A ZIP containing an RPM whose extracted content has prohibited words
        must be reported (ZIP → RPM → extracted file → violation).
        """
        import re

        def fake_rpm2cpio(cmd, **kwargs):
            if isinstance(cmd, str) and 'rpm2cpio' in cmd:
                m = re.match(r'^cd (.+) && rpm2cpio', cmd)
                if m:
                    extract_dir = m.group(1)
                    os.makedirs(extract_dir, exist_ok=True)
                    with open(os.path.join(extract_dir, 'app.conf'), 'w') as f:
                        f.write(DIRTY_CONTENT)
            result = MagicMock()
            result.returncode = 0
            return result

        mock_run.side_effect = fake_rpm2cpio
        self.write_bytes('release.zip', _make_zip({'package.rpm': b'fake rpm payload'}))
        results = self.scan()
        self.assertGreater(len(results), 0)


# ══════════════════════════════════════════════════════════════════════════════
# Excluded paths
# ══════════════════════════════════════════════════════════════════════════════

class TestExcludedPaths(ScannerTestCase):

    def test_excluded_directory_not_scanned(self):
        excl_dir = os.path.join(self.scan_dir, 'config')
        os.makedirs(excl_dir)
        self.write_text('config/settings.py', DIRTY_CONTENT)   # inside excluded dir
        self.write_text('src/clean.py',        CLEAN_CONTENT)
        scanner = self.make_scanner(excluded_paths=[excl_dir])
        results = scanner.scan_directory(self.scan_dir)
        scanner.cleanup()
        self.assertEqual(results, [])

    def test_excluded_file_not_scanned(self):
        excl_file = self.write_text('secrets.env', DIRTY_CONTENT)
        self.write_text('normal.py', CLEAN_CONTENT)
        scanner = self.make_scanner(excluded_paths=[excl_file])
        results = scanner.scan_directory(self.scan_dir)
        scanner.cleanup()
        self.assertEqual(results, [])

    def test_non_excluded_files_still_scanned(self):
        excl_dir = os.path.join(self.scan_dir, 'skip_me')
        os.makedirs(excl_dir)
        self.write_text('skip_me/ignore.py', CLEAN_CONTENT)
        self.write_text('scan_me.py',         DIRTY_CONTENT)
        scanner = self.make_scanner(excluded_paths=[excl_dir])
        results = scanner.scan_directory(self.scan_dir)
        scanner.cleanup()
        self.assertGreater(len(results), 0)
        # Nothing from excluded dir must appear
        self.assertFalse(any('skip_me' in r['file'] for r in results))

    def test_zip_inside_excluded_dir_not_scanned(self):
        """Archives in excluded directories should also be skipped."""
        excl_dir = os.path.join(self.scan_dir, 'vendor')
        os.makedirs(excl_dir)
        zip_path = os.path.join(excl_dir, 'lib.zip')
        with open(zip_path, 'wb') as f:
            f.write(_make_zip({'internal.py': DIRTY_CONTENT}))
        scanner = self.make_scanner(excluded_paths=[excl_dir])
        results = scanner.scan_directory(self.scan_dir)
        scanner.cleanup()
        self.assertEqual(results, [])


# ══════════════════════════════════════════════════════════════════════════════
# Directory scanning behaviour
# ══════════════════════════════════════════════════════════════════════════════

class TestDirectoryScanning(ScannerTestCase):

    def test_recursive_scan_finds_nested_files(self):
        self.write_text('a/b/c/deep.py', DIRTY_CONTENT)
        self.assertGreater(len(self.scan()), 0)

    def test_non_recursive_skips_subdirectories(self):
        # Violation only in subdir — non-recursive scan must miss it
        self.write_text('subdir/file.py', DIRTY_CONTENT)
        scanner = self.make_scanner()
        results = scanner.scan_directory(self.scan_dir, recursive=False)
        scanner.cleanup()
        self.assertEqual(results, [])

    def test_non_recursive_scans_root_files(self):
        # Violation at root level — non-recursive scan must find it
        self.write_text('root.py', DIRTY_CONTENT)
        scanner = self.make_scanner()
        results = scanner.scan_directory(self.scan_dir, recursive=False)
        scanner.cleanup()
        self.assertGreater(len(results), 0)

    def test_temp_dirs_cleaned_up_after_archive_scan(self):
        self.write_bytes('archive.zip', _make_zip({'f.py': DIRTY_CONTENT}))
        scanner = self.make_scanner()
        scanner.scan_directory(self.scan_dir)
        saved_temps = list(scanner.temp_dirs)
        scanner.cleanup()
        for d in saved_temps:
            self.assertFalse(os.path.exists(d), f"Temp dir leaked: {d}")

    def test_format_results_no_violations(self):
        scanner = self.make_scanner()
        output = scanner.format_results([])
        self.assertIn('No prohibited words found', output)

    def test_format_results_with_violations(self):
        self.write_text('f.py', DIRTY_CONTENT)
        scanner = self.make_scanner()
        results = scanner.scan_directory(self.scan_dir)
        scanner.cleanup()
        output = scanner.format_results(results)
        self.assertIn('violation', output.lower())


# ══════════════════════════════════════════════════════════════════════════════
# Partial (substring) match detection
# ══════════════════════════════════════════════════════════════════════════════

class TestPartialMatching(ScannerTestCase):
    """
    Verifies that words appearing as substrings of larger tokens are reported
    separately from whole-word matches, each with the correct match_type.
    """

    def test_standalone_word_is_exact(self):
        self.write_text('f.py', 'password = "x"\n')
        results = self.scan()
        exact = [r for r in results if r.get('match_type') == 'exact']
        self.assertGreater(len(exact), 0)
        self.assertTrue(any(r['prohibited_word'] == 'password' for r in exact))

    def test_embedded_word_is_partial(self):
        # 'password' is a substring of 'passwordmanager' — should be partial
        self.write_text('f.py', 'passwordmanager = "x"\n')
        results = self.scan()
        partial = [r for r in results if r.get('match_type') == 'partial']
        self.assertGreater(len(partial), 0)
        self.assertTrue(any(r['prohibited_word'] == 'password' for r in partial))

    def test_embedded_word_is_not_exact(self):
        self.write_text('f.py', 'passwordmanager = "x"\n')
        results = self.scan()
        exact = [r for r in results if r.get('match_type') == 'exact']
        self.assertFalse(any(r['prohibited_word'] == 'password' for r in exact))

    def test_both_exact_and_partial_on_same_line(self):
        # 'password' standalone (exact) and inside 'passwordmanager' (partial)
        self.write_text('f.py', 'password = passwordmanager\n')
        results = self.scan()
        pw = [r for r in results if r['prohibited_word'] == 'password']
        types = {r['match_type'] for r in pw}
        self.assertIn('exact',   types, "Standalone 'password' should be exact")
        self.assertIn('partial', types, "Embedded 'password' in 'passwordmanager' should be partial")

    def test_no_position_duplicate(self):
        # Each position should appear at most once per word on a given line
        self.write_text('f.py', 'password = "x"\n')
        results = self.scan()
        pw = [r for r in results if r['prohibited_word'] == 'password']
        positions = [r['position'] for r in pw]
        self.assertEqual(len(positions), len(set(positions)))

    def test_match_type_field_on_all_results(self):
        self.write_text('f.py', 'password = passwordmanager\n')
        results = self.scan()
        self.assertGreater(len(results), 0)
        for r in results:
            self.assertIn('match_type', r)
            self.assertIn(r['match_type'], ('exact', 'partial'))

    def test_underscore_embedded_word_is_partial(self):
        # 'secret' inside 'old_secret_value' — underscore is a word char so no \b
        self.write_text('f.py', 'old_secret_value = 1\n')
        results = self.scan()
        partial = [r for r in results
                   if r.get('match_type') == 'partial' and r['prohibited_word'] == 'secret']
        self.assertGreater(len(partial), 0)

    def test_custom_word_binary_example(self):
        """User's example: 'bin' should match inside 'binary' as a partial match."""
        bin_words = os.path.join(self.tmpdir, 'bin_words.txt')
        with open(bin_words, 'w') as f:
            f.write('bin\n')
        cfg_path = self._write_config(prohibited_words_file=bin_words)
        scanner = ProhibitedWordScanner(cfg_path)

        self.write_text('f.py', 'mode = binary\n')
        results = scanner.scan_directory(self.scan_dir)
        scanner.cleanup()

        partial = [r for r in results if r.get('match_type') == 'partial']
        self.assertGreater(len(partial), 0)
        self.assertTrue(any(r['prohibited_word'] == 'bin' for r in partial))

    def test_custom_word_exact_standalone(self):
        """'bin' as a standalone word should be exact, not partial."""
        bin_words = os.path.join(self.tmpdir, 'bin_words.txt')
        with open(bin_words, 'w') as f:
            f.write('bin\n')
        cfg_path = self._write_config(prohibited_words_file=bin_words)
        scanner = ProhibitedWordScanner(cfg_path)

        self.write_text('f.py', 'path = "/usr/bin"\n')
        results = scanner.scan_directory(self.scan_dir)
        scanner.cleanup()

        exact = [r for r in results if r.get('match_type') == 'exact']
        self.assertGreater(len(exact), 0)
        self.assertTrue(any(r['prohibited_word'] == 'bin' for r in exact))

    def test_format_results_shows_exact_section(self):
        self.write_text('f.py', 'password = "x"\n')
        scanner = self.make_scanner()
        results = scanner.scan_directory(self.scan_dir)
        scanner.cleanup()
        output = scanner.format_results(results)
        self.assertIn('EXACT', output.upper())

    def test_format_results_shows_partial_section(self):
        self.write_text('f.py', 'passwordmanager = "x"\n')
        scanner = self.make_scanner()
        results = scanner.scan_directory(self.scan_dir)
        scanner.cleanup()
        output = scanner.format_results(results)
        self.assertIn('PARTIAL', output.upper())

    def test_format_results_summary_counts(self):
        # One exact + one partial on same line → summary should reflect both
        self.write_text('f.py', 'password = passwordmanager\n')
        scanner = self.make_scanner()
        results = scanner.scan_directory(self.scan_dir)
        scanner.cleanup()
        output = scanner.format_results(results)
        self.assertRegex(output, r'\d+ exact')
        self.assertRegex(output, r'\d+ partial')


if __name__ == '__main__':
    unittest.main()
