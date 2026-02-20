# Jenkins Integration Guide

This guide covers multiple ways to integrate the Repository Scanner into your Jenkins pipelines.

## Table of Contents
1. [Basic Pipeline](#basic-pipeline)
2. [Parameterized Pipeline](#parameterized-pipeline)
3. [Docker-Based Pipeline](#docker-based-pipeline)
4. [Shared Library](#shared-library)
5. [Freestyle Job](#freestyle-job)
6. [Best Practices](#best-practices)

---

## Basic Pipeline

Use the standard `Jenkinsfile` for a simple integration that fails the build on violations.

### Setup:

1. Copy `Jenkinsfile` to your repository root
2. Ensure `config.yaml` and `prohibited_words.txt` are in the repository
3. Create a Pipeline job in Jenkins pointing to your repository

### Features:
- ✅ Automatic dependency installation
- ✅ Scans entire workspace
- ✅ Archives results as artifacts
- ✅ Fails build on violations
- ✅ Email notifications on failure

### Usage:
```groovy
// Just add the Jenkinsfile to your repo
// Jenkins will automatically use it
```

---

## Parameterized Pipeline

Use `Jenkinsfile.parameterized` for flexible scanning with runtime options.

### Features:
- ✅ Choose between FAIL or WARN modes
- ✅ Specify custom scan paths
- ✅ Build badges showing violation counts
- ✅ Detailed result summaries

### Setup:

1. Copy `Jenkinsfile.parameterized` to your repository as `Jenkinsfile`
2. When running the build, you'll see parameter options:
   - **SCAN_MODE**: `FAIL_ON_VIOLATIONS` or `WARN_ONLY`
   - **SCAN_PATH**: Path to scan (default: `.`)

### Usage Example:
```bash
# Via Jenkins UI: Build with Parameters
# - SCAN_MODE: WARN_ONLY
# - SCAN_PATH: src/

# Via Jenkins CLI:
java -jar jenkins-cli.jar build MyJob \
  -p SCAN_MODE=WARN_ONLY \
  -p SCAN_PATH=src/
```

---

## Docker-Based Pipeline

Use `Jenkinsfile.docker` to run scans in isolated Docker containers.

### Advantages:
- ✅ Consistent environment
- ✅ No system dependencies required
- ✅ Supports RPM scanning (includes rpm2cpio)
- ✅ Clean execution environment

### Prerequisites:
- Docker installed on Jenkins agent
- Jenkins with Docker Pipeline plugin

### Setup:

1. Copy `Jenkinsfile.docker` to your repository as `Jenkinsfile`
2. Ensure Docker is available on your Jenkins agents

---

## Shared Library

Use `jenkins-shared-library.groovy` for reusable scanning across multiple projects.

### Setup:

1. **Create a Shared Library in Jenkins:**
   - Go to: Jenkins → Manage Jenkins → Configure System
   - Under "Global Pipeline Libraries", add a new library
   - Name: `repository-scanner`
   - Default version: `main`
   - Retrieval method: Modern SCM (Git)
   - Source: Your library repository

2. **Library Structure:**
   ```
   repository-scanner-library/
   ├── vars/
   │   └── scanProhibitedWords.groovy
   └── resources/
       ├── config.yaml
       └── prohibited_words.txt
   ```

3. **Usage in Jenkinsfile:**

   ```groovy
   @Library('repository-scanner') _
   
   // Simple usage with defaults
   scanProhibitedWords()
   
   // Custom configuration
   scanProhibitedWords([
       configFile: 'custom-config.yaml',
       scanPath: 'src/',
       failOnViolations: false,
       verbose: true
   ])
   ```

### Configuration Options:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `configFile` | `config.yaml` | Path to config file |
| `scanPath` | `.` | Directory to scan |
| `outputFile` | `scan_results_{BUILD_NUMBER}.txt` | Results filename |
| `failOnViolations` | `true` | Fail build on violations |
| `archiveResults` | `true` | Archive results as artifacts |
| `verbose` | `true` | Verbose output |

---

## Freestyle Job

For Jenkins Freestyle projects (non-pipeline).

### Setup:

1. **Create a new Freestyle project**

2. **Source Code Management:**
   - Configure your repository (Git/SVN)

3. **Build Environment:**
   - Check "Delete workspace before build starts" (optional)

4. **Build Steps:**

   **Execute Shell:**
   ```bash
   #!/bin/bash
   
   # Install dependencies
   pip3 install -r requirements.txt --break-system-packages

   # Run scan
   python3 run-cli.py -c config/config.yaml -r . -o scan_results.txt -v
   
   # Capture exit code
   SCAN_EXIT=$?
   
   # Exit with scan result
   exit $SCAN_EXIT
   ```

5. **Post-build Actions:**
   - **Archive the artifacts:** `scan_results.txt`
   - **Email Notification** (configure as needed)

---

## Best Practices

### 1. Configuration Management

**Store scanner config in repository:**
```yaml
# config.yaml - committed to repo
prohibited_words_file: prohibited_words.txt
case_sensitive: false
max_file_size_mb: 10
```

**Store sensitive word lists externally:**
```groovy
// In Jenkinsfile
stage('Setup') {
    steps {
        // Copy from Jenkins credentials or secure location
        withCredentials([file(credentialsId: 'prohibited-words', variable: 'WORDS_FILE')]) {
            sh 'cp $WORDS_FILE prohibited_words.txt'
        }
    }
}
```

### 2. Conditional Scanning

**Scan only on certain branches:**
```groovy
stage('Scan') {
    when {
        anyOf {
            branch 'main'
            branch 'develop'
            branch 'release/*'
        }
    }
    steps {
        sh 'python3 run-cli.py -c config.yaml -r . -o results.txt'
    }
}
```

**Scan only changed files:**
```groovy
stage('Scan Changed Files') {
    steps {
        script {
            def changedFiles = sh(
                script: 'git diff --name-only HEAD~1',
                returnStdout: true
            ).trim()
            
            if (changedFiles) {
                sh "python3 run-cli.py -c config.yaml -r ${changedFiles} -o results.txt"
            }
        }
    }
}
```

### 3. Multi-Stage Scanning

**Different rules for different directories:**
```groovy
stage('Scan Source Code') {
    steps {
        sh 'python3 run-cli.py -c config-strict.yaml -r src/ -o src_results.txt'
    }
}

stage('Scan Tests') {
    steps {
        sh 'python3 run-cli.py -c config-relaxed.yaml -r tests/ -o test_results.txt'
    }
}
```

### 4. Parallel Scanning

**Scan multiple directories in parallel:**
```groovy
stage('Parallel Scans') {
    parallel {
        stage('Scan Backend') {
            steps {
                sh 'python3 run-cli.py -c config.yaml -r backend/ -o backend_results.txt'
            }
        }
        stage('Scan Frontend') {
            steps {
                sh 'python3 run-cli.py -c config.yaml -r frontend/ -o frontend_results.txt'
            }
        }
        stage('Scan Infrastructure') {
            steps {
                sh 'python3 run-cli.py -c config.yaml -r infra/ -o infra_results.txt'
            }
        }
    }
}
```

### 5. Integration with Other Tools

**Combine with SonarQube:**
```groovy
stage('Code Quality') {
    parallel {
        stage('Prohibited Words') {
            steps {
                sh 'python3 run-cli.py -c config.yaml -r . -o scan_results.txt'
            }
        }
        stage('SonarQube Analysis') {
            steps {
                withSonarQubeEnv('SonarQube') {
                    sh 'mvn sonar:sonar'
                }
            }
        }
    }
}
```

**Publish to Slack:**
```groovy
post {
    failure {
        script {
            def results = readFile('scan_results.txt')
            def matcher = (results =~ /Found (\d+) violation/)
            def count = matcher ? matcher[0][1] : 'unknown'
            
            slackSend(
                color: 'danger',
                message: "🚨 Prohibited words detected: ${count} violations in ${env.JOB_NAME} #${env.BUILD_NUMBER}\n${env.BUILD_URL}"
            )
        }
    }
}
```

### 6. Artifact Management

**Keep results for a limited time:**
```groovy
post {
    always {
        archiveArtifacts(
            artifacts: 'scan_results.txt',
            allowEmptyArchive: true,
            fingerprint: true,
            onlyIfSuccessful: false
        )
        
        // Clean up old artifacts (keep last 10)
        script {
            def builds = currentBuild.rawBuild.getParent().getBuilds()
            builds.drop(10).each { build ->
                build.deleteArtifacts()
            }
        }
    }
}
```

### 7. Gate Deployment

**Use scan as deployment gate:**
```groovy
stage('Deploy to Production') {
    when {
        branch 'main'
    }
    steps {
        script {
            // Run final scan before deployment
            def exitCode = sh(
                script: 'python3 run-cli.py -c config-production.yaml -r . -v',
                returnStatus: true
            )
            
            if (exitCode != 0) {
                error('Deployment blocked: Prohibited words found!')
            }
            
            // Proceed with deployment
            sh 'kubectl apply -f k8s/'
        }
    }
}
```

---

## Troubleshooting

### Scanner not found
```groovy
// Ensure scanner is in workspace
sh 'ls -la run-cli.py src/scanner.py'
sh 'pwd'
```

### Permission denied
```groovy
// Make sure scripts are executable
sh 'chmod +x run-cli.py'
```

### Dependencies not installing
```groovy
// Use virtual environment
sh '''
    python3 -m venv venv
    . venv/bin/activate
    pip install -r requirements.txt
'''
```

### RPM scanning fails
```groovy
// Install system dependencies in Docker
sh '''
    apt-get update
    apt-get install -y rpm2cpio cpio
'''
```

---

## Example Complete Pipeline

Here's a production-ready pipeline with all best practices:

```groovy
pipeline {
    agent any
    
    parameters {
        choice(name: 'SCAN_MODE', choices: ['FAIL', 'WARN'], description: 'Scan mode')
        booleanParam(name: 'SKIP_SCAN', defaultValue: false, description: 'Skip scanning')
    }
    
    environment {
        SCANNER_CONFIG = 'config.yaml'
        RESULTS_FILE = "scan_results_${env.BUILD_NUMBER}.txt"
    }
    
    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }
        
        stage('Setup Scanner') {
            when {
                expression { !params.SKIP_SCAN }
            }
            steps {
                sh '''
                    pip3 install -r requirements.txt --break-system-packages
                '''
            }
        }
        
        stage('Scan Repository') {
            when {
                expression { !params.SKIP_SCAN }
            }
            steps {
                script {
                    def exitCode = sh(
                        script: "python3 run-cli.py -c ${SCANNER_CONFIG} -r . -o ${RESULTS_FILE} -v",
                        returnStatus: true
                    )
                    
                    env.SCAN_EXIT_CODE = exitCode
                    
                    if (fileExists(RESULTS_FILE)) {
                        def results = readFile(RESULTS_FILE)
                        def matcher = (results =~ /Found (\d+) violation/)
                        env.VIOLATION_COUNT = matcher ? matcher[0][1] : '0'
                        
                        echo results
                    }
                }
            }
        }
        
        stage('Evaluate Results') {
            when {
                expression { !params.SKIP_SCAN }
            }
            steps {
                script {
                    if (env.SCAN_EXIT_CODE == '1') {
                        def message = "${env.VIOLATION_COUNT} prohibited word violations found!"
                        
                        if (params.SCAN_MODE == 'FAIL') {
                            error(message)
                        } else {
                            unstable(message: message)
                            echo "⚠ WARNING: ${message}"
                        }
                    } else {
                        echo '✓ No violations found'
                    }
                }
            }
        }
        
        stage('Build') {
            steps {
                echo 'Building application...'
                // Your build steps here
            }
        }
        
        stage('Test') {
            steps {
                echo 'Running tests...'
                // Your test steps here
            }
        }
    }
    
    post {
        always {
            archiveArtifacts artifacts: "${RESULTS_FILE}", allowEmptyArchive: true
            
            script {
                if (env.VIOLATION_COUNT && env.VIOLATION_COUNT != '0') {
                    addShortText(
                        text: "${env.VIOLATION_COUNT} violations",
                        color: params.SCAN_MODE == 'WARN' ? 'orange' : 'red'
                    )
                }
            }
        }
        
        success {
            echo 'Pipeline completed successfully!'
        }
        
        failure {
            emailext(
                subject: "Build Failed: ${env.JOB_NAME} #${env.BUILD_NUMBER}",
                body: "Check console output at ${env.BUILD_URL}",
                to: '${DEFAULT_RECIPIENTS}'
            )
        }
    }
}
```

---

## Summary

Choose the integration method that best fits your needs:

- **Basic**: Simple fail-on-violation scanning
- **Parameterized**: Flexible with runtime options
- **Docker**: Isolated, consistent environments
- **Shared Library**: Reusable across projects
- **Freestyle**: For non-pipeline jobs

All methods support the same core features: archive extraction, configurable word lists, and detailed reporting.
