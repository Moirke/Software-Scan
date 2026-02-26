# Runbook: Diagnosing Scan Failures

Use this when a scan returns an error, hangs indefinitely, or produces
unexpected results.

---

## Step 1 — Check the error response

The API returns structured errors:

```json
{ "error": { "code": "SCAN_FAILED", "message": "..." } }
```

| Code | Likely cause |
|---|---|
| `VALIDATION_ERROR` | Missing or invalid request field — check your request |
| `SCAN_FAILED` | Scan started but failed — check the message for details |
| `TOO_MANY_REQUESTS` | 5 concurrent scan limit hit — wait and retry |
| `NOT_FOUND` | Scan UUID doesn't exist or was deleted |
| `SERVER_ERROR` | Unexpected server fault — check logs immediately |

---

## Step 2 — Check application logs

```bash
# Live log tail
docker compose -f /opt/repo-scanner/docker-compose.yml logs -f scanner

# Last 100 lines
docker compose -f /opt/repo-scanner/docker-compose.yml logs --tail=100 scanner
```

Look for lines containing `scan_failed`, `ERROR`, or `CRITICAL`.

Each scan has a short `scan_id` prefix on every log line (e.g. `scan_id=3fa85f64`)
so you can filter to a single scan:

```bash
docker compose -f /opt/repo-scanner/docker-compose.yml logs scanner \
    | grep scan_id=3fa85f64
```

---

## Step 3 — Common failure scenarios

### Git clone failed

```
clone_failed url=https://github.com/org/repo stderr='...'
```

- Check the repo URL is correct and publicly accessible from the VM
- For private repos, confirm credentials are included in the URL or the
  server has SSH access
- Check outbound HTTPS from the VM: `curl -I https://github.com`

### Artifactory authentication failed

```
artifactory_download_failed ... error='Artifactory authentication failed'
```

- Re-check the API key or username/password in the request
- Confirm the Artifactory URL is reachable from the VM:
  `curl -I https://artifactory.corp.example.com`

### Prohibited words file not found

```
scan_failed error='Config file not found in repository: ...'
```

- Confirm the words file path is correct
- If using a git repo config source, verify the file exists at that path
  in the repo

### File skipped — too large

```
file_skipped_size path=... size_mb=... limit_mb=...
```

Not a failure — just informational.  If you need those files scanned,
increase `max_file_size_mb` in the scan request.

### Archive nesting depth limit reached

```
archive_depth_limit_reached path=... depth=10 limit=10
```

The scanner limits archive-in-archive extraction to 10 levels deep.  Archives
beyond this limit are skipped and results for their contents will be absent.

**CLI:** a warning is printed to stderr:
> Warning: N archive(s) were skipped because the nesting depth limit of 10 was
> reached. Results may be incomplete.

**Web UI:** a warning banner appears in the results panel.

**API:** the scan response includes `"depth_limit_hits": N`.

If the skipped archives contain content that must be scanned, pre-extract them
manually and scan the extracted directory directly.

---

### Disk full during archive extraction

```
file_read_error path=... error='[Errno 28] No space left on device'
```

Check disk space on the VM:

```bash
df -h
```

The scanner extracts archives to a tmpfs (`/tmp`, 2 GB limit per the
docker-compose config).  Large nested archives can fill this.  Free space
or increase the tmpfs size in `docker-compose.yml`.

---

## Step 4 — Scan is hanging (no response)

If a scan has been running for several minutes with no result:

**Check whether a Gunicorn worker is stuck:**

```bash
docker compose -f /opt/repo-scanner/docker-compose.yml exec scanner \
    ps aux
```

Look for `python` or `gunicorn` worker processes consuming CPU.

**Check disk and memory:**

```bash
docker stats --no-stream
df -h
```

**Cancel the hung scan:**

The simplest recovery is to restart the scanner container — Gunicorn will
drain in-flight requests (up to 30 seconds) then restart cleanly:

```bash
cd /opt/repo-scanner
docker compose restart scanner
```

---

## Step 5 — Check the health endpoint

If the health endpoint is not responding, the issue is at the infrastructure
level rather than the application:

```bash
# From the VM itself (bypasses Nginx)
curl http://localhost:8080/health

# Through Nginx
curl https://scanner.corp.example.com/health
```

- If the first works but the second doesn't → Nginx issue (check Nginx logs)
- If neither works → scanner container is down (see `docs/runbooks/restart-service.md`)

```bash
docker compose -f /opt/repo-scanner/docker-compose.yml logs nginx
```

---

## Step 6 — Escalate

If the above steps don't identify the cause, collect the following before
escalating:

```bash
# Full logs from the last hour
docker compose -f /opt/repo-scanner/docker-compose.yml logs \
    --since 1h > /tmp/scanner-logs.txt

# Container resource usage
docker stats --no-stream >> /tmp/scanner-logs.txt

# Disk and memory
df -h >> /tmp/scanner-logs.txt
free -h >> /tmp/scanner-logs.txt
```

Share `/tmp/scanner-logs.txt` with whoever is investigating.
