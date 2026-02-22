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
| coverage | 7.13.4 | Test coverage measurement; used by `ci/Jenkinsfile` and locally |

All other modules used (`os`, `re`, `zipfile`, `tarfile`, `subprocess`, `ipaddress`, `socket`, `tempfile`, `shutil`, `json`, `base64`, `io`, `pathlib`, `queue`, `threading`, `multiprocessing`) are Python standard library — no installation required.

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
| docker-compose-plugin | any | Manages the scanner + Nginx stack; used in both local development and on the production VM |

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
| Jenkins | Pipeline runner (`ci/Jenkinsfile`) |
| Jenkins Pipeline plugin | Declarative pipeline support; part of Jenkins suggested plugins |
| Jenkins Git plugin | SCM checkout; part of Jenkins suggested plugins |
| Jenkins HTML Publisher plugin | Publishes the coverage HTML report as a build sidebar link |
| Docker daemon (agent) | `docker build`, `docker run`, and `docker save` run directly on the agent; the `jenkins` user must be in the `docker` group |
| `curl` (agent) | Used by the smoke test stage to hit `/health` on the running container |

---

## Credentials and secrets (external, not bundled)

| Secret | How it is used |
|---|---|
| Git credentials (optional) | Scanning private git repositories via the web UI |
| Artifactory API key or username/password | Scanning Artifactory paths via the web UI |

---

## What is intentionally NOT a dependency

| Item | Reason |
|---|---|
| Database | Scan history is in-memory per-instance; ephemeral by design |
| Redis / message queue | Scans run in-process; the streaming endpoint uses a `queue.Queue` and background thread but no external broker |
| Persistent storage (EBS, disks) | All scan work writes to tmpfs and is cleaned up after each request |
