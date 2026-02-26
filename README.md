# Repository Scanner

Scans code repositories and archives for prohibited words — credentials,
dev markers, sensitive data — via a web UI, REST API, or CLI.

## Features

- **Deep scanning** — walks directories recursively, extracts and scans archives
- **Archive support** — ZIP, TAR (all variants), RPM, Docker image tarballs
- **Dual interface** — web UI for humans, REST API (`/api/v1/`) for pipelines
- **Exact and partial matching** — word-boundary matches flagged separately from substrings
- **PDF and JSON export** — shareable reports out of the box
- **Configurable** — YAML/JSON config, per-scan options (case sensitivity, file size limit, recursion)
- **Structured logging** — syslog-compatible format, configurable target (file, stdout, syslog)

## Installation

### Prerequisites

- Python 3.8 or higher
- For RPM support: `rpm2cpio` and `cpio` utilities

### Install Dependencies

```bash
pip install -r requirements.txt
```

On Ubuntu/Debian, install RPM tools:
```bash
sudo apt-get install rpm2cpio cpio
```

## Configuration

### Config File Format (YAML)

```yaml
# prohibited_words.txt path or inline list
prohibited_words_file: config/prohibited_words.txt

# Alternative: specify words directly
# prohibited_words:
#   - password
#   - secret

# Case sensitivity
case_sensitive: false

# Max file size to scan (in MB)
max_file_size_mb: 10
```

### Prohibited Words File

Three entry formats are supported, one per line:

```
# prohibited_words.txt

# Plain word — matched at word boundaries (reported as exact or partial)
password
secret
api_key
TODO

# Quoted literal — matched as a substring (always reported as partial)
# Use this to search for text that would otherwise be parsed as a prefix.
"regex:"

# Regex pattern — compiled and matched as-is (always reported as exact)
regex:AKIA[0-9A-Z]{16}
regex:sk-[A-Za-z0-9]{48}
```

Lines starting with `#` are treated as comments. Invalid regex patterns are skipped with a warning rather than failing the scan.

## Usage

### Command Line Interface

Basic usage:
```bash
python run-cli.py --config config/config.yaml --repo /path/to/repository
```

Save results to file:
```bash
python run-cli.py -c config/config.yaml -r /path/to/repo -o results.txt
```

Scan only top-level directory (no recursion):
```bash
python run-cli.py -c config/config.yaml -r /path/to/repo --no-recursive
```

Verbose output:
```bash
python run-cli.py -c config/config.yaml -r /path/to/repo --verbose
```

**Exit Codes:**
- `0`: No violations found
- `1`: Violations found
- `2`: Error occurred

### Web Interface

Start the web server:
```bash
python run-web.py
```

Then open your browser to: `http://localhost:5000`

The web interface provides:
- Interactive configuration editor
- Real-time scanning progress
- Visual results display
- Export functionality
- Scan history

## How It Works

1. **Configuration Loading**: Reads prohibited words from config file
2. **Directory Traversal**: Walks through the repository recursively
3. **Archive Detection**: Identifies compressed files by extension
4. **Automatic Extraction**: Extracts archives to temporary directories
5. **Content Scanning**: Searches text files for prohibited words using word boundary matching
6. **Results Collection**: Aggregates all violations with file paths and line numbers
7. **Cleanup**: Removes temporary extraction directories

## Archive Support Details

### Supported Formats

| Format | Extensions | Notes |
|--------|-----------|-------|
| ZIP | .zip | Full support |
| TAR | .tar, .tar.gz, .tgz, .tar.bz2, .tar.xz | All compression methods |
| RPM | .rpm | Requires rpm2cpio and cpio |
| Docker Images | .tar (with "docker" in name) | Extracts all layers |

### Nesting Depth Limit

Archives may contain other archives (e.g. a ZIP that contains a TAR that contains another ZIP).
To prevent runaway extraction from consuming all available disk space and CPU, the scanner limits
extraction to **10 levels of nesting**.

- Archives nested beyond level 10 are skipped with a log warning (`archive_depth_limit_reached`).
- The CLI prints a warning to stderr when any archives were skipped due to the limit.
- The web UI shows a warning banner in the results panel when the limit was hit.
- The API includes a `depth_limit_hits` field in every scan response indicating how many archives
  were skipped.

If you encounter this limit with a legitimate repository, consider pre-extracting the deeply
nested archives and scanning the extracted contents directly.

### Docker Image Scanning

Docker images saved as TAR files are automatically detected and all layers are extracted and scanned.

Example:
```bash
# Save a Docker image
docker save myimage:latest -o myimage.tar

# Scan it
python run-cli.py -c config/config.yaml -r myimage.tar
```

## Examples

### Example 1: Scan a Git Repository

```bash
python run-cli.py \
  --config config/config.yaml \
  --repo /home/user/projects/myapp \
  --output scan_results.txt \
  --verbose
```

### Example 2: Scan a ZIP Archive

```bash
python run-cli.py -c config/config.yaml -r archive.zip
```

### Example 3: Custom Configuration

Create `custom_config.yaml`:
```yaml
prohibited_words:
  - hardcoded_password
  - admin123
  - test_key
case_sensitive: true
max_file_size_mb: 5
```

Run scan:
```bash
python run-cli.py -c config/custom_config.yaml -r /path/to/code
```

### Example 4: JSON Configuration

Create `config.json`:
```json
{
  "prohibited_words_file": "words.txt",
  "case_sensitive": false,
  "max_file_size_mb": 10
}
```

## Output Format

### CLI Output

```
================================================================================
SCAN RESULTS: Found 3 violation(s)
================================================================================

File: /path/to/file.py
Violations: 2
--------------------------------------------------------------------------------
  Line 15: Found 'password'
    db_password = "secret123"

  Line 42: Found 'TODO'
    # TODO: Fix this later

File: /path/to/config.json
Violations: 1
--------------------------------------------------------------------------------
  Line 8: Found 'api_key'
    "api_key": "abc123xyz"
```

### Web UI

The web interface displays:
- Total violation count
- Violations grouped by file
- Line numbers and content
- Highlighted prohibited words
- Export to JSON functionality

## Advanced Usage

### Integrating with CI/CD

See [docs/runbooks/ci-cd-integration.md](docs/runbooks/ci-cd-integration.md) for ready-to-use
Jenkinsfile examples (basic, Docker agent, parameterized warn/fail, and shared library).

Quick bash reference for non-Jenkins pipelines:

```bash
python run-cli.py -c config/config.yaml -r .
if [ $? -eq 1 ]; then
  echo "Prohibited words found! Build failed."
  exit 1
fi
```

### Custom File Extensions

The scanner automatically skips binary files based on:
- File extension (exe, dll, so, jpg, png, etc.)
- Content detection (presence of null bytes)

### Performance Tuning

For large repositories:
- Adjust `max_file_size_mb` to skip large files
- Use `--no-recursive` to scan only top-level
- Configure `.gitignore`-style exclusions in your scanning workflow

## Limitations

- Binary files are skipped (cannot search within compiled code)
- Very large files (> configured limit) are skipped
- Encrypted archives cannot be extracted
- Some proprietary archive formats may not be supported

## Troubleshooting

### RPM extraction fails
Install required tools:
```bash
sudo apt-get install rpm2cpio cpio
```

### Memory issues with large repositories
Reduce `max_file_size_mb` or scan subdirectories separately.

### Web UI not accessible
Check firewall settings and ensure port 5000 is open.

## Security Considerations

- Temporary files are created during archive extraction
- Ensure adequate disk space for large archives
- Temporary directories are cleaned up after scanning
- In production, consider using a database for scan history instead of in-memory storage

## Documentation

### Tutorials (start here)

| Guide | Description |
|---|---|
| [Your first scan](docs/tutorials/your-first-scan.md) | Walk through the web UI end to end |
| [Scanning with the REST API](docs/tutorials/scanning-with-the-rest-api.md) | `curl` examples for all v1 endpoints |
| [Running with Gunicorn](docs/tutorials/running-with-gunicorn.md) | Production-like local setup |
| [Deploying to Rocky Linux](docs/tutorials/deploying-to-rocky-linux.md) | Full on-premise VM deployment with Docker + Nginx + TLS |
| [Setting up the Jenkins pipeline](docs/tutorials/setting-up-the-jenkins-pipeline.md) | Create the build job that produces the Docker image artifact |

### Runbooks (day-to-day operations)

| Runbook | When to use it |
|---|---|
| [Upgrade](docs/runbooks/upgrade.md) | Deploy a new build artifact to the VM |
| [Renew certificates](docs/runbooks/renew-certificates.md) | Annual TLS cert renewal |
| [Restart service](docs/runbooks/restart-service.md) | Service unresponsive or config changed |
| [Diagnose scan failure](docs/runbooks/diagnose-scan-failure.md) | Scan errors, hangs, or unexpected results |
| [CI/CD integration](docs/runbooks/ci-cd-integration.md) | Add a scan step to Jenkins or other CI pipelines |
| [HTTP-only deployment](docs/runbooks/http-only-deployment.md) | Deploy without TLS on isolated internal networks |

## License

MIT License - feel free to use and modify as needed.
