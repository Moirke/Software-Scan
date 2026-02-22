# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Install dependencies:**
```bash
pip install -r requirements.txt
```

**Run tests:**
```bash
python -m unittest discover tests/
```

**Run a single test file:**
```bash
python -m unittest tests/test_scanner.py
```

**Run tests with coverage:**
```bash
coverage run -m unittest discover tests/
coverage report        # terminal summary (fails if below 75%)
coverage html          # browse htmlcov/index.html for line-by-line detail
```

**Run CLI scanner:**
```bash
python run-cli.py --config config/config.yaml --repo /path/to/scan
python run-cli.py -c config/config.yaml -r /path/to/repo -o results.txt --verbose
```

**Run linter:**
```bash
pylint src/
```

**Run web server** (accessible at http://localhost:5000):
```bash
python run-web.py
```

## Architecture

This is a repository scanner that detects prohibited words (credentials, dev markers, sensitive data) in source code and archives.

**Entry points:**
- `run-cli.py` → `src/cli.py` → CLI interface using argparse
- `run-web.py` → `src/web.py` → Flask web app
- `repo-scanner` (installed command) → same as `run-cli.py`

**Core engine:** `src/scanner.py` contains two classes:
- `ArchiveExtractor`: Extracts ZIP, TAR, RPM, and Docker image archives to temp dirs
- `ProhibitedWordScanner`: Main scanning class — loads config/word list, walks directories, detects binary files, handles archives recursively, and matches words with regex word boundaries (`\b...\b`)

**Configuration:** `config/config.yaml` controls case sensitivity, file size limits, excluded paths, and points to the prohibited words file (`config/prohibited_words.txt`). Config can be YAML or JSON.

**Web API endpoints** (`src/web.py`):
- `POST /api/scan` — runs a scan, stores results in memory
- `GET /api/history` — lists past scans
- `GET /api/scan/<id>` — retrieves a specific scan result
- `GET /api/export/<id>` — exports results as JSON

**Archive support:** ZIP, TAR (all compression variants), RPM (requires system `rpm2cpio`/`cpio`), and Docker image tarballs. Archives are extracted to temp dirs, scanned recursively, then cleaned up.

**CLI exit codes:** `0` = no violations, `1` = violations found, `2` = error.

**Tests** use `unittest` and live in `tests/` with fixtures in `tests/fixtures/`.

**CI/CD:** Four Jenkinsfile variants are in `ci/` (basic, Docker-based, parameterized, shared library).
