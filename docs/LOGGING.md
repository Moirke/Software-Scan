# Logging Strategy

---

## Objectives

Production incidents in a scanning service tend to fall into a few patterns:

- **Silent scan failures** — a file or directory was skipped and nobody noticed,
  so a violation was missed.
- **Performance regressions** — slow git clones or oversized archive extraction
  that are invisible until a user complains.
- **Config problems** — wrong words-file path, expired credentials, stale config.
- **Infrastructure issues** — disk full during extraction, tmpfs exhausted.

A consistent structured log strategy lets an operator diagnose any of these
from a log stream without attaching a debugger or reproducing the environment.

---

## Format — Syslog Compatibility

We follow **RFC 5424** lightweight conventions so log lines can be forwarded to
syslog daemons, Splunk, Datadog, CloudWatch, or any other aggregator without a
custom parser.

### Line structure

```
<ISO8601-TIMESTAMP> <HOSTNAME> repo-scanner[<PID>]: <LEVEL> <EVENT> [key=value ...]
```

| Field     | Example                      | Notes                                           |
|-----------|------------------------------|-------------------------------------------------|
| Timestamp | `2026-02-21T10:14:32.401Z`   | UTC, millisecond precision, ISO 8601 / RFC 3339 |
| Hostname  | `prod-scanner-01`            | `socket.gethostname()`                          |
| App + PID | `repo-scanner[4821]`         | Literal app name, OS pid                        |
| Level     | `WARNING`                    | Uppercase long form (see below)                 |
| Event     | `scan_completed`             | snake_case event name — always first token      |
| Key=value | `scan_id=17 violations=3`    | No spaces inside values; quote if needed        |

### Example lines

```
2026-02-21T10:14:30.011Z prod-01 repo-scanner[4821]: INFO scan_started scan_id=17 source=git target=github.com/org/repo
2026-02-21T10:14:30.045Z prod-01 repo-scanner[4821]: INFO config_loaded path=/etc/scanner/config.yaml words=42 case_sensitive=false
2026-02-21T10:14:32.110Z prod-01 repo-scanner[4821]: DEBUG clone_started scan_id=17 url=github.com/org/repo
2026-02-21T10:14:34.800Z prod-01 repo-scanner[4821]: WARNING file_skipped scan_id=17 reason=size path=data/dump.sql size_mb=47 limit_mb=10
2026-02-21T10:14:35.200Z prod-01 repo-scanner[4821]: INFO scan_completed scan_id=17 files=1203 violations=3 exact=1 partial=2 duration_ms=3100
2026-02-21T10:14:40.000Z prod-01 repo-scanner[4821]: ERROR clone_failed scan_id=18 url=github.com/org/private stderr="authentication_required"
2026-02-21T10:14:40.001Z prod-01 repo-scanner[4821]: ERROR scan_failed scan_id=18 error="Failed to clone repository"
2026-02-21T10:15:00.000Z prod-01 repo-scanner[4821]: CRITICAL words_file_not_found path=/etc/scanner/words.txt
```

### Python formatter

```python
LOG_FORMAT = (
    '%(asctime)s %(hostname)s repo-scanner[%(process)d]: '
    '%(levelname)s %(message)s'
)
```

A custom `logging.Formatter` subclass will:
1. Format `asctime` as ISO 8601 UTC with milliseconds.
2. Inject `hostname` via a `logging.Filter` (avoids threading it through every call site).

---

## Log Levels

Five levels are used. The gap between WARNING and CRITICAL is bridged by ERROR,
which maps directly to syslog severity 3 and allows ops to page on CRITICAL,
watch dashboards for ERROR, and review WARNING asynchronously.

| Level    | Syslog severity   | When to use in this service |
|----------|-------------------|-----------------------------|
| DEBUG    | 7 — Debug         | File-by-file trace, individual word matches, archive layer steps. Available but off by default. |
| INFO     | 6 — Informational | Scan lifecycle events, config loaded, archive extracted. Always on in production. |
| WARNING  | 4 — Warning       | Non-fatal anomalies: skipped files, shallow-clone caveats, unrecognised config keys. Scan continues. |
| ERROR    | 3 — Error         | A scan request failed (clone error, words file missing). Service stays up; other scans unaffected. |
| CRITICAL | 2 — Critical      | The process cannot serve any requests: missing startup config, fatal import error. |

Default production level is **INFO**. `LOG_LEVEL=DEBUG` is available for
temporary diagnosis but will emit file paths and match details — do not leave
it enabled in production.

---

## Event Catalog

Every event emitted within a scan includes `scan_id=<N>`. Startup and health
check events omit it.

Individual word matches are logged at DEBUG. The total match count is captured
at INFO via `scan_completed` (`violations`, `exact`, `partial` fields), keeping
the INFO stream readable even on large repos with many hits.

### Service startup

| Event                  | Level    | Key fields                                              |
|------------------------|----------|---------------------------------------------------------|
| `logging_initialized`  | INFO     | `level`, `target`                                       |
| `config_loaded`        | INFO     | `path`, `words_count`, `case_sensitive`, `max_file_size_mb` |
| `config_not_found`     | CRITICAL | `path`                                                  |
| `words_file_not_found` | CRITICAL | `path`                                                  |
| `startup_failed`       | CRITICAL | `error`                                                 |

### Web API / request lifecycle

| Event                          | Level   | Key fields                                                           |
|--------------------------------|---------|----------------------------------------------------------------------|
| `scan_requested`               | DEBUG   | `scan_id`, `source_type`, `remote_addr`                              |
| `scan_started`                 | INFO    | `scan_id`, `source_type`, `target`                                   |
| `ssrf_blocked`                 | WARNING | `scan_id`, `url`, `resolved_ip`                                      |
| `clone_started`                | DEBUG   | `scan_id`, `url`                                                     |
| `clone_completed`              | INFO    | `scan_id`, `url`, `duration_ms`                                      |
| `clone_failed`                 | ERROR   | `scan_id`, `url`, `stderr`                                           |
| `artifactory_download_started` | INFO    | `scan_id`, `url`, `files`                                            |
| `artifactory_download_failed`  | ERROR   | `scan_id`, `url`, `status_code`                                      |
| `config_words_resolved`        | INFO    | `scan_id`, `source`, `words_count`                                   |
| `scan_completed`               | INFO    | `scan_id`, `files_scanned`, `violations`, `exact`, `partial`, `duration_ms` |
| `scan_failed`                  | ERROR   | `scan_id`, `error`                                                   |

### Scanner / per-file

| Event                       | Level   | Key fields                                |
|-----------------------------|---------|-------------------------------------------|
| `file_scanning`             | DEBUG   | `scan_id`, `path`                         |
| `file_skipped_binary`       | DEBUG   | `scan_id`, `path`                         |
| `file_skipped_size`         | WARNING | `scan_id`, `path`, `size_mb`, `limit_mb`  |
| `file_skipped_permission`   | WARNING | `scan_id`, `path`, `error`                |
| `archive_extracting`        | INFO    | `scan_id`, `path`, `format`               |
| `archive_extraction_failed` | WARNING | `scan_id`, `path`, `error`                |
| `match_found`               | DEBUG   | `scan_id`, `path`, `line`, `word`, `match_type` |

---

## Sensitive Data — What NOT to Log

| Data                             | Rule                                                                             |
|----------------------------------|----------------------------------------------------------------------------------|
| Credential-bearing URLs          | Strip userinfo before logging: `https://token@host/` → `https://host/`          |
| Artifactory API keys / passwords | Never log auth headers or credential values                                      |
| `line_content` of matches        | Stays in the API response only — matched lines can contain the secrets being hunted |
| Full config file body            | Log path and metadata only, never contents                                       |

---

## Correlation — Scan ID

Every log line within a scan is tagged `scan_id=<N>`, allowing an operator to
filter a single scan's full timeline from a busy log stream.

Propagated via a `ScanAdapter` (`logging.LoggerAdapter` subclass) that injects
`scan_id` into every message automatically. Scanner internals accept a logger or
adapter as a constructor parameter — no globals.

```python
# In web.py, at the start of each scan request:
scan_log = ScanAdapter(logging.getLogger('repo_scanner'), scan_id)

scan_log.info('scan_started source=%s target=%s', source_type, target)
# → INFO scan_started scan_id=17 source=git target=github.com/org/repo
```

---

## Output Targets & Configuration

Controlled entirely by environment variables — no code changes between environments.

| Variable         | Default                                  | Description                                                             |
|------------------|------------------------------------------|-------------------------------------------------------------------------|
| `LOG_LEVEL`      | `INFO`                                   | Minimum level to emit (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`) |
| `LOG_TARGET`     | `file`                                   | Where to write: `file`, `stdout`, `stderr`, or `syslog`                 |
| `LOG_FILE`       | `/var/log/repo-scanner/scanner.log`      | Path to the log file when `LOG_TARGET=file`                             |
| `SYSLOG_ADDRESS` | `/dev/log`                               | Unix socket path or `host:port` when `LOG_TARGET=syslog`                |

The default target is `file` at `/var/log/repo-scanner/scanner.log`. The
application will create the directory on startup if it does not exist. If the
directory cannot be created or written to, startup fails with a `CRITICAL` log
to stderr and a non-zero exit code. When `LOG_TARGET=syslog`, facility
`LOG_LOCAL0` is used. Set `LOG_TARGET=stdout` for Docker or Kubernetes
deployments where a collector reads container stdout.

---

## Implementation

Changes are applied in this order, each independently testable:

1. **`src/logging_config.py`** *(new)*
   - `configure_logging()` — reads env vars, builds the formatter, attaches the
     appropriate handler, sets root level.
   - `ScanAdapter` — `LoggerAdapter` subclass that prepends `scan_id=N` to
     every message.

2. **`run-web.py` and `run-cli.py`**
   - Call `configure_logging()` before any other imports.

3. **`src/scanner.py`**
   - Accept an optional logger/adapter in `ProhibitedWordScanner.__init__`.
   - Replace every `print()` call with the appropriate level.

4. **`src/web.py`**
   - Obtain a `ScanAdapter` at the start of each request.
   - Log all events in the catalog above.
   - Add `_sanitize_url()` to strip credentials before logging.

5. **`tests/test_logging.py`** *(new)*
   - Capture log records via `unittest.mock`.
   - Assert correct level, event name, and key fields for: `scan_started`,
     `scan_completed`, `scan_failed`, `file_skipped_size`.
