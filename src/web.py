"""
Web Interface for Repository Scanner
"""
from flask import Flask, render_template, request, jsonify, send_file, Response
import base64
import io
import ipaddress
import logging
import os
import json
import queue
import shutil
import socket
import subprocess
import threading
import uuid
import yaml
import requests
from urllib.parse import urlparse, urlunparse
from src.scanner import ProhibitedWordScanner
from src.report import generate_pdf
from src.logging_config import LOGGER_NAME, ScanAdapter
from src.suppressions import (
    load_suppressions, apply_suppressions,
    add_suppression, remove_suppression, make_fingerprint,
)
import src.metrics as metrics
import tempfile
import time
from datetime import datetime

_log = logging.getLogger(LOGGER_NAME)

# Calculate template folder relative to project root
template_folder = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'templates')
app = Flask(__name__, template_folder=template_folder)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB max upload
app.config['DEBUG'] = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'

# Scan storage — shared by v0 (list/index) and v1 (UUID dict)
scan_history: list  = []          # v0: indexed by integer scan_id
scan_store:   dict  = {}          # v1: keyed by UUID string

# Concurrency cap — at most 5 scans may run simultaneously
_SCAN_CONCURRENCY_LIMIT = 5
_scan_semaphore = threading.Semaphore(_SCAN_CONCURRENCY_LIMIT)

# Feedback log — written to a host-mounted volume in Docker deployments
_FEEDBACK_FILE = os.environ.get('FEEDBACK_FILE', '/feedback/feedback.log')

# Suppressions file — operators can override with SUPPRESSIONS_FILE env var
_SUPPRESSIONS_FILE = os.environ.get('SUPPRESSIONS_FILE', 'config/suppressions.yaml')


class _InMemoryFile:
    """
    Wraps bytes captured from a Werkzeug FileStorage before the request
    context closes, so a background thread can call .save() safely.
    """
    def __init__(self, data: bytes, filename: str):
        self.filename = filename
        self._data    = data

    def save(self, dst):
        if isinstance(dst, (str, os.PathLike)):
            with open(dst, 'wb') as f:
                f.write(self._data)
        else:
            dst.write(self._data)


def _sse(event: str, data: dict) -> str:
    """Format a single Server-Sent Event."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


_SSE_HEADERS = {
    'Cache-Control':     'no-cache',
    'X-Accel-Buffering': 'no',   # tell Nginx not to buffer this response
}


# ── SSRF protection ───────────────────────────────────────────────────────

def _check_ssrf(url):
    """
    Resolve the URL's hostname and raise ValueError if any resolved address
    is private, loopback, link-local, or otherwise non-routable.
    Protects against SSRF attacks that probe internal services.
    """
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("Could not parse a hostname from the URL.")

    try:
        results = socket.getaddrinfo(hostname, None)
    except socket.gaierror as e:
        raise ValueError(f"Could not resolve hostname '{hostname}': {e}")

    for result in results:
        raw_addr = result[4][0]
        try:
            ip = ipaddress.ip_address(raw_addr)
        except ValueError:
            continue
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            raise ValueError(
                f"Requests to private or internal addresses are not allowed "
                f"('{hostname}' resolved to {raw_addr})."
            )


# ── Artifactory helpers ────────────────────────────────────────────────────

def _artifactory_headers(api_key, username, password):
    """Build Artifactory auth headers from whichever credentials were supplied."""
    if api_key:
        return {'X-JFrog-Art-Api': api_key}
    if username and password:
        creds = base64.b64encode(f"{username}:{password}".encode()).decode()
        return {'Authorization': f'Basic {creds}'}
    return {}


def _parse_artifactory_url(url):
    """
    Split an Artifactory URL into (base_url, repo_key, item_path).

    Example:
        https://company.jfrog.io/artifactory/libs-release/com/example/1.0/app.zip
        → ('https://company.jfrog.io', 'libs-release', 'com/example/1.0/app.zip')
    """
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path

    idx = path.find('/artifactory/')
    if idx == -1:
        raise ValueError(
            "URL does not contain /artifactory/ — does not look like an Artifactory URL."
        )

    after = path[idx + len('/artifactory/'):]
    parts = after.split('/', 1)
    repo_key = parts[0]
    item_path = parts[1] if len(parts) > 1 else ''
    return base, repo_key, item_path


def _download_artifact(url, dest_path, headers, max_size):
    """
    Stream-download a single Artifactory artifact to dest_path.
    Skips the file if it exceeds max_size bytes.
    """
    with requests.get(url, headers=headers, stream=True, timeout=120) as r:
        r.raise_for_status()
        content_length = int(r.headers.get('content-length', 0))
        if content_length and content_length > max_size:
            return  # Skip — scanner will also skip oversized files

        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        downloaded = 0
        with open(dest_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=65536):
                downloaded += len(chunk)
                if downloaded > max_size:
                    break
                f.write(chunk)


def _scan_from_artifactory(url, auth_headers, temp_dir, max_file_size):
    """
    Inspect the Artifactory storage API at *url*, then download either the
    single artifact or all files under a folder path into *temp_dir*.
    """
    base, repo_key, item_path = _parse_artifactory_url(url)
    storage_url = f"{base}/artifactory/api/storage/{repo_key}/{item_path}"

    resp = requests.get(storage_url, headers=auth_headers, timeout=30)
    if resp.status_code == 401:
        raise ValueError("Artifactory authentication failed — check your credentials.")
    if resp.status_code == 404:
        raise ValueError(f"Artifactory path not found: {url}")
    resp.raise_for_status()
    info = resp.json()

    if 'downloadUri' in info:
        # ── Single file ──────────────────────────────────────────────
        filename = os.path.basename(item_path.rstrip('/')) or 'artifact'
        dest = os.path.join(temp_dir, filename)
        _download_artifact(info['downloadUri'], dest, auth_headers, max_file_size)

    elif 'children' in info or 'uri' in info:
        # ── Folder — enumerate with the deep file-list API ───────────
        list_url = f"{storage_url}?list&deep=1&listFolders=0"
        list_resp = requests.get(list_url, headers=auth_headers, timeout=30)
        list_resp.raise_for_status()
        files = list_resp.json().get('files', [])

        if not files:
            raise ValueError("No files found at the specified Artifactory path.")

        for entry in files:
            file_uri = entry['uri']          # e.g. /subdir/app-1.0.jar
            file_size = entry.get('size', 0)
            if file_size and file_size > max_file_size:
                continue  # Skip oversized files

            download_url = (
                f"{base}/artifactory/{repo_key}/"
                f"{item_path.rstrip('/')}{file_uri}"
            )
            rel_path = file_uri.lstrip('/')
            dest = os.path.join(temp_dir, rel_path)
            _download_artifact(download_url, dest, auth_headers, max_file_size)

    else:
        raise ValueError("Unexpected response from Artifactory storage API.")


# ── Git helpers ───────────────────────────────────────────────────────────

_DEFAULT_WORDS_FILE = 'prohibited_words.txt'


def _split_config_git_url(url: str) -> tuple[str, str]:
    """
    Parse a config source URL and return (clone_url, words_file_path).

    Supports GitHub, GitLab, and Bitbucket web-UI URLs that may embed a
    directory or file path after the repo slug.  If the resolved path ends
    with a directory (no file extension on the final segment),
    _DEFAULT_WORDS_FILE is appended automatically.

    Examples
    --------
    github.com/org/repo                              → (..., 'prohibited_words.txt')
    github.com/org/repo/config                       → (..., 'config/prohibited_words.txt')
    github.com/org/repo/tree/main/config             → (..., 'config/prohibited_words.txt')
    github.com/org/repo/blob/main/config/words.txt   → (..., 'config/words.txt')
    """
    parsed = urlparse(url)
    hostname = (parsed.hostname or '').lower()
    parts = [p for p in parsed.path.split('/') if p]

    def _finish(base: str, rest: list) -> tuple[str, str]:
        if not rest:
            return base, _DEFAULT_WORDS_FILE
        # Last segment with a '.' is treated as a filename; otherwise directory
        if '.' in rest[-1]:
            return base, '/'.join(rest)
        return base, '/'.join(rest) + '/' + _DEFAULT_WORDS_FILE

    if 'github.com' in hostname:
        if len(parts) < 2:
            return url, _DEFAULT_WORDS_FILE
        base = f"{parsed.scheme}://{parsed.netloc}/{parts[0]}/{parts[1]}"
        rest = parts[2:]
        if rest and rest[0] in ('tree', 'blob') and len(rest) >= 2:
            rest = rest[2:]   # strip tree/<branch> or blob/<branch>
        return _finish(base, rest)

    if 'gitlab.com' in hostname or hostname.startswith('gitlab.'):
        if len(parts) < 2:
            return url, _DEFAULT_WORDS_FILE
        base = f"{parsed.scheme}://{parsed.netloc}/{parts[0]}/{parts[1]}"
        rest = parts[2:]
        if len(rest) >= 3 and rest[0] == '-' and rest[1] in ('tree', 'blob'):
            rest = rest[3:]
        elif len(rest) >= 2 and rest[0] in ('tree', 'blob'):
            rest = rest[2:]
        return _finish(base, rest)

    if 'bitbucket.org' in hostname:
        if len(parts) < 2:
            return url, _DEFAULT_WORDS_FILE
        base = f"{parsed.scheme}://{parsed.netloc}/{parts[0]}/{parts[1]}"
        rest = parts[2:]
        if len(rest) >= 2 and rest[0] == 'src':
            rest = rest[2:]   # strip src/<branch>
        return _finish(base, rest)

    return url, _DEFAULT_WORDS_FILE


def _sanitize_url(url: str) -> str:
    """Strip userinfo (credentials) from a URL before logging."""
    try:
        p = urlparse(url)
        if p.username or p.password:
            netloc = p.hostname or ''
            if p.port:
                netloc += f':{p.port}'
            return urlunparse(p._replace(netloc=netloc))
    except Exception:
        pass
    return url


def _normalize_git_url(url: str) -> str:
    """Strip trailing slashes and .git suffix for reliable URL comparison."""
    url = url.strip().rstrip('/')
    if url.endswith('.git'):
        url = url[:-4]
    return url.lower()


def _clone_repo(url: str, dest_dir: str) -> None:
    """Clone a git repo (shallow) into dest_dir, raising ValueError on failure."""
    result = subprocess.run(
        ['git', 'clone', '--depth=1', url, dest_dir],
        capture_output=True, text=True, timeout=300
    )
    if result.returncode != 0:
        raise ValueError(f'Failed to clone repository: {result.stderr.strip()}')


def _resolve_words_file(config_source_type: str, upload, server_path: str,
                        git_url: str, git_file_path: str,
                        existing_clone_dir: str | None) -> tuple[str, str | None]:
    """
    Resolve the prohibited-words file from whichever config source was chosen.

    Returns (words_path, config_clone_dir) where:
      - words_path        is the absolute path to the words file to use
      - config_clone_dir  is a temp dir to clean up (None if not needed)

    The caller is responsible for deleting words_path unless words_path_is_server
    is True (server_path case — the file belongs to the server, don't delete it).
    """
    if config_source_type == 'upload':
        with tempfile.NamedTemporaryFile(mode='wb', suffix='.txt', delete=False) as f:
            upload.save(f)
            return f.name, None

    if config_source_type == 'server_path':
        path = server_path.strip()
        if os.path.isdir(path):
            candidate = os.path.join(path, 'prohibited_words.txt')
            if not os.path.isfile(candidate):
                raise ValueError(f"No prohibited_words.txt found in folder: {path}")
            return candidate, None
        if os.path.isfile(path):
            return path, None
        raise ValueError(f"Config path does not exist: {path}")

    # git_repo — use an existing clone or clone fresh
    if existing_clone_dir:
        src = os.path.join(existing_clone_dir, git_file_path)
        if not os.path.isfile(src):
            raise ValueError(f"Config file not found in repository: {git_file_path}")
        with tempfile.NamedTemporaryFile(mode='wb', suffix='.txt', delete=False) as f:
            shutil.copy2(src, f.name)
            return f.name, None

    config_clone_dir = tempfile.mkdtemp(prefix='repo_scanner_cfg_')
    try:
        _clone_repo(git_url, config_clone_dir)
    except ValueError:
        shutil.rmtree(config_clone_dir, ignore_errors=True)
        raise
    src = os.path.join(config_clone_dir, git_file_path)
    if not os.path.isfile(src):
        shutil.rmtree(config_clone_dir, ignore_errors=True)
        raise ValueError(f"Config file not found in repository: {git_file_path}")
    with tempfile.NamedTemporaryFile(mode='wb', suffix='.txt', delete=False) as f:
        shutil.copy2(src, f.name)
        words_path = f.name
    shutil.rmtree(config_clone_dir, ignore_errors=True)
    return words_path, None


# ── Finding enrichment ─────────────────────────────────────────────────────

def _enrich_findings(results: list, repo_root: str) -> list:
    """
    Add ``fingerprint`` and ``relative_file`` to each finding dict in-place.
    Returns the same list (mutated).
    """
    for r in results:
        try:
            rel = os.path.relpath(r['file'], repo_root)
        except ValueError:
            rel = r['file']
        r['relative_file'] = rel
        r['fingerprint'] = make_fingerprint(
            rel, r.get('line_content', ''), r.get('prohibited_word', '')
        )
    return results


# ── Core scan execution ────────────────────────────────────────────────────

def _execute_scan_core(
    *,
    source_type:        str,
    repo_url:           str  = '',
    repo_server_path:   str  = '',
    zip_file                 = None,   # werkzeug FileStorage or _InMemoryFile or None
    zip_filename:       str  = '',
    config_source_type: str,
    cfg_upload               = None,   # werkzeug FileStorage or _InMemoryFile or None
    cfg_server_path:    str  = '',
    cfg_git_url:        str  = '',
    cfg_file_path:      str  = _DEFAULT_WORDS_FILE,
    art_api_key:        str  = '',
    art_username:       str  = '',
    art_password:       str  = '',
    case_sensitive:     bool = False,
    max_file_size_mb:   int  = 10,
    recursive:          bool = True,
    scan_uuid:          str,
    remote_addr:        str  = '',
    slog,
    on_progress              = None,   # callable(event_type, payload) | None
) -> dict:
    """
    Execute a scan and return a scan-record dict (without the integer 'id').
    Handles source fetching, config resolution, scanning, and temp-file cleanup.
    Raises ValueError for client/input errors, Exception for server faults.
    """
    words_path      = None
    words_is_server = False
    config_path     = None
    work_dir        = None

    def _emit(event_type, payload):
        if on_progress:
            on_progress(event_type, payload)

    # Throttle per-file progress: emit at most once every 150 ms
    _last_progress_t = [0.0]
    _THROTTLE_S = 0.15

    def _file_progress(files_scanned, path):
        now = time.monotonic()
        if now - _last_progress_t[0] >= _THROTTLE_S:
            _last_progress_t[0] = now
            _emit('progress', {
                'files_scanned': files_scanned,
                'current_file':  os.path.basename(path),
            })

    try:
        max_file_size_bytes = max_file_size_mb * 1024 * 1024

        scan_label = (zip_filename       if source_type == 'zip'
                      else repo_server_path if source_type == 'server_path'
                      else repo_url)

        slog.debug('scan_requested source_type=%s remote_addr=%s', source_type, remote_addr)
        slog.info('scan_started source_type=%s target=%s',
                  source_type, _sanitize_url(scan_label))

        # same_repo: git scan + git config from same URL → clone once
        same_repo = (
            source_type == 'git'
            and config_source_type == 'git_repo'
            and _normalize_git_url(repo_url) == _normalize_git_url(cfg_git_url)
        )
        excluded_paths = []

        # ── Fetch scan source ─────────────────────────────────────────
        if source_type == 'server_path':
            if not os.path.exists(repo_server_path):
                raise ValueError(f'Path does not exist on server: {repo_server_path}')
            scan_target = repo_server_path

        else:
            work_dir    = tempfile.mkdtemp(prefix='repo_scanner_')
            scan_target = work_dir

            if source_type == 'git':
                parsed = urlparse(repo_url)
                if parsed.scheme not in ('http', 'https', 'git', 'ssh') or not parsed.netloc:
                    raise ValueError(
                        'Invalid repository URL — must be http, https, git, or ssh'
                    )
                if parsed.scheme in ('http', 'https'):
                    try:
                        _check_ssrf(repo_url)
                    except ValueError as ssrf_exc:
                        slog.warning('ssrf_blocked url=%s reason=%r',
                                     _sanitize_url(repo_url), str(ssrf_exc))
                        raise
                _emit('phase', {'message': 'Cloning repository\u2026'})
                clone_start = time.monotonic()
                slog.debug('clone_started url=%s', _sanitize_url(repo_url))
                try:
                    _clone_repo(repo_url, work_dir)
                except ValueError as exc:
                    slog.error('clone_failed url=%s stderr=%r',
                               _sanitize_url(repo_url), str(exc))
                    raise
                slog.info('clone_completed url=%s duration_ms=%d',
                          _sanitize_url(repo_url),
                          int((time.monotonic() - clone_start) * 1000))

            elif source_type == 'artifactory':
                _check_ssrf(repo_url)
                auth_headers = _artifactory_headers(art_api_key, art_username, art_password)
                _emit('phase', {'message': 'Downloading from Artifactory\u2026'})
                slog.info('artifactory_download_started url=%s', _sanitize_url(repo_url))
                try:
                    _scan_from_artifactory(repo_url, auth_headers, work_dir, max_file_size_bytes)
                except Exception as exc:
                    slog.error('artifactory_download_failed url=%s error=%r',
                               _sanitize_url(repo_url), str(exc))
                    raise

            elif source_type == 'zip':
                zip_file.save(os.path.join(work_dir, 'upload.zip'))

        # ── Resolve config (prohibited words file) ────────────────────
        words_path, _ = _resolve_words_file(
            config_source_type,
            upload             = cfg_upload,
            server_path        = cfg_server_path,
            git_url            = cfg_git_url,
            git_file_path      = cfg_file_path,
            existing_clone_dir = work_dir if same_repo else None,
        )
        words_is_server = (config_source_type == 'server_path')

        # ── Exclude config file from scan when both live in the same repo ──
        if same_repo:
            config_dir = os.path.dirname(cfg_file_path)
            excl = (os.path.join(work_dir, config_dir) if config_dir
                    else os.path.join(work_dir, cfg_file_path))
            excluded_paths.append(excl)

        # ── Run scanner ───────────────────────────────────────────────
        scanner_config: dict = {
            'prohibited_words_file': words_path,
            'case_sensitive':        case_sensitive,
            'max_file_size_mb':      max_file_size_mb,
        }
        if excluded_paths:
            scanner_config['excluded_paths'] = excluded_paths

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            yaml.dump(scanner_config, f)
            config_path = f.name

        scanner = ProhibitedWordScanner(config_path, logger=slog.logger)
        slog.info('config_words_resolved source=%s words_count=%d',
                  config_source_type, len(scanner.prohibited_words))

        _emit('phase', {'message': 'Scanning files\u2026'})
        results         = scanner.scan_directory(
            scan_target,
            recursive=recursive,
            on_progress=_file_progress if on_progress else None,
        )
        words_evaluated = list(scanner.prohibited_words)
        scanner.cleanup()

        # Enrich findings with fingerprint + relative_file
        _enrich_findings(results, scan_target)

        # Apply suppressions
        suppressions = load_suppressions(_SUPPRESSIONS_FILE)
        results, suppressed_count = apply_suppressions(results, scan_target, suppressions)

        exact_count   = sum(1 for r in results if r.get('match_type') == 'exact')
        partial_count = sum(1 for r in results if r.get('match_type') == 'partial')
        files_scanned = len({r['file'] for r in results})

        slog.info(
            'scan_completed files_scanned=%d violations=%d exact=%d partial=%d suppressed=%d',
            files_scanned, len(results), exact_count, partial_count, suppressed_count,
        )

        return {
            'uuid':              scan_uuid,
            'timestamp':         datetime.now().isoformat(),
            'repo_path':         scan_label,
            'source_type':       source_type,
            'case_sensitive':    case_sensitive,
            'max_file_size_mb':  max_file_size_mb,
            'recursive':         recursive,
            'words_evaluated':   words_evaluated,
            'total_violations':  len(results),
            'exact_violations':  exact_count,
            'partial_violations': partial_count,
            'suppressed_count':  suppressed_count,
            'results':           results,
        }

    finally:
        if work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)
        if words_path and not words_is_server and os.path.exists(words_path):
            os.unlink(words_path)
        if config_path and os.path.exists(config_path):
            os.unlink(config_path)


# ── Flask routes ───────────────────────────────────────────────────────────

@app.route('/health')
def health():
    """
    Lightweight health check for load balancer probes.
    Returns 200 as long as the process is alive and can import its deps.
    No I/O or heavy work — must respond in < 1 s.
    """
    return jsonify({'status': 'ok'}), 200


@app.route('/')
def index():
    """Render main page"""
    metrics.record_page_view()
    return render_template('index.html')


@app.route('/api/scan', methods=['POST'])
def scan():
    """Perform a scan — git repo, Artifactory path, server path, or uploaded ZIP."""
    if not _scan_semaphore.acquire(blocking=False):
        return jsonify({'error': 'Too many concurrent scans — please try again shortly'}), 429
    scan_recorded = False
    scan_start    = time.monotonic()
    scan_uuid     = str(uuid.uuid4())
    slog          = ScanAdapter(_log, scan_uuid[:8])
    try:
        source_type        = request.form.get('source_type', 'git')
        config_source_type = request.form.get('config_source_type', 'upload')
        repo_url           = request.form.get('repo_url', '').strip()
        repo_server_path   = request.form.get('repo_server_path', '').strip()
        zip_file           = request.files.get('zip_file')
        cfg_upload         = request.files.get('prohibited_words_file')
        cfg_server_path    = request.form.get('config_server_path', '').strip()
        cfg_git_url        = request.form.get('config_git_url', '').strip()
        art_api_key        = request.form.get('art_api_key', '').strip()
        art_username       = request.form.get('art_username', '').strip()
        art_password       = request.form.get('art_password', '').strip()
        case_sensitive     = request.form.get('case_sensitive', 'false').lower() == 'true'
        max_file_size_mb   = int(request.form.get('max_file_size_mb', '10'))
        recursive          = request.form.get('recursive', 'true').lower() != 'false'

        if config_source_type == 'git_repo' and cfg_git_url:
            cfg_git_url, cfg_file_path = _split_config_git_url(cfg_git_url)
        else:
            cfg_file_path = _DEFAULT_WORDS_FILE

        if source_type in ('git', 'artifactory') and not repo_url:
            return jsonify({'error': 'Missing repository URL'}), 400
        if source_type == 'server_path' and not repo_server_path:
            return jsonify({'error': 'Missing repo_server_path'}), 400
        if source_type == 'zip' and (not zip_file or not zip_file.filename):
            return jsonify({'error': 'No ZIP file provided'}), 400
        if config_source_type == 'upload' and (not cfg_upload or not cfg_upload.filename):
            return jsonify({'error': 'No prohibited words file provided'}), 400
        if config_source_type == 'server_path' and not cfg_server_path:
            return jsonify({'error': 'No server path provided for config'}), 400
        if config_source_type == 'git_repo' and not cfg_git_url:
            return jsonify({'error': 'No git URL provided for config'}), 400

        metrics.record_scan_started(source_type)
        scan_recorded = True

        record = _execute_scan_core(
            source_type        = source_type,
            repo_url           = repo_url,
            repo_server_path   = repo_server_path,
            zip_file           = zip_file,
            zip_filename       = zip_file.filename if zip_file else '',
            config_source_type = config_source_type,
            cfg_upload         = cfg_upload,
            cfg_server_path    = cfg_server_path,
            cfg_git_url        = cfg_git_url,
            cfg_file_path      = cfg_file_path,
            art_api_key        = art_api_key,
            art_username       = art_username,
            art_password       = art_password,
            case_sensitive     = case_sensitive,
            max_file_size_mb   = max_file_size_mb,
            recursive          = recursive,
            scan_uuid          = scan_uuid,
            remote_addr        = request.remote_addr or '',
            slog               = slog,
        )

        record['id'] = len(scan_history)
        scan_history.append(record)
        scan_store[scan_uuid] = record

        duration_ms = int((time.monotonic() - scan_start) * 1000)
        metrics.record_scan_completed(duration_ms, record['total_violations'])

        results = record['results']
        return jsonify({
            'success':            True,
            'scan_id':            record['id'],
            'scan_uuid':          scan_uuid,
            'total_violations':   record['total_violations'],
            'exact_violations':   record['exact_violations'],
            'partial_violations': record['partial_violations'],
            'suppressed_count':   record.get('suppressed_count', 0),
            'results':            results[:100],
            'has_more':           len(results) > 100,
        })

    except ValueError as e:
        if scan_recorded:
            metrics.record_scan_failed()
        slog.error('scan_failed error=%r', str(e))
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        if scan_recorded:
            metrics.record_scan_failed()
        slog.error('scan_failed error=%r', str(e), exc_info=True)
        return jsonify({'error': str(e)}), 500
    finally:
        _scan_semaphore.release()


@app.route('/api/scan/stream', methods=['POST'])
def scan_stream():
    """
    Same inputs as POST /api/scan, but streams Server-Sent Events while the
    scan runs instead of blocking until completion.

    Event types:
      phase    {"message": "Cloning repository…"}
      progress {"files_scanned": 42, "current_file": "src/main.py"}
      complete {same payload as /api/scan success response}
      error    {"error": "..."}
    """
    if not _scan_semaphore.acquire(blocking=False):
        def _reject():
            yield _sse('error', {
                'error': 'Too many concurrent scans — please try again shortly'
            })
        return Response(_reject(), content_type='text/event-stream',
                        headers=_SSE_HEADERS)

    # ── Read all request data now (request context closes when we return) ──
    scan_uuid = str(uuid.uuid4())
    slog      = ScanAdapter(_log, scan_uuid[:8])

    source_type        = request.form.get('source_type', 'git')
    config_source_type = request.form.get('config_source_type', 'upload')
    repo_url           = request.form.get('repo_url', '').strip()
    repo_server_path   = request.form.get('repo_server_path', '').strip()
    cfg_server_path    = request.form.get('config_server_path', '').strip()
    cfg_git_url        = request.form.get('config_git_url', '').strip()
    art_api_key        = request.form.get('art_api_key', '').strip()
    art_username       = request.form.get('art_username', '').strip()
    art_password       = request.form.get('art_password', '').strip()
    case_sensitive     = request.form.get('case_sensitive', 'false').lower() == 'true'
    max_file_size_mb   = int(request.form.get('max_file_size_mb', '10'))
    recursive          = request.form.get('recursive', 'true').lower() != 'false'
    remote_addr        = request.remote_addr or ''

    # Buffer file uploads into memory before the request context closes
    raw_zip    = request.files.get('zip_file')
    raw_words  = request.files.get('prohibited_words_file')
    zip_file   = (_InMemoryFile(raw_zip.read(),   raw_zip.filename)
                  if raw_zip and raw_zip.filename else None)
    cfg_upload = (_InMemoryFile(raw_words.read(), raw_words.filename)
                  if raw_words and raw_words.filename else None)

    if config_source_type == 'git_repo' and cfg_git_url:
        cfg_git_url, cfg_file_path = _split_config_git_url(cfg_git_url)
    else:
        cfg_file_path = _DEFAULT_WORDS_FILE

    # ── Validate ──────────────────────────────────────────────────────────
    def _validation_err(msg):
        _scan_semaphore.release()
        def _gen():
            yield _sse('error', {'error': msg})
        return Response(_gen(), content_type='text/event-stream',
                        headers=_SSE_HEADERS)

    if source_type in ('git', 'artifactory') and not repo_url:
        return _validation_err('Missing repository URL')
    if source_type == 'server_path' and not repo_server_path:
        return _validation_err('Missing repo_server_path')
    if source_type == 'zip' and not zip_file:
        return _validation_err('No ZIP file provided')
    if config_source_type == 'upload' and not cfg_upload:
        return _validation_err('No prohibited words file provided')
    if config_source_type == 'server_path' and not cfg_server_path:
        return _validation_err('No server path provided for config')
    if config_source_type == 'git_repo' and not cfg_git_url:
        return _validation_err('No git URL provided for config')

    # ── Set up event queue and background thread ───────────────────────────
    event_q       = queue.Queue()
    scan_recorded = [False]
    scan_start    = time.monotonic()

    def _on_progress(event_type, payload):
        event_q.put((event_type, payload))

    def _run():
        try:
            metrics.record_scan_started(source_type)
            scan_recorded[0] = True

            record = _execute_scan_core(
                source_type        = source_type,
                repo_url           = repo_url,
                repo_server_path   = repo_server_path,
                zip_file           = zip_file,
                zip_filename       = zip_file.filename if zip_file else '',
                config_source_type = config_source_type,
                cfg_upload         = cfg_upload,
                cfg_server_path    = cfg_server_path,
                cfg_git_url        = cfg_git_url,
                cfg_file_path      = cfg_file_path,
                art_api_key        = art_api_key,
                art_username       = art_username,
                art_password       = art_password,
                case_sensitive     = case_sensitive,
                max_file_size_mb   = max_file_size_mb,
                recursive          = recursive,
                scan_uuid          = scan_uuid,
                remote_addr        = remote_addr,
                slog               = slog,
                on_progress        = _on_progress,
            )

            record['id'] = len(scan_history)
            scan_history.append(record)
            scan_store[scan_uuid] = record

            duration_ms = int((time.monotonic() - scan_start) * 1000)
            metrics.record_scan_completed(duration_ms, record['total_violations'])

            results = record['results']
            event_q.put(('complete', {
                'success':            True,
                'scan_id':            record['id'],
                'scan_uuid':          scan_uuid,
                'total_violations':   record['total_violations'],
                'exact_violations':   record['exact_violations'],
                'partial_violations': record['partial_violations'],
                'suppressed_count':   record.get('suppressed_count', 0),
                'results':            results[:100],
                'has_more':           len(results) > 100,
            }))

        except ValueError as e:
            if scan_recorded[0]:
                metrics.record_scan_failed()
            slog.error('scan_failed error=%r', str(e))
            event_q.put(('error', {'error': str(e)}))
        except Exception as e:
            if scan_recorded[0]:
                metrics.record_scan_failed()
            slog.error('scan_failed error=%r', str(e), exc_info=True)
            event_q.put(('error', {'error': str(e)}))
        finally:
            event_q.put(None)          # sentinel — tells generator to stop
            _scan_semaphore.release()

    threading.Thread(target=_run, daemon=True).start()

    # ── Stream events to the client ───────────────────────────────────────
    def _generate():
        while True:
            try:
                item = event_q.get(timeout=620)   # slightly > Gunicorn timeout
            except queue.Empty:
                yield _sse('error', {'error': 'Scan timed out'})
                break
            if item is None:
                break
            event_type, payload = item
            yield _sse(event_type, payload)

    return Response(_generate(), content_type='text/event-stream',
                    headers=_SSE_HEADERS)


@app.route('/api/history')
def get_history():
    """Get scan history"""
    return jsonify([{
        'id': s['id'],
        'timestamp': s['timestamp'],
        'repo_path': s['repo_path'],
        'source_type': s.get('source_type', 'git'),
        'total_violations': s['total_violations'],
    } for s in scan_history])


@app.route('/api/scan/<int:scan_id>')
def get_scan(scan_id):
    """Get specific scan results"""
    if scan_id >= len(scan_history):
        return jsonify({'error': 'Scan not found'}), 404
    return jsonify(scan_history[scan_id])


@app.route('/api/export/<int:scan_id>')
def export_scan(scan_id):
    """Export scan results as JSON"""
    if scan_id >= len(scan_history):
        return jsonify({'error': 'Scan not found'}), 404

    scan_data = scan_history[scan_id]
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(scan_data, f, indent=2)
        temp_path = f.name

    return send_file(
        temp_path,
        as_attachment=True,
        download_name=f"scan_results_{scan_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )


@app.route('/api/metrics')
def get_metrics():
    """
    Current usage metrics for this process instance.

    Values reset on restart and are scoped to a single worker process.
    In a multi-instance deployment, scrape each instance separately and
    aggregate in your monitoring stack.
    """
    return jsonify(metrics.get_snapshot())


@app.route('/api/export/<int:scan_id>/pdf')
def export_scan_pdf(scan_id):
    """Export scan results as a formatted PDF report."""
    if scan_id >= len(scan_history):
        return jsonify({'error': 'Scan not found'}), 404

    pdf_bytes = generate_pdf(scan_history[scan_id])
    filename  = f"scan_report_{scan_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"

    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype='application/pdf',
        as_attachment=True,
        download_name=filename,
    )


# ── API v1 ─────────────────────────────────────────────────────────────────
#
# All responses use a consistent envelope:
#   success → {"data": ..., "meta": ...}
#   error   → {"error": {"code": "...", "message": "..."}}
#
# Scan IDs in v1 are UUIDs (stable across restarts, safe for sharing).
# The legacy integer scan_id from /api/scan is still available in the
# 'legacy_id' field of every v1 scan record.

_V1_PAGE_DEFAULT  = 1
_V1_LIMIT_DEFAULT = 20
_V1_LIMIT_MAX     = 200


def _v1_ok(data, *, meta=None, status: int = 200):
    body = {'data': data}
    if meta:
        body['meta'] = meta
    return jsonify(body), status


def _v1_err(code: str, message: str, status: int):
    return jsonify({'error': {'code': code, 'message': message}}), status


def _v1_paginate(items: list, page: int, limit: int) -> tuple:
    limit       = max(1, min(limit, _V1_LIMIT_MAX))
    total       = len(items)
    total_pages = max(1, (total + limit - 1) // limit)
    page        = max(1, min(page, total_pages))
    start       = (page - 1) * limit
    meta = {'page': page, 'limit': limit, 'total': total, 'total_pages': total_pages}
    return items[start:start + limit], meta


def _v1_scan_record(record: dict) -> dict:
    """Serialisable scan summary — no results array."""
    return {
        'id':               record['uuid'],
        'legacy_id':        record.get('id'),
        'timestamp':        record['timestamp'],
        'target':           record['repo_path'],
        'source_type':      record.get('source_type', 'unknown'),
        'case_sensitive':   record.get('case_sensitive', False),
        'max_file_size_mb': record.get('max_file_size_mb', 10),
        'recursive':        record.get('recursive', True),
        'total_violations': record['total_violations'],
        'exact_violations': record['exact_violations'],
        'partial_violations': record['partial_violations'],
        'words_evaluated':  len(record.get('words_evaluated', [])),
    }


def _v1_get_body(key, default=''):
    """Read a field from a JSON body or multipart form, stripping whitespace."""
    if request.is_json:
        val = (request.json or {}).get(key, default)
    else:
        val = request.form.get(key, default)
    return val.strip() if isinstance(val, str) else val


def _v1_get_scan(scan_uuid: str) -> dict | None:
    record = scan_store.get(scan_uuid)
    if record and record.get('_deleted'):
        return None
    return record


# ── v1: health & metrics ───────────────────────────────────────────────────

@app.route('/api/v1/health')
def v1_health():
    return _v1_ok({'status': 'ok'})


@app.route('/api/v1/metrics')
def v1_metrics():
    return _v1_ok(metrics.get_snapshot())


# ── v1: scans collection ───────────────────────────────────────────────────

@app.route('/api/v1/scans', methods=['GET'])
def v1_scans_list():
    """Paginated list of all scans. ?page=1&limit=20"""
    try:
        page  = int(request.args.get('page',  _V1_PAGE_DEFAULT))
        limit = int(request.args.get('limit', _V1_LIMIT_DEFAULT))
    except ValueError:
        return _v1_err('VALIDATION_ERROR', 'page and limit must be integers', 400)

    visible = [r for r in scan_history if not r.get('_deleted')]
    visible.reverse()   # newest first
    page_items, meta = _v1_paginate(visible, page, limit)
    return _v1_ok([_v1_scan_record(r) for r in page_items], meta=meta)


@app.route('/api/v1/scans', methods=['POST'])
def v1_scans_post():
    """
    Submit a scan.  Accepts application/json or multipart/form-data.
    File uploads (zip source, words upload) require multipart.
    Returns the scan record plus the first page of violations.
    """
    if not _scan_semaphore.acquire(blocking=False):
        return _v1_err('TOO_MANY_REQUESTS',
                       f'Server is already running {_SCAN_CONCURRENCY_LIMIT} concurrent scans — '
                       f'please try again shortly', 429)
    scan_recorded = False
    scan_start    = time.monotonic()
    scan_uuid     = str(uuid.uuid4())
    slog          = ScanAdapter(_log, scan_uuid[:8])
    try:
        source_type        = _v1_get_body('source_type', 'git')
        config_source_type = _v1_get_body('config_source_type', 'upload')
        repo_url           = _v1_get_body('repo_url')
        repo_server_path   = _v1_get_body('repo_path')    # v1 uses 'repo_path'
        cfg_git_url        = _v1_get_body('config_git_url')
        cfg_server_path    = _v1_get_body('config_server_path')
        art_api_key        = _v1_get_body('art_api_key')
        art_username       = _v1_get_body('art_username')
        art_password       = _v1_get_body('art_password')
        recursive          = _v1_get_body('recursive', True)
        case_sensitive     = _v1_get_body('case_sensitive', False)
        max_file_size_mb   = _v1_get_body('max_file_size_mb', 10)

        # Coerce types when coming from JSON (already correct type) or form (strings)
        if isinstance(recursive, str):
            recursive = recursive.lower() != 'false'
        if isinstance(case_sensitive, str):
            case_sensitive = case_sensitive.lower() == 'true'
        try:
            max_file_size_mb = int(max_file_size_mb)
        except (TypeError, ValueError):
            return _v1_err('VALIDATION_ERROR', 'max_file_size_mb must be an integer', 400)

        # File uploads (multipart only)
        zip_file   = request.files.get('zip_file')
        cfg_upload = request.files.get('prohibited_words_file')

        if config_source_type == 'git_repo' and cfg_git_url:
            cfg_git_url, cfg_file_path = _split_config_git_url(cfg_git_url)
        else:
            cfg_file_path = _DEFAULT_WORDS_FILE

        # ── Validate ──────────────────────────────────────────────────
        valid_sources = ('git', 'artifactory', 'zip', 'server_path')
        if source_type not in valid_sources:
            return _v1_err('VALIDATION_ERROR',
                           f'source_type must be one of: {", ".join(valid_sources)}', 400)
        if source_type in ('git', 'artifactory') and not repo_url:
            return _v1_err('VALIDATION_ERROR', 'repo_url is required for git and artifactory sources', 400)
        if source_type == 'server_path' and not repo_server_path:
            return _v1_err('VALIDATION_ERROR', 'repo_path is required for server_path source', 400)
        if source_type == 'zip' and (not zip_file or not zip_file.filename):
            return _v1_err('VALIDATION_ERROR', 'zip_file upload is required for zip source', 400)
        if config_source_type == 'upload' and (not cfg_upload or not cfg_upload.filename):
            return _v1_err('VALIDATION_ERROR', 'prohibited_words_file upload is required', 400)
        if config_source_type == 'server_path' and not cfg_server_path:
            return _v1_err('VALIDATION_ERROR', 'config_server_path is required', 400)
        if config_source_type == 'git_repo' and not cfg_git_url:
            return _v1_err('VALIDATION_ERROR', 'config_git_url is required', 400)

        metrics.record_scan_started(source_type)
        scan_recorded = True

        record = _execute_scan_core(
            source_type        = source_type,
            repo_url           = repo_url,
            repo_server_path   = repo_server_path,
            zip_file           = zip_file,
            zip_filename       = zip_file.filename if zip_file else '',
            config_source_type = config_source_type,
            cfg_upload         = cfg_upload,
            cfg_server_path    = cfg_server_path,
            cfg_git_url        = cfg_git_url,
            cfg_file_path      = cfg_file_path,
            art_api_key        = art_api_key,
            art_username       = art_username,
            art_password       = art_password,
            case_sensitive     = case_sensitive,
            max_file_size_mb   = max_file_size_mb,
            recursive          = recursive,
            scan_uuid          = scan_uuid,
            remote_addr        = request.remote_addr or '',
            slog               = slog,
        )

        record['id'] = len(scan_history)
        scan_history.append(record)
        scan_store[scan_uuid] = record

        duration_ms = int((time.monotonic() - scan_start) * 1000)
        metrics.record_scan_completed(duration_ms, record['total_violations'])

        results           = record['results']
        page_items, pmeta = _v1_paginate(results, 1, _V1_LIMIT_DEFAULT)
        pmeta['duration_ms'] = duration_ms

        data = _v1_scan_record(record)
        data['suppressed_count'] = record.get('suppressed_count', 0)
        data['results'] = page_items
        return _v1_ok(data, meta=pmeta)

    except ValueError as e:
        if scan_recorded:
            metrics.record_scan_failed()
        slog.error('scan_failed error=%r', str(e))
        return _v1_err('SCAN_FAILED', str(e), 422)
    except Exception as e:
        if scan_recorded:
            metrics.record_scan_failed()
        slog.error('scan_failed error=%r', str(e), exc_info=True)
        return _v1_err('SERVER_ERROR', str(e), 500)
    finally:
        _scan_semaphore.release()


# ── v1: individual scan ────────────────────────────────────────────────────

@app.route('/api/v1/scans/<scan_uuid>', methods=['GET'])
def v1_scan_get(scan_uuid):
    """Scan metadata (no results — use /results for violations)."""
    record = _v1_get_scan(scan_uuid)
    if record is None:
        return _v1_err('NOT_FOUND', f'Scan {scan_uuid} not found', 404)
    return _v1_ok(_v1_scan_record(record))


@app.route('/api/v1/scans/<scan_uuid>', methods=['DELETE'])
def v1_scan_delete(scan_uuid):
    """Remove a scan from the store. Returns 204 No Content."""
    record = _v1_get_scan(scan_uuid)
    if record is None:
        return _v1_err('NOT_FOUND', f'Scan {scan_uuid} not found', 404)
    record['_deleted'] = True
    scan_store.pop(scan_uuid, None)
    return '', 204


# ── v1: paginated results ──────────────────────────────────────────────────

@app.route('/api/v1/scans/<scan_uuid>/results', methods=['GET'])
def v1_scan_results(scan_uuid):
    """
    Paginated violations for a scan.
    ?page=1&limit=50&match_type=exact|partial
    """
    record = _v1_get_scan(scan_uuid)
    if record is None:
        return _v1_err('NOT_FOUND', f'Scan {scan_uuid} not found', 404)

    try:
        page  = int(request.args.get('page',  _V1_PAGE_DEFAULT))
        limit = int(request.args.get('limit', _V1_LIMIT_DEFAULT))
    except ValueError:
        return _v1_err('VALIDATION_ERROR', 'page and limit must be integers', 400)

    match_type = request.args.get('match_type', '').lower()
    results    = record['results']
    if match_type in ('exact', 'partial'):
        results = [r for r in results if r.get('match_type') == match_type]

    page_items, meta = _v1_paginate(results, page, limit)
    return _v1_ok(page_items, meta=meta)


# ── v1: exports ────────────────────────────────────────────────────────────

@app.route('/api/v1/scans/<scan_uuid>/export.json', methods=['GET'])
def v1_export_json(scan_uuid):
    """Download full scan record as JSON."""
    record = _v1_get_scan(scan_uuid)
    if record is None:
        return _v1_err('NOT_FOUND', f'Scan {scan_uuid} not found', 404)

    buf      = io.BytesIO(json.dumps(record, indent=2).encode())
    filename = f"scan_{scan_uuid[:8]}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    return send_file(buf, mimetype='application/json',
                     as_attachment=True, download_name=filename)


@app.route('/api/v1/scans/<scan_uuid>/export.pdf', methods=['GET'])
def v1_export_pdf(scan_uuid):
    """Download formatted PDF report."""
    record = _v1_get_scan(scan_uuid)
    if record is None:
        return _v1_err('NOT_FOUND', f'Scan {scan_uuid} not found', 404)

    pdf_bytes = generate_pdf(record)
    filename  = f"scan_{scan_uuid[:8]}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    return send_file(io.BytesIO(pdf_bytes), mimetype='application/pdf',
                     as_attachment=True, download_name=filename)


# ── v1: suppressions ───────────────────────────────────────────────────────

@app.route('/api/v1/suppressions', methods=['GET'])
def v1_suppressions_list():
    """List all suppression entries."""
    suppressions = load_suppressions(_SUPPRESSIONS_FILE)
    return _v1_ok(list(suppressions.values()))


@app.route('/api/v1/suppressions', methods=['POST'])
def v1_suppressions_add():
    """
    Add a suppression.
    Body: {"file": str, "line_content": str, "prohibited_word": str, "reason": str}
    """
    body = request.get_json(silent=True) or {}
    rel_file      = str(body.get('file',           '')).strip()
    line_content  = str(body.get('line_content',   '')).strip()
    prohibited_word = str(body.get('prohibited_word', '')).strip()
    reason        = str(body.get('reason',         '')).strip()

    if not rel_file:
        return _v1_err('VALIDATION_ERROR', 'file is required', 400)
    if not line_content:
        return _v1_err('VALIDATION_ERROR', 'line_content is required', 400)
    if not prohibited_word:
        return _v1_err('VALIDATION_ERROR', 'prohibited_word is required', 400)

    try:
        fp = add_suppression(_SUPPRESSIONS_FILE, rel_file, line_content, prohibited_word, reason)
    except Exception as exc:
        _log.error('suppression_add_failed error=%r', str(exc))
        return _v1_err('SERVER_ERROR', str(exc), 500)

    suppressions = load_suppressions(_SUPPRESSIONS_FILE)
    entry = suppressions.get(fp, {'id': fp})
    return _v1_ok(entry, meta={}), 201


@app.route('/api/v1/suppressions/<fingerprint>', methods=['DELETE'])
def v1_suppressions_delete(fingerprint):
    """Remove a suppression by fingerprint. Returns 204 No Content."""
    try:
        found = remove_suppression(_SUPPRESSIONS_FILE, fingerprint)
    except Exception as exc:
        _log.error('suppression_remove_failed error=%r', str(exc))
        return _v1_err('SERVER_ERROR', str(exc), 500)
    if not found:
        return _v1_err('NOT_FOUND', f'Suppression {fingerprint} not found', 404)
    return '', 204


# ── Feedback ───────────────────────────────────────────────────────────────

@app.route('/api/feedback', methods=['POST'])
def submit_feedback():
    """
    Record a star rating (1-5) and optional comment for a scan.
    Appends a JSON line to _FEEDBACK_FILE on the host.
    Body: {"rating": int, "scan_id": str, "comment": str (optional)}
    """
    body    = request.get_json(silent=True) or {}
    rating  = body.get('rating')
    comment = str(body.get('comment', '')).strip()
    scan_id = str(body.get('scan_id', '')).strip()

    if not isinstance(rating, int) or not (1 <= rating <= 5):
        return jsonify({'error': 'rating must be an integer between 1 and 5'}), 400

    entry = {
        'timestamp': datetime.now().isoformat(),
        'scan_id':   scan_id or None,
        'rating':    rating,
        'comment':   comment or None,
    }
    try:
        os.makedirs(os.path.dirname(_FEEDBACK_FILE), exist_ok=True)
        with open(_FEEDBACK_FILE, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry) + '\n')
    except OSError as exc:
        _log.error('feedback_write_failed path=%s error=%r', _FEEDBACK_FILE, str(exc))
        return jsonify({'error': 'Could not save feedback'}), 500

    _log.info('feedback_received scan_id=%s rating=%d', scan_id or 'none', rating)
    return jsonify({'success': True}), 201


if __name__ == '__main__':
    app.run(debug=app.config['DEBUG'], host='0.0.0.0', port=5000)
