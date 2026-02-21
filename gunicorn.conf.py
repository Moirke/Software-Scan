"""
Gunicorn configuration for Repository Scanner.

All values can be overridden via environment variables so the same image
works in dev, staging, and production without rebuilding.

Reference:
  https://docs.gunicorn.org/en/stable/settings.html
"""
import multiprocessing
import os

# ── Bind ──────────────────────────────────────────────────────────────────────
# The container exposes port 8080; the load balancer talks to this port.
# Do not bind to 127.0.0.1 inside a container — the LB can't reach it.
port = os.environ.get('PORT', '8080')
bind = f'0.0.0.0:{port}'

# ── Workers ───────────────────────────────────────────────────────────────────
# gthread: each worker is a thread pool, good for I/O-bound work
# (git clone, HTTP requests to Artifactory, archive extraction).
# Formula: (2 × vCPU) + 1, capped so we don't over-subscribe small instances.
_cpus   = multiprocessing.cpu_count()
workers = int(os.environ.get('GUNICORN_WORKERS', min(_cpus * 2 + 1, 9)))
worker_class = 'gthread'
threads = int(os.environ.get('GUNICORN_THREADS', '4'))

# ── Timeouts ──────────────────────────────────────────────────────────────────
# Scans can take several minutes: large repo clones or slow Artifactory servers.
# Set the LB idle timeout to at least this value.
timeout          = int(os.environ.get('GUNICORN_TIMEOUT', '600'))
graceful_timeout = 30
keepalive        = 5

# ── Logging ───────────────────────────────────────────────────────────────────
# Write to stdout/stderr so the container runtime captures logs.
# Pipe to CloudWatch, Stackdriver, Azure Monitor, etc. from there.
accesslog = '-'
errorlog  = '-'
loglevel  = os.environ.get('LOG_LEVEL', 'info')
access_log_format = (
    '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s %(L)ss'
)

# ── Performance ───────────────────────────────────────────────────────────────
# Use /dev/shm (RAM-backed tmpfs) for Gunicorn's worker heartbeat files.
# Avoids disk I/O on the worker-check path.
worker_tmp_dir = '/dev/shm'

# ── Security ──────────────────────────────────────────────────────────────────
limit_request_line   = 8190
limit_request_fields = 100
