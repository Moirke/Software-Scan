# TODO

Things we want to improve but haven't tackled yet.

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

---

## Reliability

- **Git submodule support** — `git clone --depth=1` does not pull submodules.
  Repositories that store significant code in submodules will produce
  incomplete scan results.

---

## Features

- **Search / filter results** — add a search/filter box to the scan results
  panel so users can find results by file path, line content, or prohibited
  word. A previous attempt used a debounced substring match but the UX wasn't
  satisfying — revisit the interaction model before implementing.
