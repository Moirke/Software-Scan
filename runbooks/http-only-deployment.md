# Runbook: HTTP-only deployment

Use this when deploying on an isolated internal network where TLS is either
handled upstream (corporate load balancer, VPN boundary) or genuinely not
required — for example, a developer workstation or an air-gapped lab environment.

**This configuration sends all traffic unencrypted. Do not use it if the server
is reachable from untrusted networks.**

---

## Files

| File | Purpose |
|------|---------|
| `deploy/docker-compose.http.yml` | Compose file for HTTP-only deployment |
| `deploy/nginx-http.conf` | Nginx config — plain HTTP, no redirect, no TLS directives |

---

## Mode A — Nginx reverse proxy over HTTP (recommended)

Keeps the benefits of Nginx: large upload buffering, long-timeout proxy, and
structured access logs. Clients connect on port 80.

### Start

```bash
cd /opt/repo-scanner
docker compose -f docker-compose.http.yml up -d
```

### Verify

```bash
curl http://localhost/health
docker compose -f docker-compose.http.yml ps
docker compose -f docker-compose.http.yml logs -f --tail=30
```

### Stop

```bash
docker compose -f docker-compose.http.yml down
```

---

## Mode B — Gunicorn direct (no Nginx)

The simplest possible setup: expose Gunicorn's port 8080 directly with no proxy
in front. Useful when you have no need for upload buffering or access logs.

1. Edit `docker-compose.http.yml` and make two changes in the `scanner` service:
   - Uncomment the `ports` block:
     ```yaml
     ports:
       - "8080:8080"
     ```
   - Remove (or comment out) the entire `nginx` service.

2. Start:
   ```bash
   docker compose -f docker-compose.http.yml up -d
   ```

3. Verify:
   ```bash
   curl http://localhost:8080/health
   ```

---

## Switching back to HTTPS

When you are ready to add TLS, switch back to the standard compose file:

```bash
docker compose -f docker-compose.http.yml down
bash deploy/generate-certs.sh <hostname>
docker compose up -d
```

See [tutorials/deploying-to-rocky-linux.md](../tutorials/deploying-to-rocky-linux.md)
for the full TLS setup walkthrough.
