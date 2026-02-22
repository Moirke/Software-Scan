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

    def test_health_endpoint_returns_200(self):
        r = self.client.get('/health')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json().get('status'), 'ok')

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
        for field in ('file', 'line_number', 'line_content', 'prohibited_word', 'match_type'):
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


# ══════════════════════════════════════════════════════════════════════════════
# Partial match classification via the web API
# ══════════════════════════════════════════════════════════════════════════════

class TestPartialMatchWeb(WebTestCase):

    def test_response_includes_violation_type_counts(self):
        r = self._scan_zip(_make_zip({'f.py': DIRTY_PY}))
        data = r.get_json()
        self.assertIn('exact_violations',   data)
        self.assertIn('partial_violations', data)

    def test_exact_plus_partial_equals_total(self):
        r = self._scan_zip(_make_zip({'f.py': DIRTY_PY}))
        data = r.get_json()
        self.assertEqual(
            data['exact_violations'] + data['partial_violations'],
            data['total_violations'],
        )

    def test_results_include_match_type_field(self):
        r = self._scan_zip(_make_zip({'f.py': DIRTY_PY}))
        data = r.get_json()
        for result in data['results']:
            self.assertIn('match_type', result)
            self.assertIn(result['match_type'], ('exact', 'partial'))

    def test_standalone_word_counted_as_exact(self):
        # 'password = ...' — the word is standalone, should be exact
        r = self._scan_zip(_make_zip({'f.py': DIRTY_PY}))
        data = r.get_json()
        self.assertGreater(data['exact_violations'], 0)

    def test_embedded_word_counted_as_partial(self):
        # 'passwordmanager' — 'password' is a substring
        content = b'config = passwordmanager_settings\n'
        r = self._scan_zip(_make_zip({'f.py': content}))
        data = r.get_json()
        self.assertGreater(data['partial_violations'], 0)
        self.assertEqual(data['exact_violations'], 0)

    def test_both_types_on_same_scan(self):
        content = b'password = passwordmanager\n'
        r = self._scan_zip(_make_zip({'f.py': content}))
        data = r.get_json()
        self.assertGreater(data['exact_violations'],   0)
        self.assertGreater(data['partial_violations'], 0)

    def test_export_includes_match_type_in_results(self):
        r = self._scan_zip(_make_zip({'f.py': DIRTY_PY}))
        scan_id = r.get_json()['scan_id']
        export_r = self.client.get(f'/api/export/{scan_id}')
        import json
        parsed = json.loads(export_r.data)
        self.assertGreater(len(parsed['results']), 0)
        for result in parsed['results']:
            self.assertIn('match_type', result)


# ══════════════════════════════════════════════════════════════════════════════
# PDF report export
# ══════════════════════════════════════════════════════════════════════════════

class TestMetrics(WebTestCase):

    def setUp(self):
        super().setUp()
        # Reset all metric counters between tests
        import src.metrics as m
        with m._lock:
            m._page_views      = 0
            m._scans_started   = 0
            m._scans_completed = 0
            m._scans_failed    = 0
            m._total_violations = 0
            m._source_counts.clear()
            m._scans_by_date.clear()
            m._durations_ms.clear()

    def test_metrics_endpoint_returns_200(self):
        r = self.client.get('/api/metrics')
        self.assertEqual(r.status_code, 200)

    def test_metrics_response_has_required_fields(self):
        r = self.client.get('/api/metrics')
        data = r.get_json()
        for field in ('page_views', 'scans_today', 'scans_started',
                      'scans_completed', 'scans_failed', 'success_rate_pct',
                      'avg_scan_duration_ms', 'total_violations_found',
                      'source_type_counts', 'scans_last_7_days', 'server_start'):
            self.assertIn(field, data, msg=f'Missing metrics field: {field}')

    def test_page_view_increments_on_index(self):
        self.client.get('/')
        self.client.get('/')
        data = self.client.get('/api/metrics').get_json()
        self.assertEqual(data['page_views'], 2)

    def test_scan_increments_started_and_completed(self):
        self._scan_zip(_make_zip({'f.py': DIRTY_PY}))
        data = self.client.get('/api/metrics').get_json()
        self.assertEqual(data['scans_started'],  1)
        self.assertEqual(data['scans_completed'], 1)
        self.assertEqual(data['scans_failed'],    0)

    def test_scans_today_increments(self):
        self._scan_zip(_make_zip({'f.py': CLEAN_PY}))
        self._scan_zip(_make_zip({'f.py': DIRTY_PY}))
        data = self.client.get('/api/metrics').get_json()
        self.assertEqual(data['scans_today'], 2)

    def test_violations_counted(self):
        self._scan_zip(_make_zip({'f.py': DIRTY_PY}))
        data = self.client.get('/api/metrics').get_json()
        self.assertGreater(data['total_violations_found'], 0)

    def test_source_type_counted(self):
        self._scan_zip(_make_zip({'f.py': CLEAN_PY}))
        data = self.client.get('/api/metrics').get_json()
        self.assertEqual(data['source_type_counts'].get('zip', 0), 1)

    def test_avg_duration_present_after_scan(self):
        self._scan_zip(_make_zip({'f.py': CLEAN_PY}))
        data = self.client.get('/api/metrics').get_json()
        self.assertGreaterEqual(data['avg_scan_duration_ms'], 0)

    def test_success_rate_100_when_all_succeed(self):
        self._scan_zip(_make_zip({'f.py': CLEAN_PY}))
        self._scan_zip(_make_zip({'f.py': DIRTY_PY}))
        data = self.client.get('/api/metrics').get_json()
        self.assertEqual(data['success_rate_pct'], 100.0)

    def test_validation_errors_do_not_increment_started(self):
        # Missing zip file — fails validation before scan starts
        self.client.post('/api/scan', data={
            'source_type':           'zip',
            'config_source_type':    'upload',
            'prohibited_words_file': (io.BytesIO(WORDS_FILE_BYTES), 'w.txt'),
        }, content_type='multipart/form-data')
        data = self.client.get('/api/metrics').get_json()
        self.assertEqual(data['scans_started'], 0)

    def test_scans_last_7_days_populated(self):
        self._scan_zip(_make_zip({'f.py': CLEAN_PY}))
        data = self.client.get('/api/metrics').get_json()
        self.assertGreater(len(data['scans_last_7_days']), 0)


class TestPdfExport(WebTestCase):

    def test_pdf_endpoint_returns_200(self):
        r = self._scan_zip(_make_zip({'f.py': DIRTY_PY}))
        scan_id = r.get_json()['scan_id']
        pdf_r = self.client.get(f'/api/export/{scan_id}/pdf')
        self.assertEqual(pdf_r.status_code, 200)

    def test_pdf_content_type(self):
        r = self._scan_zip(_make_zip({'f.py': DIRTY_PY}))
        scan_id = r.get_json()['scan_id']
        pdf_r = self.client.get(f'/api/export/{scan_id}/pdf')
        self.assertIn('application/pdf', pdf_r.content_type)

    def test_pdf_bytes_start_with_magic(self):
        r = self._scan_zip(_make_zip({'f.py': DIRTY_PY}))
        scan_id = r.get_json()['scan_id']
        pdf_r = self.client.get(f'/api/export/{scan_id}/pdf')
        self.assertTrue(pdf_r.data.startswith(b'%PDF'),
                        'Response does not look like a PDF')

    def test_pdf_for_clean_repo(self):
        """A clean scan should also produce a valid PDF."""
        r = self._scan_zip(_make_zip({'f.py': CLEAN_PY}))
        scan_id = r.get_json()['scan_id']
        pdf_r = self.client.get(f'/api/export/{scan_id}/pdf')
        self.assertEqual(pdf_r.status_code, 200)
        self.assertTrue(pdf_r.data.startswith(b'%PDF'))

    def test_pdf_404_for_invalid_scan(self):
        pdf_r = self.client.get('/api/export/99999/pdf')
        self.assertEqual(pdf_r.status_code, 404)

    def test_generate_pdf_with_violations(self):
        from src.report import generate_pdf
        record = {
            'id': 0,
            'timestamp': '2026-02-21T12:00:00',
            'repo_path': 'https://github.com/test/repo',
            'source_type': 'git',
            'case_sensitive': False,
            'max_file_size_mb': 10,
            'words_evaluated': ['password', 'secret'],
            'total_violations': 2,
            'exact_violations': 1,
            'partial_violations': 1,
            'results': [
                {
                    'file': '/tmp/repo/app.py',
                    'line_number': 5,
                    'line_content': 'password = "hunter2"',
                    'prohibited_word': 'password',
                    'position': 0,
                    'match_type': 'exact',
                },
                {
                    'file': '/tmp/repo/config.py',
                    'line_number': 12,
                    'line_content': 'passwordmanager = "x"',
                    'prohibited_word': 'password',
                    'position': 0,
                    'match_type': 'partial',
                },
            ],
        }
        pdf_bytes = generate_pdf(record)
        self.assertIsInstance(pdf_bytes, bytes)
        self.assertTrue(pdf_bytes.startswith(b'%PDF'))

    def test_generate_pdf_with_no_violations(self):
        from src.report import generate_pdf
        record = {
            'id': 1,
            'timestamp': '2026-02-21T12:00:00',
            'repo_path': 'https://github.com/test/repo',
            'source_type': 'zip',
            'case_sensitive': False,
            'max_file_size_mb': 10,
            'words_evaluated': ['password', 'secret'],
            'total_violations': 0,
            'exact_violations': 0,
            'partial_violations': 0,
            'results': [],
        }
        pdf_bytes = generate_pdf(record)
        self.assertIsInstance(pdf_bytes, bytes)
        self.assertTrue(pdf_bytes.startswith(b'%PDF'))

    def test_scan_record_includes_words_evaluated(self):
        """words_evaluated must be stored in the scan record for the PDF report."""
        r = self._scan_zip(_make_zip({'f.py': DIRTY_PY}))
        scan_id = r.get_json()['scan_id']
        detail = self.client.get(f'/api/scan/{scan_id}').get_json()
        self.assertIn('words_evaluated', detail)
        self.assertIsInstance(detail['words_evaluated'], list)
        self.assertGreater(len(detail['words_evaluated']), 0)

    def test_scan_record_includes_case_sensitive_and_file_size(self):
        r = self._scan_zip(_make_zip({'f.py': DIRTY_PY}))
        scan_id = r.get_json()['scan_id']
        detail = self.client.get(f'/api/scan/{scan_id}').get_json()
        self.assertIn('case_sensitive',   detail)
        self.assertIn('max_file_size_mb', detail)


# ══════════════════════════════════════════════════════════════════════════════
# Scan progress streaming  (/api/scan/stream)
# ══════════════════════════════════════════════════════════════════════════════

class TestScanStream(WebTestCase):
    """
    Tests for POST /api/scan/stream.

    Flask's test client buffers the entire streaming response, so r.data
    contains all SSE events concatenated — we can assert on the full content.
    """

    def _stream_zip(self, zip_bytes, words=WORDS_FILE_BYTES):
        return self.client.post('/api/scan/stream', data={
            'source_type':           'zip',
            'config_source_type':    'upload',
            'zip_file':              (io.BytesIO(zip_bytes), 'test.zip'),
            'prohibited_words_file': (io.BytesIO(words),    'words.txt'),
        }, content_type='multipart/form-data')

    def _parse_sse(self, r):
        """Return list of (event_type, data_dict) from a streaming response."""
        events = []
        for block in r.data.decode().split('\n\n'):
            block = block.strip()
            if not block:
                continue
            event_type = 'message'
            data = None
            for line in block.split('\n'):
                if line.startswith('event: '):
                    event_type = line[7:].strip()
                elif line.startswith('data: '):
                    try:
                        data = json.loads(line[6:])
                    except Exception:
                        data = line[6:]
            if data is not None:
                events.append((event_type, data))
        return events

    def test_stream_endpoint_returns_event_stream(self):
        r = self._stream_zip(_make_zip({'f.py': CLEAN_PY}))
        self.assertIn('text/event-stream', r.content_type)

    def test_stream_clean_scan_complete_event(self):
        r = self._stream_zip(_make_zip({'f.py': CLEAN_PY}))
        events = self._parse_sse(r)
        types = [e for e, _ in events]
        self.assertIn('complete', types)

    def test_stream_dirty_scan_complete_event(self):
        r = self._stream_zip(_make_zip({'f.py': DIRTY_PY}))
        events = self._parse_sse(r)
        complete = [d for e, d in events if e == 'complete']
        self.assertEqual(len(complete), 1)
        self.assertGreater(complete[0]['total_violations'], 0)

    def test_stream_complete_payload_matches_api_scan(self):
        """The complete event must have the same shape as /api/scan's JSON."""
        r = self._stream_zip(_make_zip({'f.py': DIRTY_PY}))
        events = self._parse_sse(r)
        data = next(d for e, d in events if e == 'complete')
        for field in ('scan_id', 'total_violations', 'exact_violations',
                      'partial_violations', 'results'):
            self.assertIn(field, data, msg=f'Missing field in complete payload: {field}')

    def test_stream_emits_phase_events(self):
        r = self._stream_zip(_make_zip({'f.py': CLEAN_PY}))
        events = self._parse_sse(r)
        phase_events = [d for e, d in events if e == 'phase']
        self.assertGreater(len(phase_events), 0)
        self.assertTrue(all('message' in d for d in phase_events))

    def test_stream_no_error_event_on_success(self):
        r = self._stream_zip(_make_zip({'f.py': CLEAN_PY}))
        events = self._parse_sse(r)
        error_events = [d for e, d in events if e == 'error']
        self.assertEqual(error_events, [])

    def test_stream_error_event_on_missing_zip(self):
        r = self.client.post('/api/scan/stream', data={
            'source_type':           'zip',
            'config_source_type':    'upload',
            'prohibited_words_file': (io.BytesIO(WORDS_FILE_BYTES), 'words.txt'),
        }, content_type='multipart/form-data')
        events = self._parse_sse(r)
        types = [e for e, _ in events]
        self.assertIn('error', types)
        self.assertNotIn('complete', types)

    def test_stream_scan_id_stored_in_history(self):
        r = self._stream_zip(_make_zip({'f.py': DIRTY_PY}))
        self._parse_sse(r)  # consume stream so background thread finishes
        history = self.client.get('/api/history').get_json()
        self.assertEqual(len(history), 1)

    def test_stream_scan_retrievable_by_id(self):
        r = self._stream_zip(_make_zip({'f.py': DIRTY_PY}))
        events = self._parse_sse(r)
        data = next(d for e, d in events if e == 'complete')
        detail = self.client.get(f'/api/scan/{data["scan_id"]}').get_json()
        self.assertIn('results', detail)


# ══════════════════════════════════════════════════════════════════════════════
# Feedback endpoint
# ══════════════════════════════════════════════════════════════════════════════

class TestFeedback(WebTestCase):

    def setUp(self):
        super().setUp()
        # Point feedback writes at a temp file so tests don't touch the filesystem
        self._feedback_file = os.path.join(self.tmpdir, 'feedback.log')
        import src.web as wm
        self._orig_feedback_file = wm._FEEDBACK_FILE
        wm._FEEDBACK_FILE = self._feedback_file

    def tearDown(self):
        import src.web as wm
        wm._FEEDBACK_FILE = self._orig_feedback_file
        super().tearDown()

    def _post_feedback(self, payload):
        return self.client.post(
            '/api/feedback',
            data=json.dumps(payload),
            content_type='application/json',
        )

    def test_valid_rating_returns_201(self):
        r = self._post_feedback({'rating': 4, 'scan_id': '42'})
        self.assertEqual(r.status_code, 201)
        self.assertTrue(r.get_json()['success'])

    def test_feedback_written_to_file(self):
        self._post_feedback({'rating': 5, 'scan_id': '1', 'comment': 'great'})
        with open(self._feedback_file, encoding='utf-8') as f:
            entry = json.loads(f.readline())
        self.assertEqual(entry['rating'],  5)
        self.assertEqual(entry['comment'], 'great')
        self.assertEqual(entry['scan_id'], '1')

    def test_all_five_star_ratings_accepted(self):
        for rating in range(1, 6):
            r = self._post_feedback({'rating': rating})
            self.assertEqual(r.status_code, 201, f'rating={rating} was rejected')

    def test_rating_zero_returns_400(self):
        r = self._post_feedback({'rating': 0})
        self.assertEqual(r.status_code, 400)

    def test_rating_six_returns_400(self):
        r = self._post_feedback({'rating': 6})
        self.assertEqual(r.status_code, 400)

    def test_missing_rating_returns_400(self):
        r = self._post_feedback({'comment': 'no rating here'})
        self.assertEqual(r.status_code, 400)

    def test_string_rating_returns_400(self):
        r = self._post_feedback({'rating': 'five'})
        self.assertEqual(r.status_code, 400)

    def test_comment_is_optional(self):
        r = self._post_feedback({'rating': 3})
        self.assertEqual(r.status_code, 201)

    def test_multiple_entries_appended(self):
        self._post_feedback({'rating': 4})
        self._post_feedback({'rating': 2, 'comment': 'second'})
        with open(self._feedback_file, encoding='utf-8') as f:
            lines = [l for l in f if l.strip()]
        self.assertEqual(len(lines), 2)

    def test_entry_has_timestamp(self):
        self._post_feedback({'rating': 3})
        with open(self._feedback_file, encoding='utf-8') as f:
            entry = json.loads(f.readline())
        self.assertIn('timestamp', entry)


if __name__ == '__main__':
    unittest.main()
