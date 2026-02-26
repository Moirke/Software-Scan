# Runbook: Upgrading Repository Scanner

Use this when a new build artifact is available from Jenkins and needs to be
deployed to the production VM.

---

## Before you start

- Confirm the Jenkins build passed all tests (green build, coverage ≥ 75%)
- Note the build number — e.g. `42` → artifact `repo-scanner-42.tar.gz`
- Notify users if you expect more than a few seconds of downtime (typically
  there is none — Gunicorn drains in-flight requests before stopping)

---

## Steps

### 1. Download the artifact from Jenkins

On the Jenkins build page:
**Build → Artifacts → repo-scanner-\<N\>.tar.gz** → Download

### 2. Copy the artifact to the VM

```bash
scp repo-scanner-42.tar.gz user@scanner.corp.example.com:/opt/repo-scanner/
```

### 3. Load the new image

```bash
ssh user@scanner.corp.example.com
cd /opt/repo-scanner
docker load -i repo-scanner-42.tar.gz
```

Confirm the new image is present:

```bash
docker images repo-scanner
```

You should see the new image tagged `latest` and the previous one (if still present).

### 4. Roll out

```bash
docker compose up -d
```

Compose detects that the `repo-scanner:latest` digest has changed and restarts
only the scanner container. Nginx is unaffected and stays up throughout.

Gunicorn waits up to 30 seconds for in-flight requests to finish before the
old container stops.

### 5. Verify

```bash
# Health check
curl https://scanner.corp.example.com/api/v1/health

# Check both containers are running
docker compose ps

# Tail logs for a minute to confirm no errors
docker compose logs -f --tail=50
```

---

## Rollback

If the new version has a problem, roll back by loading the previous artifact
and running `docker compose up -d` again.

```bash
docker load -i repo-scanner-41.tar.gz   # previous build
docker compose up -d
```

Previous `.tar.gz` files should be kept on the VM for at least one release
cycle for exactly this reason.

---

## Clean up old images

Once you are confident the new version is stable, remove old images to free
disk space:

```bash
docker image prune -f
```
