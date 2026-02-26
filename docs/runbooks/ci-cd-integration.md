# Runbook: CI/CD Integration

Use this to add a repo-scanner step to your own Jenkins (or other CI) pipelines.
The scanner CLI returns exit code `0` (clean), `1` (violations found), or `2` (error),
which makes it straightforward to gate builds on scan results.

---

## Option 1 — Inline Jenkinsfile (no Docker)

The simplest approach: install the scanner on the Jenkins agent and run it as a shell step.

```groovy
pipeline {
    agent any

    environment {
        SCANNER_CONFIG = 'config/config.yaml'
        RESULTS_FILE   = "scan_results_${env.BUILD_NUMBER}.txt"
    }

    stages {

        stage('Install scanner') {
            steps {
                sh 'pip3 install -r requirements.txt --break-system-packages || true'
            }
        }

        stage('Scan') {
            steps {
                script {
                    def rc = sh(
                        script: "python3 run-cli.py -c ${SCANNER_CONFIG} -r . -o ${RESULTS_FILE} -v",
                        returnStatus: true
                    )
                    env.SCAN_RC = rc
                }
            }
            post {
                always {
                    archiveArtifacts artifacts: "${RESULTS_FILE}", allowEmptyArchive: true
                }
            }
        }

        stage('Evaluate') {
            steps {
                script {
                    if (env.SCAN_RC == '1') {
                        error('Prohibited words found — see scan results artifact.')
                    } else if (env.SCAN_RC != '0') {
                        error('Scanner encountered an error.')
                    }
                }
            }
        }
    }
}
```

---

## Option 2 — Docker agent

Run the scan inside a `python:3.11-slim` container so the agent needs no Python
installation. Mount the Docker socket only if you also need to build images in
the same pipeline.

```groovy
pipeline {
    agent {
        docker { image 'python:3.11-slim' }
    }

    environment {
        SCANNER_CONFIG = 'config/config.yaml'
        RESULTS_FILE   = "scan_results_${env.BUILD_NUMBER}.txt"
    }

    stages {

        stage('Install system deps') {
            steps {
                // rpm2cpio/cpio are only needed if you scan RPM archives
                sh 'apt-get update -qq && apt-get install -y --no-install-recommends rpm2cpio cpio'
            }
        }

        stage('Install Python deps') {
            steps {
                sh 'pip install --no-cache-dir -r requirements.txt'
            }
        }

        stage('Scan') {
            steps {
                script {
                    def rc = sh(
                        script: "python3 run-cli.py -c ${SCANNER_CONFIG} -r . -o ${RESULTS_FILE} -v",
                        returnStatus: true
                    )
                    env.SCAN_RC = rc
                }
            }
            post {
                always {
                    archiveArtifacts artifacts: "${RESULTS_FILE}", allowEmptyArchive: true
                }
            }
        }

        stage('Evaluate') {
            steps {
                script {
                    if (env.SCAN_RC == '1') {
                        error('Prohibited words found — see scan results artifact.')
                    } else if (env.SCAN_RC != '0') {
                        error('Scanner encountered an error.')
                    }
                }
            }
        }
    }
}
```

---

## Option 3 — Parameterized build (warn or fail)

Adds a `SCAN_MODE` parameter so teams can run in warn-only mode during initial
adoption and switch to fail-on-violations once the backlog is cleared.

```groovy
pipeline {
    agent any

    parameters {
        choice(
            name:        'SCAN_MODE',
            choices:     ['FAIL_ON_VIOLATIONS', 'WARN_ONLY'],
            description: 'FAIL_ON_VIOLATIONS fails the build; WARN_ONLY marks it unstable'
        )
        string(
            name:         'SCAN_PATH',
            defaultValue: '.',
            description:  'Path to scan (relative to workspace)'
        )
    }

    environment {
        SCANNER_CONFIG = 'config/config.yaml'
        RESULTS_FILE   = "scan_results_${env.BUILD_NUMBER}.txt"
    }

    stages {

        stage('Install scanner') {
            steps {
                sh 'pip3 install -r requirements.txt --break-system-packages || true'
            }
        }

        stage('Scan') {
            steps {
                script {
                    def rc = sh(
                        script: "python3 run-cli.py -c ${SCANNER_CONFIG} -r ${params.SCAN_PATH} -o ${RESULTS_FILE} -v",
                        returnStatus: true
                    )
                    env.SCAN_RC = rc

                    // Parse violation count from output file
                    if (fileExists(RESULTS_FILE)) {
                        def text    = readFile(RESULTS_FILE)
                        def matcher = (text =~ /Found (\d+) violation/)
                        env.VIOLATION_COUNT = matcher ? matcher[0][1] : '0'
                    }
                }
            }
            post {
                always {
                    archiveArtifacts artifacts: "${RESULTS_FILE}", allowEmptyArchive: true
                }
            }
        }

        stage('Evaluate') {
            steps {
                script {
                    if (env.SCAN_RC == '1') {
                        def msg = "Found ${env.VIOLATION_COUNT} prohibited word violation(s)"
                        if (params.SCAN_MODE == 'FAIL_ON_VIOLATIONS') {
                            error(msg)
                        } else {
                            unstable(message: msg)
                            echo "WARNING: ${msg} (warn-only mode)"
                        }
                    } else if (env.SCAN_RC != '0') {
                        error('Scanner encountered an error.')
                    }
                }
            }
        }
    }
}
```

---

## Option 4 — Jenkins Shared Library

If multiple teams use the scanner, package the logic as a shared library step so
each team's `Jenkinsfile` is just a one-liner.

**`vars/scanProhibitedWords.groovy`** (in your shared library repo):

```groovy
def call(Map config = [:]) {
    def defaults = [
        configFile:      'config/config.yaml',
        scanPath:        '.',
        outputFile:      "scan_results_${env.BUILD_NUMBER}.txt",
        failOnViolations: true,
    ]
    def cfg = defaults + config

    stage('Install scanner') {
        sh 'pip3 install -r requirements.txt --break-system-packages || true'
    }

    stage('Scan') {
        def rc = sh(
            script: "python3 run-cli.py -c ${cfg.configFile} -r ${cfg.scanPath} -o ${cfg.outputFile} -v",
            returnStatus: true
        )
        archiveArtifacts artifacts: "${cfg.outputFile}", allowEmptyArchive: true

        if (rc == 1) {
            def msg = 'Prohibited words found — see scan results artifact.'
            if (cfg.failOnViolations) { error(msg) } else { unstable(message: msg) }
        } else if (rc != 0) {
            error('Scanner encountered an error.')
        }
    }
}
```

**Calling it in any team's `Jenkinsfile`:**

```groovy
@Library('your-shared-library') _

pipeline {
    agent any
    stages {
        stage('Prohibited word scan') {
            steps {
                scanProhibitedWords(scanPath: 'src/', failOnViolations: true)
            }
        }
    }
}
```

---

## Exit codes

| Code | Meaning |
|------|---------|
| `0`  | No violations found |
| `1`  | One or more violations found |
| `2`  | Scanner error (bad config, unreadable path, etc.) |

## See also

- [REST API tutorial](../tutorials/scanning-with-the-rest-api.md) — call the web service from CI instead of the CLI
- [Deploying to Rocky Linux](../tutorials/deploying-to-rocky-linux.md) — host the scanner centrally for teams that prefer the web API
