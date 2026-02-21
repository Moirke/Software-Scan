# Dependency Inventory

Complete list of all dependencies across the project — runtime, system, infrastructure, and CI/CD.

---

## Python runtime

| Item | Version | Why |
|---|---|---|
| Python | ≥ 3.8, tested on 3.11 | Language runtime (`setup.py` declares 3.8 minimum) |

---

## Python packages (`requirements.txt`)

| Package | Version | Why |
|---|---|---|
| Flask | 3.0.0 | Web framework, HTTP routing, template rendering |
| PyYAML | 6.0.1 | Config file parsing (YAML format) |
| requests | 2.31.0 | Artifactory HTTP downloads |
| gunicorn | 21.2.0 | Production WSGI server |
| fpdf2 | 2.7.9 | PDF report generation (pure Python, no system deps) |

All other modules used (`os`, `re`, `zipfile`, `tarfile`, `subprocess`, `ipaddress`, `socket`, `tempfile`, `shutil`, `json`, `base64`, `io`, `pathlib`, `multiprocessing`) are Python standard library — no installation required.

---

## System packages (container / host)

| Package | Why | Install |
|---|---|---|
| `git` | Clone remote repositories | `apt install git` / `yum install git` |
| `rpm2cpio` | Extract RPM archives | `apt install rpm2cpio` (Debian/Ubuntu); part of `rpm` package on RHEL/Amazon Linux |
| `cpio` | Called internally by `rpm2cpio` | `apt install cpio` / usually pre-installed |

`git` requires outbound HTTPS (port 443) to reach remote repositories.

---

## Container infrastructure

| Component | Version | Why |
|---|---|---|
| Docker Engine | 20.10+ | Runs the container (`docker.io` on Ubuntu/Debian) |
| docker-compose-plugin | any | Local development only (`docker compose up`); not needed on production instances |
| Container registry | n/a | Stores the built image — ECR, GHCR, Artifact Registry, Docker Hub, or private |

---

## Optional infrastructure

| Component | Why | Required? |
|---|---|---|
| Nginx | Upload buffering for large files; TLS termination on single-node deployments | No — the load balancer handles this in cloud deployments |
| systemd | Service lifecycle management on Linux instances | Yes, if using `deploy/scanner.service` |

---

## Load balancer configuration requirements

These are not software packages but must be set correctly on whichever LB you use.

| Setting | Value | Why |
|---|---|---|
| Health check path | `GET /health` → expect HTTP 200 | Instance readiness probe |
| Health check interval | 30 s | Matches container `HEALTHCHECK` interval |
| Idle / read timeout | ≥ 600 s | Large git clones and Artifactory downloads can take several minutes |
| Max request body | 500 MB | ZIP file uploads (`MAX_CONTENT_LENGTH` in `src/web.py`) |
| Protocol | HTTP (LB terminates TLS) | Container speaks plain HTTP on port 8080 |

---

## CI/CD

| Component | Why |
|---|---|
| Jenkins | Pipeline runner (`ci/Jenkinsfile*`) |
| Jenkins Email Extension plugin | `emailext` step used in `post { failure }` blocks |
| Docker daemon (agent) | Required for `Jenkinsfile.docker`; agent mounts `/var/run/docker.sock` |

---

## Credentials and secrets (external, not bundled)

| Secret | How it is used |
|---|---|
| Container registry credentials | `docker pull` on instance boot; `docker push` in CI |
| Git credentials (optional) | Scanning private git repositories via the web UI |
| Artifactory API key or username/password | Scanning Artifactory paths via the web UI |

---

## What is intentionally NOT a dependency

| Item | Reason |
|---|---|
| Database | Scan history is in-memory per-instance; ephemeral by design |
| Redis / message queue | Scans run synchronously and return results in the HTTP response |
| Persistent storage (EBS, disks) | All scan work writes to tmpfs and is cleaned up after each request |
