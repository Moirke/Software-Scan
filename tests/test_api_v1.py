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
import yaml

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


# ══════════════════════════════════════════════════════════════════════════════
# ZIP config bundle upload
# ══════════════════════════════════════════════════════════════════════════════

class TestZipConfigUpload(V1TestCase):

    def _make_config_zip(self, files: dict) -> bytes:
        """Build a config ZIP in memory. files: {name: str|bytes}"""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            for name, content in files.items():
                if isinstance(content, str):
                    content = content.encode()
                zf.writestr(name, content)
        return buf.getvalue()

    def _post_zip_config_scan(self, config_zip: bytes, scan_zip: bytes = None) -> object:
        if scan_zip is None:
            scan_zip = _make_zip({'code.py': CLEAN_PY})
        data = {
            'source_type':           'zip',
            'config_source_type':    'upload',
            'zip_file':              (io.BytesIO(scan_zip),    'scan.zip'),
            'prohibited_words_file': (io.BytesIO(config_zip),  'config.zip'),
            'case_sensitive':        'false',
            'max_file_size_mb':      '10',
        }
        return self.client.post('/api/v1/scans', data=data,
                                content_type='multipart/form-data')

    def test_zip_with_words_only_succeeds(self):
        config_zip = self._make_config_zip({'prohibited_words.txt': WORDS_FILE_BYTES})
        r = self._post_zip_config_scan(config_zip)
        self.assertEqual(r.status_code, 200)

    def test_zip_with_words_only_has_empty_base_suppressions(self):
        config_zip = self._make_config_zip({'prohibited_words.txt': WORDS_FILE_BYTES})
        r = self._post_zip_config_scan(config_zip)
        scan_uuid = self._ok_data(r)['id']
        self.assertEqual(web_module.scan_store[scan_uuid]['base_suppressions'], {})

    def test_zip_with_words_finds_violations(self):
        config_zip = self._make_config_zip({'prohibited_words.txt': WORDS_FILE_BYTES})
        r = self._post_zip_config_scan(config_zip, _make_zip({'code.py': DIRTY_PY}))
        self.assertGreater(self._ok_data(r)['total_violations'], 0)

    def test_zip_with_words_and_suppressions_loads_both(self):
        """suppressions.yaml from the config ZIP is loaded into base_suppressions."""
        from src.suppressions import make_fingerprint
        fp = make_fingerprint('code.py', "password = 'hunter2'", 'password')
        supp_yaml = yaml.dump({'suppressions': [
            {'id': fp, 'file': 'code.py',
             'line_content': "password = 'hunter2'", 'prohibited_word': 'password'}
        ]})
        config_zip = self._make_config_zip({
            'prohibited_words.txt': WORDS_FILE_BYTES,
            'suppressions.yaml':    supp_yaml,
        })
        r = self._post_zip_config_scan(config_zip)
        scan_uuid = self._ok_data(r)['id']
        self.assertIn(fp, web_module.scan_store[scan_uuid]['base_suppressions'])

    def test_zip_missing_words_file_returns_422(self):
        config_zip = self._make_config_zip({'suppressions.yaml': 'suppressions: []\n'})
        r = self._post_zip_config_scan(config_zip)
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self._err_body(r)['code'], 'SCAN_FAILED')

    def test_invalid_zip_returns_422(self):
        r = self._post_zip_config_scan(b'this is not a zip file')
        self.assertEqual(r.status_code, 422)

    def test_suppressions_yaml_in_zip_is_optional(self):
        """A ZIP with only prohibited_words.txt must not fail due to absent suppressions."""
        config_zip = self._make_config_zip({'prohibited_words.txt': b'password\n'})
        r = self._post_zip_config_scan(config_zip)
        self.assertEqual(r.status_code, 200)


# ══════════════════════════════════════════════════════════════════════════════
# Scan record suppression fields
# ══════════════════════════════════════════════════════════════════════════════

class TestScanRecordSuppressionFields(V1TestCase):
    """Verify that each scan record carries base_suppressions and session_suppressions."""

    def test_scan_record_has_base_suppressions(self):
        r = self._post_zip_scan()
        scan_uuid = self._ok_data(r)['id']
        record = web_module.scan_store[scan_uuid]
        self.assertIn('base_suppressions', record)
        self.assertIsInstance(record['base_suppressions'], dict)

    def test_scan_record_has_session_suppressions(self):
        r = self._post_zip_scan()
        scan_uuid = self._ok_data(r)['id']
        record = web_module.scan_store[scan_uuid]
        self.assertIn('session_suppressions', record)
        self.assertIsInstance(record['session_suppressions'], dict)

    def test_base_suppressions_empty_for_upload_config(self):
        """config_source_type=upload has no folder to read from → empty base."""
        r = self._post_zip_scan()
        scan_uuid = self._ok_data(r)['id']
        record = web_module.scan_store[scan_uuid]
        self.assertEqual(record['base_suppressions'], {})

    def test_base_suppressions_loaded_from_server_path_config(self):
        """base_suppressions is populated when suppressions.yaml sits next to words file."""
        import yaml
        from src.suppressions import make_fingerprint
        tmp = tempfile.mkdtemp(prefix='v1_supp_test_')
        try:
            # Write words file
            words_path = os.path.join(tmp, 'prohibited_words.txt')
            with open(words_path, 'w') as f:
                f.write('password\n')
            # Write suppressions.yaml alongside it
            fp = make_fingerprint('secrets.py', "password = 'hunter2'", 'password')
            supp_data = {'suppressions': [{'id': fp, 'file': 'secrets.py',
                                           'line_content': "password = 'hunter2'",
                                           'prohibited_word': 'password'}]}
            supp_path = os.path.join(tmp, 'suppressions.yaml')
            with open(supp_path, 'w') as f:
                yaml.dump(supp_data, f)
            # Write repo file
            repo_tmp = tempfile.mkdtemp(prefix='v1_supp_repo_')
            try:
                with open(os.path.join(repo_tmp, 'secrets.py'), 'w') as f:
                    f.write("password = 'hunter2'\n")
                body = {
                    'source_type':        'server_path',
                    'config_source_type': 'server_path',
                    'repo_path':          repo_tmp,
                    'config_server_path': words_path,
                }
                r = self._post_scan_json(body)
                self.assertEqual(r.status_code, 200)
                data = self._ok_data(r)
                scan_uuid = data['id']
                record = web_module.scan_store[scan_uuid]
                self.assertIn(fp, record['base_suppressions'])
            finally:
                shutil.rmtree(repo_tmp, ignore_errors=True)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_suppressed_findings_reduce_total_violations(self):
        """A finding covered by base_suppressions is excluded from results."""
        import yaml
        from src.suppressions import make_fingerprint
        tmp = tempfile.mkdtemp(prefix='v1_supp_apply_')
        try:
            words_path = os.path.join(tmp, 'prohibited_words.txt')
            with open(words_path, 'w') as f:
                f.write('password\n')
            # The finding in secrets.py will be suppressed
            fp = make_fingerprint('secrets.py', "password = 'hunter2'", 'password')
            supp_data = {'suppressions': [{'id': fp, 'file': 'secrets.py',
                                           'line_content': "password = 'hunter2'",
                                           'prohibited_word': 'password'}]}
            with open(os.path.join(tmp, 'suppressions.yaml'), 'w') as f:
                yaml.dump(supp_data, f)
            repo_tmp = tempfile.mkdtemp(prefix='v1_supp_apply_repo_')
            try:
                with open(os.path.join(repo_tmp, 'secrets.py'), 'w') as f:
                    f.write("password = 'hunter2'\n")
                body = {
                    'source_type':        'server_path',
                    'config_source_type': 'server_path',
                    'repo_path':          repo_tmp,
                    'config_server_path': words_path,
                }
                r = self._post_scan_json(body)
                data = self._ok_data(r)
                self.assertEqual(data['total_violations'], 0)
                self.assertEqual(data['suppressed_count'], 1)
            finally:
                shutil.rmtree(repo_tmp, ignore_errors=True)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/v1/suppressions — in-session suppression
# ══════════════════════════════════════════════════════════════════════════════

class TestV1SuppressionsAdd(V1TestCase):

    def _do_scan(self):
        """Run a dirty scan and return the scan UUID."""
        r = self._post_zip_scan(_make_zip({'code.py': DIRTY_PY}))
        return self._ok_data(r)['id']

    def _post_suppression(self, body):
        return self.client.post(
            '/api/v1/suppressions',
            data=json.dumps(body),
            content_type='application/json',
        )

    def test_missing_scan_id_returns_400(self):
        r = self._post_suppression({'file': 'a.py', 'line_content': 'x', 'prohibited_word': 'x'})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self._err_body(r)['code'], 'VALIDATION_ERROR')

    def test_unknown_scan_id_returns_404(self):
        r = self._post_suppression({
            'scan_id': 'deadbeef-0000-0000-0000-000000000000',
            'file': 'a.py', 'line_content': 'x', 'prohibited_word': 'x',
        })
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self._err_body(r)['code'], 'NOT_FOUND')

    def test_missing_file_returns_400(self):
        scan_id = self._do_scan()
        r = self._post_suppression({'scan_id': scan_id, 'line_content': 'x', 'prohibited_word': 'x'})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self._err_body(r)['code'], 'VALIDATION_ERROR')

    def test_missing_line_content_returns_400(self):
        scan_id = self._do_scan()
        r = self._post_suppression({'scan_id': scan_id, 'file': 'a.py', 'prohibited_word': 'x'})
        self.assertEqual(r.status_code, 400)

    def test_missing_prohibited_word_returns_400(self):
        scan_id = self._do_scan()
        r = self._post_suppression({'scan_id': scan_id, 'file': 'a.py', 'line_content': 'x'})
        self.assertEqual(r.status_code, 400)

    def test_valid_request_returns_201(self):
        scan_id = self._do_scan()
        r = self._post_suppression({
            'scan_id': scan_id, 'file': 'code.py',
            'line_content': "password = 'hunter2'", 'prohibited_word': 'password',
        })
        self.assertEqual(r.status_code, 201)

    def test_valid_request_returns_entry_with_id(self):
        scan_id = self._do_scan()
        r = self._post_suppression({
            'scan_id': scan_id, 'file': 'code.py',
            'line_content': "password = 'hunter2'", 'prohibited_word': 'password',
        })
        entry = self._ok_data(r)
        self.assertIn('id', entry)
        self.assertEqual(entry['file'], 'code.py')
        self.assertEqual(entry['prohibited_word'], 'password')

    def test_suppression_stored_in_session_suppressions(self):
        scan_id = self._do_scan()
        r = self._post_suppression({
            'scan_id': scan_id, 'file': 'code.py',
            'line_content': "password = 'hunter2'", 'prohibited_word': 'password',
        })
        entry = self._ok_data(r)
        fp = entry['id']
        # Verify it's in the in-memory record
        record = web_module.scan_store[scan_id]
        self.assertIn(fp, record['session_suppressions'])

    def test_duplicate_suppression_is_idempotent(self):
        """Posting the same suppression twice returns 201 both times without duplication."""
        scan_id = self._do_scan()
        body = {
            'scan_id': scan_id, 'file': 'code.py',
            'line_content': "password = 'hunter2'", 'prohibited_word': 'password',
        }
        r1 = self._post_suppression(body)
        r2 = self._post_suppression(body)
        self.assertEqual(r1.status_code, 201)
        self.assertEqual(r2.status_code, 201)
        record = web_module.scan_store[scan_id]
        self.assertEqual(len(record['session_suppressions']), 1)

    def test_suppression_not_written_to_global_file(self):
        """Adding a session suppression must not create or modify the global file."""
        scan_id = self._do_scan()
        global_path = web_module._SUPPRESSIONS_FILE
        existed_before = os.path.exists(global_path)
        self._post_suppression({
            'scan_id': scan_id, 'file': 'code.py',
            'line_content': "password = 'hunter2'", 'prohibited_word': 'password',
        })
        if not existed_before:
            self.assertFalse(os.path.exists(global_path),
                             'Global suppressions file must not be created by a session suppress')

    def test_reason_stored_when_provided(self):
        scan_id = self._do_scan()
        r = self._post_suppression({
            'scan_id': scan_id, 'file': 'code.py',
            'line_content': "password = 'hunter2'", 'prohibited_word': 'password',
            'reason': 'test credential',
        })
        entry = self._ok_data(r)
        self.assertEqual(entry.get('reason'), 'test credential')


# ══════════════════════════════════════════════════════════════════════════════
# DELETE /api/v1/suppressions/<fingerprint> — undo a session suppression
# ══════════════════════════════════════════════════════════════════════════════

class TestV1SuppressionsDelete(V1TestCase):

    def _do_scan(self):
        r = self._post_zip_scan(_make_zip({'code.py': DIRTY_PY}))
        return self._ok_data(r)['id']

    def _suppress(self, scan_id):
        """Add a session suppression and return the fingerprint."""
        r = self.client.post(
            '/api/v1/suppressions',
            data=json.dumps({
                'scan_id': scan_id, 'file': 'code.py',
                'line_content': "password = 'hunter2'", 'prohibited_word': 'password',
            }),
            content_type='application/json',
        )
        return self._ok_data(r)['id']

    def _delete(self, fp, scan_id):
        return self.client.delete(
            f'/api/v1/suppressions/{fp}?scan_id={scan_id}'
        )

    def test_missing_scan_id_returns_400(self):
        scan_id = self._do_scan()
        fp = self._suppress(scan_id)
        r = self.client.delete(f'/api/v1/suppressions/{fp}')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self._err_body(r)['code'], 'VALIDATION_ERROR')

    def test_unknown_scan_id_returns_404(self):
        r = self._delete('deadbeefdeadbeef', 'deadbeef-0000-0000-0000-000000000000')
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self._err_body(r)['code'], 'NOT_FOUND')

    def test_unknown_fingerprint_returns_404(self):
        scan_id = self._do_scan()
        r = self._delete('deadbeefdeadbeef', scan_id)
        self.assertEqual(r.status_code, 404)

    def test_valid_delete_returns_204(self):
        scan_id = self._do_scan()
        fp = self._suppress(scan_id)
        r = self._delete(fp, scan_id)
        self.assertEqual(r.status_code, 204)

    def test_delete_removes_from_session_suppressions(self):
        scan_id = self._do_scan()
        fp = self._suppress(scan_id)
        self._delete(fp, scan_id)
        record = web_module.scan_store[scan_id]
        self.assertNotIn(fp, record['session_suppressions'])

    def test_delete_does_not_affect_other_suppressions(self):
        scan_id = self._do_scan()
        fp1 = self._suppress(scan_id)
        # Add a second suppression with different data
        r2 = self.client.post(
            '/api/v1/suppressions',
            data=json.dumps({
                'scan_id': scan_id, 'file': 'other.py',
                'line_content': "secret = 'x'", 'prohibited_word': 'secret',
            }),
            content_type='application/json',
        )
        fp2 = self._ok_data(r2)['id']
        self._delete(fp1, scan_id)
        record = web_module.scan_store[scan_id]
        self.assertNotIn(fp1, record['session_suppressions'])
        self.assertIn(fp2, record['session_suppressions'])

    def test_delete_does_not_touch_global_file(self):
        scan_id = self._do_scan()
        fp = self._suppress(scan_id)
        existed_before = os.path.exists(web_module._SUPPRESSIONS_FILE)
        self._delete(fp, scan_id)
        if not existed_before:
            self.assertFalse(os.path.exists(web_module._SUPPRESSIONS_FILE))


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/v1/scans/<uuid>/suppressions/export
# ══════════════════════════════════════════════════════════════════════════════

class TestV1SuppressionsExport(V1TestCase):

    def _do_dirty_scan(self):
        r = self._post_zip_scan(_make_zip({'code.py': DIRTY_PY}))
        return self._ok_data(r)['id']

    def _add_suppression(self, scan_id):
        self.client.post(
            '/api/v1/suppressions',
            data=json.dumps({
                'scan_id': scan_id, 'file': 'code.py',
                'line_content': "password = 'hunter2'", 'prohibited_word': 'password',
            }),
            content_type='application/json',
        )

    def test_export_nonexistent_scan_returns_404(self):
        r = self.client.get('/api/v1/scans/deadbeef-0000-0000-0000-000000000000/suppressions/export')
        self.assertEqual(r.status_code, 404)

    def test_export_returns_200(self):
        scan_id = self._do_dirty_scan()
        self._add_suppression(scan_id)
        r = self.client.get(f'/api/v1/scans/{scan_id}/suppressions/export')
        self.assertEqual(r.status_code, 200)

    def test_export_content_type_is_yaml(self):
        scan_id = self._do_dirty_scan()
        self._add_suppression(scan_id)
        r = self.client.get(f'/api/v1/scans/{scan_id}/suppressions/export')
        self.assertIn('yaml', r.content_type)

    def test_export_is_valid_yaml_with_suppressions_key(self):
        import yaml
        scan_id = self._do_dirty_scan()
        self._add_suppression(scan_id)
        r = self.client.get(f'/api/v1/scans/{scan_id}/suppressions/export')
        data = yaml.safe_load(r.data)
        self.assertIsInstance(data, dict)
        self.assertIn('suppressions', data)
        self.assertIsInstance(data['suppressions'], list)

    def test_export_includes_session_suppression(self):
        import yaml
        scan_id = self._do_dirty_scan()
        self._add_suppression(scan_id)
        r = self.client.get(f'/api/v1/scans/{scan_id}/suppressions/export')
        data = yaml.safe_load(r.data)
        self.assertEqual(len(data['suppressions']), 1)
        self.assertEqual(data['suppressions'][0]['file'], 'code.py')

    def test_export_merges_base_and_session_suppressions(self):
        """Merged YAML contains entries from both base_suppressions and session_suppressions."""
        import yaml
        from src.suppressions import make_fingerprint
        # Inject a fake base suppression directly into a scan record
        scan_id = self._do_dirty_scan()
        base_fp = make_fingerprint('other.py', 'secret = 1', 'secret')
        web_module.scan_store[scan_id]['base_suppressions'] = {
            base_fp: {'id': base_fp, 'file': 'other.py',
                      'line_content': 'secret = 1', 'prohibited_word': 'secret'},
        }
        self._add_suppression(scan_id)
        r = self.client.get(f'/api/v1/scans/{scan_id}/suppressions/export')
        data = yaml.safe_load(r.data)
        ids = {e['id'] for e in data['suppressions']}
        session_fp = list(web_module.scan_store[scan_id]['session_suppressions'].keys())[0]
        self.assertIn(base_fp,    ids)
        self.assertIn(session_fp, ids)

    def test_export_clean_scan_has_empty_suppressions_list(self):
        """A scan with no suppressions exports a valid YAML with an empty list."""
        import yaml
        scan_id = self._do_dirty_scan()  # no suppress call
        r = self.client.get(f'/api/v1/scans/{scan_id}/suppressions/export')
        self.assertEqual(r.status_code, 200)
        data = yaml.safe_load(r.data)
        self.assertEqual(data['suppressions'], [])


if __name__ == '__main__':
    unittest.main()
