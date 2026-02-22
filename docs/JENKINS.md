# Jenkins CI/CD Pipeline

This document explains how to set up the Jenkins pipeline for this repository.

## Overview

The pipeline lives in `ci/Jenkinsfile`. It runs on every push, lints the code,
runs the full test suite with coverage, builds a Docker image, smoke-tests it,
and archives the image as a build artifact for deployment.

## Prerequisites

- Jenkins with the **Pipeline** and **HTML Publisher** plugins installed
- **Docker** available on the Jenkins agent
- **Python 3** available on the Jenkins agent

## Creating the Jenkins Job

1. **New Item** → **Pipeline** → give it a name (e.g. `repo-scanner`)
2. Under **Pipeline**, set **Definition** to `Pipeline script from SCM`
3. Set **SCM** to Git and point it at this repository
4. Set **Script Path** to `ci/Jenkinsfile`
5. Save and run the first build

## Pipeline Stages

| Stage | What it does |
|---|---|
| Install dependencies | `pip install -r requirements.txt` |
| Lint | `pylint src/` — fails if score drops below 10.00 |
| Test | Runs the full test suite with `coverage`; publishes an HTML coverage report |
| Build image | Builds the Docker image tagged `repo-scanner:<BUILD_NUMBER>` and `repo-scanner:latest` |
| Smoke test | Starts the container and confirms `/health` responds on port 18080 |
| Save artifact | Saves the image as `repo-scanner-<BUILD_NUMBER>.tar.gz` and archives it to Jenkins |
| Remove local image | Removes the local Docker image to keep the agent disk clean |

## Build Artifacts

A successful build produces one artifact:

```
repo-scanner-<BUILD_NUMBER>.tar.gz
```

Download it from the Jenkins build page at:
```
<BUILD_URL>artifact/repo-scanner-<BUILD_NUMBER>.tar.gz
```

## Deploying After a Successful Build

See `runbooks/upgrade.md` for instructions on deploying the artifact to the VM.

## Troubleshooting

**Lint fails** — fix any pylint errors in `src/` until the score reaches 10.00.

**Coverage fails** — the coverage threshold is 75%. Add tests until coverage is
above the threshold.

**Docker build fails** — ensure Docker is running on the agent and the agent user
has permission to run Docker commands.

**Smoke test fails** — the container must respond on port 18080 within 5 seconds.
Check that port 18080 is free on the agent and that nothing in the `Dockerfile` or
app startup is broken.
