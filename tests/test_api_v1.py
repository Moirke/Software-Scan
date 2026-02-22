"""
Tests for the /api/v1/ REST endpoints (src/web.py).

All scan operations use source_type=zip so tests never need a network
connection or a real git server.  The config is always supplied via an
in-memory upload (config_source_type=upload).
"""
import csv
import io
import json
import os
import shutil
import tempfile
import unittest
import zipfile

from src import web as web_module
from src.web import app


# ── Helpers ────────────────────────────────────────────────────────────────

WORDS_FILE_BYTES = b'password\nsecret\napi_key\n'
DIRTY_PY         = b"password = 'hunter2'\n"
CLEAN_PY         = b'x = 1  # nothing sensitive here\n'


def _make_zip(files: dict) -> bytes:
    """Build a ZIP in memory.  files: {name: str|bytes}"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        for name, content in files.items():
            if isinstance(content, str):
                content = content.encode()
            zf.writestr(name, content)
    return buf.getvalue()


# ── Base test case ─────────────────────────────────────────────────────────

class V1TestCase(unittest.TestCase):
    def setUp(self):
        app.config['TESTING'] = True
        self.client = app.test_client()
        # Isolate storage between tests
        web_module.scan_history.clear()
        web_module.scan_store.clear()

    def tearDown(self):
        web_module.scan_history.clear()
        web_module.scan_store.clear()

    # -- Convenience helpers ------------------------------------------------

    def _post_zip_scan(self, zip_bytes: bytes = None,
                       words: bytes = WORDS_FILE_BYTES,
                       **extra) -> object:
        """POST a ZIP-upload scan to /api/v1/scans and return the response."""
        if zip_bytes is None:
            zip_bytes = _make_zip({'code.py': CLEAN_PY})
        data = {
            'source_type':           'zip',
            'config_source_type':    'upload',
            'zip_file':              (io.BytesIO(zip_bytes), 'test.zip'),
            'prohibited_words_file': (io.BytesIO(words),    'words.txt'),
            'case_sensitive':        'false',
            'max_file_size_mb':      '10',
            **extra,
        }
        return self.client.post(
            '/api/v1/scans',
            data=data,
            content_type='multipart/form-data',
        )

    def _post_scan_json(self, body: dict) -> object:
        """POST a JSON body to /api/v1/scans."""
        return self.client.post(
            '/api/v1/scans',
            data=json.dumps(body),
            content_type='application/json',
        )

    def _ok_data(self, r) -> dict:
        """Assert 200-level response and return the 'data' payload."""
        body = json.loads(r.data)
        self.assertIn('data', body, f'Expected envelope with "data", got: {body}')
        return body['data']

    def _ok_meta(self, r) -> dict:
        body = json.loads(r.data)
        return body.get('meta', {})

    def _err_body(self, r) -> dict:
        body = json.loads(r.data)
        self.assertIn('error', body, f'Expected envelope with "error", got: {body}')
        return body['error']


# ══════════════════════════════════════════════════════════════════════════════
# Health and metrics
# ══════════════════════════════════════════════════════════════════════════════

class TestV1HealthMetrics(V1TestCase):

    def test_health_returns_ok(self):
        r = self.client.get('/api/v1/health')
        self.assertEqual(r.status_code, 200)
        data = self._ok_data(r)
        self.assertEqual(data.get('status'), 'ok')

    def test_metrics_returns_data_envelope(self):
        r = self.client.get('/api/v1/metrics')
        self.assertEqual(r.status_code, 200)
        data = self._ok_data(r)
        # Metrics must contain at least a scan count field
        self.assertIsInstance(data, dict)
        self.assertIn('scans_started', data)


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/v1/scans — submit a scan
# ══════════════════════════════════════════════════════════════════════════════

class TestV1ScansPost(V1TestCase):

    # ── Success cases ──────────────────────────────────────────────────────

    def test_clean_zip_returns_zero_violations(self):
        r = self._post_zip_scan(_make_zip({'readme.txt': CLEAN_PY}))
        self.assertEqual(r.status_code, 200)
        data = self._ok_data(r)
        self.assertEqual(data['total_violations'], 0)

    def test_dirty_zip_reports_violations(self):
        r = self._post_zip_scan(_make_zip({'code.py': DIRTY_PY}))
        self.assertEqual(r.status_code, 200)
        data = self._ok_data(r)
        self.assertGreater(data['total_violations'], 0)

    def test_response_has_scan_id_uuid(self):
        r = self._post_zip_scan()
        self.assertEqual(r.status_code, 200)
        data = self._ok_data(r)
        self.assertIn('id', data)
        # UUID has 5 hyphen-separated groups
        parts = data['id'].split('-')
        self.assertEqual(len(parts), 5)

    def test_response_envelope_has_meta(self):
        r = self._post_zip_scan()
        self.assertEqual(r.status_code, 200)
        meta = self._ok_meta(r)
        self.assertIn('duration_ms', meta)
        self.assertIsInstance(meta['duration_ms'], int)

    def test_scan_stored_in_scan_store(self):
        r = self._post_zip_scan()
        self.assertEqual(r.status_code, 200)
        data = self._ok_data(r)
        self.assertIn(data['id'], web_module.scan_store)

    def test_scan_also_in_history(self):
        r = self._post_zip_scan()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(web_module.scan_history), 1)

    def test_legacy_id_present(self):
        self._post_zip_scan()  # scan_id = 0
        r = self._post_zip_scan()  # scan_id = 1
        data = self._ok_data(r)
        self.assertIsNotNone(data.get('legacy_id'))

    def test_recursive_flag_defaults_true(self):
        r = self._post_zip_scan()
        self.assertEqual(r.status_code, 200)
        data = self._ok_data(r)
        self.assertTrue(data['recursive'])

    def test_recursive_flag_can_be_set_false(self):
        r = self._post_zip_scan(recursive='false')
        self.assertEqual(r.status_code, 200)
        data = self._ok_data(r)
        self.assertFalse(data['recursive'])

    def test_case_sensitive_flag(self):
        r = self._post_zip_scan(case_sensitive='true')
        self.assertEqual(r.status_code, 200)
        data = self._ok_data(r)
        self.assertTrue(data['case_sensitive'])

    def test_source_type_recorded(self):
        r = self._post_zip_scan()
        self.assertEqual(r.status_code, 200)
        data = self._ok_data(r)
        self.assertEqual(data['source_type'], 'zip')

    def test_results_first_page_embedded(self):
        """POST response embeds the first page of violations."""
        r = self._post_zip_scan(_make_zip({'code.py': DIRTY_PY}))
        self.assertEqual(r.status_code, 200)
        data = self._ok_data(r)
        self.assertIn('results', data)
        self.assertIsInstance(data['results'], list)

    def test_server_path_scan(self):
        """source_type=server_path scans a real directory on disk."""
        tmp = tempfile.mkdtemp(prefix='v1_test_')
        try:
            with open(os.path.join(tmp, 'secrets.py'), 'w') as f:
                f.write("password = 'hunter2'\n")
            words_tmp = tempfile.NamedTemporaryFile(
                mode='w', suffix='.txt', delete=False)
            words_tmp.write('password\n')
            words_tmp.close()
            try:
                data = {
                    'source_type':        'server_path',
                    'config_source_type': 'server_path',
                    'repo_path':          tmp,
                    'config_server_path': words_tmp.name,
                }
                r = self._post_scan_json(data)
                self.assertEqual(r.status_code, 200)
                body = self._ok_data(r)
                self.assertGreater(body['total_violations'], 0)
            finally:
                os.unlink(words_tmp.name)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # ── Validation / error cases ───────────────────────────────────────────

    def test_missing_zip_returns_400(self):
        data = {
            'source_type':           'zip',
            'config_source_type':    'upload',
            'prohibited_words_file': (io.BytesIO(WORDS_FILE_BYTES), 'words.txt'),
        }
        r = self.client.post('/api/v1/scans', data=data,
                             content_type='multipart/form-data')
        self.assertEqual(r.status_code, 400)
        err = self._err_body(r)
        self.assertEqual(err['code'], 'VALIDATION_ERROR')

    def test_missing_words_file_returns_400(self):
        zip_bytes = _make_zip({'code.py': CLEAN_PY})
        data = {
            'source_type':        'zip',
            'config_source_type': 'upload',
            'zip_file':           (io.BytesIO(zip_bytes), 'test.zip'),
        }
        r = self.client.post('/api/v1/scans', data=data,
                             content_type='multipart/form-data')
        self.assertEqual(r.status_code, 400)
        err = self._err_body(r)
        self.assertEqual(err['code'], 'VALIDATION_ERROR')

    def test_invalid_source_type_returns_400(self):
        body = {
            'source_type':        'floppy_disk',
            'config_source_type': 'upload',
            'repo_url':           'http://example.com/repo',
        }
        r = self._post_scan_json(body)
        self.assertEqual(r.status_code, 400)
        err = self._err_body(r)
        self.assertEqual(err['code'], 'VALIDATION_ERROR')

    def test_git_source_missing_url_returns_400(self):
        body = {
            'source_type':        'git',
            'config_source_type': 'upload',
        }
        r = self._post_scan_json(body)
        self.assertEqual(r.status_code, 400)
        err = self._err_body(r)
        self.assertEqual(err['code'], 'VALIDATION_ERROR')

    def test_server_path_source_missing_path_returns_400(self):
        body = {
            'source_type':        'server_path',
            'config_source_type': 'server_path',
            'config_server_path': '/tmp',
        }
        r = self._post_scan_json(body)
        self.assertEqual(r.status_code, 400)
        err = self._err_body(r)
        self.assertEqual(err['code'], 'VALIDATION_ERROR')

    def test_nonexistent_server_path_returns_422(self):
        body = {
            'source_type':        'server_path',
            'config_source_type': 'server_path',
            'repo_path':          '/nonexistent/path/that/does/not/exist',
            'config_server_path': '/tmp',
        }
        r = self._post_scan_json(body)
        self.assertEqual(r.status_code, 422)
        err = self._err_body(r)
        self.assertEqual(err['code'], 'SCAN_FAILED')

    def test_invalid_max_file_size_mb_returns_400(self):
        zip_bytes = _make_zip({'code.py': CLEAN_PY})
        data = {
            'source_type':           'zip',
            'config_source_type':    'upload',
            'zip_file':              (io.BytesIO(zip_bytes), 'test.zip'),
            'prohibited_words_file': (io.BytesIO(WORDS_FILE_BYTES), 'words.txt'),
            'max_file_size_mb':      'notanumber',
        }
        r = self.client.post('/api/v1/scans', data=data,
                             content_type='multipart/form-data')
        self.assertEqual(r.status_code, 400)
        err = self._err_body(r)
        self.assertEqual(err['code'], 'VALIDATION_ERROR')

    def test_error_envelope_has_code_and_message(self):
        body = {'source_type': 'invalid_type', 'config_source_type': 'upload'}
        r = self._post_scan_json(body)
        err = self._err_body(r)
        self.assertIn('code', err)
        self.assertIn('message', err)


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/v1/scans — list scans (pagination)
# ══════════════════════════════════════════════════════════════════════════════

class TestV1ScansList(V1TestCase):

    def _seed_scans(self, n: int):
        """Submit n clean scans."""
        for _ in range(n):
            self._post_zip_scan()

    def test_empty_list_returns_empty_data(self):
        r = self.client.get('/api/v1/scans')
        self.assertEqual(r.status_code, 200)
        data = self._ok_data(r)
        self.assertEqual(data, [])

    def test_list_returns_submitted_scans(self):
        self._seed_scans(3)
        r = self.client.get('/api/v1/scans')
        self.assertEqual(r.status_code, 200)
        data = self._ok_data(r)
        self.assertEqual(len(data), 3)

    def test_list_newest_first(self):
        """Scans are returned in reverse-chronological order."""
        r1 = self._post_zip_scan()
        r2 = self._post_zip_scan()
        id1 = json.loads(r1.data)['data']['id']
        id2 = json.loads(r2.data)['data']['id']

        r = self.client.get('/api/v1/scans')
        data = self._ok_data(r)
        ids = [s['id'] for s in data]
        self.assertEqual(ids[0], id2)
        self.assertEqual(ids[1], id1)

    def test_pagination_meta_present(self):
        self._seed_scans(2)
        r = self.client.get('/api/v1/scans?page=1&limit=1')
        self.assertEqual(r.status_code, 200)
        meta = self._ok_meta(r)
        self.assertEqual(meta['limit'], 1)
        self.assertEqual(meta['total'], 2)
        self.assertEqual(meta['total_pages'], 2)

    def test_pagination_returns_correct_page(self):
        self._seed_scans(3)
        r = self.client.get('/api/v1/scans?page=1&limit=2')
        data = self._ok_data(r)
        self.assertEqual(len(data), 2)

        r2 = self.client.get('/api/v1/scans?page=2&limit=2')
        data2 = self._ok_data(r2)
        self.assertEqual(len(data2), 1)

    def test_invalid_page_param_returns_400(self):
        r = self.client.get('/api/v1/scans?page=banana')
        self.assertEqual(r.status_code, 400)
        err = self._err_body(r)
        self.assertEqual(err['code'], 'VALIDATION_ERROR')

    def test_list_record_has_required_fields(self):
        self._post_zip_scan()
        r = self.client.get('/api/v1/scans')
        data = self._ok_data(r)
        record = data[0]
        for field in ('id', 'timestamp', 'target', 'source_type',
                      'total_violations', 'exact_violations', 'partial_violations'):
            self.assertIn(field, record, f'Missing field: {field}')

    def test_deleted_scan_excluded_from_list(self):
        r = self._post_zip_scan()
        scan_id = json.loads(r.data)['data']['id']
        self.client.delete(f'/api/v1/scans/{scan_id}')

        r = self.client.get('/api/v1/scans')
        data = self._ok_data(r)
        ids = [s['id'] for s in data]
        self.assertNotIn(scan_id, ids)


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/v1/scans/<uuid> — retrieve a scan
# ══════════════════════════════════════════════════════════════════════════════

class TestV1ScanGet(V1TestCase):

    def test_get_existing_scan(self):
        r = self._post_zip_scan()
        scan_id = json.loads(r.data)['data']['id']

        r2 = self.client.get(f'/api/v1/scans/{scan_id}')
        self.assertEqual(r2.status_code, 200)
        data = self._ok_data(r2)
        self.assertEqual(data['id'], scan_id)

    def test_get_nonexistent_scan_returns_404(self):
        r = self.client.get('/api/v1/scans/00000000-0000-0000-0000-000000000000')
        self.assertEqual(r.status_code, 404)
        err = self._err_body(r)
        self.assertEqual(err['code'], 'NOT_FOUND')

    def test_get_record_has_no_results_array(self):
        """The scan-summary endpoint omits the full violations list."""
        r = self._post_zip_scan(_make_zip({'code.py': DIRTY_PY}))
        scan_id = json.loads(r.data)['data']['id']

        r2 = self.client.get(f'/api/v1/scans/{scan_id}')
        data = self._ok_data(r2)
        self.assertNotIn('results', data)

    def test_get_record_includes_words_evaluated_count(self):
        r = self._post_zip_scan()
        scan_id = json.loads(r.data)['data']['id']

        r2 = self.client.get(f'/api/v1/scans/{scan_id}')
        data = self._ok_data(r2)
        self.assertIn('words_evaluated', data)
        self.assertIsInstance(data['words_evaluated'], int)
        self.assertGreater(data['words_evaluated'], 0)


# ══════════════════════════════════════════════════════════════════════════════
# DELETE /api/v1/scans/<uuid>
# ══════════════════════════════════════════════════════════════════════════════

class TestV1ScanDelete(V1TestCase):

    def test_delete_returns_204(self):
        r = self._post_zip_scan()
        scan_id = json.loads(r.data)['data']['id']

        r2 = self.client.delete(f'/api/v1/scans/{scan_id}')
        self.assertEqual(r2.status_code, 204)
        self.assertEqual(r2.data, b'')

    def test_get_after_delete_returns_404(self):
        r = self._post_zip_scan()
        scan_id = json.loads(r.data)['data']['id']

        self.client.delete(f'/api/v1/scans/{scan_id}')
        r2 = self.client.get(f'/api/v1/scans/{scan_id}')
        self.assertEqual(r2.status_code, 404)

    def test_delete_nonexistent_returns_404(self):
        r = self.client.delete('/api/v1/scans/00000000-0000-0000-0000-000000000000')
        self.assertEqual(r.status_code, 404)
        err = self._err_body(r)
        self.assertEqual(err['code'], 'NOT_FOUND')

    def test_delete_does_not_shift_legacy_ids(self):
        """Soft delete must leave scan_history intact (v0 integer IDs stable)."""
        r1 = self._post_zip_scan()
        r2 = self._post_zip_scan()
        id1 = json.loads(r1.data)['data']['id']

        self.client.delete(f'/api/v1/scans/{id1}')

        # scan_history must still hold 2 records
        self.assertEqual(len(web_module.scan_history), 2)
        # Second scan legacy_id must still be 1
        self.assertEqual(web_module.scan_history[1].get('id'), 1)


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/v1/scans/<uuid>/results — paginated violations
# ══════════════════════════════════════════════════════════════════════════════

class TestV1ScanResults(V1TestCase):

    def _scan_with_violations(self) -> str:
        """Submit a dirty scan and return the scan UUID."""
        r = self._post_zip_scan(_make_zip({'code.py': DIRTY_PY}))
        return json.loads(r.data)['data']['id']

    def test_results_endpoint_returns_list(self):
        scan_id = self._scan_with_violations()
        r = self.client.get(f'/api/v1/scans/{scan_id}/results')
        self.assertEqual(r.status_code, 200)
        data = self._ok_data(r)
        self.assertIsInstance(data, list)

    def test_results_nonexistent_scan_returns_404(self):
        r = self.client.get(
            '/api/v1/scans/00000000-0000-0000-0000-000000000000/results')
        self.assertEqual(r.status_code, 404)

    def test_results_pagination_meta(self):
        scan_id = self._scan_with_violations()
        r = self.client.get(f'/api/v1/scans/{scan_id}/results?page=1&limit=1')
        self.assertEqual(r.status_code, 200)
        meta = self._ok_meta(r)
        self.assertIn('page', meta)
        self.assertIn('total', meta)
        self.assertIn('total_pages', meta)

    def test_results_match_type_filter_exact(self):
        scan_id = self._scan_with_violations()
        r = self.client.get(
            f'/api/v1/scans/{scan_id}/results?match_type=exact')
        self.assertEqual(r.status_code, 200)
        data = self._ok_data(r)
        for item in data:
            self.assertEqual(item['match_type'], 'exact')

    def test_results_match_type_filter_partial(self):
        # 'password' appears as whole-word in DIRTY_PY so partial may be 0
        # Use a word that is a substring to guarantee partials
        zip_bytes = _make_zip({'code.py': b'mypasswordvalue = 1\n'})
        words = b'password\n'
        r = self._post_zip_scan(zip_bytes, words)
        scan_id = json.loads(r.data)['data']['id']

        r2 = self.client.get(
            f'/api/v1/scans/{scan_id}/results?match_type=partial')
        self.assertEqual(r2.status_code, 200)
        data = self._ok_data(r2)
        for item in data:
            self.assertEqual(item['match_type'], 'partial')

    def test_results_invalid_pagination_returns_400(self):
        scan_id = self._scan_with_violations()
        r = self.client.get(
            f'/api/v1/scans/{scan_id}/results?page=abc')
        self.assertEqual(r.status_code, 400)
        err = self._err_body(r)
        self.assertEqual(err['code'], 'VALIDATION_ERROR')

    def test_results_each_item_has_required_fields(self):
        scan_id = self._scan_with_violations()
        r = self.client.get(f'/api/v1/scans/{scan_id}/results')
        data = self._ok_data(r)
        self.assertTrue(data, 'Expected at least one violation')
        item = data[0]
        for field in ('file', 'line_number', 'line_content',
                      'prohibited_word', 'match_type'):
            self.assertIn(field, item, f'Missing field in result: {field}')


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/v1/scans/<uuid>/export.json
# ══════════════════════════════════════════════════════════════════════════════

class TestV1ExportJson(V1TestCase):

    def test_export_json_returns_200(self):
        r = self._post_zip_scan()
        scan_id = json.loads(r.data)['data']['id']

        r2 = self.client.get(f'/api/v1/scans/{scan_id}/export.json')
        self.assertEqual(r2.status_code, 200)

    def test_export_json_content_type(self):
        r = self._post_zip_scan()
        scan_id = json.loads(r.data)['data']['id']

        r2 = self.client.get(f'/api/v1/scans/{scan_id}/export.json')
        self.assertIn('application/json', r2.content_type)

    def test_export_json_is_valid_json(self):
        r = self._post_zip_scan()
        scan_id = json.loads(r.data)['data']['id']

        r2 = self.client.get(f'/api/v1/scans/{scan_id}/export.json')
        body = json.loads(r2.data)
        self.assertIn('uuid', body)

    def test_export_json_nonexistent_returns_404(self):
        r = self.client.get(
            '/api/v1/scans/00000000-0000-0000-0000-000000000000/export.json')
        self.assertEqual(r.status_code, 404)

    def test_export_json_download_name(self):
        r = self._post_zip_scan()
        scan_id = json.loads(r.data)['data']['id']

        r2 = self.client.get(f'/api/v1/scans/{scan_id}/export.json')
        cd = r2.headers.get('Content-Disposition', '')
        self.assertIn('attachment', cd)
        self.assertIn('.json', cd)


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/v1/scans/<uuid>/export.pdf
# ══════════════════════════════════════════════════════════════════════════════

class TestV1ExportPdf(V1TestCase):

    def test_export_pdf_returns_200(self):
        r = self._post_zip_scan()
        scan_id = json.loads(r.data)['data']['id']

        r2 = self.client.get(f'/api/v1/scans/{scan_id}/export.pdf')
        self.assertEqual(r2.status_code, 200)

    def test_export_pdf_content_type(self):
        r = self._post_zip_scan()
        scan_id = json.loads(r.data)['data']['id']

        r2 = self.client.get(f'/api/v1/scans/{scan_id}/export.pdf')
        self.assertIn('application/pdf', r2.content_type)

    def test_export_pdf_starts_with_pdf_magic(self):
        r = self._post_zip_scan()
        scan_id = json.loads(r.data)['data']['id']

        r2 = self.client.get(f'/api/v1/scans/{scan_id}/export.pdf')
        self.assertTrue(r2.data.startswith(b'%PDF'))

    def test_export_pdf_nonexistent_returns_404(self):
        r = self.client.get(
            '/api/v1/scans/00000000-0000-0000-0000-000000000000/export.pdf')
        self.assertEqual(r.status_code, 404)

    def test_export_pdf_download_name(self):
        r = self._post_zip_scan()
        scan_id = json.loads(r.data)['data']['id']

        r2 = self.client.get(f'/api/v1/scans/{scan_id}/export.pdf')
        cd = r2.headers.get('Content-Disposition', '')
        self.assertIn('attachment', cd)
        self.assertIn('.pdf', cd)


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/v1/scans/<uuid>/export.csv
# ══════════════════════════════════════════════════════════════════════════════

class TestV1ExportCsv(V1TestCase):

    def _parse_csv(self, data: bytes) -> list:
        return list(csv.DictReader(io.StringIO(data.decode('utf-8'))))

    def _scan_dirty(self) -> str:
        """Submit a scan with violations and return the UUID."""
        r = self._post_zip_scan(_make_zip({'code.py': DIRTY_PY}))
        return json.loads(r.data)['data']['id']

    def test_export_csv_returns_200(self):
        scan_id = self._scan_dirty()
        r = self.client.get(f'/api/v1/scans/{scan_id}/export.csv')
        self.assertEqual(r.status_code, 200)

    def test_export_csv_content_type(self):
        scan_id = self._scan_dirty()
        r = self.client.get(f'/api/v1/scans/{scan_id}/export.csv')
        self.assertIn('text/csv', r.content_type)

    def test_export_csv_download_name(self):
        scan_id = self._scan_dirty()
        r = self.client.get(f'/api/v1/scans/{scan_id}/export.csv')
        cd = r.headers.get('Content-Disposition', '')
        self.assertIn('attachment', cd)
        self.assertIn('.csv', cd)

    def test_export_csv_has_expected_columns(self):
        scan_id = self._scan_dirty()
        r = self.client.get(f'/api/v1/scans/{scan_id}/export.csv')
        rows = self._parse_csv(r.data)
        self.assertTrue(rows)
        for col in ('file', 'line_number', 'prohibited_word',
                    'match_type', 'position', 'line_content'):
            self.assertIn(col, rows[0], f'Missing column: {col}')

    def test_export_csv_row_count_matches_violations(self):
        r = self._post_zip_scan(_make_zip({'code.py': DIRTY_PY}))
        body = json.loads(r.data)
        scan_id = body['data']['id']
        total   = body['data']['total_violations']
        csv_r = self.client.get(f'/api/v1/scans/{scan_id}/export.csv')
        rows = self._parse_csv(csv_r.data)
        self.assertEqual(len(rows), total)

    def test_export_csv_clean_scan_header_only(self):
        r = self._post_zip_scan(_make_zip({'code.py': CLEAN_PY}))
        scan_id = json.loads(r.data)['data']['id']
        csv_r = self.client.get(f'/api/v1/scans/{scan_id}/export.csv')
        self.assertEqual(csv_r.status_code, 200)
        rows = self._parse_csv(csv_r.data)
        self.assertEqual(rows, [])

    def test_export_csv_contains_all_results_not_capped(self):
        """CSV must include all violations, not just the first page."""
        files = {f'file_{i}.py': DIRTY_PY for i in range(101)}
        r = self._post_zip_scan(_make_zip(files))
        scan_id = json.loads(r.data)['data']['id']
        csv_r = self.client.get(f'/api/v1/scans/{scan_id}/export.csv')
        rows = self._parse_csv(csv_r.data)
        self.assertGreater(len(rows), 100)

    def test_export_csv_nonexistent_returns_404(self):
        r = self.client.get(
            '/api/v1/scans/00000000-0000-0000-0000-000000000000/export.csv')
        self.assertEqual(r.status_code, 404)
        err = self._err_body(r)
        self.assertEqual(err['code'], 'NOT_FOUND')

    def test_export_csv_violation_data_correct(self):
        scan_id = self._scan_dirty()
        csv_r = self.client.get(f'/api/v1/scans/{scan_id}/export.csv')
        rows = self._parse_csv(csv_r.data)
        self.assertTrue(rows)
        row = rows[0]
        self.assertIn('password', row['prohibited_word'])
        self.assertTrue(row['line_number'].isdigit())
        self.assertIn(row['match_type'], ('exact', 'partial'))


# ══════════════════════════════════════════════════════════════════════════════
# Cross-version consistency — v0 and v1 share the same underlying data
# ══════════════════════════════════════════════════════════════════════════════

class TestV0V1Consistency(V1TestCase):

    def test_v1_scan_visible_via_v0_history(self):
        """A scan submitted through v1 must appear in /api/history."""
        r = self._post_zip_scan()
        data = json.loads(r.data)['data']
        legacy_id = data['legacy_id']

        history = json.loads(self.client.get('/api/history').data)
        ids = [s.get('id') for s in history]
        self.assertIn(legacy_id, ids)

    def test_v1_scan_retrievable_via_v0_integer_id(self):
        """A scan submitted through v1 must be retrievable by its legacy integer id."""
        r = self._post_zip_scan()
        data = json.loads(r.data)['data']
        legacy_id = data['legacy_id']

        r2 = self.client.get(f'/api/scan/{legacy_id}')
        self.assertEqual(r2.status_code, 200)
        record = json.loads(r2.data)
        self.assertEqual(record['uuid'], data['id'])


if __name__ == '__main__':
    unittest.main()
