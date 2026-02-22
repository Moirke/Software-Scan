# TODO

Things we want to improve but haven't tackled yet, roughly grouped by theme.

---

## Security

- **TLS certificate management** — the default deployment (`docker-compose.yml`)
  already terminates TLS at Nginx on port 443 (TLSv1.2/1.3) and redirects HTTP
  to HTTPS; `docker-compose.http.yml` is a deliberate plain-HTTP opt-out for
  isolated networks. What is not handled is certificate lifecycle: the bundled
  `generate-certs.sh` creates a self-signed cert that browsers will warn on.
  For real deployments, swap in a cert from the corporate PKI or a wildcard
  cert, and arrange automated renewal.

- **Authentication** — the web UI is completely open. Anyone who can reach the
  port can submit a scan. For on-premise deployments the most practical options
  are: HTTP Basic Auth in Nginx backed by LDAP/Active Directory, a corporate
  SAML IdP via an Nginx auth proxy, or a shared API key header for pipeline
  integrations.


- **Container image scanning** — the Docker image we build is not scanned for
  OS-level CVEs. Add a Trivy or Snyk stage to `ci/Jenkinsfile` so vulnerable
  base images are caught before the artifact is archived.

- **Dependency pinning / automated updates** — `requirements.txt` is pinned but
  not automatically kept current. Renovate is the better fit for on-premise
  deployments as it can run as a self-hosted service against Bitbucket or
  GitLab without needing external connectivity.

---

## Reliability and Scale

- **Async scan jobs** — long-running scans still tie up a Gunicorn worker for
  the full duration; SSE streaming means the user gets live progress feedback
  but the underlying worker-per-scan model is unchanged. Under heavy load the
  worker pool can still be exhausted. A proper fix would be a job queue
  (Celery + Redis, both self-hostable) where the API returns a job ID
  immediately and the client polls or streams for completion.

- **Per-scan resource limits** — no cap on how much disk a single scan can
  consume during archive extraction. A very large archive could fill the tmpfs.
  Add a per-scan disk quota and abort cleanly when it is exceeded.

- **Git submodule support** — `git clone --depth=1` does not pull submodules.
  Repositories that store significant code in submodules will produce
  incomplete scan results.

---

## Features

- **Word list management UI** — the prohibited words file is a plain text file
  managed out-of-band. A simple CRUD interface in the web UI would make the
  tool self-contained for non-technical users.

- **Search / filter results** — add a search/filter box to the scan results
  panel so users can find results by file path, line content, or prohibited
  word. A previous attempt used a debounced substring match but the UX wasn't
  satisfying — revisit the interaction model before implementing.
