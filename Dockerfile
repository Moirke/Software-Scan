# ── Repository Scanner — Dockerfile ──────────────────────────────────────────
#
# Build:   docker build -t scanner:latest .
# Run:     docker run -p 8080:8080 scanner:latest
#
# The image bundles all system-level dependencies so it runs identically on
# any Linux host (EC2, GCP, Azure, DigitalOcean, on-prem, etc.) that has
# Docker installed.

FROM python:3.11-slim

LABEL org.opencontainers.image.title="Repository Scanner"
LABEL org.opencontainers.image.description="Scan repositories for prohibited words"

# ── System dependencies ───────────────────────────────────────────────────────
#   git       — shallow-clone remote repositories
#   rpm2cpio  — extract RPM archives
#   cpio      — called by rpm2cpio
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        git \
        rpm2cpio \
        cpio \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Python dependencies (separate layer for cache efficiency) ─────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Application source ────────────────────────────────────────────────────────
COPY src/            ./src/
COPY templates/      ./templates/
COPY config/         ./config/
COPY wsgi.py         gunicorn.conf.py ./

# ── Non-root user ─────────────────────────────────────────────────────────────
# Running as root inside a container is an unnecessary risk.
RUN useradd -r -u 1001 -s /sbin/nologin scanner \
 && chown -R scanner:scanner /app
USER scanner

# ── Runtime environment ───────────────────────────────────────────────────────
# All of these can be overridden at `docker run` time via -e or --env-file.
ENV FLASK_DEBUG=false
ENV PORT=8080
ENV LOG_LEVEL=info

EXPOSE 8080

# ── Health check ──────────────────────────────────────────────────────────────
# The LB uses this; it is also run by `docker ps` to show container health.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c \
        "import urllib.request, os; \
         urllib.request.urlopen('http://localhost:' + os.environ.get('PORT','8080') + '/health')" \
    || exit 1

# ── Entry point ───────────────────────────────────────────────────────────────
CMD ["gunicorn", "--config", "gunicorn.conf.py", "wsgi:app"]
