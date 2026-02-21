"""
Web Interface for Repository Scanner
"""
from flask import Flask, render_template, request, jsonify, send_file
import base64
import ipaddress
import os
import json
import shutil
import socket
import subprocess
import yaml
import requests
from urllib.parse import urlparse
from src.scanner import ProhibitedWordScanner
import tempfile
from datetime import datetime

# Calculate template folder relative to project root
template_folder = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'templates')
app = Flask(__name__, template_folder=template_folder)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB max upload
app.config['DEBUG'] = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'

# Store scan results in memory (in production, use a database)
scan_history = []


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


# ── Flask routes ───────────────────────────────────────────────────────────

@app.route('/')
def index():
    """Render main page"""
    return render_template('index.html')


@app.route('/api/scan', methods=['POST'])
def scan():
    """Perform a scan — git repo, Artifactory path, or uploaded ZIP."""
    work_dir        = None
    words_path      = None
    words_is_server = False   # server_path words file belongs to the server — don't delete
    config_path     = None
    try:
        # ── Collect form fields ───────────────────────────────────────
        source_type        = request.form.get('source_type', 'git')
        config_source_type = request.form.get('config_source_type', 'upload')

        repo_url    = request.form.get('repo_url', '').strip()
        zip_file    = request.files.get('zip_file')

        # Config source fields
        cfg_upload      = request.files.get('prohibited_words_file')
        cfg_server_path = request.form.get('config_server_path', '').strip()
        cfg_git_url     = request.form.get('config_git_url', '').strip()
        cfg_file_path   = request.form.get('config_file_path', 'prohibited_words.txt').strip() \
                          or 'prohibited_words.txt'

        # Artifactory auth
        art_api_key  = request.form.get('art_api_key', '').strip()
        art_username = request.form.get('art_username', '').strip()
        art_password = request.form.get('art_password', '').strip()

        case_sensitive   = request.form.get('case_sensitive', 'false').lower() == 'true'
        max_file_size_mb = int(request.form.get('max_file_size_mb', '10'))
        max_file_size_bytes = max_file_size_mb * 1024 * 1024

        # ── Validate inputs ───────────────────────────────────────────
        if source_type in ('git', 'artifactory') and not repo_url:
            return jsonify({'error': 'Missing repository URL'}), 400
        if source_type == 'zip' and (not zip_file or not zip_file.filename):
            return jsonify({'error': 'No ZIP file provided'}), 400
        if config_source_type == 'upload' and (not cfg_upload or not cfg_upload.filename):
            return jsonify({'error': 'No prohibited words file provided'}), 400
        if config_source_type == 'server_path' and not cfg_server_path:
            return jsonify({'error': 'No server path provided for config'}), 400
        if config_source_type == 'git_repo' and not cfg_git_url:
            return jsonify({'error': 'No git URL provided for config'}), 400

        # ── Detect same-repo (clone once, exclude config path) ────────
        same_repo = (
            source_type == 'git'
            and config_source_type == 'git_repo'
            and _normalize_git_url(repo_url) == _normalize_git_url(cfg_git_url)
        )

        work_dir      = tempfile.mkdtemp(prefix='repo_scanner_')
        excluded_paths = []
        scan_label     = repo_url  # used in history

        # ── Fetch scan source ─────────────────────────────────────────
        if source_type == 'git':
            parsed = urlparse(repo_url)
            if parsed.scheme not in ('http', 'https', 'git', 'ssh') or not parsed.netloc:
                return jsonify({'error': 'Invalid repository URL — must be http, https, git, or ssh'}), 400
            if parsed.scheme in ('http', 'https'):
                _check_ssrf(repo_url)
            _clone_repo(repo_url, work_dir)

        elif source_type == 'artifactory':
            _check_ssrf(repo_url)
            auth_headers = _artifactory_headers(art_api_key, art_username, art_password)
            _scan_from_artifactory(repo_url, auth_headers, work_dir, max_file_size_bytes)

        elif source_type == 'zip':
            scan_label = zip_file.filename
            zip_file.save(os.path.join(work_dir, 'upload.zip'))

        # ── Resolve config (prohibited words file) ────────────────────
        words_path, _ = _resolve_words_file(
            config_source_type,
            upload          = cfg_upload,
            server_path     = cfg_server_path,
            git_url         = cfg_git_url,
            git_file_path   = cfg_file_path,
            existing_clone_dir = work_dir if same_repo else None,
        )
        words_is_server = (config_source_type == 'server_path')

        # ── Build excluded_paths for same-repo config ──────────────────
        if same_repo:
            config_dir = os.path.dirname(cfg_file_path)
            excl = os.path.join(work_dir, config_dir) if config_dir \
                   else os.path.join(work_dir, cfg_file_path)
            excluded_paths.append(excl)

        # ── Build scanner config and run ──────────────────────────────
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

        scanner = ProhibitedWordScanner(config_path)
        results  = scanner.scan_directory(work_dir, recursive=True)
        scanner.cleanup()

        scan_record = {
            'id':               len(scan_history),
            'timestamp':        datetime.now().isoformat(),
            'repo_path':        scan_label,
            'source_type':      source_type,
            'total_violations': len(results),
            'results':          results,
        }
        scan_history.append(scan_record)

        return jsonify({
            'success':          True,
            'scan_id':          scan_record['id'],
            'total_violations': len(results),
            'results':          results[:100],
            'has_more':         len(results) > 100,
        })

    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)
        if words_path and not words_is_server and os.path.exists(words_path):
            os.unlink(words_path)
        if config_path and os.path.exists(config_path):
            os.unlink(config_path)


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


if __name__ == '__main__':
    app.run(debug=app.config['DEBUG'], host='0.0.0.0', port=5000)
