# Running Repository Scanner with Gunicorn

This tutorial walks you through running the app with Gunicorn instead of Flask's
built-in development server.  Gunicorn is what you would use in production and is
useful for testing production-like behaviour locally.

## Prerequisites

Install Gunicorn if you haven't already:

```bash
pip install gunicorn
```

Verify the install:

```bash
gunicorn --version
```

---

## Starting the app

Run this from the project root:

```bash
gunicorn "src.web:app" --bind 0.0.0.0:5000 --workers 1 --log-level info
```

Then open http://localhost:5000 in your browser.

> **macOS users:** macOS does not have `/dev/shm`, which Gunicorn uses by default
> for worker temp files.  Add `--worker-tmp-dir /tmp` to avoid a startup error:
>
> ```bash
> gunicorn "src.web:app" --bind 0.0.0.0:5000 --workers 1 --log-level info --worker-tmp-dir /tmp
> ```
>
> This flag is not needed on Linux.

---

## Common options

| Flag | Purpose | Example |
|---|---|---|
| `--bind` | Address and port to listen on | `--bind 0.0.0.0:8080` |
| `--workers` | Number of worker processes | `--workers 4` |
| `--log-level` | Gunicorn's own access log verbosity | `--log-level debug` |
| `--worker-tmp-dir` | Temp dir for worker heartbeat files (macOS) | `--worker-tmp-dir /tmp` |

For production the recommended worker count is `2 × CPU cores + 1`.

---

## Adding environment variables

Pass application env vars before the command as usual:

```bash
LOG_TARGET=stdout LOG_LEVEL=DEBUG gunicorn "src.web:app" \
  --bind 0.0.0.0:5000 \
  --workers 1 \
  --log-level info \
  --worker-tmp-dir /tmp   # macOS only
```

---

## A note on in-memory storage

Each Gunicorn worker process has its own copy of `scan_history` in memory.
With more than one worker, a scan submitted on one request may be invisible
to a subsequent request if it lands on a different worker.

For local testing, **keep `--workers 1`** to avoid this.  In a real production
deployment this is solved by moving scan storage to a shared database.

---

## Differences from `python run-web.py`

| | `run-web.py` | Gunicorn |
|---|---|---|
| Use case | Development | Production / production testing |
| Workers | 1 (single thread) | Configurable |
| Auto-reload on code change | Yes (with `FLASK_DEBUG=true`) | No |
| Handles concurrent requests | No | Yes |
| macOS `/dev/shm` issue | Not applicable | Needs `--worker-tmp-dir /tmp` |
