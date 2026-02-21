"""
Comprehensive tests for the web API (src/web.py).

All scan operations use source_type=zip so tests never need a network
connection or a real git server.
"""
import io
import json
import os
import shutil
import tempfile
import unittest
import zipfile

from src import web as web_module
from src.web import app, _check_ssrf, _normalize_git_url


# ── Helpers ────────────────────────────────────────────────────────────────

WORDS_FILE_BYTES = b'password\nsecret\napi_key\n'
DIRTY_PY         = b"password = 'hunter2'\n"
CLEAN_PY         = b'x = 1  # nothing sensitive here\n'


def _make_zip(files: dict) -> bytes:
    """Build a ZIP in memory. files: {name: str|bytes}"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        for name, content in files.items():
            if isinstance(content, str):
                content = content.encode()
            zf.writestr(name, content)
    return buf.getvalue()


# ── Base test case ─────────────────────────────────────────────────────────

class WebTestCase(unittest.TestCase):
    def setUp(self):
        app.config['TESTING'] = True
        self.client  = app.test_client()
        self.tmpdir  = tempfile.mkdtemp(prefix='web_test_')
        # Isolate scan_history so tests don't bleed into one another
        web_module.scan_history.clear()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        web_module.scan_history.clear()

    def _scan_zip(self, zip_bytes: bytes, words: bytes = WORDS_FILE_BYTES,
                  **extra) -> object:
        """POST a ZIP-upload scan and return the Flask response."""
        data = {
            'source_type':          'zip',
            'config_source_type':   'upload',
            'zip_file':             (io.BytesIO(zip_bytes), 'test.zip'),
            'prohibited_words_file': (io.BytesIO(words),   'words.txt'),
            'case_sensitive':        'false',
            'max_file_size_mb':      '10',
            **extra,
        }
        return self.client.post('/api/scan', data=data,
                                content_type='multipart/form-data')


# ══════════════════════════════════════════════════════════════════════════════
# Core endpoint availability
# ══════════════════════════════════════════════════════════════════════════════

class TestEndpoints(WebTestCase):

    def test_index_page_loads(self):
        r = self.client.get('/')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Repository Scanner', r.data)

    def test_history_endpoint_returns_list(self):
        r = self.client.get('/api/history')
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.get_json(), list)

    def test_get_scan_not_found_returns_404(self):
        r = self.client.get('/api/scan/99999')
        self.assertEqual(r.status_code, 404)

    def test_export_not_found_returns_404(self):
        r = self.client.get('/api/export/99999')
        self.assertEqual(r.status_code, 404)


# ══════════════════════════════════════════════════════════════════════════════
# Input validation
# ══════════════════════════════════════════════════════════════════════════════

class TestInputValidation(WebTestCase):

    def test_missing_repo_url_returns_400(self):
        r = self.client.post('/api/scan', data={
            'source_type':           'git',
            'config_source_type':    'upload',
            'prohibited_words_file': (io.BytesIO(WORDS_FILE_BYTES), 'words.txt'),
        }, content_type='multipart/form-data')
        self.assertEqual(r.status_code, 400)

    def test_missing_zip_file_returns_400(self):
        r = self.client.post('/api/scan', data={
            'source_type':           'zip',
            'config_source_type':    'upload',
            'prohibited_words_file': (io.BytesIO(WORDS_FILE_BYTES), 'words.txt'),
        }, content_type='multipart/form-data')
        self.assertEqual(r.status_code, 400)

    def test_missing_words_upload_returns_400(self):
        r = self.client.post('/api/scan', data={
            'source_type':        'zip',
            'config_source_type': 'upload',
            'zip_file':           (io.BytesIO(_make_zip({'f.py': CLEAN_PY})), 'test.zip'),
        }, content_type='multipart/form-data')
        self.assertIn(r.status_code, [400, 500])

    def test_missing_server_path_returns_400(self):
        r = self.client.post('/api/scan', data={
            'source_type':        'zip',
            'config_source_type': 'server_path',
            'zip_file':           (io.BytesIO(_make_zip({'f.py': CLEAN_PY})), 'test.zip'),
            # config_server_path intentionally omitted
        }, content_type='multipart/form-data')
        self.assertEqual(r.status_code, 400)

    def test_missing_config_git_url_returns_400(self):
        r = self.client.post('/api/scan', data={
            'source_type':        'zip',
            'config_source_type': 'git_repo',
            'zip_file':           (io.BytesIO(_make_zip({'f.py': CLEAN_PY})), 'test.zip'),
            # config_git_url intentionally omitted
        }, content_type='multipart/form-data')
        self.assertEqual(r.status_code, 400)

    def test_invalid_git_scheme_returns_error(self):
        r = self.client.post('/api/scan', data={
            'source_type':           'git',
            'config_source_type':    'upload',
            'repo_url':              'ftp://example.com/repo',
            'prohibited_words_file': (io.BytesIO(WORDS_FILE_BYTES), 'words.txt'),
        }, content_type='multipart/form-data')
        self.assertIn(r.status_code, [400, 500])
        self.assertIn('error', r.get_json())


# ══════════════════════════════════════════════════════════════════════════════
# ZIP upload scanning
# ══════════════════════════════════════════════════════════════════════════════

class TestZipScan(WebTestCase):

    def test_clean_zip_zero_violations(self):
        r = self._scan_zip(_make_zip({'readme.md': CLEAN_PY}))
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(data['total_violations'], 0)

    def test_dirty_zip_reports_violations(self):
        r = self._scan_zip(_make_zip({'config.py': DIRTY_PY}))
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertTrue(data['success'])
        self.assertGreater(data['total_violations'], 0)

    def test_violation_result_fields_present(self):
        r = self._scan_zip(_make_zip({'app.py': DIRTY_PY}))
        data = r.get_json()
        self.assertGreater(len(data['results']), 0)
        result = data['results'][0]
        for field in ('file', 'line_number', 'line_content', 'prohibited_word'):
            self.assertIn(field, result, msg=f"Missing field: {field}")

    def test_nested_zip_violations_found(self):
        """Violations inside a ZIP-within-a-ZIP must be detected."""
        inner = _make_zip({'inner.py': DIRTY_PY})
        outer = _make_zip({'inner.zip': inner})
        r = self._scan_zip(outer)
        self.assertEqual(r.status_code, 200)
        self.assertGreater(r.get_json()['total_violations'], 0)

    def test_zip_with_only_binary_no_violations(self):
        zip_bytes = _make_zip({'app.exe': b'password\x00binary content'})
        r = self._scan_zip(zip_bytes)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()['total_violations'], 0)

    def test_case_insensitive_scan_catches_uppercase_words(self):
        r = self._scan_zip(_make_zip({'f.py': b'PASSWORD = "x"\n'}),
                           case_sensitive='false')
        self.assertGreater(r.get_json()['total_violations'], 0)

    def test_case_sensitive_scan_ignores_uppercase_words(self):
        r = self._scan_zip(_make_zip({'f.py': b'PASSWORD = "x"\n'}),
                           case_sensitive='true')
        self.assertEqual(r.get_json()['total_violations'], 0)

    def test_has_more_flag_when_over_100_results(self):
        # 101 files each with one violation
        files = {f'file_{i}.py': DIRTY_PY for i in range(101)}
        r = self._scan_zip(_make_zip(files))
        data = r.get_json()
        self.assertTrue(data['has_more'])
        self.assertEqual(len(data['results']), 100)

    def test_no_has_more_flag_when_under_limit(self):
        r = self._scan_zip(_make_zip({'one.py': DIRTY_PY}))
        self.assertFalse(r.get_json()['has_more'])


# ══════════════════════════════════════════════════════════════════════════════
# Config — server path source
# ══════════════════════════════════════════════════════════════════════════════

class TestConfigServerPath(WebTestCase):

    def _post_with_server_path(self, path_value: str, zip_bytes: bytes):
        return self.client.post('/api/scan', data={
            'source_type':        'zip',
            'config_source_type': 'server_path',
            'zip_file':           (io.BytesIO(zip_bytes), 'test.zip'),
            'config_server_path': path_value,
        }, content_type='multipart/form-data')

    def test_valid_file_path_works(self):
        words_file = os.path.join(self.tmpdir, 'words.txt')
        with open(words_file, 'w') as f:
            f.write('password\n')
        r = self._post_with_server_path(words_file, _make_zip({'f.py': DIRTY_PY}))
        self.assertEqual(r.status_code, 200)
        self.assertGreater(r.get_json()['total_violations'], 0)

    def test_valid_folder_path_works(self):
        cfg_dir = os.path.join(self.tmpdir, 'cfg')
        os.makedirs(cfg_dir)
        with open(os.path.join(cfg_dir, 'prohibited_words.txt'), 'w') as f:
            f.write('password\n')
        r = self._post_with_server_path(cfg_dir, _make_zip({'f.py': DIRTY_PY}))
        self.assertEqual(r.status_code, 200)
        self.assertGreater(r.get_json()['total_violations'], 0)

    def test_nonexistent_path_returns_400(self):
        r = self._post_with_server_path('/nonexistent/does/not/exist.txt',
                                        _make_zip({'f.py': DIRTY_PY}))
        self.assertEqual(r.status_code, 400)

    def test_folder_without_words_file_returns_400(self):
        empty_dir = os.path.join(self.tmpdir, 'empty')
        os.makedirs(empty_dir)
        r = self._post_with_server_path(empty_dir, _make_zip({'f.py': DIRTY_PY}))
        self.assertEqual(r.status_code, 400)

    def test_server_words_file_not_deleted_after_scan(self):
        """The server's own words file must never be deleted post-scan."""
        words_file = os.path.join(self.tmpdir, 'persistent_words.txt')
        with open(words_file, 'w') as f:
            f.write('password\n')
        self._post_with_server_path(words_file, _make_zip({'f.py': DIRTY_PY}))
        self.assertTrue(os.path.exists(words_file),
                        "Server words file was deleted after scan")


# ══════════════════════════════════════════════════════════════════════════════
# Scan history and export
# ══════════════════════════════════════════════════════════════════════════════

class TestScanHistory(WebTestCase):

    def test_scan_appends_to_history(self):
        self._scan_zip(_make_zip({'f.py': CLEAN_PY}))
        history = self.client.get('/api/history').get_json()
        self.assertEqual(len(history), 1)

    def test_multiple_scans_all_in_history(self):
        self._scan_zip(_make_zip({'a.py': CLEAN_PY}))
        self._scan_zip(_make_zip({'b.py': DIRTY_PY}))
        history = self.client.get('/api/history').get_json()
        self.assertEqual(len(history), 2)

    def test_history_entry_has_required_fields(self):
        self._scan_zip(_make_zip({'f.py': DIRTY_PY}))
        entry = self.client.get('/api/history').get_json()[0]
        for field in ('id', 'timestamp', 'repo_path', 'total_violations'):
            self.assertIn(field, entry)

    def test_get_scan_by_id_returns_full_results(self):
        r = self._scan_zip(_make_zip({'f.py': DIRTY_PY}))
        scan_id = r.get_json()['scan_id']
        detail = self.client.get(f'/api/scan/{scan_id}').get_json()
        self.assertIn('results', detail)
        self.assertIn('total_violations', detail)

    def test_export_returns_downloadable_json(self):
        r = self._scan_zip(_make_zip({'f.py': DIRTY_PY}))
        scan_id = r.get_json()['scan_id']
        export_r = self.client.get(f'/api/export/{scan_id}')
        self.assertEqual(export_r.status_code, 200)
        self.assertIn('application/json', export_r.content_type)
        parsed = json.loads(export_r.data)
        self.assertIn('results', parsed)

    def test_export_contains_all_results(self):
        """Export must include the full result set, not just the first 100."""
        files = {f'file_{i}.py': DIRTY_PY for i in range(101)}
        r = self._scan_zip(_make_zip(files))
        scan_id = r.get_json()['scan_id']
        export_r = self.client.get(f'/api/export/{scan_id}')
        parsed = json.loads(export_r.data)
        self.assertGreater(parsed['total_violations'], 100)
        self.assertEqual(len(parsed['results']), parsed['total_violations'])

    def test_source_type_recorded_in_history(self):
        self._scan_zip(_make_zip({'f.py': CLEAN_PY}))
        entry = self.client.get('/api/history').get_json()[0]
        self.assertEqual(entry.get('source_type'), 'zip')


# ══════════════════════════════════════════════════════════════════════════════
# SSRF protection — _check_ssrf() function
# ══════════════════════════════════════════════════════════════════════════════

class TestSsrfProtection(unittest.TestCase):
    """
    Tests use literal IP addresses so they work offline (no DNS lookup needed
    for the blocked cases).
    """

    def _assert_blocked(self, url: str):
        with self.assertRaises(ValueError, msg=f"Expected {url!r} to be blocked"):
            _check_ssrf(url)

    def test_loopback_127_blocked(self):
        self._assert_blocked('http://127.0.0.1/repo')

    def test_loopback_localhost_blocked(self):
        # localhost resolves to 127.0.0.1 or ::1 — both are loopback
        self._assert_blocked('http://localhost/repo')

    def test_private_class_a_blocked(self):
        self._assert_blocked('http://10.0.0.1/repo')

    def test_private_class_b_blocked(self):
        self._assert_blocked('http://172.16.0.1/repo')

    def test_private_class_c_blocked(self):
        self._assert_blocked('http://192.168.1.100/repo')

    def test_link_local_blocked(self):
        # AWS/GCP metadata endpoint
        self._assert_blocked('http://169.254.169.254/latest/meta-data/')

    def test_ipv6_loopback_blocked(self):
        self._assert_blocked('http://[::1]/repo')

    def test_ssrf_via_api_git_source(self):
        """SSRF via the scan API endpoint (git source) must be rejected."""
        app.config['TESTING'] = True
        client = app.test_client()
        r = client.post('/api/scan', data={
            'source_type':           'git',
            'config_source_type':    'upload',
            'repo_url':              'http://127.0.0.1/internal-repo',
            'prohibited_words_file': (io.BytesIO(b'password\n'), 'words.txt'),
        }, content_type='multipart/form-data')
        self.assertIn(r.status_code, [400, 500])
        self.assertIn('error', r.get_json())

    def test_ssrf_via_api_artifactory_source(self):
        """SSRF via the scan API endpoint (Artifactory source) must be rejected."""
        app.config['TESTING'] = True
        client = app.test_client()
        r = client.post('/api/scan', data={
            'source_type':           'artifactory',
            'config_source_type':    'upload',
            'repo_url':              'http://10.0.0.1/artifactory/libs-release/app.zip',
            'prohibited_words_file': (io.BytesIO(b'password\n'), 'words.txt'),
        }, content_type='multipart/form-data')
        self.assertIn(r.status_code, [400, 500])
        self.assertIn('error', r.get_json())


# ══════════════════════════════════════════════════════════════════════════════
# URL normalisation for same-repo detection
# ══════════════════════════════════════════════════════════════════════════════

class TestNormalizeGitUrl(unittest.TestCase):

    def test_strips_git_suffix(self):
        self.assertEqual(
            _normalize_git_url('https://github.com/org/repo.git'),
            'https://github.com/org/repo',
        )

    def test_strips_trailing_slash(self):
        self.assertEqual(
            _normalize_git_url('https://github.com/org/repo/'),
            'https://github.com/org/repo',
        )

    def test_lowercases_url(self):
        self.assertEqual(
            _normalize_git_url('https://GitHub.COM/Org/Repo'),
            'https://github.com/org/repo',
        )

    def test_git_suffix_and_slash_both_stripped(self):
        self.assertEqual(
            _normalize_git_url('https://github.com/org/repo.git/'),
            'https://github.com/org/repo',
        )

    def test_same_url_different_forms_match(self):
        a = _normalize_git_url('https://github.com/org/repo.git')
        b = _normalize_git_url('https://github.com/org/repo/')
        self.assertEqual(a, b)

    def test_different_repos_do_not_match(self):
        a = _normalize_git_url('https://github.com/org/repo-a')
        b = _normalize_git_url('https://github.com/org/repo-b')
        self.assertNotEqual(a, b)


if __name__ == '__main__':
    unittest.main()
