# Scanning with the REST API

The `/api/v1/` endpoints let you integrate the scanner into scripts, CI pipelines,
and other automated workflows.  All examples use `curl` and `jq`.

## Before you start

Start the application:

```bash
python run-web.py
```

Verify the API is up:

```bash
curl http://localhost:5000/api/v1/health
```

Expected response:

```json
{"data": {"status": "ok"}}
```

---

## Concepts

Every response from the v1 API uses a consistent envelope:

```json
{ "data": <payload>,  "meta": <pagination info> }   // success
{ "error": { "code": "...", "message": "..." } }     // error
```

Scans are identified by a **UUID** returned when you submit the scan.  Hold on to
this ID — you need it to fetch results, export, or delete.

---

## Submitting a scan

### Option A — ZIP file upload (multipart)

Zip the code you want to scan, then POST it along with a prohibited words file:

```bash
zip -r /tmp/my-project.zip ./my-project

curl -X POST http://localhost:5000/api/v1/scans \
  -F "source_type=zip" \
  -F "config_source_type=upload" \
  -F "zip_file=@/tmp/my-project.zip" \
  -F "prohibited_words_file=@/path/to/words.txt"
```

### Option B — Server path (JSON)

If the scanner is running on the same machine as your code, point it at a local path:

```bash
curl -X POST http://localhost:5000/api/v1/scans \
  -H "Content-Type: application/json" \
  -d '{
    "source_type": "server_path",
    "repo_path": "/path/to/your/project",
    "config_source_type": "server_path",
    "config_server_path": "/etc/scanner/prohibited_words.txt"
  }'
```

### Option C — Git repository (JSON)

```bash
curl -X POST http://localhost:5000/api/v1/scans \
  -H "Content-Type: application/json" \
  -d '{
    "source_type": "git",
    "repo_url": "https://github.com/owner/repo",
    "config_source_type": "server_path",
    "config_server_path": "/etc/scanner/prohibited_words.txt"
  }'
```

### Scan options

Add any of these to your request body or form data:

| Field | Type | Default | Description |
|---|---|---|---|
| `recursive` | bool | `true` | Scan subdirectories |
| `case_sensitive` | bool | `false` | Case-sensitive word matching |
| `max_file_size_mb` | int | `10` | Skip files larger than this |

Example with options:

```bash
curl -X POST http://localhost:5000/api/v1/scans \
  -H "Content-Type: application/json" \
  -d '{
    "source_type": "server_path",
    "repo_path": "/path/to/project",
    "config_source_type": "server_path",
    "config_server_path": "/etc/scanner/words.txt",
    "case_sensitive": true,
    "max_file_size_mb": 50
  }'
```

### Reading the response

A successful scan returns the scan summary plus the first page of violations:

```json
{
  "data": {
    "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "legacy_id": 0,
    "timestamp": "2026-02-21T17:30:00.123456",
    "target": "/path/to/project",
    "source_type": "server_path",
    "total_violations": 12,
    "exact_violations": 8,
    "partial_violations": 4,
    "words_evaluated": 6,
    "recursive": true,
    "case_sensitive": false,
    "max_file_size_mb": 10,
    "results": [ ... first page of violations ... ]
  },
  "meta": {
    "page": 1,
    "limit": 20,
    "total": 12,
    "total_pages": 1,
    "duration_ms": 143
  }
}
```

Save the `id` for subsequent requests:

```bash
SCAN_ID=$(curl -s -X POST http://localhost:5000/api/v1/scans \
  -H "Content-Type: application/json" \
  -d '{ ... }' | jq -r '.data.id')
```

---

## Fetching results

### Scan summary (no violations list)

```bash
curl http://localhost:5000/api/v1/scans/$SCAN_ID
```

### Paginated violations

```bash
# First page, default page size (20)
curl "http://localhost:5000/api/v1/scans/$SCAN_ID/results"

# Specific page and size
curl "http://localhost:5000/api/v1/scans/$SCAN_ID/results?page=2&limit=50"
```

### Filter by match type

```bash
# Exact matches only
curl "http://localhost:5000/api/v1/scans/$SCAN_ID/results?match_type=exact"

# Partial matches only
curl "http://localhost:5000/api/v1/scans/$SCAN_ID/results?match_type=partial"
```

Each violation in the list looks like this:

```json
{
  "file": "/path/to/project/src/config.py",
  "line_number": 42,
  "line_content": "password = 'hunter2'",
  "prohibited_word": "password",
  "match_type": "exact",
  "position": 0
}
```

---

## Listing past scans

```bash
# Most recent scans first (default page size 20)
curl http://localhost:5000/api/v1/scans

# Page through older scans
curl "http://localhost:5000/api/v1/scans?page=2&limit=10"
```

---

## Exporting results

### JSON export (full scan record)

```bash
curl -O http://localhost:5000/api/v1/scans/$SCAN_ID/export.json
```

### PDF report

```bash
curl -O http://localhost:5000/api/v1/scans/$SCAN_ID/export.pdf
```

---

## Deleting a scan

```bash
curl -X DELETE http://localhost:5000/api/v1/scans/$SCAN_ID
# Returns 204 No Content on success
```

Deleted scans are removed from the scan list and all subsequent lookups by UUID
will return 404.

---

## Checking metrics

```bash
curl http://localhost:5000/api/v1/metrics
```

Returns live counters for this process instance: scans started, completed, failed,
total violations found, average scan duration, and a breakdown by source type.

---

## Using the API in a shell script

A minimal example that scans a directory and exits with a non-zero code if
violations are found — useful in CI:

```bash
#!/usr/bin/env bash
set -euo pipefail

SCANNER_URL="http://localhost:5000"
REPO_PATH="${1:?Usage: $0 <path-to-scan>}"
WORDS_FILE="/etc/scanner/prohibited_words.txt"

response=$(curl -s -X POST "$SCANNER_URL/api/v1/scans" \
  -H "Content-Type: application/json" \
  -d "{
    \"source_type\": \"server_path\",
    \"repo_path\": \"$REPO_PATH\",
    \"config_source_type\": \"server_path\",
    \"config_server_path\": \"$WORDS_FILE\"
  }")

violations=$(echo "$response" | jq '.data.total_violations')

if [ "$violations" -gt 0 ]; then
  echo "FAIL: $violations violation(s) found in $REPO_PATH"
  echo "$response" | jq '.data.results[]'
  exit 1
else
  echo "PASS: no violations found"
fi
```

---

## Error responses

All errors follow the same shape:

```json
{ "error": { "code": "NOT_FOUND", "message": "Scan abc123 not found" } }
```

Common codes:

| Code | HTTP status | Meaning |
|---|---|---|
| `VALIDATION_ERROR` | 400 | Missing or invalid request field |
| `SCAN_FAILED` | 422 | Scan was submitted but failed (e.g. bad path, clone failed) |
| `NOT_FOUND` | 404 | Scan UUID does not exist or was deleted |
| `SERVER_ERROR` | 500 | Unexpected server fault |
