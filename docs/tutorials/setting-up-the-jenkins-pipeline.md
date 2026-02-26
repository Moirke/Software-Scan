# Setting up the Jenkins build pipeline

This tutorial walks through creating a Jenkins pipeline job that runs the
Repository Scanner test suite, builds the Docker image, and archives a
deployable `.tar.gz` artifact.

The pipeline is defined in `ci/Jenkinsfile`.  It is the pipeline for building
and releasing the scanner itself — not for calling it from other projects
(see [runbooks/ci-cd-integration.md](../runbooks/ci-cd-integration.md) for that).

---

## What the pipeline produces

A successful build outputs two things visible in Jenkins:

| Output | Where to find it |
|--------|-----------------|
| `repo-scanner-<N>.tar.gz` | Build → Artifacts |
| Test coverage HTML report | Build → Coverage Report (sidebar link) |

The `.tar.gz` is the artifact handed to the deployment process described in
[runbooks/upgrade.md](../runbooks/upgrade.md).

---

## Prerequisites

### Jenkins plugins

Three plugins are required.  Check **Manage Jenkins → Plugins → Installed** for
each:

| Plugin | Likely already installed? | What it provides |
|--------|--------------------------|-----------------|
| **Pipeline** | Yes — part of Jenkins suggested plugins | Declarative pipeline support |
| **Git** | Yes — part of Jenkins suggested plugins | SCM checkout |
| **HTML Publisher** | Not always | Publishes the coverage report as a sidebar link |

To install HTML Publisher: **Manage Jenkins → Plugins → Available** → search
`HTML Publisher` → Install → restart Jenkins.

### Build agent requirements

The pipeline uses `agent any`, so Jenkins assigns it to any available agent.
That agent must have:

- **Python 3.8+** with `pip` — used to install dependencies and run tests.
  All Python packages (including `coverage`) are installed by the pipeline
  itself from `requirements.txt`, so nothing beyond a base Python install is
  needed on the agent.
- **Docker** — the agent user must be able to run `docker build`, `docker run`,
  and `docker save`.  On Linux this typically means adding the `jenkins` user
  to the `docker` group:

  ```bash
  sudo usermod -aG docker jenkins
  # Restart Jenkins for the group change to take effect
  sudo systemctl restart jenkins
  ```

Confirm both are available on the agent before creating the job:

```bash
python3 --version
pip3 --version
docker info
```

---

## Step 1 — Create the pipeline job

1. From the Jenkins dashboard, click **New Item**.
2. Enter a name — e.g. `repo-scanner-build`.
3. Select **Pipeline** and click **OK**.

---

## Step 2 — Configure the pipeline

On the configuration page:

### General

- **Discard old builds** (recommended): keep last 10 builds to avoid filling
  the agent disk with `.tar.gz` artifacts.
  - Strategy: Log Rotation
  - Max # of builds to keep: `10`

### Build Triggers

Choose whatever fits your workflow.  Common options:

- **Poll SCM** — check for new commits on a schedule, e.g. `H/5 * * * *`
  (every 5 minutes).
- **GitHub / Bitbucket webhook** — trigger immediately on push.
- Leave blank to trigger manually only.

### Pipeline

- **Definition**: Pipeline script from SCM
- **SCM**: Git
- **Repository URL**: your repo URL, e.g.
  `https://git.corp.example.com/tools/repo-scanner.git`
- **Credentials**: select or add credentials if the repo is private
- **Branch Specifier**: `*/main` (or whichever branch you build from)
- **Script Path**: `ci/Jenkinsfile`

  > Jenkins defaults to `Jenkinsfile` at the repo root.  You must change this
  > to `ci/Jenkinsfile` or the job will not find the pipeline.

Click **Save**.

---

## Step 3 — Run the first build

Click **Build Now** on the job page.

Open the build's **Console Output** to follow along.  The stages run in order:

| Stage | What happens |
|-------|-------------|
| **Install dependencies** | `pip install -r requirements.txt` — installs Flask, coverage, pylint, etc. |
| **Lint** | `pylint src/` — fails the build if the score drops below 10.00 |
| **Test** | `coverage run -m unittest discover tests/` — runs all tests; coverage HTML is generated and published regardless of pass/fail |
| **Build image** | `docker build` — builds `repo-scanner:<N>` and `repo-scanner:latest`, labels the image with the git SHA and build timestamp |
| **Smoke test** | Starts the container on port 18080, waits 5 seconds, hits `/health`, then stops the container |
| **Save artifact** | `docker save | gzip` — compresses the image to `repo-scanner-<N>.tar.gz` and archives it |
| **Remove local image** | Removes the image from the agent to free disk space |

A clean build takes roughly 3–5 minutes depending on agent speed and whether
the Docker layer cache is warm.

---

## Step 4 — Verify the outputs

After a successful build:

**Artifact** — on the build page, click **Build Artifacts** (or the filename
directly).  Download `repo-scanner-<N>.tar.gz` to deploy it following
[runbooks/upgrade.md](../runbooks/upgrade.md).

**Coverage report** — click **Coverage Report** in the left sidebar of the
build page.  The report shows line and branch coverage per module.  The
pipeline always publishes the report, even when tests fail, so you can
diagnose coverage regressions.

**Stage view** — back on the job page, the stage view shows pass/fail and
timing for each stage across all recent builds at a glance.

---

## Troubleshooting

### `docker: command not found`

Docker is not on the agent's `PATH`, or the `jenkins` user does not have
permission to use it.  Verify:

```bash
# Run as the jenkins user on the agent
sudo -u jenkins docker info
```

If that fails with a permissions error, add jenkins to the docker group and
restart:

```bash
sudo usermod -aG docker jenkins
sudo systemctl restart jenkins
```

### `publishHTML` step fails or plugin not found

The HTML Publisher plugin is not installed.  Go to **Manage Jenkins → Plugins
→ Available**, search for `HTML Publisher`, install it, and restart Jenkins.
The Test stage will then publish the coverage report correctly.

### Port 18080 already in use during smoke test

Another process or a previous failed build left a container running on that
port.  Clean up:

```bash
docker ps | grep 18080
docker stop <container-id>
```

The pipeline's `post { cleanup }` block removes the image after every build,
but a container that failed to stop will hold the port.  If this happens
repeatedly, change the host port in the smoke test stage of `ci/Jenkinsfile`
to another unused high port.

### Tests fail but you still need the artifact

The pipeline fails the build if any test fails, so no artifact is produced.
This is intentional — never deploy a build with a failing test suite.  Fix the
failing tests first, then re-run the build.

### `coverage report` shows lower than expected coverage

The test stage runs `coverage run -m unittest discover tests/`, which measures
coverage of the full application.  The report is informational — there is no
enforced threshold in the pipeline, so a low coverage number will not fail the
build.  If you want to enforce a minimum, add `coverage report --fail-under=75`
to the Test stage in `ci/Jenkinsfile`.

---

## Next steps

- **Deploy the artifact** — [runbooks/upgrade.md](../runbooks/upgrade.md)
- **Set up a scan step in your own pipelines** — [runbooks/ci-cd-integration.md](../runbooks/ci-cd-integration.md)
