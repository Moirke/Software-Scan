# TODO

Things we want to improve but haven't tackled yet, roughly grouped by theme.

---

## Security

- **HTTPS / TLS** — currently the service speaks plain HTTP and relies on the
  load balancer for TLS termination. For deployments without a LB (single-node,
  internal tools) we need a first-class TLS story — likely Let's Encrypt via
  certbot or a managed certificate from the cloud provider. Complicated because
  certificate provisioning, renewal, and hot-reload all need to be handled.

- **Authentication** — the web UI is completely open. Anyone who can reach port
  8080 can submit a scan. Options range from simple HTTP Basic Auth in Nginx, to
  OAuth2/OIDC (Okta, Google, Azure AD), to a shared API key header. The right
  choice depends on the deployment context (internal tool vs. exposed service).

- **Rate limiting** — no throttle on the scan endpoint. A single client can
  submit unlimited concurrent scans, exhausting memory and CPU. Needs a
  per-IP or per-user limit, either in Nginx or a middleware layer.

- **Container image scanning** — the Docker image we build is not scanned for
  OS-level CVEs. Add Trivy or Snyk to CI so vulnerable base images are caught
  before deployment.

- **Dependency pinning / automated updates** — `requirements.txt` is pinned but
  not automatically kept current. Add Dependabot or Renovate to get automated
  PRs when new versions are available.

---

## Reliability and Scale

- **Async scan jobs** — long-running scans (large repos, slow Artifactory
  servers) tie up a Gunicorn worker for the full duration. Under load this
  exhausts the worker pool. The fix is a job queue (Celery + Redis, or a
  cloud-native equivalent like SQS + Lambda) where the API immediately returns
  a job ID and the client polls for completion.

- **Concurrent scan limit** — related to the above, but a simpler short-term
  fix: cap the number of scans that can run simultaneously (e.g. via a
  semaphore) and return 429 when the limit is hit, rather than silently queuing
  behind busy workers.

- **Per-scan resource limits** — no cap on how much disk a single scan can
  consume during archive extraction. A deeply nested or very large archive could
  fill the tmpfs. Add a per-scan disk quota and abort cleanly when it is
  exceeded.

- **Archive nesting depth limit** — the scanner recurses into archives
  indefinitely (zip inside zip inside zip...). Add a configurable max depth to
  prevent pathological inputs from consuming unbounded resources.

- **Git submodule support** — `git clone --depth=1` does not pull submodules.
  Repositories that store significant code in submodules will produce
  incomplete scan results.

---

## Observability

- **Structured (JSON) logging** — current logs are plain text printed to
  stdout. For log aggregation tools (CloudWatch Logs Insights, Splunk,
  Datadog) structured JSON is much easier to query. Replace `print()` calls
  with a proper logging configuration.

- **Metrics endpoint** — no way to observe scan throughput, error rates, or
  latency from outside the process. Add a `/metrics` endpoint (Prometheus
  format) or push metrics to a cloud monitoring service.

- **Scan audit trail** — if a compliance team ever asks "was repo X clean on
  date Y?", today we have no answer. The in-memory scan history is intentionally
  ephemeral (a design strength for stateless operation), but a lightweight
  write-only audit log (append to S3, write to a managed DB) could satisfy
  compliance requirements without changing the stateless architecture.

---

## Features

- **Regex / pattern matching** — the word list supports literal strings only.
  Real secrets often follow patterns (AWS access keys start with `AKIA`,
  private keys have `-----BEGIN`, etc.). Adding optional regex patterns to
  the word list would catch a much broader class of sensitive data.

- **Scan progress / streaming** — for large scans the UI shows a spinner with
  no feedback until the scan completes. Streaming results via Server-Sent Events
  or WebSocket would make the tool feel significantly more responsive.

- **Word list management UI** — the prohibited words file is a plain text file
  managed out-of-band. A simple CRUD interface in the web UI would make the
  tool self-contained for non-technical users.

- **Result export formats** — currently exports as JSON only. CSV and a simple
  HTML report would make results easier to share with stakeholders who don't
  process JSON.

- **Scan result pagination in the UI** — the web UI caps display at 100 results
  with an "export for full results" message. Proper pagination would avoid
  forcing the user to download a file just to see result 101.

---

## User Feedback

- **In-app feedback collection** — no mechanism currently exists for users to
  report false positives, request new prohibited words, or flag issues with a
  scan result. Options: a simple thumbs-up/thumbs-down on each result, a free-
  text feedback form that posts to a Slack webhook or email, or an integration
  with an existing ticketing system (Jira, ServiceNow). Feedback data would be
  valuable for tuning the prohibited words list and improving partial-match
  accuracy over time.

---

## CI / CD

- **Docker build and push pipeline** — the existing Jenkinsfiles scan for
  prohibited words but do not build or publish the Docker image. Add a pipeline
  that builds the image, runs the test suite inside it, and pushes to the
  registry on merge to main.

- **Test coverage reporting** — we have 126 tests but no coverage metrics.
  Add `coverage.py` to the test run and fail the build if coverage drops below
  a threshold.

- **Integration / smoke test against the running container** — current tests
  call Flask's test client directly. A separate suite that starts the real
  container and exercises the API over HTTP would catch Gunicorn configuration
  issues and system dependency problems that unit tests miss.
