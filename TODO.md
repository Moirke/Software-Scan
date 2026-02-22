# TODO

Things we want to improve but haven't tackled yet, roughly grouped by theme.

---

## Security

- **HTTPS / TLS** — currently the service speaks plain HTTP. For on-premise
  deployments the realistic options are: terminating TLS at Nginx using a
  certificate from the corporate PKI or a wildcard cert, or running certbot
  against an internal ACME server if one exists. Certificate renewal and
  Gunicorn hot-reload both need to be handled.

- **Authentication** — the web UI is completely open. Anyone who can reach the
  port can submit a scan. For on-premise deployments the most practical options
  are: HTTP Basic Auth in Nginx backed by LDAP/Active Directory, a corporate
  SAML IdP via an Nginx auth proxy, or a shared API key header for pipeline
  integrations.

- **Rate limiting** — a concurrency cap of 5 simultaneous scans is in place
  (returns 429 when exceeded), but there is no per-IP or per-user throttle.
  A single client could still saturate all 5 slots. Needs a per-IP limit,
  either in Nginx or a middleware layer.

- **Container image scanning** — the Docker image we build is not scanned for
  OS-level CVEs. Add a Trivy or Snyk stage to `ci/Jenkinsfile` so vulnerable
  base images are caught before the artifact is archived.

- **Dependency pinning / automated updates** — `requirements.txt` is pinned but
  not automatically kept current. Renovate is the better fit for on-premise
  deployments as it can run as a self-hosted service against Bitbucket or
  GitLab without needing external connectivity.

---

## Reliability and Scale

- **Async scan jobs** — long-running scans (large repos, slow Artifactory
  servers) tie up a Gunicorn worker for the full duration. Under load this
  exhausts the worker pool. The fix is a job queue (Celery + Redis are both
  self-hostable on the same server) where the API immediately returns a job ID
  and the client polls for completion.

- **Per-scan resource limits** — no cap on how much disk a single scan can
  consume during archive extraction. A very large archive could fill the tmpfs.
  Add a per-scan disk quota and abort cleanly when it is exceeded.

- **Git submodule support** — `git clone --depth=1` does not pull submodules.
  Repositories that store significant code in submodules will produce
  incomplete scan results.

---

## Observability

- **Scan audit trail** — if a compliance team ever asks "was repo X clean on
  date Y?", today we have no answer. The in-memory scan history is intentionally
  ephemeral, but a lightweight write-only audit log would satisfy compliance
  requirements without changing the stateless architecture. On-premise options:
  append to a structured log file picked up by the SIEM, or write to a local
  SQLite or Postgres database on the same host.

---

## Features

- **Word list management UI** — the prohibited words file is a plain text file
  managed out-of-band. A simple CRUD interface in the web UI would make the
  tool self-contained for non-technical users.
